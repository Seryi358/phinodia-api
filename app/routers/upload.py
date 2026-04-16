import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.config import Settings

router = APIRouter()
settings = Settings()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB
MIN_SIZE = 1024  # 1KB — real product photos are at least this big; rejects fake files
UPLOAD_DIR = os.path.join("data", "uploads")


def _detect_format(content: bytes) -> str | None:
    """Return canonical extension from magic bytes, or None if unrecognized."""
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    # WebP: RIFF????WEBP
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Solo se permiten imágenes JPEG, PNG y WebP")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "La imagen no puede superar 10MB")
    if len(content) < MIN_SIZE:
        raise HTTPException(400, "La imagen es demasiado pequeña. Sube una foto del producto.")

    detected = _detect_format(content)
    if not detected:
        raise HTTPException(400, "El archivo no es una imagen valida")

    # Use the format detected from magic bytes, not the user-supplied extension.
    # This avoids serving a JPEG with a .png filename (mime mismatch).
    filename = f"{uuid.uuid4().hex}.{detected}"
    path = os.path.join(UPLOAD_DIR, filename)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)

    relative = f"/uploads/{filename}"
    full_url = f"{settings.api_base_url}{relative}"

    return {"path": relative, "url": full_url, "filename": filename}
