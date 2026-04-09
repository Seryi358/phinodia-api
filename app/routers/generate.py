import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.config import Settings
from app.database import db
from app.services.kie_ai import KieAIClient
from app.services.script_generator import ScriptGenerator
from app.services.credits import CreditService

router = APIRouter()
settings = Settings()

DURATION_TO_SERVICE = {8: "video_8s", 15: "video_15s", 22: "video_22s", 30: "video_30s"}
FORMAT_TO_ASPECT = {"portrait": "9:16", "landscape": "16:9"}
DURATION_EXTENSIONS = {8: 0, 15: 1, 22: 2, 30: 3}


class VideoRequest(BaseModel):
    email: EmailStr
    image_url: str
    description: str
    format: str
    duration: int
    product_name: str
    creative_direction: str = ""
    data_consent: bool


class ImageRequest(BaseModel):
    email: EmailStr
    image_url: str
    description: str
    aspect_ratio: str = "1:1"
    product_name: str
    creative_direction: str = ""
    data_consent: bool


class LandingRequest(BaseModel):
    email: EmailStr
    image_url: str
    description: str
    product_name: str
    style_preference: str = ""
    data_consent: bool


class GenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str


async def _poll_video_until_done(kie: KieAIClient, task_id: str, max_polls: int = 120, interval: float = 5.0) -> dict:
    for _ in range(max_polls):
        status = await kie.get_video_status(task_id)
        if status["state"] in ("success", "failed"):
            return status
        await asyncio.sleep(interval)
    return {"state": "failed", "progress": 0, "result_urls": []}


async def _process_video(job_id: str, req: VideoRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    try:
        product_analysis = await script_gen.analyze_product(
            product_name=req.product_name, description=req.description,
        )
        buyer_persona = await script_gen.generate_buyer_persona(
            product_name=req.product_name, product_analysis=product_analysis,
        )
        first_frame_prompt = await script_gen.generate_image_prompt(
            product_name=req.product_name, description=req.description,
            aspect_ratio=FORMAT_TO_ASPECT.get(req.format, "9:16"),
            creative_direction="UGC first frame: product in real-world context, natural lighting, iPhone photo style",
        )
        first_frame_task_id = await kie.create_image_task(
            prompt=first_frame_prompt, image_url=req.image_url,
            aspect_ratio=FORMAT_TO_ASPECT.get(req.format, "9:16"),
        )
        first_frame_url = req.image_url
        for _ in range(60):
            img_status = await kie.get_task_status(first_frame_task_id)
            if img_status["state"] == "success" and img_status["result_urls"]:
                first_frame_url = img_status["result_urls"][0]
                break
            if img_status["state"] in ("fail", "failed"):
                break
            await asyncio.sleep(3)

        prompt = await script_gen.generate_video_prompt(
            product_name=req.product_name, description=req.description,
            duration=req.duration, format_type=req.format,
            creative_direction=req.creative_direction,
            product_analysis=product_analysis, buyer_persona=buyer_persona,
        )
        # Compress the detailed script into a short VEO 3.1-compatible prompt
        veo_prompt = await script_gen.compress_for_veo(prompt)
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"generated_prompt": prompt, "status": "generating"})

        aspect = FORMAT_TO_ASPECT.get(req.format, "9:16")
        task_id = await kie.create_video_task(prompt=veo_prompt, image_url=first_frame_url, aspect_ratio=aspect)
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"kie_task_id": task_id})

        num_extensions = DURATION_EXTENSIONS.get(req.duration, 0)
        current_task_id = task_id
        for ext_num in range(1, num_extensions + 1):
            status = await _poll_video_until_done(kie, current_task_id)
            if status["state"] != "success":
                await db.update("jobs", {"id": f"eq.{job_id}"}, {
                    "status": "failed", "error_message": f"Video generation failed at extension {ext_num}"
                })
                return
            ext_prompt = await script_gen.generate_extension_prompt(
                original_prompt=prompt, extension_number=ext_num, duration=req.duration,
            )
            current_task_id = await kie.extend_video(task_id=current_task_id, prompt=ext_prompt)
            await db.update("jobs", {"id": f"eq.{job_id}"}, {"kie_task_id": current_task_id})

    except Exception as e:
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"status": "failed", "error_message": str(e)})


async def _process_image(job_id: str, req: ImageRequest):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    try:
        prompt = await script_gen.generate_image_prompt(
            product_name=req.product_name, description=req.description,
            aspect_ratio=req.aspect_ratio, creative_direction=req.creative_direction,
        )
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"generated_prompt": prompt, "status": "generating"})
        task_id = await kie.create_image_task(prompt=prompt, image_url=req.image_url, aspect_ratio=req.aspect_ratio)
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"kie_task_id": task_id})
    except Exception as e:
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"status": "failed", "error_message": str(e)})


async def _process_landing(job_id: str, req: LandingRequest):
    from datetime import datetime, timezone
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    try:
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"status": "generating"})
        html = await script_gen.generate_landing_page(
            product_name=req.product_name, description=req.description,
            image_url=req.image_url, style_preference=req.style_preference,
        )
        await db.update("jobs", {"id": f"eq.{job_id}"}, {
            "generated_prompt": f"[Landing page HTML — {len(html)} chars]",
            "result_url": html, "result_type": "html",
            "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        await db.update("jobs", {"id": f"eq.{job_id}"}, {"status": "failed", "error_message": str(e)})


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
        raise HTTPException(402, "Insufficient credits for this service")
    job = await db.insert("jobs", {
        "user_id": user["id"], "service_type": service_type,
        "input_image_url": req.image_url, "input_description": req.description,
        "input_format": req.format, "status": "processing",
    })
    asyncio.create_task(_process_video(job["id"], req))
    return GenerateResponse(job_id=job["id"], status="processing",
                            message="Your video is being generated. Check /api/v1/jobs/status/{job_id} for updates.")


@router.post("/image", response_model=GenerateResponse, status_code=202)
async def generate_image(req: ImageRequest):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user["id"], "image"):
        raise HTTPException(402, "Insufficient credits for this service")
    job = await db.insert("jobs", {
        "user_id": user["id"], "service_type": "image",
        "input_image_url": req.image_url, "input_description": req.description,
        "input_format": req.aspect_ratio, "status": "processing",
    })
    asyncio.create_task(_process_image(job["id"], req))
    return GenerateResponse(job_id=job["id"], status="processing",
                            message="Your image is being generated. Check /api/v1/jobs/status/{job_id} for updates.")


@router.post("/landing", response_model=GenerateResponse, status_code=202)
async def generate_landing(req: LandingRequest):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user["id"], "landing_page"):
        raise HTTPException(402, "Insufficient credits for this service")
    job = await db.insert("jobs", {
        "user_id": user["id"], "service_type": "landing_page",
        "input_image_url": req.image_url, "input_description": req.description,
        "status": "processing",
    })
    asyncio.create_task(_process_landing(job["id"], req))
    return GenerateResponse(job_id=job["id"], status="processing",
                            message="Your landing page is being generated. Check /api/v1/jobs/status/{job_id} for updates.")
