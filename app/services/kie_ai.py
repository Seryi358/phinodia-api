import json
import logging
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)

KIE_BASE_URL = "https://api.kie.ai/api/v1"
GPT_IMAGE_2_TEXT_MODEL = "gpt-image-2-text-to-image"
GPT_IMAGE_2_EDIT_MODEL = "gpt-image-2-image-to-image"


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
        if resp.status_code >= 400:
            raise httpx.HTTPError(f"KIE AI HTTP error {resp.status_code}")

    # ── Video (VEO 3.1) ──────────────────────────────────────────────

    async def create_video_task(
        self,
        prompt: str,
        image_url: str,
        aspect_ratio: str = "9:16",
        model: str = "veo3",
        use_image: bool = True,
    ) -> str:
        """Generate base 8s video using VEO 3.1 Quality mode."""
        if use_image and image_url:
            body = {
                "prompt": prompt,
                "imageUrls": [image_url],
                "model": model,
                "generationType": "IMAGE_2_VIDEO",
                "aspect_ratio": aspect_ratio,
                "quality": "high",
                "enableTranslation": False,
            }
        else:
            body = {
                "prompt": prompt,
                "model": model,
                "generationType": "TEXT_2_VIDEO",
                "aspect_ratio": aspect_ratio,
                "quality": "high",
                "enableTranslation": False,
            }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{KIE_BASE_URL}/veo/generate",
                json=body,
                headers=self.headers,
            )
            self._check_status(resp)
            return resp.json()["data"]["taskId"]

    async def extend_video(self, task_id: str, prompt: str, model: str = "fast") -> str:
        """Extend video by +7 seconds"""
        body = {
            "taskId": task_id,
            "prompt": prompt,
            "model": model,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{KIE_BASE_URL}/veo/extend",
                json=body,
                headers=self.headers,
            )
            self._check_status(resp)
            return resp.json()["data"]["taskId"]

    async def get_video_status(self, task_id: str) -> dict:
        """Poll VEO 3.1 task status"""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KIE_BASE_URL}/veo/record-info",
                params={"taskId": task_id},
                headers=self.headers,
            )
            self._check_status(resp)
            data = resp.json().get("data", {})

        # VEO uses successFlag: 0=generating, 1=success, 2=failed, 3=gen_failed
        success_flag = data.get("successFlag", 0)
        state_map = {0: "generating", 1: "success", 2: "failed", 3: "failed"}
        state = state_map.get(success_flag, "generating")

        # Video URL can be in data.videoUrl or data.response.resultUrls
        video_url = data.get("videoUrl", "")
        if not video_url:
            response = data.get("response") or {}
            result_urls = response.get("resultUrls") or []
            video_url = result_urls[0] if result_urls else ""
        video_url = _safe_result_url(video_url)

        return {
            "state": state,
            "progress": 100 if state == "success" else (50 if state == "generating" else 0),
            "result_urls": [video_url] if video_url else [],
        }

    async def get_hd_video(self, task_id: str) -> str | None:
        """Get 1080p version of completed video"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{KIE_BASE_URL}/veo/get-1080p-video",
                params={"taskId": task_id},
                headers=self.headers,
            )
            self._check_status(resp)
            data = resp.json().get("data", {})
            return data.get("videoUrl")

    # ── Images (GPT Image 2) ──────────────────────────────────────────

    async def create_image_task(
        self,
        prompt: str,
        image_url: str,
        aspect_ratio: str = "1:1",
    ) -> str:
        input_payload = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
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
            data = resp.json()
            return data["data"]["taskId"]

    async def get_task_status(self, task_id: str) -> dict:
        """Poll general KIE task status (used for images)."""
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

        return {
            "state": data.get("state", "unknown"),
            "progress": data.get("progress", 0),
            "result_urls": result_urls,
        }
