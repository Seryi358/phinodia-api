import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings
from app.database import get_db_session
from app.models import Job
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
    """Poll VEO 3.1 task status until success or failure."""
    for _ in range(max_polls):
        status = await kie.get_video_status(task_id)
        if status["state"] in ("success", "failed"):
            return status
        await asyncio.sleep(interval)
    return {"state": "failed", "progress": 0, "result_urls": []}


async def _process_video(job_id: str, req: VideoRequest, db_session_factory):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    async with db_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        try:
            # Step 1: Analyze product
            product_analysis = await script_gen.analyze_product(
                product_name=req.product_name, description=req.description,
            )

            # Step 2: Generate buyer persona
            buyer_persona = await script_gen.generate_buyer_persona(
                product_name=req.product_name, product_analysis=product_analysis,
            )

            # Step 3: Generate first frame image prompt + create image
            first_frame_prompt = await script_gen.generate_image_prompt(
                product_name=req.product_name, description=req.description,
                aspect_ratio=FORMAT_TO_ASPECT.get(req.format, "9:16"),
                creative_direction="UGC first frame: product in real-world context, natural lighting, iPhone photo style",
            )
            first_frame_task_id = await kie.create_image_task(
                prompt=first_frame_prompt, image_url=req.image_url,
                aspect_ratio=FORMAT_TO_ASPECT.get(req.format, "9:16"),
            )

            # Poll for first frame image completion
            first_frame_url = req.image_url  # fallback
            for _ in range(60):
                img_status = await kie.get_task_status(first_frame_task_id)
                if img_status["state"] == "success" and img_status["result_urls"]:
                    first_frame_url = img_status["result_urls"][0]
                    break
                if img_status["state"] in ("fail", "failed"):
                    break
                await asyncio.sleep(3)

            # Step 4: Generate video script/prompt
            prompt = await script_gen.generate_video_prompt(
                product_name=req.product_name, description=req.description,
                duration=req.duration, format_type=req.format,
                creative_direction=req.creative_direction,
                product_analysis=product_analysis, buyer_persona=buyer_persona,
            )
            job.generated_prompt = prompt
            job.status = "generating"
            await session.commit()

            # Step 5: Create base 8s video (VEO 3.1)
            aspect = FORMAT_TO_ASPECT.get(req.format, "9:16")
            task_id = await kie.create_video_task(
                prompt=prompt, image_url=first_frame_url,
                aspect_ratio=aspect,
            )
            job.kie_task_id = task_id
            await session.commit()

            # Step 6: Extend video for durations > 8s
            num_extensions = DURATION_EXTENSIONS.get(req.duration, 0)
            current_task_id = task_id
            for ext_num in range(1, num_extensions + 1):
                # Wait for current segment to complete
                status = await _poll_video_until_done(kie, current_task_id)
                if status["state"] != "success":
                    job.status = "failed"
                    job.error_message = f"Video generation failed at extension {ext_num}"
                    await session.commit()
                    return

                # Generate extension prompt
                ext_prompt = await script_gen.generate_extension_prompt(
                    original_prompt=prompt, extension_number=ext_num, duration=req.duration,
                )
                current_task_id = await kie.extend_video(
                    task_id=current_task_id, prompt=ext_prompt,
                )
                job.kie_task_id = current_task_id
                await session.commit()

        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            await session.commit()


async def _process_image(job_id: str, req: ImageRequest, db_session_factory):
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    kie = KieAIClient(api_key=settings.kie_api_key)
    async with db_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        try:
            prompt = await script_gen.generate_image_prompt(
                product_name=req.product_name, description=req.description,
                aspect_ratio=req.aspect_ratio, creative_direction=req.creative_direction,
            )
            job.generated_prompt = prompt
            job.status = "generating"
            await session.commit()
            task_id = await kie.create_image_task(
                prompt=prompt, image_url=req.image_url, aspect_ratio=req.aspect_ratio,
            )
            job.kie_task_id = task_id
            await session.commit()
        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            await session.commit()


async def _process_landing(job_id: str, req: LandingRequest, db_session_factory):
    from datetime import datetime, timezone
    script_gen = ScriptGenerator(api_key=settings.openai_api_key)
    async with db_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        try:
            job.status = "generating"
            await session.commit()
            html = await script_gen.generate_landing_page(
                product_name=req.product_name, description=req.description,
                image_url=req.image_url, style_preference=req.style_preference,
            )
            job.generated_prompt = f"[Landing page HTML — {len(html)} chars]"
            job.result_url = html
            job.result_type = "html"
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            await session.commit()


@router.post("/video", response_model=GenerateResponse, status_code=202)
async def generate_video(req: VideoRequest, session: AsyncSession = Depends(get_db_session)):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    service_type = DURATION_TO_SERVICE.get(req.duration)
    if not service_type:
        raise HTTPException(400, f"Invalid duration: {req.duration}. Must be 8, 15, 22, or 30.")
    credit_svc = CreditService(session)
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user.id, service_type):
        raise HTTPException(402, "Insufficient credits for this service")
    job = Job(user_id=user.id, service_type=service_type, input_image_url=req.image_url,
              input_description=req.description, input_format=req.format)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    from app.database import SessionLocal
    asyncio.create_task(_process_video(job.id, req, SessionLocal))
    return GenerateResponse(job_id=job.id, status="processing",
                            message="Your video is being generated. Check /api/v1/jobs/status/{job_id} for updates.")


@router.post("/image", response_model=GenerateResponse, status_code=202)
async def generate_image(req: ImageRequest, session: AsyncSession = Depends(get_db_session)):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    credit_svc = CreditService(session)
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user.id, "image"):
        raise HTTPException(402, "Insufficient credits for this service")
    job = Job(user_id=user.id, service_type="image", input_image_url=req.image_url,
              input_description=req.description, input_format=req.aspect_ratio)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    from app.database import SessionLocal
    asyncio.create_task(_process_image(job.id, req, SessionLocal))
    return GenerateResponse(job_id=job.id, status="processing",
                            message="Your image is being generated. Check /api/v1/jobs/status/{job_id} for updates.")


@router.post("/landing", response_model=GenerateResponse, status_code=202)
async def generate_landing(req: LandingRequest, session: AsyncSession = Depends(get_db_session)):
    if not req.data_consent:
        raise HTTPException(400, "Data processing consent is required")
    credit_svc = CreditService(session)
    user = await credit_svc.get_or_create_user(req.email)
    if not await credit_svc.deduct_credit(user.id, "landing_page"):
        raise HTTPException(402, "Insufficient credits for this service")
    job = Job(user_id=user.id, service_type="landing_page", input_image_url=req.image_url,
              input_description=req.description)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    from app.database import SessionLocal
    asyncio.create_task(_process_landing(job.id, req, SessionLocal))
    return GenerateResponse(job_id=job.id, status="processing",
                            message="Your landing page is being generated. Check /api/v1/jobs/status/{job_id} for updates.")
