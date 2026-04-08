import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.config import Settings

router = APIRouter()
settings = Settings()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB
UPLOAD_DIR = os.path.join("data", "uploads")


@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Solo se permiten imágenes JPEG, PNG y WebP")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "La imagen no puede superar 10MB")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"

    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_DIR, filename)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)

    relative = f"/uploads/{filename}"
    full_url = f"{settings.api_base_url}{relative}"

    return {"path": relative, "url": full_url, "filename": filename}
