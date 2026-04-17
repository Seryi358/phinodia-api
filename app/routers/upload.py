import io
import os
import uuid
import warnings
from fastapi import APIRouter, UploadFile, File, HTTPException
from PIL import Image, ImageOps
from app.config import Settings

# Cap decoded pixel count well below product-photo needs to defend against
# decompression bombs (a tiny PNG can declare 50000x50000 dimensions and
# allocate gigabytes during decode). 24MP covers a 6000x4000 DSLR shot.
Image.MAX_IMAGE_PIXELS = 24_000_000

router = APIRouter()
settings = Settings()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB
MIN_SIZE = 1024  # 1KB — real product photos are at least this big; rejects fake files
UPLOAD_DIR = os.path.join("data", "uploads")
# Map declared content-type to expected magic-byte format. Mismatch → reject.
MIME_TO_FORMAT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
PILLOW_FORMATS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


def _detect_format(content: bytes) -> str | None:
    """Return canonical extension from magic bytes, or None if unrecognized."""
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    # WebP: RIFF????WEBP + a VP8/VP8L/VP8X chunk header to reject "RIFF????WEBP"
    # crafted-but-empty payloads.
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" and content[12:15] in (b"VP8", b"VP8"):
        # The chunk fourcc is 4 chars: "VP8 ", "VP8L", or "VP8X".
        return "webp"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" and content[12:16] in (b"VP8 ", b"VP8L", b"VP8X"):
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
    # Defense-in-depth: declared mime must agree with magic bytes.
    if MIME_TO_FORMAT.get(file.content_type) != detected:
        raise HTTPException(400, "El tipo declarado no coincide con el contenido")

    # Re-encode through Pillow to (a) strip EXIF (GPS/device PII), (b) make
    # sure the bytes actually decode as a real image — defeats polyglot tricks
    # that pass magic-byte checks but contain trailing JS payloads, and
    # (c) bake EXIF orientation into pixels (otherwise iPhone portraits show
    # sideways since the saved image has no EXIF tag).
    try:
        with warnings.catch_warnings():
            # Pillow only EMITS a warning between MAX_IMAGE_PIXELS and 2x; turn
            # that into a hard reject so a 100MP PNG can't allocate ~286MB.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(content))
            img.verify()
            img = Image.open(io.BytesIO(content))  # re-open: verify() exhausts the file
            img = ImageOps.exif_transpose(img)
            if detected == "jpg" and img.mode != "RGB":
                img = img.convert("RGB")
            elif detected == "png" and img.mode in ("I", "F", "CMYK"):
                img = img.convert("RGBA" if "A" in img.mode else "RGB")
            out = io.BytesIO()
            save_kwargs = {"quality": 92, "optimize": True} if detected == "jpg" else {}
            img.save(out, format=PILLOW_FORMATS[detected], **save_kwargs)
            clean_bytes = out.getvalue()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "El archivo no es una imagen valida")
    # Re-encoded bytes can outsize the original (e.g. JPEG q=92 from a q=20
    # source). Enforce the cap on the FINAL on-disk bytes, not just the upload.
    if len(clean_bytes) > MAX_SIZE:
        raise HTTPException(400, "La imagen procesada supera el tamaño permitido")

    # Use the format detected from magic bytes, not the user-supplied extension.
    # This avoids serving a JPEG with a .png filename (mime mismatch).
    filename = f"{uuid.uuid4().hex}.{detected}"
    path = os.path.join(UPLOAD_DIR, filename)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(clean_bytes)

    relative = f"/uploads/{filename}"
    full_url = f"{settings.api_base_url}{relative}"

    return {"path": relative, "url": full_url, "filename": filename}
