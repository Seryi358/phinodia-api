from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, EmailStr
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

    # Auto-fail stuck jobs (older than 30 minutes still in processing/generating)
    if job["status"] in ("generating", "processing") and job.get("created_at"):
        try:
            created = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
            age_minutes = (datetime.now(timezone.utc) - created).total_seconds() / 60
            if age_minutes > 30:
                await db.update("jobs", {"id": f"eq.{job_id}"}, {
                    "status": "failed",
                    "error_message": "La generacion tomo demasiado tiempo. Tu credito fue restaurado.",
                })
                from app.services.credits import CreditService
                credit_svc = CreditService()
                await credit_svc.refund_credit(job["user_id"], job.get("service_type"))
                job["status"] = "failed"
                job["error_message"] = "La generacion tomo demasiado tiempo. Tu credito fue restaurado."
        except (ValueError, TypeError):
            pass

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


class JobSummary(BaseModel):
    job_id: str
    service_type: str
    status: str
    result_type: str | None = None
    result_url: str | None = None
    input_image_url: str | None = None
    input_description: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


@router.get("/by-email", response_model=list[JobSummary])
async def list_jobs_by_email(email: EmailStr = Query(..., description="User email")):
    """List all jobs for a user, newest first. For result viewing on /mis-generaciones."""
    email = email.strip().lower()
    user = await db.select_one("users", {"email": f"eq.{email}"})
    if not user:
        return []

    jobs = await db.select("jobs", {
        "user_id": f"eq.{user['id']}",
        "order": "created_at.desc",
        "limit": "100",
    })

    summaries = []
    for job in jobs:
        st = job.get("service_type", "")
        rt = job.get("result_type") or _infer_type(st)
        # For landing pages, return job_id link instead of HTML content blob
        result_url = job.get("result_url")
        if rt == "html" and result_url:
            result_url = f"/estado/?job_id={job['id']}"
        summaries.append(JobSummary(
            job_id=job["id"],
            service_type=st,
            status=job.get("status", "unknown"),
            result_type=rt,
            result_url=result_url,
            input_image_url=job.get("input_image_url"),
            input_description=job.get("input_description"),
            created_at=job.get("created_at"),
            completed_at=job.get("completed_at"),
            error_message=job.get("error_message"),
        ))
    return summaries
