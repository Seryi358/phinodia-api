from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import Settings
from app.database import db
from app.services.kie_ai import KieAIClient

router = APIRouter()
settings = Settings()

KIE_STATE_MAP = {
    "waiting": "processing", "queuing": "processing", "generating": "processing",
    "success": "completed", "fail": "failed", "failed": "failed",
}


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int = 0
    result_url: str | None = None
    result_type: str | None = None
    error_message: str | None = None


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    job = await db.select_one("jobs", {"id": f"eq.{job_id}"})
    if not job:
        raise HTTPException(404, "Job not found")

    if job.get("kie_task_id") and job["status"] in ("generating", "processing"):
        kie = KieAIClient(api_key=settings.kie_api_key)
        if job["service_type"].startswith("video_"):
            kie_status = await kie.get_video_status(job["kie_task_id"])
        else:
            kie_status = await kie.get_task_status(job["kie_task_id"])

        mapped_status = KIE_STATE_MAP.get(kie_status["state"], job["status"])

        if mapped_status == "completed" and kie_status["result_urls"]:
            await db.update("jobs", {"id": f"eq.{job_id}"}, {
                "status": "completed",
                "result_url": kie_status["result_urls"][0],
                "result_type": _infer_type(job["service_type"]),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            job["status"] = "completed"
            job["result_url"] = kie_status["result_urls"][0]
            job["result_type"] = _infer_type(job["service_type"])
        elif mapped_status == "failed":
            await db.update("jobs", {"id": f"eq.{job_id}"}, {
                "status": "failed", "error_message": "Generation failed at provider",
            })
            job["status"] = "failed"
            job["error_message"] = "Generation failed at provider"

        return JobStatusResponse(
            job_id=job["id"], status=job["status"],
            progress=kie_status.get("progress", 0),
            result_url=job.get("result_url"), result_type=job.get("result_type"),
            error_message=job.get("error_message"),
        )

    return JobStatusResponse(
        job_id=job["id"], status=job["status"],
        progress=100 if job["status"] == "completed" else 0,
        result_url=job.get("result_url"), result_type=job.get("result_type"),
        error_message=job.get("error_message"),
    )


def _infer_type(service_type: str) -> str:
    if "video" in service_type:
        return "mp4"
    if service_type == "image":
        return "jpg"
    return "html"
