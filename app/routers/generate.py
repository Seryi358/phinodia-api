import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.config import Settings
from app.database import db
from app.services.kie_ai import KieAIClient
from app.services.script_generator import ScriptGenerator
from app.services.credits import CreditService

router = APIRouter()
settings = Settings()
logger = logging.getLogger(__name__)

# Hold references to background tasks to prevent GC collection
_background_tasks: set = set()

DURATION_TO_SERVICE = {8: "video_8s", 15: "video_15s", 22: "video_22s", 30: "video_30s"}
FORMAT_TO_ASPECT = {"portrait": "9:16", "landscape": "16:9"}
DURATION_EXTENSIONS = {8: 0, 15: 1, 22: 2, 30: 3}

# Max retries for KIE AI generation failures
MAX_RETRIES = 3

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


class VideoRequest(BaseModel):
    email: EmailStr
    image_url: str
    description: str
    format: str
    duration: int
    product_name: str
    product_category: str = ""
    pain_point: str = ""
    creative_direction: str = ""
    data_consent: bool


class ImageRequest(BaseModel):
    email: EmailStr
    image_url: str
    description: str
    aspect_ratio: str = "1:1"
    product_name: str
    product_category: str = ""
    creative_direction: str = ""
    image_style: str = "product"
    data_consent: bool


class LandingRequest(BaseModel):
    email: EmailStr
    image_url: str
    description: str
    product_name: str
    product_category: str = ""
    target_audience: str = ""
    style_preference: str = ""
    data_consent: bool


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


async def _retry_kie_task(kie: KieAIClient, create_fn, poll_is_video: bool, max_retries: int = MAX_RETRIES) -> tuple[str, dict]:
    """Retry KIE AI task creation + polling up to max_retries times."""
    last_error = None
    for attempt in range(max_retries):
        try:
            task_id = await create_fn()
            status = await _poll_until_done(kie, task_id, is_video=poll_is_video, max_polls=90)
            if status["state"] == "success":
                return task_id, status
            last_error = f"Attempt {attempt + 1} failed"
            logger.warning("KIE task %s failed on attempt %d", task_id, attempt + 1)
        except Exception as e:
            last_error = str(e)
            logger.warning("KIE task creation failed on attempt %d: %s", attempt + 1, e)
        if attempt < max_retries - 1:
            await asyncio.sleep(5)
    return "", {"state": "failed", "result_urls": [], "error": last_error}


async def _process_video(job_id: str, req: VideoRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    rich_description = _build_description(req)
    try:
        # Step 1: Deep product analysis
        product_analysis = await script_gen.analyze_product(
            product_name=req.product_name, description=rich_description,
        )

        # Step 2: Buyer persona (Colombian UGC creator)
        buyer_persona = await script_gen.generate_buyer_persona(
            product_name=req.product_name, product_analysis=product_analysis,
        )

        # Step 3: Generate first frame with Nano Banana 2 (POV selfie, no phone visible)
        first_frame_prompt = await script_gen.generate_image_prompt(
            product_name=req.product_name, description=rich_description,
            aspect_ratio=FORMAT_TO_ASPECT.get(req.format, "9:16"),
            creative_direction="Close-up selfie photo taken with front-facing phone camera at arm's length. Young woman smiling at camera, one arm extended toward viewer holding the phone (arm visible reaching forward), other hand holding the product at chest level with label facing camera. Slightly above eye-level angle, natural window light, casual home background. Raw unedited phone photo quality.",
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
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"generated_prompt": prompt, "status": "generating"})

        # Step 5: Generate video with VEO 3.1 (retry with fallback)
        async def _create_video_with_image():
            return await kie.create_video_task(prompt=prompt, image_url=first_frame_url, aspect_ratio=aspect, use_image=True)

        async def _create_video_text_only():
            return await kie.create_video_task(prompt=prompt, image_url="", aspect_ratio=aspect, use_image=False)

        # Try IMAGE_2_VIDEO with retries
        task_id, base_status = await _retry_kie_task(kie, _create_video_with_image, poll_is_video=True, max_retries=2)

        # If IMAGE_2_VIDEO failed, fallback to TEXT_2_VIDEO with retries
        if base_status["state"] != "success":
            logger.info("IMAGE_2_VIDEO failed for job %s, falling back to TEXT_2_VIDEO", job_id)
            task_id, base_status = await _retry_kie_task(kie, _create_video_text_only, poll_is_video=True, max_retries=2)

        if base_status["state"] != "success":
            # All retries exhausted — refund credit and show friendly error
            credit_svc = CreditService()
            service_type = DURATION_TO_SERVICE.get(req.duration, "video_8s")
            user = await credit_svc.get_or_create_user(req.email)
            await credit_svc.refund_credit(user["id"], service_type)
            await db.update("jobs", {"id": f"eq.{job_id}"}, {
                "status": "failed", "error_message": _friendly_error(base_status.get("error", "unknown"))
            })
            return

        # Save base video result URL for fallback
        base_result_url = base_status["result_urls"][0] if base_status.get("result_urls") else ""
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"kie_task_id": task_id})

        # Step 6: Extend video for durations > 8s
        num_extensions = DURATION_EXTENSIONS.get(req.duration, 0)
        current_task_id = task_id
        final_result_url = base_result_url
        for ext_num in range(1, num_extensions + 1):
            ext_prompt = await script_gen.generate_extension_prompt(
                original_prompt=prompt, extension_number=ext_num, duration=req.duration,
            )
            try:
                current_task_id = await kie.extend_video(task_id=current_task_id, prompt=ext_prompt)
                await db.update("jobs", {"id": f"eq.{job_id}"}, {"kie_task_id": current_task_id})
                ext_status = await _poll_until_done(kie, current_task_id, is_video=True)
                if ext_status["state"] != "success":
                    # Extension failed — save the base video as partial result
                    await db.update("jobs", {"id": f"eq.{job_id}"}, {
                        "status": "completed", "result_url": base_result_url, "result_type": "mp4",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "error_message": f"La extension fallo, pero tu video base de 8 segundos esta listo.",
                    })
                    return
                final_result_url = ext_status["result_urls"][0] if ext_status.get("result_urls") else final_result_url
            except Exception as e:
                logger.error("Extension %d failed for job %s: %s", ext_num, job_id, e)
                await db.update("jobs", {"id": f"eq.{job_id}"}, {
                    "status": "completed", "result_url": base_result_url, "result_type": "mp4",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error_message": f"La extension fallo, pero tu video base de 8 segundos esta listo.",
                })
                return

        # All extensions done (or no extensions needed) — save final result
        await db.update("jobs", {"id": f"eq.{job_id}"}, {
            "status": "completed", "result_url": final_result_url, "result_type": "mp4",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        logger.error("Video pipeline failed for job %s: %s", job_id, e)
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"status": "failed", "error_message": _friendly_error(str(e))})


async def _process_image(job_id: str, req: ImageRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    rich_description = _build_description(req)
    try:
        # Product analysis for better prompts
        product_analysis = await script_gen.analyze_product(
            product_name=req.product_name, description=rich_description,
        )

        # Build creative direction based on style
        creative = req.creative_direction or ""
        if req.image_style == "ugc":
            creative = (
                "UGC influencer style Instagram photo. A young Colombian woman in her mid-20s "
                "with natural makeup and casual clothing holds the product at chest level, "
                "looking at camera with a warm authentic smile. Selfie angle, front-facing "
                "phone camera perspective, natural window lighting, casual home background. "
                "Raw unfiltered phone photo aesthetic, not studio quality. " + creative
            )

        prompt = await script_gen.generate_image_prompt(
            product_name=req.product_name,
            description=rich_description + "\n\nProduct Analysis:\n" + product_analysis,
            aspect_ratio=req.aspect_ratio, creative_direction=creative,
        )
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"generated_prompt": prompt, "status": "generating"})

        # Retry image generation up to MAX_RETRIES
        task_id, status = await _retry_kie_task(
            kie,
            lambda: kie.create_image_task(prompt=prompt, image_url=req.image_url, aspect_ratio=req.aspect_ratio),
            poll_is_video=False,
        )

        if status["state"] != "success":
            credit_svc = CreditService()
            user = await credit_svc.get_or_create_user(req.email)
            await credit_svc.refund_credit(user["id"], "image")
            await db.update("jobs", {"id": f"eq.{job_id}"}, {
                "status": "failed", "error_message": _friendly_error(status.get("error", "unknown"))
            })
            return

        result_url = status["result_urls"][0] if status.get("result_urls") else ""
        await db.update("jobs", {"id": f"eq.{job_id}"}, {
            "kie_task_id": task_id, "status": "completed",
            "result_url": result_url, "result_type": "jpg",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        logger.error("Image generation failed for job %s: %s", job_id, e)
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"status": "failed", "error_message": _friendly_error(str(e))})


async def _process_landing(job_id: str, req: LandingRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    rich_description = _build_description(req)

    try:
        # Step 1: Product analysis (same as videos)
        product_analysis = await script_gen.analyze_product(
            product_name=req.product_name, description=rich_description,
        )

        # Step 2: Buyer persona
        buyer_persona = await script_gen.generate_buyer_persona(
            product_name=req.product_name, product_analysis=product_analysis,
        )

        # Step 3: Generate extra product images with Nano Banana 2
        extra_image_urls = []
        image_prompts = [
            f"Professional product photography of {req.product_name} on a clean white surface with soft studio lighting, minimal shadows, centered composition, 4K quality",
            f"Lifestyle flat lay photo featuring {req.product_name} surrounded by complementary items, warm natural lighting, Instagram aesthetic, top-down view",
        ]
        for img_prompt in image_prompts:
            try:
                task_id = await kie.create_image_task(
                    prompt=img_prompt, image_url=req.image_url, aspect_ratio="16:9",
                )
                for _ in range(60):
                    status = await kie.get_task_status(task_id)
                    if status["state"] == "success" and status["result_urls"]:
                        extra_image_urls.append(status["result_urls"][0])
                        break
                    if status["state"] in ("failed", "fail"):
                        break
                    await asyncio.sleep(3)
            except Exception as e:
                logger.warning("Extra image generation failed: %s", e)
    except Exception as e:
        logger.warning("Landing pre-processing failed: %s", e)
        product_analysis = ""
        buyer_persona = ""
        extra_image_urls = []

    # Retry landing page generation up to MAX_RETRIES
    for attempt in range(MAX_RETRIES):
        try:
            await db.update("jobs", {"id": f"eq.{job_id}"}, {"status": "generating"})
            html = await script_gen.generate_landing_page(
                product_name=req.product_name, description=rich_description,
                image_url=req.image_url, style_preference=req.style_preference,
                product_analysis=product_analysis, buyer_persona=buyer_persona,
                extra_image_urls=extra_image_urls,
            )
            if html and len(html) > 500:
                await db.update("jobs", {"id": f"eq.{job_id}"}, {
                    "generated_prompt": f"[Landing page HTML — {len(html)} chars]",
                    "result_url": html, "result_type": "html",
                    "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                return
            logger.warning("Landing page too short (%d chars) on attempt %d", len(html) if html else 0, attempt + 1)
        except Exception as e:
            logger.warning("Landing generation attempt %d failed: %s", attempt + 1, e)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(3)

    # All retries failed — refund credit
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    await credit_svc.refund_credit(user["id"], "landing_page")
    await db.update("jobs", {"id": f"eq.{job_id}"}, {
        "status": "failed", "error_message": "No se pudo generar la landing page. Tu credito fue restaurado. Intenta de nuevo con una descripcion mas detallada."
    })


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
