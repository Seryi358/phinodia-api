import logging
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, EmailStr
from app.config import get_settings
from app.database import db
from app.services.kie_ai import KieAIClient

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

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


@router.api_route("/status/{job_id}", methods=["GET", "HEAD"], response_model=JobStatusResponse)
async def get_job_status(job_id: UUID, request: Request):
    # UUID coercion stops PostgREST filter injection via commas/dots/etc — the
    # path-param annotation makes FastAPI 422 on anything malformed.
    job_id_str = str(job_id)
    job = await db.select_one("jobs", {"id": f"eq.{job_id_str}"})
    if not job:
        raise HTTPException(404, "Job not found")
    job_id = job_id_str  # rest of function uses .format / f-strings on this
    # HEAD must be safe/idempotent (RFC 9110 §9.2.1). Don't run the auto-fail
    # or KIE-poll branches on HEAD — they mutate DB + refund credits and
    # would fire on every link-checker / monitoring probe.
    head_only = request.method == "HEAD"

    # Auto-fail stuck jobs (older than 30 minutes still in processing/generating).
    # CAS-guarded: only the first writer wins, so concurrent polls and the
    # worker can't both refund. Critically: if the row already has a partial
    # result_url (e.g. base 8s saved while extensions slow), mark it
    # completed-with-warning instead of failing+refunding — otherwise the
    # user gets the partial deliverable AND a refund (free product).
    if not head_only and job["status"] in ("generating", "processing") and job.get("created_at"):
        try:
            created = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
            age_minutes = (datetime.now(timezone.utc) - created).total_seconds() / 60
            # Multi-step videos (15s/22s/30s = base + extensions) legitimately
            # take 30-60min when KIE is backed up. Use 60min as both the outer
            # gate AND the promote/refund threshold — refunding at 30min while
            # the worker is still extending would drop the deliverable entirely
            # (the worker's later CAS save would miss against status=failed).
            multi_step = job.get("service_type") in ("video_15s", "video_22s", "video_30s")
            threshold = 60 if multi_step else 30
            if age_minutes > threshold:
                if job.get("result_url"):
                    # Partial deliverable already saved AND worker has had
                    # ample time to finish — promote to completed-with-warning
                    # instead of refunding.
                    partial_msg = "La generacion tomo mas tiempo del esperado pero tu resultado parcial esta listo."
                    promoted = await db.update(
                        "jobs",
                        {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                        {
                            "status": "completed",
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "error_message": partial_msg,
                        },
                    )
                    if promoted:
                        logger.info("Auto-promoted long-running job %s to completed (partial result existed)", job_id)
                        job["status"] = "completed"
                        job["error_message"] = partial_msg
                        # Notify the user — the worker's CAS will miss
                        # ("status in (processing,generating)" no longer
                        # matches), so without this email the user has a
                        # completed job they never hear about. Look up the
                        # user's email since jobs only stores user_id.
                        try:
                            user_row = await db.select_one("users", {"id": f"eq.{job.get('user_id')}"})
                            user_email = user_row.get("email") if user_row else None
                            if user_email:
                                from app.routers.generate import _send_delivery_email_safe
                                import asyncio as _aio
                                await _aio.to_thread(
                                    _send_delivery_email_safe,
                                    user_email,
                                    job.get("input_description", "tu producto") or "tu producto",
                                    job.get("service_type", "video_8s"),
                                    job_id,
                                    job.get("result_url", "") or "",
                                )
                        except Exception as _e:
                            logger.warning("Auto-promote email send failed for %s: %s", job_id, _e)
                else:
                    updated = await db.update(
                        "jobs",
                        {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                        {
                            "status": "failed",
                            "error_message": "La generacion tomo demasiado tiempo. Tu credito fue restaurado.",
                        },
                    )
                    if updated:
                        from app.services.credits import CreditService
                        credit_svc = CreditService()
                        service_type = job.get("service_type")
                        if service_type:
                            await credit_svc.refund_credit(job["user_id"], service_type)
                        else:
                            logger.error("Auto-failed job %s missing service_type — credit not refunded, manual review", job_id)
                        logger.info("Auto-failed stuck job %s (age=%.1fmin)", job_id, age_minutes)
                        job["status"] = "failed"
                        job["error_message"] = "La generacion tomo demasiado tiempo. Tu credito fue restaurado."
                    else:
                        # Another writer already terminated this job; reload to see current state
                        refreshed = await db.select_one("jobs", {"id": f"eq.{job_id}"})
                        if refreshed:
                            job = refreshed
        except (ValueError, TypeError):
            # Force-fail jobs whose created_at is malformed — otherwise they
            # stay "processing" forever and the credit is never refunded.
            # CAS-guard so we don't double-refund a row a worker just finished
            # OR refund a row that already has a partial result_url (would
            # gift the user both refund and product).
            logger.error("Auto-fail timestamp parse failed for job %s: %r — forcing failed", job_id, job.get("created_at"))
            from app.services.credits import CreditService
            credit_svc = CreditService()
            service_type = job.get("service_type")
            uid = job.get("user_id")
            if job.get("result_url"):
                # Partial deliverable exists — promote instead of fail+refund.
                partial_msg = "Tu resultado parcial esta listo."
                promoted = await db.update(
                    "jobs",
                    {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                    {"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
                     "error_message": partial_msg},
                )
                if promoted:
                    job["status"] = "completed"
                    job["error_message"] = partial_msg
            else:
                fail_msg = "Error interno. Tu credito fue restaurado."
                forced = await db.update(
                    "jobs",
                    {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                    {"status": "failed", "error_message": fail_msg},
                )
                if forced and service_type and uid:
                    await credit_svc.refund_credit(uid, service_type)
                elif forced and not uid:
                    logger.error("Auto-fail job %s missing user_id — credit not refunded", job_id)
                if forced:
                    job["status"] = "failed"
                    job["error_message"] = fail_msg

    # Don't auto-poll-and-complete if a partial result_url is already saved.
    # For multi-step pipelines (video extensions), the worker stores the base
    # URL + base task_id mid-chain, then continues extending. A poll here
    # would see the base task as "success", CAS-complete the job, and silently
    # truncate a paid 30s video to 8s — the worker's later extension writes
    # would all CAS-miss against the now-completed status.
    if not head_only and job.get("kie_task_id") and job["status"] in ("generating", "processing") and not job.get("result_url"):
        kie = KieAIClient(api_key=settings.kie_api_key)
        # KIE upstream can be slow/flaky; don't 500 the user — fall back to
        # whatever DB row we have. The worker will keep its own progress.
        try:
            if job["service_type"].startswith("video_"):
                kie_status = await kie.get_video_status(job["kie_task_id"])
            else:
                kie_status = await kie.get_task_status(job["kie_task_id"])
        except Exception as e:
            logger.warning("KIE status poll failed for job %s: %s", job_id, type(e).__name__)
            return JobStatusResponse(
                job_id=job["id"], status=job["status"],
                progress=100 if job["status"] == "completed" else 0,
                result_url=job.get("result_url"), result_type=job.get("result_type"),
                error_message=job.get("error_message"),
            )

        mapped_status = KIE_STATE_MAP.get(kie_status["state"], job["status"])

        if mapped_status == "completed" and kie_status["result_urls"]:
            # CAS-guarded: don't resurrect an auto-failed-and-refunded job.
            await db.update(
                "jobs",
                {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                {
                    "status": "completed",
                    "result_url": kie_status["result_urls"][0],
                    "result_type": _infer_type(job["service_type"]),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error_message": None,
                },
            )
            job["status"] = "completed"
            job["result_url"] = kie_status["result_urls"][0]
            job["result_type"] = _infer_type(job["service_type"])
        elif mapped_status == "failed":
            # KIE reported failure. CAS-guard so we don't clobber a worker's
            # successful "completed" write that landed concurrently. Refund
            # the credit since the worker won't (it'd CAS-fail too).
            failed_now = await db.update(
                "jobs",
                {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                {"status": "failed", "error_message": "El proveedor de IA fallo. Tu credito fue restaurado."},
            )
            if failed_now:
                from app.services.credits import CreditService
                credit_svc = CreditService()
                service_type = job.get("service_type")
                if service_type:
                    await credit_svc.refund_credit(job["user_id"], service_type)
                logger.info("KIE-fail terminal for job %s — credit refunded", job_id)
                job["status"] = "failed"
                job["error_message"] = "El proveedor de IA fallo. Tu credito fue restaurado."

        return JobStatusResponse(
            job_id=job["id"], status=job["status"],
            progress=kie_status.get("progress", 0),
            result_url=_normalize_result(job.get("result_url"), job.get("result_type")),
            result_type=job.get("result_type"),
            error_message=job.get("error_message"),
        )

    return JobStatusResponse(
        job_id=job["id"], status=job["status"],
        progress=100 if job["status"] == "completed" else 0,
        result_url=_normalize_result(job.get("result_url"), job.get("result_type")),
        result_type=job.get("result_type"),
        error_message=job.get("error_message"),
    )


# Strip the legacy internal EasyPanel hostname from URLs AND from any HTML
# payload (landing pages embed the hero image's URL inside <style> background
# rules, which is what was leaking the host into the rendered page).
_INTERNAL_HOST_RE = __import__('re').compile(
    r'https://(?:[a-z0-9-]+\.)?zb12wf\.easypanel\.host', __import__('re').I
)
def _normalize_result(value: str | None, result_type: str | None) -> str | None:
    if not value:
        return value
    public = (settings.api_base_url or "https://app.phinodia.com").rstrip("/")
    return _INTERNAL_HOST_RE.sub(public, value)


# Last-resort layout safety net injected into every landing response.
# The AI sometimes emits sections with vertical-only padding, which
# leaves cards flush to the left edge on desktop. This wrapper rule
# centers everything inside a max 1200px frame WITHOUT touching the
# AI's color/typography decisions.
_LANDING_SAFETY_CSS = """<style>
:root { --ph-max: 1200px; --ph-pad: clamp(20px, 4vw, 64px); }
body { margin: 0; min-width: 320px; }
section, footer, header, main > div, main > article {
    padding-left: max(var(--ph-pad), calc((100vw - var(--ph-max)) / 2));
    padding-right: max(var(--ph-pad), calc((100vw - var(--ph-max)) / 2));
    box-sizing: border-box;
}
section > *, footer > * { max-width: var(--ph-max); margin-left: auto; margin-right: auto; }
img { max-width: 100%; height: auto; display: block; }
@media (max-width: 768px) {
    section, footer, header { padding-left: var(--ph-pad); padding-right: var(--ph-pad); }
}
</style>"""


@router.get("/landing/{job_id}", response_class=HTMLResponse)
async def get_landing_html(job_id: UUID):
    """Serve a completed landing-page job's HTML directly so the /estado
    iframe can load it via `src=` (works under our parent CSP — `srcdoc`
    inherited the parent CSP's `default-src 'self'` which broke the inline
    background-image URLs and rendered the page blank).

    The Content-Security-Policy on the response sandboxes the AI-generated
    HTML so any future template that ships JS can't access the parent.
    """
    job = await db.select_one("jobs", {"id": f"eq.{job_id}"})
    if not job or job.get("result_type") != "html" or not job.get("result_url"):
        raise HTTPException(404, "Landing not found")
    html = _normalize_result(job["result_url"], "html") or ""
    # Inject layout safety CSS as the LAST <style> in <head> so it overrides
    # any AI-generated rule. Without it, GPT-4o-fallback landings render
    # as left-edge-flush sections at desktop widths.
    if "</head>" in html:
        html = html.replace("</head>", _LANDING_SAFETY_CSS + "</head>", 1)
    # Strict CSP for the AI-generated content. `frame-ancestors 'self'`
    # only allows our /estado iframe to embed it; nobody else can iframe
    # it to phish. Drop `default-src 'none'` so inline styles/images work.
    headers = {
        "Content-Security-Policy": (
            "default-src 'self'; "
            # KIE returns gallery images on tempfile.aiquickdraw.com — must
            # allow them so the AI-built gallery actually renders. Earlier
            # CSP only allowed our own origin + ImageKit, so the 4
            # extra-image_urls came back as broken icons.
            "img-src 'self' data: https://app.phinodia.com https://phinodia.com https://ik.imagekit.io https://tempfile.aiquickdraw.com https://*.aiquickdraw.com; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            # Allow inline scripts ONLY: the AI-generated landings include
            # an inline IntersectionObserver for scroll-reveal animations
            # AND for animated counters. With script-src 'none' those
            # scripts never ran, so any element starting at opacity:0
            # stayed invisible forever — the landing rendered as a blank
            # white page. No external script sources are allowed, so an
            # injected <script src="evil.com"> still can't load.
            "script-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'self'; "
            "form-action 'none'; "
            "base-uri 'none'; "
            "object-src 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=html, media_type="text/html", headers=headers)


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


@router.api_route("/by-email", response_model=list[JobSummary], methods=["GET", "HEAD"])
async def list_jobs_by_email(
    email: EmailStr = Query(..., description="User email"),
    limit: int = Query(100, ge=1, le=200, description="Max jobs returned (1-200)"),
    offset: int = Query(0, ge=0, le=10000, description="Skip first N jobs"),
):
    """List jobs for a user, newest first. Paginated so power users with
    >100 generations can scroll back through history."""
    email = email.strip().lower()
    user = await db.select_one("users", {"email": f"eq.{email}"})
    if not user:
        return []

    jobs = await db.select("jobs", {
        "user_id": f"eq.{user['id']}",
        "order": "created_at.desc",
        "limit": str(limit),
        "offset": str(offset),
    })

    summaries = []
    # Legacy rows hold the EasyPanel internal hostname in input_image_url
    # AND inside the landing-page HTML (background-image: url(...) inside
    # <style>). Normalize on serialization so /mis-generaciones thumbnails,
    # download links and rendered landings all use the public hostname.
    for job in jobs:
        st = job.get("service_type", "")
        rt = job.get("result_type") or _infer_type(st)
        # For landing pages, return job_id link instead of HTML content blob
        result_url = job.get("result_url")
        if rt == "html" and result_url:
            result_url = f"/estado/?job_id={job['id']}"
        else:
            result_url = _normalize_result(result_url, rt)
        summaries.append(JobSummary(
            job_id=job["id"],
            service_type=st,
            status=job.get("status", "unknown"),
            result_type=rt,
            result_url=result_url,
            input_image_url=_normalize_result(job.get("input_image_url"), "url"),
            input_description=job.get("input_description"),
            created_at=job.get("created_at"),
            completed_at=job.get("completed_at"),
            error_message=job.get("error_message"),
        ))
    return summaries
