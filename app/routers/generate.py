import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.config import get_settings
from app.database import db
from app.services.kie_ai import KieAIClient
from app.services.script_generator import ScriptGenerator
from app.services.credits import CreditService
from app.services.gmail import GmailSender, build_delivery_email
from app.services.media_probe import is_multi_step_video_service, is_video_duration_sufficient
from app.services.result_storage import persist_external_url

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


def _send_delivery_email_safe(email: str, product_name: str, service_type: str, job_id: str, result_url: str):
    """Send delivery email asynchronously. For landing pages, link to /estado/?job_id=..."""
    try:
        if service_type == "landing_page":
            link = f"https://app.phinodia.com/estado/?job_id={job_id}"
        else:
            link = f"{settings.api_base_url.rstrip('/')}/api/v1/jobs/download/{job_id}"
        subject, html = build_delivery_email(product_name, service_type, link)
        sender = GmailSender(
            client_id=settings.gmail_client_id, client_secret=settings.gmail_client_secret,
            refresh_token=settings.gmail_refresh_token, sender_email=settings.gmail_sender_email,
        )
        sender.send_email(to=email, subject=subject, html_body=html)
        # Don't log the email address — Habeas Data (Ley 1581). job_id is enough.
        logger.info("Delivery email sent for job %s", job_id)
    except Exception as e:
        logger.warning("Failed to send delivery email for job %s: %s", job_id, type(e).__name__)


async def _fail_video_job(job_id: str, req, message: str) -> bool:
    credit_svc = CreditService()
    service_type = DURATION_TO_SERVICE.get(req.duration, "video_8s")
    user = await credit_svc.get_or_create_user(req.email)
    failed_now = await db.update(
        "jobs",
        {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
        {
            "status": "failed",
            "result_url": None,
            "result_type": None,
            "error_message": message,
        },
    )
    if failed_now:
        await credit_svc.refund_credit(user["id"], service_type)
    return bool(failed_now)

# Hold references to background tasks to prevent GC collection
_background_tasks: set = set()

DURATION_TO_SERVICE = {8: "video_8s", 15: "video_15s", 22: "video_22s", 30: "video_30s"}
FORMAT_TO_ASPECT = {"portrait": "9:16", "landscape": "16:9"}
DURATION_EXTENSIONS = {8: 0, 15: 1, 22: 2, 30: 3}

# Max retries for KIE AI generation failures
MAX_RETRIES = 3
VIDEO_RENDER_RETRIES = 2

# User-friendly error messages based on KIE AI errors
ERROR_MESSAGES = {
    "timed out": "La generacion tomo demasiado tiempo. Por favor intenta de nuevo con una descripcion mas corta.",
    "content policy": "Tu solicitud fue rechazada por politicas de contenido. Intenta con una descripcion diferente que no incluya contenido sensible.",
    "rate limit": "El servicio esta saturado en este momento. Tu credito fue restaurado, intenta de nuevo en unos minutos.",
    "invalid": "Hubo un problema con los datos enviados. Verifica tu imagen y descripcion e intenta de nuevo.",
}


def _friendly_error(raw_error: str) -> str:
    """Convert technical KIE AI errors to user-friendly Spanish messages."""
    lower = raw_error.lower()
    for key, msg in ERROR_MESSAGES.items():
        if key in lower:
            return msg
    return f"Hubo un error en la generacion. Por favor intenta de nuevo. Si el problema persiste, contactanos a scastellanos@phinodia.com"


def _escape_braces(s: str) -> str:
    """Escape literal curly braces so they survive str.format() in prompt templates.
    Without this, a user submitting product_name='Cool {brand}' crashes the
    worker with KeyError: 'brand' and burns a credit on guaranteed failure.
    """
    if not s:
        return s
    return s.replace("{", "{{").replace("}", "}}")


def _build_description(req) -> str:
    """Combine all user-provided fields into a rich description for the AI agents."""
    parts = []
    if hasattr(req, 'product_category') and req.product_category:
        parts.append(f"Tipo de producto: {req.product_category}")
    if hasattr(req, 'pain_point') and req.pain_point:
        parts.append(f"Problema que resuelve: {req.pain_point}")
    if hasattr(req, 'target_audience') and req.target_audience:
        parts.append(f"Publico objetivo: {req.target_audience}")
    if req.description:
        parts.append(f"Instrucciones del usuario: {req.description}")
    return "\n".join(parts) if parts else req.description


def _strip_required(v: str) -> str:
    """Reject whitespace-only required text fields. min_length=1 alone passes
    "   " which then sends a useless prompt to OpenAI and burns a credit on
    a guaranteed-bad generation."""
    v = (v or "").strip()
    if not v:
        raise ValueError("Cannot be empty or whitespace-only")
    return v


def _normalize_whatsapp(v: str) -> str:
    """Strip non-digits from WhatsApp; default-prefix Colombian 10-digit
    numbers with 57 so wa.me works. Empty stays empty (CTA falls back to
    a #comprar anchor)."""
    v = (v or "").strip()
    if not v:
        return ""
    digits = "".join(c for c in v if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 10 and digits.startswith("3"):
        digits = "57" + digits
    if len(digits) < 10 or len(digits) > 15:
        raise ValueError("whatsapp_number must be 10-15 digits (or include country code)")
    return digits


def _validate_image_url(v: str) -> str:
    """image_url must reference an upload from our /uploads/ directory.

    Locking down to our own host prevents (a) bypassing upload's size/magic-byte
    checks, (b) SSRF/cost-amplification by pointing KIE AI at attacker URLs, and
    (c) unbounded-cost downloads of arbitrary external files.
    """
    v = (v or "").strip()
    if not v:
        raise ValueError("image_url is required")
    if not v.startswith("https://"):
        # Drop http://: every legitimate uploads URL is HTTPS, allowing http
        # let SSRF/MITM payloads sneak in (a victim's hijacked subdomain on
        # the same parent domain could become a fake uploads source).
        raise ValueError("image_url must start with https://")
    # Accept either the absolute api_base_url + /uploads/ OR a future relative
    # path (defense-in-depth — also normalize trailing slash).
    base = (settings.api_base_url or "").rstrip("/")
    allowed_prefix = f"{base}/uploads/"
    if not v.startswith(allowed_prefix):
        raise ValueError("image_url must come from PhinodIA uploads")
    # Strict format match: tail must be exactly <32-hex>.{jpg,png,webp}.
    # Earlier loose check ("/" or ".." in tail) let `?evil=1` and `#frag`
    # sneak through, plus accepted any extension. Lock to what /upload/image
    # actually produces.
    tail = v[len(allowed_prefix):]
    import re as _re
    if not _re.fullmatch(r'[a-f0-9]{32}\.(jpg|png|webp)', tail):
        raise ValueError("image_url has invalid filename")
    return v


class VideoRequest(BaseModel):
    email: EmailStr
    image_url: str = Field(..., max_length=2000)
    description: str = Field(..., min_length=1, max_length=2000)
    # Locked to UI choices — prevents tier-hopping (charging 8s credits while
    # claiming 22s) and silent format defaults via tampered hidden inputs.
    format: Literal["portrait", "landscape"]
    duration: Literal[8, 15, 22, 30]
    product_name: str = Field(..., min_length=1, max_length=200)
    product_category: str = Field("", max_length=300)
    pain_point: str = Field("", max_length=300)
    creative_direction: str = Field("", max_length=500)
    data_consent: bool

    _v_url = field_validator("image_url")(lambda cls, v: _validate_image_url(v))
    _v_strip = field_validator("description", "product_name")(lambda cls, v: _strip_required(v))


class ImageRequest(BaseModel):
    email: EmailStr
    image_url: str = Field(..., max_length=2000)
    description: str = Field(..., min_length=1, max_length=2000)
    aspect_ratio: Literal["1:1", "9:16", "16:9", "4:5"] = "1:1"
    product_name: str = Field(..., min_length=1, max_length=200)
    product_category: str = Field("", max_length=300)
    creative_direction: str = Field("", max_length=500)
    image_style: Literal["product", "ugc"] = "product"
    data_consent: bool

    _v_url = field_validator("image_url")(lambda cls, v: _validate_image_url(v))
    _v_strip = field_validator("description", "product_name")(lambda cls, v: _strip_required(v))


class LandingRequest(BaseModel):
    email: EmailStr
    image_url: str = Field(..., max_length=2000)
    description: str = Field(..., min_length=1, max_length=2000)
    product_name: str = Field(..., min_length=1, max_length=200)
    product_category: str = Field("", max_length=300)
    target_audience: str = Field("", max_length=300)
    style_preference: str = Field("", max_length=200)
    # Pricing/offer fields — all optional. If price is empty, the prompt
    # falls back to "AI inventa precio razonable" instead of leaving the
    # CTA section blank. Capped at 2 billion COP to block junk like
    # 99999999999 from making the rendered "$" look broken.
    price: int = Field(0, ge=0, le=2_000_000_000)
    original_price: int = Field(0, ge=0, le=2_000_000_000)
    discount_percent: int = Field(0, ge=0, le=99)
    stock_urgency: str = Field("", max_length=200)
    guarantee: str = Field("", max_length=200)
    bonus: str = Field("", max_length=300)
    # Contact + offer detail — all optional. whatsapp_number drives the CTA
    # destination (without it, buttons go to a #comprar anchor that does
    # nothing). key_benefits stops Opus from inventing benefits the user
    # didn't ask for. shipping_info fills the offer story in S11.
    whatsapp_number: str = Field("", max_length=20)
    key_benefits: str = Field("", max_length=1000)
    shipping_info: str = Field("", max_length=200)
    data_consent: bool

    _v_url = field_validator("image_url")(lambda cls, v: _validate_image_url(v))
    _v_strip = field_validator("description", "product_name")(lambda cls, v: _strip_required(v))
    _v_wa = field_validator("whatsapp_number")(lambda cls, v: _normalize_whatsapp(v))


class GenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str


async def _poll_until_done(kie: KieAIClient, task_id: str, is_video: bool, max_polls: int = 120, interval: float = 5.0) -> dict:
    for _ in range(max_polls):
        if is_video:
            status = await kie.get_video_status(task_id)
        else:
            status = await kie.get_task_status(task_id)
        if status["state"] in ("success", "failed", "fail"):
            return status
        await asyncio.sleep(interval)
    return {"state": "failed", "progress": 0, "result_urls": []}


async def _retry_kie_task(
    kie: KieAIClient,
    create_fn,
    poll_is_video: bool,
    max_retries: int = MAX_RETRIES,
    require_result_url: bool = True,
) -> tuple[str, dict]:
    """Retry KIE AI task creation + polling up to max_retries times."""
    last_error = None
    for attempt in range(max_retries):
        try:
            task_id = await create_fn()
            status = await _poll_until_done(kie, task_id, is_video=poll_is_video, max_polls=90)
            if status["state"] == "success" and (status.get("result_urls") or not require_result_url):
                return task_id, status
            if status["state"] == "success":
                last_error = "KIE task completed without result URLs"
                logger.warning("KIE task %s completed without result URLs on attempt %d", task_id, attempt + 1)
            else:
                last_error = f"Attempt {attempt + 1} failed"
            logger.warning("KIE task %s failed on attempt %d", task_id, attempt + 1)
        except Exception as e:
            last_error = str(e)
            logger.warning("KIE task creation failed on attempt %d: %s", attempt + 1, e)
        if attempt < max_retries - 1:
            await asyncio.sleep(5)
    return "", {"state": "failed", "result_urls": [], "error": last_error}


async def _retry_video_extension(
    *,
    kie: KieAIClient,
    parent_task_id: str,
    prompt: str,
    job_id: str,
    max_retries: int = 2,
) -> tuple[str, dict]:
    """Retry a Veo extension from the same parent task before failing.

    KIE's extension docs explicitly call out multi-minute processing times
    and recommend retry/error handling in production. A single extension
    miss should not immediately fail a paid 15s/22s/30s job.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            ext_task_id = await kie.extend_video(
                task_id=parent_task_id,
                prompt=prompt,
                model="quality",
            )
            still_active = await db.update(
                "jobs",
                {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                {"kie_task_id": ext_task_id},
            )
            if not still_active:
                logger.info("Video job %s terminated mid-extension retry — aborting", job_id)
                return "", {"state": "terminated", "result_urls": []}
            status = await _poll_until_done(kie, ext_task_id, is_video=True)
            if status["state"] == "success" and status.get("result_urls"):
                return ext_task_id, status
            if status["state"] == "success":
                last_error = "extension completed without result URLs"
                logger.warning(
                    "Extension task %s completed without result URLs on attempt %d for job %s",
                    ext_task_id,
                    attempt + 1,
                    job_id,
                )
            else:
                last_error = f"extension attempt {attempt + 1} failed"
                logger.warning(
                    "Extension task %s failed on attempt %d for job %s",
                    ext_task_id,
                    attempt + 1,
                    job_id,
                )
        except Exception as e:
            last_error = str(e)
            logger.warning(
                "Extension creation failed on attempt %d for job %s: %s",
                attempt + 1,
                job_id,
                e,
            )
        if attempt < max_retries - 1:
            await asyncio.sleep(5)
    return "", {"state": "failed", "result_urls": [], "error": last_error}


async def _render_video_with_extensions(
    *,
    kie: KieAIClient,
    script_gen: ScriptGenerator,
    job_id: str,
    req: VideoRequest,
    prompt: str,
    first_frame_url: str,
    aspect: str,
    product_analysis: str,
    buyer_persona: str,
    multi_step_video: bool,
    service_type: str,
) -> tuple[str | None, str | None]:
    """Render base Veo video plus extensions, returning a final URL or an error.

    Returns:
      (url, None) on success
      (None, "terminated") if the job was already failed/cancelled elsewhere
      (None, user_facing_error_message) on failure
    """

    async def _create_video_with_image():
        return await kie.create_video_task(prompt=prompt, image_url=first_frame_url, aspect_ratio=aspect, use_image=True)

    async def _create_video_text_only():
        return await kie.create_video_task(prompt=prompt, image_url="", aspect_ratio=aspect, use_image=False)

    task_id, base_status = await _retry_kie_task(kie, _create_video_with_image, poll_is_video=True, max_retries=2)
    if base_status["state"] != "success":
        logger.info("Image-seeded Veo generation failed for job %s, falling back to TEXT_2_VIDEO", job_id)
        task_id, base_status = await _retry_kie_task(kie, _create_video_text_only, poll_is_video=True, max_retries=2)
    if base_status["state"] != "success":
        return None, _friendly_error(base_status.get("error", "unknown"))

    base_result_url = base_status["result_urls"][0] if base_status.get("result_urls") else ""
    saved = await db.update(
        "jobs",
        {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
        {
            "kie_task_id": task_id,
            "result_type": "mp4",
        },
    )
    if not saved:
        logger.info("Video job %s already terminated; not saving state", job_id)
        return None, "terminated"

    num_extensions = DURATION_EXTENSIONS.get(req.duration, 0)
    current_task_id = task_id
    final_result_url = base_result_url
    for ext_num in range(1, num_extensions + 1):
        ext_prompt = await script_gen.generate_extension_prompt(
            original_prompt=prompt,
            extension_number=ext_num,
            duration=req.duration,
            product_name=req.product_name,
            product_analysis=product_analysis,
            buyer_persona=buyer_persona,
        )
        try:
            ext_task_id, ext_status = await _retry_video_extension(
                kie=kie,
                parent_task_id=current_task_id,
                prompt=ext_prompt,
                job_id=job_id,
                max_retries=2,
            )
            if ext_status["state"] == "terminated":
                return None, "terminated"
            ext_url = ext_status["result_urls"][0] if ext_status.get("result_urls") else None
            if ext_status["state"] != "success" or not ext_url:
                return None, "No pudimos completar el video con la duracion solicitada. Tu credito fue restaurado."
            current_task_id = ext_task_id
            final_result_url = ext_url
        except Exception as e:
            logger.exception("Extension %d failed for job %s: %s", ext_num, job_id, e)
            return None, "No pudimos completar el video con la duracion solicitada. Tu credito fue restaurado."

    final_result_url = await persist_external_url(final_result_url, job_id, "mp4")
    if multi_step_video and not await is_video_duration_sufficient(final_result_url, service_type):
        return None, "El video generado no alcanzo la duracion solicitada. Tu credito fue restaurado."
    return final_result_url, None


async def _process_video(job_id: str, req: VideoRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    rich_description = _build_description(req)
    service_type = DURATION_TO_SERVICE.get(req.duration, "video_8s")
    multi_step_video = is_multi_step_video_service(service_type)
    try:
        # Step 1: Deep product analysis
        product_analysis = await script_gen.analyze_product(
            product_name=req.product_name, description=rich_description, image_url=req.image_url,
        )

        # Step 2: Buyer persona (Colombian UGC creator)
        buyer_persona = await script_gen.generate_buyer_persona(
            product_name=req.product_name, product_analysis=product_analysis,
        )

        # Step 3: Generate first frame with GPT Image 2 (POV selfie, no phone visible)
        first_frame_prompt = await script_gen.generate_image_prompt(
            product_name=req.product_name, description=rich_description,
            aspect_ratio=FORMAT_TO_ASPECT.get(req.format, "9:16"),
            creative_direction=(
                "Close-up arm's-length front-camera selfie. The creator matches the buyer persona, "
                "looks into the lens, and holds the real product at chest level with the packaging clearly visible. "
                "Natural home setting, candid phone-photo realism, slight imperfections, no visible phone in frame."
            ),
            product_analysis=product_analysis,
            buyer_persona=buyer_persona,
            prompt_mode="video_first_frame",
        )

        # Retry first frame generation
        aspect = FORMAT_TO_ASPECT.get(req.format, "9:16")
        ff_task_id, ff_status = await _retry_kie_task(
            kie,
            lambda: kie.create_image_task(prompt=first_frame_prompt, image_url=req.image_url, aspect_ratio=aspect),
            poll_is_video=False, max_retries=2,
        )
        first_frame_url = ff_status["result_urls"][0] if ff_status["state"] == "success" and ff_status["result_urls"] else req.image_url

        # Step 4: Generate video prompt (AIDA, Colombian Spanish, raw/imperfect)
        prompt = await script_gen.generate_video_prompt(
            product_name=req.product_name, description=rich_description,
            duration=req.duration, format_type=req.format,
            creative_direction=req.creative_direction,
            product_analysis=product_analysis, buyer_persona=buyer_persona,
        )
        # CAS: only flip processing→generating. If auto-fail already moved
        # the job to "failed", abort the pipeline so we don't re-arm a
        # refunded job (which would also expose us to a 2nd auto-refund).
        still_active = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {"generated_prompt": prompt, "status": "generating"},
        )
        if not still_active:
            logger.info("Video job %s already terminated by auto-fail — aborting", job_id)
            return

        # Step 5/6: Generate base video + extensions. If the final long-form
        # asset still comes back short or an extension fails in a recoverable
        # way, rerun the full long-video render once before refunding.
        final_result_url = None
        render_error = "No pudimos completar el video con la duracion solicitada. Tu credito fue restaurado."
        for render_attempt in range(1, VIDEO_RENDER_RETRIES + 1):
            final_result_url, render_error = await _render_video_with_extensions(
                kie=kie,
                script_gen=script_gen,
                job_id=job_id,
                req=req,
                prompt=prompt,
                first_frame_url=first_frame_url,
                aspect=aspect,
                product_analysis=product_analysis,
                buyer_persona=buyer_persona,
                multi_step_video=multi_step_video,
                service_type=service_type,
            )
            if final_result_url:
                break
            if render_error == "terminated":
                return
            logger.warning(
                "Video render attempt %d/%d failed for job %s: %s",
                render_attempt,
                VIDEO_RENDER_RETRIES,
                job_id,
                render_error,
            )
            if render_attempt < VIDEO_RENDER_RETRIES:
                await asyncio.sleep(5)
        if not final_result_url:
            await _fail_video_job(job_id, req, render_error)
            return
        # CAS-guarded so an auto-failed-and-refunded row can't be resurrected
        # to "completed" (which would leave the user with both a video and a
        # refund). Clear error_message in case auto-fail had set one.
        completed_now = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {
                "status": "completed", "result_url": final_result_url, "result_type": "mp4",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error_message": None,
            },
        )
        # Only send the delivery email if WE actually flipped to completed.
        # Otherwise the auto-fail or another path already terminated the job
        # and may have already messaged the user (or refunded).
        if completed_now:
            await asyncio.to_thread(_send_delivery_email_safe, req.email, req.product_name, DURATION_TO_SERVICE.get(req.duration, "video_8s"), job_id, final_result_url)

    except (Exception, asyncio.CancelledError) as e:
        # CancelledError must be caught explicitly — it's BaseException, not
        # Exception, so a bare `except Exception` would let lifespan-drain
        # cancellations skip the refund/checkpoint and leave the user's
        # credit trapped on a "generating" row until the auto-fail sweep.
        logger.exception("Video pipeline failed for job %s: %s", job_id, type(e).__name__)
        # Re-read the job to see if a partial result_url was already saved
        # (e.g. 8s flow completed before a cancellation landed). Multi-step
        # videos intentionally do NOT ship partials: if the requested duration
        # wasn't completed, the credit must be restored.
        current = await db.select_one("jobs", {"id": f"eq.{job_id}"})
        if current and current.get("result_url") and not multi_step_video:
            # Mirror the partial KIE URL onto our own /uploads/results/ so
            # the user can still access it after KIE's tempfile expires.
            mirrored = await persist_external_url(current["result_url"], job_id, "mp4")
            await db.update(
                "jobs",
                {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                {
                    "status": "completed",
                    "result_url": mirrored,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error_message": "La generacion fue interrumpida pero tu video parcial esta listo.",
                },
            )
            if isinstance(e, asyncio.CancelledError):
                raise  # re-raise after checkpoint so asyncio task state is correct
            return
        if current and current.get("result_url") and multi_step_video:
            enough = await is_video_duration_sufficient(current["result_url"], service_type)
            if enough:
                mirrored = await persist_external_url(current["result_url"], job_id, "mp4")
                await db.update(
                    "jobs",
                    {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                    {
                        "status": "completed",
                        "result_url": mirrored,
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "error_message": None,
                    },
                )
                if isinstance(e, asyncio.CancelledError):
                    raise
                return
        await _fail_video_job(job_id, req, _friendly_error(str(e)))
        if isinstance(e, asyncio.CancelledError):
            raise


async def _process_image(job_id: str, req: ImageRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    rich_description = _build_description(req)
    try:
        # Product analysis for better prompts
        product_analysis = await script_gen.analyze_product(
            product_name=req.product_name, description=rich_description, image_url=req.image_url,
        )

        # Build creative direction based on style
        creative = req.creative_direction or ""
        if req.image_style == "ugc":
            # Generate buyer persona for UGC influencer appearance
            buyer_persona = await script_gen.generate_buyer_persona(
                product_name=req.product_name, product_analysis=product_analysis,
            )
            creative = (
                "UGC influencer style Instagram photo. " + buyer_persona + " "
                "She holds the product at chest level, looking at camera with a warm authentic smile. "
                "Selfie angle, front-facing phone camera perspective, natural window lighting, "
                "casual home background. Raw unfiltered phone photo aesthetic, not studio quality. "
                + creative
            )

        prompt = await script_gen.generate_image_prompt(
            product_name=req.product_name,
            description=rich_description,
            aspect_ratio=req.aspect_ratio, creative_direction=creative,
            product_analysis=product_analysis,
            buyer_persona=buyer_persona if req.image_style == "ugc" else "",
            is_ugc=(req.image_style == "ugc"),
        )
        still_active = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {"generated_prompt": prompt, "status": "generating"},
        )
        if not still_active:
            logger.info("Image job %s already terminated by auto-fail — aborting", job_id)
            return

        # Retry image generation up to MAX_RETRIES
        task_id, status = await _retry_kie_task(
            kie,
            lambda: kie.create_image_task(prompt=prompt, image_url=req.image_url, aspect_ratio=req.aspect_ratio),
            poll_is_video=False,
        )

        if status["state"] != "success":
            credit_svc = CreditService()
            user = await credit_svc.get_or_create_user(req.email)
            failed_now = await db.update(
                "jobs",
                {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                {"status": "failed", "error_message": _friendly_error(status.get("error", "unknown"))},
            )
            if failed_now:
                await credit_svc.refund_credit(user["id"], "image")
            return

        result_url = status["result_urls"][0] if status.get("result_urls") else ""
        # Mirror the upstream URL onto our own /uploads/results/ so the
        # thumbnail in "Mis generaciones" doesn't 404 once KIE's tempfile
        # expires. On failure, returns the original URL.
        result_url = await persist_external_url(result_url, job_id, "jpg")
        # CAS-guarded: don't resurrect an auto-failed-and-refunded job.
        completed_now = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {
                "kie_task_id": task_id, "status": "completed",
                "result_url": result_url, "result_type": "jpg",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error_message": None,
            },
        )
        # Email only if we actually completed (gate prevents emailing a user
        # whose job was auto-failed-and-refunded by /jobs/status).
        if completed_now:
            await asyncio.to_thread(_send_delivery_email_safe, req.email, req.product_name, "image", job_id, result_url)

    except (Exception, asyncio.CancelledError) as e:
        # Catch CancelledError too — see _process_video for the rationale.
        logger.exception("Image generation failed for job %s: %s", job_id, type(e).__name__)
        credit_svc = CreditService()
        user = await credit_svc.get_or_create_user(req.email)
        failed_now = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {"status": "failed", "error_message": _friendly_error(str(e))},
        )
        if failed_now:
            await credit_svc.refund_credit(user["id"], "image")
        if isinstance(e, asyncio.CancelledError):
            raise


async def _process_landing(job_id: str, req: LandingRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    rich_description = _build_description(req)

    try:
        # Step 1: Product analysis (same as videos)
        product_analysis = await script_gen.analyze_product(
            product_name=req.product_name, description=rich_description, image_url=req.image_url,
        )

        # Step 2: Buyer persona
        buyer_persona = await script_gen.generate_buyer_persona(
            product_name=req.product_name, product_analysis=product_analysis,
        )

        # Step 3: Generate 4 extra product images via image-to-image so the
        # landing has visual richness (gallery, lifestyle, ingredient close-up,
        # in-context shot). All 4 fired CONCURRENTLY — sequential cost ~6 min,
        # parallel ~90 s. Each promotes the same source product image into a
        # different scene/angle so the page never repeats the same photo.
        image_specs = [
            {
                "mode": "landing_hero",
                "creative_direction": (
                    "Landing-page hero banner. Center the product prominently with premium but believable styling, "
                    "clean negative space for future copy, photorealistic materials, and a backdrop aligned with the brand tone."
                ),
            },
            {
                "mode": "landing_flatlay",
                "creative_direction": (
                    "Lifestyle flat lay for a sales page. Top-down composition with tasteful complementary props, "
                    "Pinterest-like realism, and the product clearly dominant over the styling elements."
                ),
            },
            {
                "mode": "landing_macro",
                "creative_direction": (
                    "Macro detail still for the landing gallery. Focus on the product texture, finish, and material cues "
                    "from the product analysis while keeping the packaging identity accurate."
                ),
            },
            {
                "mode": "landing_lifestyle",
                "creative_direction": (
                    "Real-life in-context lifestyle image for the landing page. Cast a believable Colombian customer from the buyer persona, "
                    "showing natural interaction with the product in a stylish but lived-in home setting."
                ),
            },
        ]

        image_prompts = await asyncio.gather(*(
            script_gen.generate_image_prompt(
                product_name=req.product_name,
                description=rich_description,
                aspect_ratio="16:9",
                creative_direction=spec["creative_direction"],
                product_analysis=product_analysis,
                buyer_persona=buyer_persona,
                prompt_mode=spec["mode"],
            )
            for spec in image_specs
        ))

        async def _gen_one(prompt: str) -> str | None:
            try:
                task_id = await kie.create_image_task(
                    prompt=prompt, image_url=req.image_url, aspect_ratio="16:9",
                )
                for _ in range(60):
                    status = await kie.get_task_status(task_id)
                    if status["state"] == "success" and status["result_urls"]:
                        return status["result_urls"][0]
                    if status["state"] in ("failed", "fail"):
                        return None
                    await asyncio.sleep(3)
            except Exception as e:
                logger.warning("Extra image generation failed (%s): %s", prompt[:40], e)
            return None

        # Fan out — gather lets ALL 4 images generate in parallel.
        results = await asyncio.gather(*(_gen_one(p) for p in image_prompts), return_exceptions=False)
        extra_image_urls = [u for u in results if u]
        # Mirror each generated image to our own /uploads/results/ so the
        # landing's gallery doesn't break in 7-30 days when KIE's tempfile
        # URLs expire. Done sequentially (4 small downloads) to keep code
        # simple — in practice each is <500 KB, total ~2 s.
        persisted = []
        for i, url in enumerate(extra_image_urls):
            mirrored = await persist_external_url(url, f"{job_id}-x{i}", "jpg")
            persisted.append(mirrored)
        extra_image_urls = persisted
        logger.info("Generated %d/%d extra images for landing job %s", len(extra_image_urls), len(image_prompts), job_id)
    except (Exception, asyncio.CancelledError) as e:
        # CancelledError must be re-raised so the outer handler refunds
        # the credit; without this, a SIGTERM during landing pre-processing
        # leaks a credit (job stuck in processing until the 30-min sweeper).
        if isinstance(e, asyncio.CancelledError):
            raise
        logger.warning("Landing pre-processing failed: %s", e)
        product_analysis = ""
        buyer_persona = ""
        extra_image_urls = []

    # Retry landing page generation up to MAX_RETRIES
    for attempt in range(MAX_RETRIES):
        try:
            # CAS: only flip to "generating" if the row is still
            # processing/generating. If /jobs/status auto-fail already
            # transitioned us to "failed" and refunded, abort the whole
            # pipeline — completing here would gift a free landing AND
            # leave the user with the refund.
            still_active = await db.update(
                "jobs",
                {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                {"status": "generating"},
            )
            if not still_active:
                logger.info("Landing job %s already terminated by auto-fail — aborting", job_id)
                return
            html = await script_gen.generate_landing_page(
                product_name=req.product_name, description=rich_description,
                image_url=req.image_url, style_preference=req.style_preference,
                product_analysis=product_analysis, buyer_persona=buyer_persona,
                extra_image_urls=extra_image_urls,
                price=req.price, original_price=req.original_price,
                discount_percent=req.discount_percent,
                stock_urgency=req.stock_urgency, guarantee=req.guarantee,
                bonus=req.bonus,
                whatsapp_number=req.whatsapp_number,
                key_benefits=req.key_benefits,
                shipping_info=req.shipping_info,
            )
            # Validate that GPT actually returned HTML, not a refusal text. A
            # 600-char "I cannot generate that" refusal would otherwise be
            # delivered as the user's "landing page".
            # Also require closing </html> tag — Opus hitting the max_tokens
            # cap mid-S8 produces valid-looking HTML that abruptly ends with
            # an unclosed <div>, breaking the rendered iframe AND skipping
            # the pricing/CTA sections (lost sale).
            stripped = (html or "").lstrip().lower()
            looks_like_html = stripped.startswith("<!doctype html") or stripped.startswith("<html")
            ends_properly = (html or "").rstrip().lower().endswith("</html>")
            if html and len(html) > 500 and looks_like_html and ends_properly:
                # CAS: only mark completed if the row is still in a non-terminal
                # state. The /jobs/status auto-fail at 30min could have already
                # transitioned this job to "failed" and refunded — overwriting
                # to "completed" here would gift a free credit.
                updated = await db.update(
                    "jobs",
                    {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                    {
                        "generated_prompt": f"[Landing page HTML — {len(html)} chars]",
                        "result_url": html, "result_type": "html",
                        "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                if updated:
                    await asyncio.to_thread(_send_delivery_email_safe, req.email, req.product_name, "landing_page", job_id, "")
                return
            logger.warning("Landing page invalid or too short (%d chars) on attempt %d", len(html) if html else 0, attempt + 1)
        except asyncio.CancelledError:
            # Lifespan-drain cancellation mid-attempt — checkpoint then re-raise
            # so the credit isn't trapped on a "generating" row until auto-fail.
            # CAS-guard so we don't double-refund a job already terminated by
            # /jobs/status auto-fail.
            credit_svc = CreditService()
            user = await credit_svc.get_or_create_user(req.email)
            failed_now = await db.update(
                "jobs",
                {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
                {"status": "failed", "error_message": "Generacion interrumpida. Tu credito fue restaurado."},
            )
            if failed_now:
                await credit_svc.refund_credit(user["id"], "landing_page")
            raise
        except Exception as e:
            logger.warning("Landing generation attempt %d failed: %s", attempt + 1, e)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(3)

    # All retries failed — CAS-guarded refund. If /jobs/status auto-fail
    # already failed and refunded the job, the CAS misses → we don't
    # double-refund.
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    failed_now = await db.update(
        "jobs",
        {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
        {"status": "failed", "error_message": "No se pudo generar la landing page. Tu credito fue restaurado. Intenta de nuevo con una descripcion mas detallada."},
    )
    if failed_now:
        await credit_svc.refund_credit(user["id"], "landing_page")


@router.post("/video", response_model=GenerateResponse, status_code=202)
async def generate_video(req: VideoRequest):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    service_type = DURATION_TO_SERVICE.get(req.duration)
    if not service_type:
        raise HTTPException(400, f"Invalid duration: {req.duration}. Must be 8, 15, 22, or 30.")
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user["id"], service_type):
        raise HTTPException(402, "No tienes creditos suficientes para este servicio. Compra mas creditos en la seccion de precios.")
    job = await db.insert("jobs", {
        "user_id": user["id"], "service_type": service_type,
        "input_image_url": req.image_url, "input_description": req.description,
        "input_format": req.format, "status": "processing",
    })
    task = asyncio.create_task(_process_video(job["id"], req))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return GenerateResponse(job_id=job["id"], status="processing",
                            message="Tu video esta siendo generado. Esto puede tomar unos minutos.")


@router.post("/image", response_model=GenerateResponse, status_code=202)
async def generate_image(req: ImageRequest):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user["id"], "image"):
        raise HTTPException(402, "No tienes creditos suficientes para este servicio. Compra mas creditos en la seccion de precios.")
    job = await db.insert("jobs", {
        "user_id": user["id"], "service_type": "image",
        "input_image_url": req.image_url, "input_description": req.description,
        "input_format": req.aspect_ratio, "status": "processing",
    })
    task = asyncio.create_task(_process_image(job["id"], req))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return GenerateResponse(job_id=job["id"], status="processing",
                            message="Tu imagen esta siendo generada. Esto toma entre 30 segundos y 2 minutos.")


@router.post("/landing", response_model=GenerateResponse, status_code=202)
async def generate_landing(req: LandingRequest):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user["id"], "landing_page"):
        raise HTTPException(402, "No tienes creditos suficientes para este servicio. Compra mas creditos en la seccion de precios.")
    job = await db.insert("jobs", {
        "user_id": user["id"], "service_type": "landing_page",
        "input_image_url": req.image_url, "input_description": req.description,
        "status": "processing",
    })
    task = asyncio.create_task(_process_landing(job["id"], req))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return GenerateResponse(job_id=job["id"], status="processing",
                            message="Tu landing page esta siendo generada. Esto toma entre 30 segundos y 2 minutos.")
