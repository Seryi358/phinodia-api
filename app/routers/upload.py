import io
import os
import uuid
import warnings
from collections import deque
from fastapi import APIRouter, UploadFile, File, HTTPException
from PIL import Image, ImageOps
from app.config import get_settings


def _strip_solid_background(img: Image.Image, tolerance: int = 25) -> Image.Image:
    """Edge-flood-fill to alpha=0 so a product photo on a uniform light/dark
    backdrop becomes transparent. Far cheaper and more predictable than
    rembg (no ML model, no extra deps), at the cost of failing on busy
    backgrounds — those simply ship unchanged.

    Algorithm: BFS from each edge pixel, marking pixels whose RGB delta vs
    the seed corner is below `tolerance` as background. Anything connected
    to the seed via that delta becomes transparent. The product (which
    typically has high contrast against the backdrop and isn't connected
    to the corners) keeps its alpha.
    """
    if img.size[0] * img.size[1] > 4_000_000:
        # 4MP guard so we don't BFS through a 24MP image and starve the worker.
        # Resize for the bg-strip pass, but keep the result at original size
        # would require either a mask resize OR skipping. Cheaper to skip.
        return img
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()

    def _is_bg(c, seed):
        return (
            abs(c[0] - seed[0]) <= tolerance
            and abs(c[1] - seed[1]) <= tolerance
            and abs(c[2] - seed[2]) <= tolerance
        )

    # Use the average of the 4 corner pixels as the background seed.
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    seed = (
        sum(c[0] for c in corners) // 4,
        sum(c[1] for c in corners) // 4,
        sum(c[2] for c in corners) // 4,
    )

    visited = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()
    # Seed with all edge pixels that match the background color.
    for x in range(w):
        for y in (0, h - 1):
            if _is_bg(px[x, y], seed):
                queue.append((x, y))
                visited[y * w + x] = 1
    for y in range(h):
        for x in (0, w - 1):
            if _is_bg(px[x, y], seed):
                queue.append((x, y))
                visited[y * w + x] = 1

    bg_pixel_count_estimate = sum(visited)
    # If fewer than 5% of edge pixels look like the seed background, the
    # photo probably isn't on a uniform backdrop — bail to avoid mangling.
    if bg_pixel_count_estimate < (2 * (w + h)) * 0.10:
        return img

    while queue:
        x, y = queue.popleft()
        px[x, y] = (0, 0, 0, 0)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
                if not visited[idx]:
                    visited[idx] = 1
                    if _is_bg(px[nx, ny], seed):
                        queue.append((nx, ny))
    return img

# Cap decoded pixel count well below product-photo needs to defend against
# decompression bombs (a tiny PNG can declare 50000x50000 dimensions and
# allocate gigabytes during decode). 24MP covers a 6000x4000 DSLR shot.
Image.MAX_IMAGE_PIXELS = 24_000_000

router = APIRouter()
settings = get_settings()

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
    # WebP: RIFF????WEBP + a 4-byte chunk fourcc ("VP8 ", "VP8L", or "VP8X").
    # The 4-byte chunk header rejects empty / crafted-but-meaningless payloads.
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" and content[12:16] in (b"VP8 ", b"VP8L", b"VP8X"):
        return "webp"
    return None


@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Solo se permiten imagenes JPEG, PNG y WebP")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "La imagen no puede superar 10MB")
    if len(content) < MIN_SIZE:
        raise HTTPException(400, "La imagen es demasiado pequena. Sube una foto del producto.")

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
            # Strip uniform backdrop so the product photo blends into any
            # landing-page background instead of looking like a sticker
            # cut from a white square. JPEGs don't support transparency,
            # so always promote to PNG when the strip succeeds (alpha
            # actually appears) — otherwise downsteam consumers see a
            # white rectangle painted by the JPEG encoder.
            stripped = _strip_solid_background(img)
            # Cheap "did it actually carve out a hole?" check: sample a few
            # corner pixels — if they're now alpha=0 the strip worked.
            had_alpha = False
            if stripped.mode == "RGBA":
                w, h = stripped.size
                samples = [stripped.getpixel((0, 0)), stripped.getpixel((w - 1, h - 1))]
                had_alpha = any(p[3] == 0 for p in samples)
            if had_alpha:
                # Force PNG output regardless of declared format.
                detected = "png"
                img = stripped
            else:
                if detected == "jpg" and img.mode != "RGB":
                    img = img.convert("RGB")
                elif detected == "png" and img.mode in ("I", "F", "CMYK"):
                    img = img.convert("RGBA" if "A" in img.mode else "RGB")
            out = io.BytesIO()
            save_kwargs = {"quality": 92, "optimize": True} if detected == "jpg" else {"optimize": True}
            img.save(out, format=PILLOW_FORMATS[detected], **save_kwargs)
            clean_bytes = out.getvalue()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "El archivo no es una imagen valida")
    # Re-encoded bytes can outsize the original (e.g. JPEG q=92 from a q=20
    # source). Enforce the cap on the FINAL on-disk bytes, not just the upload.
    if len(clean_bytes) > MAX_SIZE:
        raise HTTPException(400, "La imagen procesada supera el tamano permitido")

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
