import asyncio
import logging
import re
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
from app.services import video_stitch
from app.prompts.video_ugc import num_clips_for, assemble_omni_prompt, safe_omni_prompt

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
    service_type = _video_service(req.duration)
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

FORMAT_TO_ASPECT = {"portrait": "9:16", "landscape": "16:9"}
# Fixed seed reused across a video's stitched clips — a soft consistency lever
# paired with the gpt-image-2 first frame + last-frame relay (the real backbone).
OMNI_SEED = 778899


def _video_service(duration: int) -> str:
    """Job label for a video of N seconds, e.g. 20 -> 'video_20s'. The credit
    cost is derived from this label by credits.credits_for_service_label
    (3 credits per 10s)."""
    return f"video_{int(duration)}s"

# Max retries for KIE AI generation failures
MAX_RETRIES = 3
VIDEO_RENDER_RETRIES = 2

# User-friendly error messages based on KIE AI errors
ERROR_MESSAGES = {
    "timed out": "La generacion tomo demasiado tiempo. Por favor intenta de nuevo con una descripcion mas corta.",
    "content policy": "Tu solicitud fue rechazada por politicas de contenido. Intenta con una descripcion diferente que no incluya contenido sensible.",
    "rate limit": "El servicio esta saturado en este momento. Tu credito fue restaurado, intenta de nuevo en unos minutos.",
    "credits insufficient": "No pudimos completar el video porque la cuenta de KIE AI no tiene creditos suficientes. Tu credito fue restaurado.",
    "current balance isn't enough": "No pudimos completar el video porque la cuenta de KIE AI no tiene creditos suficientes. Tu credito fue restaurado.",
    "invalid": "Hubo un problema con los datos enviados. Verifica tu imagen y descripcion e intenta de nuevo.",
}


def _friendly_error(raw_error: str) -> str:
    """Convert technical KIE AI errors to user-friendly Spanish messages."""
    lower = raw_error.lower()
    for key, msg in ERROR_MESSAGES.items():
        if key in lower:
            return msg
    if "non-english" in lower or "only english prompts are supported" in lower:
        return "El proveedor de video rechazo el prompt por idioma. Tu credito fue restaurado."
    if "failed to fetch image" in lower or "access limit" in lower:
        return "No pudimos usar la imagen de referencia para generar el video. Tu credito fue restaurado."
    if "unsafe image upload" in lower:
        return "La imagen de referencia fue rechazada por seguridad. Tu credito fue restaurado."
    if "content polic" in lower:
        return "El proveedor de video rechazo el contenido por politicas. Tu credito fue restaurado."
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


def _compact_text(value: str, *, max_chars: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _default_product_analysis(req: "VideoRequest") -> str:
    return (
        f"Product name: {req.product_name}. "
        f"Category: {req.product_category or 'consumer product'}. "
        f"Use the reference image as the exact source of truth for packaging, shape, colors, label placement, and scale. "
        f"Primary pain point: {req.pain_point or 'the routine takes too long'}."
    )


def _default_buyer_persona(req: "VideoRequest") -> str:
    return (
        "Believable Colombian woman, 24-35, everyday home setting, relaxed styling, warm tone, "
        "natural phone-camera confidence, not a polished actress or luxury influencer."
    )


def _build_safe_first_frame_prompt(req: "VideoRequest") -> str:
    product = _compact_text(req.product_name or "the product", max_chars=40)
    category = _compact_text(req.product_category or "consumer product", max_chars=40)
    return (
        f"SCENE: Real home interior with natural window light and authentic everyday background.\n"
        f"SUBJECT: Believable Colombian woman, 24-35, front-camera selfie framing from chest up, natural expression, holding the exact {product} naturally near her chest.\n"
        f"PRODUCT: Preserve the exact {category} packaging from the reference image, including shape, colors, label placement, and visible brand text.\n"
        f"COMPOSITION: Vertical 9:16 first frame for UGC video, phone-camera realism, motion-ready pose, clear face and clear product, no visible phone.\n"
        f"CONSTRAINTS: Photorealistic, raw smartphone aesthetic, no ultra-HD polish, no text overlay, no extra products, no packaging redesign, no anatomy glitches."
    )


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
    duration: Literal[10, 20, 30]
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
    # Omni video AND gpt-image-2 images both run on the unified /jobs endpoint,
    # so polling always uses get_task_status (is_video kept for call-site compat).
    for _ in range(max_polls):
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
                last_error = status.get("error") or f"Attempt {attempt + 1} failed"
            logger.warning("KIE task %s failed on attempt %d", task_id, attempt + 1)
        except Exception as e:
            last_error = str(e)
            logger.warning("KIE task creation failed on attempt %d: %s", attempt + 1, e)
        if attempt < max_retries - 1:
            await asyncio.sleep(5)
    return "", {"state": "failed", "result_urls": [], "error": last_error}


async def _omni_generate_clip(kie, rich_prompt, safe_prompt, frames, aspect, product_ref=""):
    # Produce ONE <=10s omni clip with graceful fallbacks. For each candidate
    # first frame (relay/gpt-image-2 frame, then the original product image) try
    # the rich prompt then the short safe prompt; finally text-only. Omni 500s on
    # long bracketed prompts, so the safe prompt is the proven short shape.
    # product_ref (the ORIGINAL product photo) is attached as a SECOND reference
    # on every attempt so the packaging/label/brand text never drifts across
    # stitched clips (omni otherwise re-invents the product name on clip 2+).
    candidates = []
    for fr in frames:
        if fr:
            candidates.append((rich_prompt, fr))
            candidates.append((safe_prompt, fr))
    candidates.append((safe_prompt, ""))
    for pr, fr in candidates:
        try:
            _task_id, status = await _retry_kie_task(
                kie,
                (lambda pr=pr, fr=fr: kie.create_omni_video_task(
                    prompt=pr, image_url=fr, reference_image_url=product_ref,
                    seed=OMNI_SEED, duration="10", aspect_ratio=aspect, resolution="720p")),
                poll_is_video=False, max_retries=2,
            )
            if status["state"] == "success" and status.get("result_urls"):
                return status["result_urls"][0]
        except Exception as e:
            logger.warning("omni clip attempt failed: %s", type(e).__name__)
    return ""


async def _omni_render_video(kie, job_id, req, first_frame_url, plan, num_clips, aspect):
    # Generate each <=10s clip (last-frame relay between clips for continuity)
    # and concat into one mp4. Returns (final_local_path, None) or (None, error).
    work = video_stitch.WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    product_hint = (req.product_name or "the product")[:60]
    identity = plan.get("identity_block", "")
    clips = plan.get("clips") or [{"action": "", "dialogue_es": ""}]
    current_frame = first_frame_url
    clip_files = []
    for i in range(num_clips):
        clip = clips[i] if i < len(clips) else clips[-1]
        rich = assemble_omni_prompt(identity, clip.get("action", ""), clip.get("dialogue_es", ""))
        safe = safe_omni_prompt(product_hint, clip.get("dialogue_es", ""))
        # Try the relay/first frame, then fall back to the original product image.
        # Always attach the original product photo as a reference so the label
        # stays identical across every stitched clip.
        frames = [current_frame, req.image_url]
        clip_url = await _omni_generate_clip(kie, rich, safe, frames, aspect, product_ref=req.image_url)
        if not clip_url:
            return None, f"Fallo en etapa clip_{i + 1}. {_friendly_error('omni internal error')}"
        local = str(work / f"{job_id}_clip{i}.mp4")
        if not await video_stitch.download_to(clip_url, local):
            return None, f"No pudimos descargar el clip {i + 1} del video."
        clip_files.append(local)
        # Keep the job alive (abort if auto-fail already terminated it).
        still = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {"result_type": "mp4"},
        )
        if not still:
            return None, "terminated"
        # Extract this clip's last frame to seed the next clip (last-frame relay).
        if i < num_clips - 1:
            current_frame = req.image_url
            lf = str(work / f"{job_id}_clip{i}_last.png")
            if await video_stitch.extract_last_frame(local, lf):
                pub = video_stitch.publish_local(lf, f"{job_id}-relay{i}.png")
                if pub:
                    current_frame = pub
    final_local = str(work / f"{job_id}_final.mp4")
    if not await video_stitch.concat_clips(clip_files, final_local):
        return None, "No pudimos unir los clips del video."
    return final_local, None


def _safe_omni_plan(req, num_clips):
    # Fallback plan if LLM script generation fails -- generic but valid
    # Colombian-Spanish AIDA lines so a paid job still ships a real video.
    product = req.product_name or "el producto"
    identity = ("An everyday Colombian woman, late 20s, natural relatable look, casual clothes, "
                "in a simple home with natural daylight, holding " + product + ".")
    lines = [
        ("She greets warmly and shows the product to the camera.",
         "Hola, les quiero mostrar " + product + ", de verdad me encanto."),
        ("She shows how she uses it and reacts happily.",
         "Miren que facil, y el resultado se nota de una."),
        ("She smiles and gives a soft, friendly call to action.",
         "Si la quieren, pidanla ya en la pagina, vale la pena."),
    ]
    clips = []
    for i in range(num_clips):
        a, d = lines[i] if i < len(lines) else lines[-1]
        clips.append({"action": a, "dialogue_es": d})
    return {"identity_block": identity, "clips": clips}


async def _process_video(job_id: str, req: "VideoRequest"):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    rich_description = _build_description(req)
    service_type = _video_service(req.duration)
    num_clips = num_clips_for(req.duration)
    aspect = FORMAT_TO_ASPECT.get(req.format, "9:16")
    phase = "starting"
    try:
        # Step 1: deep product analysis (vision)
        phase = "product_analysis"
        try:
            product_analysis = await script_gen.analyze_product(
                product_name=req.product_name, description=rich_description, image_url=req.image_url)
        except Exception as e:
            logger.warning("Product analysis failed for job %s; fallback: %s", job_id, type(e).__name__)
            product_analysis = _default_product_analysis(req)

        # Step 2: buyer persona (the Colombian UGC creator)
        phase = "buyer_persona"
        try:
            buyer_persona = await script_gen.generate_buyer_persona(
                product_name=req.product_name, product_analysis=product_analysis)
        except Exception as e:
            logger.warning("Buyer persona failed for job %s; fallback: %s", job_id, type(e).__name__)
            buyer_persona = _default_buyer_persona(req)

        # Step 3: gpt-image-2 first frame (vertical selfie UGC, product in hand)
        phase = "first_frame_prompt"
        try:
            first_frame_prompt = await script_gen.generate_image_prompt(
                product_name=req.product_name, description=rich_description, aspect_ratio=aspect,
                creative_direction=("Front-camera selfie first frame. The creator matches the buyer persona, "
                                    "looks into the lens, and holds the real product at chest level with the "
                                    "packaging clearly visible. Natural home setting, candid phone-photo realism, "
                                    "no visible phone in frame."),
                product_analysis=product_analysis, buyer_persona=buyer_persona,
                prompt_mode="video_first_frame")
        except Exception as e:
            logger.warning("First-frame prompt failed for job %s; fallback: %s", job_id, type(e).__name__)
            first_frame_prompt = _build_safe_first_frame_prompt(req)

        phase = "first_frame_render"
        _ff_task, ff_status = await _retry_kie_task(
            kie,
            (lambda: kie.create_image_task(prompt=first_frame_prompt, image_url=req.image_url,
                                           aspect_ratio=aspect, resolution="1K")),
            poll_is_video=False, max_retries=2)
        first_frame_url = (ff_status["result_urls"][0]
                           if ff_status["state"] == "success" and ff_status.get("result_urls")
                           else req.image_url)

        # Step 4: multi-clip omni script plan (AIDA + Colombian Spanish)
        phase = "script_plan"
        try:
            plan = await script_gen.generate_omni_plan(
                product_name=req.product_name, description=rich_description,
                duration=req.duration, num_clips=num_clips,
                product_analysis=product_analysis, buyer_persona=buyer_persona)
        except Exception as e:
            logger.warning("Omni plan failed for job %s; using safe plan: %s", job_id, type(e).__name__)
            plan = _safe_omni_plan(req, num_clips)

        # Flip processing -> generating (CAS); abort if auto-fail already ran.
        phase = "activate_generating"
        prompt_summary = ((plan.get("identity_block", "") + " | "
                           + " || ".join(c.get("dialogue_es", "") for c in plan.get("clips", [])))[:1500])
        still_active = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {"generated_prompt": prompt_summary, "status": "generating"})
        if not still_active:
            logger.info("Video job %s already terminated by auto-fail -- aborting", job_id)
            return

        # Step 5: render each clip + stitch into the final video
        phase = "render"
        final_local, render_error = await _omni_render_video(
            kie, job_id, req, first_frame_url, plan, num_clips, aspect)
        if render_error == "terminated":
            return
        if not final_local:
            await _fail_video_job(job_id, req, render_error or
                                  "No pudimos completar el video con la duracion solicitada. Tu credito fue restaurado.")
            return

        # Publish the final mp4 to our own /uploads/results/ (served + downloadable)
        phase = "publish"
        result_url = video_stitch.publish_local(final_local, f"{job_id}.mp4")
        if not result_url:
            await _fail_video_job(job_id, req,
                                  "No pudimos guardar el video final. Tu credito fue restaurado.")
            return

        # CAS-guarded completion so an auto-failed-and-refunded row can't be resurrected.
        phase = "complete_job"
        completed_now = await db.update(
            "jobs",
            {"id": f"eq.{job_id}", "status": "in.(processing,generating)"},
            {"status": "completed", "result_url": result_url, "result_type": "mp4",
             "completed_at": datetime.now(timezone.utc).isoformat(), "error_message": None})
        if completed_now:
            await asyncio.to_thread(_send_delivery_email_safe, req.email, req.product_name,
                                    service_type, job_id, result_url)

    except (Exception, asyncio.CancelledError) as e:
        logger.exception("Video pipeline failed for job %s: %s", job_id, type(e).__name__)
        await _fail_video_job(job_id, req, f"Fallo interno en etapa {phase}. {_friendly_error(str(e))}")
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
    service_type = _video_service(req.duration)  # video_10s | video_20s | video_30s
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    # deduct_credit derives the cost from the label (3 credits per 10s segment).
    if not await credit_svc.deduct_credit(user["id"], service_type):
        raise HTTPException(402, "No tienes creditos suficientes. Compra mas creditos en la seccion de precios.")
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
