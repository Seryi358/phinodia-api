import json
import httpx

KIE_BASE_URL = "https://api.kie.ai/api/v1"


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
        model: str = "veo3_fast",
    ) -> str:
        """Generate base 8s video from first frame image + prompt using VEO 3.1"""
        body = {
            "prompt": prompt,
            "imageUrls": [image_url],
            "model": model,
            "generationType": "FIRST_AND_LAST_FRAMES_2_VIDEO",
            "aspect_ratio": aspect_ratio,
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

        video_url = data.get("videoUrl", "")

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

    # ── Images (Nano Banana Pro — unchanged) ──────────────────────────

    async def create_image_task(
        self,
        prompt: str,
        image_url: str,
        aspect_ratio: str = "1:1",
    ) -> str:
        body = {
            "model": "nano-banana-pro",
            "input": {
                "prompt": prompt,
                "image_input": [image_url],
                "aspect_ratio": aspect_ratio,
                "resolution": "2K",
                "output_format": "jpg",
            },
        }
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
            data = resp.json()["data"]

        result_urls = []
        if data.get("resultJson"):
            try:
                parsed = json.loads(data["resultJson"])
                result_urls = parsed.get("resultUrls", [])
            except (json.JSONDecodeError, KeyError):
                pass

        return {
            "state": data.get("state", "unknown"),
            "progress": data.get("progress", 0),
            "result_urls": result_urls,
        }
