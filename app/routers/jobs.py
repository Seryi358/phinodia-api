from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings
from app.database import get_db_session
from app.models import Job
from app.services.kie_ai import KieAIClient

router = APIRouter()
settings = Settings()

KIE_STATE_MAP = {
    "waiting": "processing", "queuing": "processing", "generating": "processing",
    "success": "completed", "fail": "failed",
}


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int = 0
    result_url: str | None = None
    result_type: str | None = None
    error_message: str | None = None


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, session: AsyncSession = Depends(get_db_session)):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")

    if job.kie_task_id and job.status in ("generating", "processing"):
        kie = KieAIClient(api_key=settings.kie_api_key)
        if job.service_type.startswith("video_"):
            kie_status = await kie.get_video_status(job.kie_task_id)
        else:
            kie_status = await kie.get_task_status(job.kie_task_id)
        mapped_status = KIE_STATE_MAP.get(kie_status["state"], job.status)
        if mapped_status == "completed" and kie_status["result_urls"]:
            job.status = "completed"
            job.result_url = kie_status["result_urls"][0]
            job.result_type = _infer_type(job.service_type)
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
        elif mapped_status == "failed":
            job.status = "failed"
            job.error_message = "Generation failed at provider"
            await session.commit()
        return JobStatusResponse(
            job_id=job.id, status=job.status, progress=kie_status.get("progress", 0),
            result_url=job.result_url, result_type=job.result_type, error_message=job.error_message,
        )

    return JobStatusResponse(
        job_id=job.id, status=job.status, progress=100 if job.status == "completed" else 0,
        result_url=job.result_url, result_type=job.result_type, error_message=job.error_message,
    )


def _infer_type(service_type: str) -> str:
    if "video" in service_type:
        return "mp4"
    if service_type == "image":
        return "jpg"
    return "html"
