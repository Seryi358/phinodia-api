import json
import logging
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)

KIE_BASE_URL = "https://api.kie.ai/api/v1"
GPT_IMAGE_2_TEXT_MODEL = "gpt-image-2-text-to-image"
GPT_IMAGE_2_EDIT_MODEL = "gpt-image-2-image-to-image"
OMNI_VIDEO_MODEL = "gemini-omni-video"


def _safe_result_url(u: str | None) -> str:
    """Reject anything that isn't an https URL — KIE response data lands in
    delivery emails and the `result_url` DB column, so a `javascript:` or
    `file:` URL would be a stored phishing link sent from our domain.
    """
    if not u or not isinstance(u, str):
        return ""
    parsed = urlparse(u)
    if parsed.scheme != "https" or not parsed.netloc:
        logger.warning("KIE returned non-https result_url %r — dropped", u[:120])
        return ""
    return u


class KieAIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _check_status(self, resp: httpx.Response) -> None:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        msg = ""
        if isinstance(payload, dict):
            msg = payload.get("msg") or payload.get("message") or ""
        if resp.status_code >= 400:
            detail = msg or resp.text[:200] or "unknown error"
            raise httpx.HTTPError(f"KIE AI HTTP error {resp.status_code}: {detail}")
        code = payload.get("code") if isinstance(payload, dict) else None
        if code not in (None, 200, "200"):
            raise httpx.HTTPError(f"KIE AI API error {code}: {msg or 'unknown error'}")

    @staticmethod
    def _extract_task_id(resp: httpx.Response) -> str:
        data = (resp.json() or {}).get("data") or {}
        task_id = data.get("taskId")
        if not task_id:
            raise httpx.HTTPError("KIE AI response missing taskId")
        return task_id

    # ── Video (Gemini Omni) ──────────────────────────────────────────
    # Omni runs on the unified /jobs/createTask + /jobs/recordInfo endpoints
    # (same as gpt-image-2), NOT the old /veo/* endpoints. A single generation
    # is capped at 10s; longer videos are built by stitching clips (see
    # app/services/video_stitch.py).

    async def create_omni_character(
        self,
        descriptions: str,
        image_url: str,
        character_name: str = "phinodia",
    ) -> str | None:
        """Create a reusable omni character from one reference photo.

        Synchronous: the characterId is returned directly in the response. Used
        to lock avatar identity across stitched clips. Best-effort — on any
        failure we return None and the caller falls back to first-frame + fixed
        seed consistency only (never fail a paid job over an optional id).
        """
        # KIE docs are inconsistent (`descriptions` in the schema, `description`
        # in the example) — send both keys so whichever the gateway validates
        # against is present.
        body = {
            "descriptions": descriptions,
            "description": descriptions,
            "image_urls": [image_url],
            "character_name": character_name,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{KIE_BASE_URL}/omni/character/create",
                    json=body,
                    headers=self.headers,
                )
            self._check_status(resp)
            data = (resp.json() or {}).get("data") or {}
            return data.get("characterId") or None
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("omni character create failed: %s", type(e).__name__)
            return None

    async def create_omni_video_task(
        self,
        prompt: str,
        image_url: str = "",
        reference_image_url: str = "",
        character_ids: list[str] | None = None,
        seed: int | None = None,
        duration: str = "10",
        aspect_ratio: str = "9:16",
        resolution: str = "720p",
    ) -> str:
        """Create a Gemini-Omni video task (<=10s).

        image_url is the first frame (gpt-image-2 output, or the previous clip's
        last frame for the continuation/last-frame relay).

        reference_image_url is the ORIGINAL product photo, attached as a second
        reference on EVERY clip so the product's packaging/label/brand text stays
        identical and doesn't drift across stitched clips — without it omni
        regenerates the product on later clips and can invent a different name.
        character_ids + a fixed seed keep the avatar consistent across clips.
        """
        images: list[str] = []
        if image_url:
            images.append(image_url)
        if reference_image_url and reference_image_url != image_url:
            images.append(reference_image_url)
        inp: dict = {
            "prompt": prompt,
            "duration": str(duration),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if images:
            inp["image_urls"] = images[:7]
        if character_ids:
            inp["character_ids"] = character_ids[:3]
        if seed is not None:
            inp["seed"] = int(seed)
        body = {"model": OMNI_VIDEO_MODEL, "input": inp}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{KIE_BASE_URL}/jobs/createTask",
                json=body,
                headers=self.headers,
            )
            self._check_status(resp)
            return self._extract_task_id(resp)

    # ── Images (GPT Image 2) ──────────────────────────────────────────

    async def create_image_task(
        self,
        prompt: str,
        image_url: str,
        aspect_ratio: str = "1:1",
        resolution: str = "1K",
    ) -> str:
        input_payload = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "nsfw_checker": False,
        }
        model = GPT_IMAGE_2_TEXT_MODEL
        if image_url:
            model = GPT_IMAGE_2_EDIT_MODEL
            input_payload["input_urls"] = [image_url]
        body = {"model": model, "input": input_payload}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{KIE_BASE_URL}/jobs/createTask",
                json=body,
                headers=self.headers,
            )
            self._check_status(resp)
            return self._extract_task_id(resp)

    async def get_task_status(self, task_id: str) -> dict:
        """Poll a KIE jobs/createTask task — used for BOTH gpt-image-2 images
        and gemini-omni-video clips (both run on the unified jobs endpoint).

        Omni states: waiting | queuing | generating | success | fail. On
        success, data.resultJson is a JSON *string* {"resultUrls": [...]} that
        must be parsed.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KIE_BASE_URL}/jobs/recordInfo",
                params={"taskId": task_id},
                headers=self.headers,
            )
            self._check_status(resp)
            # KIE's gateway sometimes returns 200 with {"code":500,"msg":"err"} —
            # `.get("data") or {}` turns that into a graceful "still generating"
            # instead of a KeyError that aborts the worker.
            data = (resp.json() or {}).get("data") or {}

        raw_urls = []
        if data.get("resultJson"):
            try:
                parsed = json.loads(data["resultJson"])
                raw_urls = parsed.get("resultUrls") or parsed.get("urls") or []
            except (json.JSONDecodeError, KeyError):
                pass
        result_urls = [u for u in (_safe_result_url(x) for x in raw_urls) if u]
        state = data.get("state", "unknown")
        error = ""
        if state in ("fail", "failed"):
            error = f"{data.get('failCode') or ''}: {data.get('failMsg') or ''}".strip(": ").strip()

        return {
            "state": state,
            "progress": data.get("progress", 0),
            "result_urls": result_urls,
            # creditsConsumed lets the worker reconcile real cost vs the credits
            # charged to the customer (cost-control safety net).
            "credits": data.get("creditsConsumed") or data.get("costCredits"),
            "error": error,
        }
