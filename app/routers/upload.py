import io
import os
import re
import socket
import ipaddress
import uuid
import warnings
from collections import deque
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from PIL import Image, ImageOps
from app.config import get_settings


def _strip_solid_background(img: Image.Image, tolerance: int = 25) -> Image.Image:
    """Edge-flood-fill to alpha=0 so a product photo on a uniform light/dark
    backdrop becomes transparent. Far cheaper and more predictable than
    rembg (no ML model, no extra deps), at the cost of failing on busy
    backgrounds — those simply ship unchanged."""
    if img.size[0] * img.size[1] > 4_000_000:
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

    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    seed = (
        sum(c[0] for c in corners) // 4,
        sum(c[1] for c in corners) // 4,
        sum(c[2] for c in corners) // 4,
    )

    visited = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()
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


Image.MAX_IMAGE_PIXELS = 24_000_000

router = APIRouter()
settings = get_settings()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB
MIN_SIZE = 1024  # 1KB
UPLOAD_DIR = os.path.join("data", "uploads")
MIME_TO_FORMAT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
PILLOW_FORMATS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


def _detect_format(content: bytes) -> str | None:
    """Return canonical extension from magic bytes, or None if unrecognized."""
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" and content[12:16] in (b"VP8 ", b"VP8L", b"VP8X"):
        return "webp"
    return None


def _process_and_save(content: bytes) -> dict:
    """Valida (tamaño + magic bytes), re-encoda por Pillow (quita EXIF, quita
    fondo uniforme), guarda en disco y devuelve {path, url, filename}. Compartido
    por la subida de archivo y la importación por URL."""
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "La imagen no puede superar 10MB")
    if len(content) < MIN_SIZE:
        raise HTTPException(400, "La imagen es demasiado pequena. Sube una foto del producto.")

    detected = _detect_format(content)
    if not detected:
        raise HTTPException(400, "El archivo no es una imagen valida")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(content))
            img.verify()
            img = Image.open(io.BytesIO(content))
            img = ImageOps.exif_transpose(img)
            stripped = _strip_solid_background(img)
            had_alpha = False
            if stripped.mode == "RGBA":
                w, h = stripped.size
                samples = [stripped.getpixel((0, 0)), stripped.getpixel((w - 1, h - 1))]
                had_alpha = any(p[3] == 0 for p in samples)
            if had_alpha:
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

    if len(clean_bytes) > MAX_SIZE:
        raise HTTPException(400, "La imagen procesada supera el tamano permitido")

    filename = f"{uuid.uuid4().hex}.{detected}"
    path = os.path.join(UPLOAD_DIR, filename)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(clean_bytes)

    relative = f"/uploads/{filename}"
    full_url = f"{settings.api_base_url}{relative}"
    return {"path": relative, "url": full_url, "filename": filename}


@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Solo se permiten imagenes JPEG, PNG y WebP")

    content = await file.read()
    detected = _detect_format(content)
    # Defense-in-depth: declared mime must agree with magic bytes.
    if MIME_TO_FORMAT.get(file.content_type) != (detected or ""):
        raise HTTPException(400, "El tipo declarado no coincide con el contenido")
    return _process_and_save(content)


# ── Importar imagen desde la URL de un producto ("pega tu link") ──────────
class UrlIn(BaseModel):
    url: str


def _is_safe_url(url: str) -> bool:
    """Solo http/https hacia hosts públicos. Bloquea SSRF (loopback, IPs
    privadas/link-local/reservadas) resolviendo el host."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


# og:image / twitter:image, en cualquier orden de atributos.
_OG_A = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_OG_B = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\']',
    re.I,
)


async def _fetch_image_from_url(url: str) -> bytes:
    if not _is_safe_url(url):
        raise HTTPException(400, "El enlace no es valido o no esta permitido")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PhinodIABot/1.0; +https://phinodia.com)"}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers=headers, max_redirects=5) as client:
            r = await client.get(url)
            r.raise_for_status()
            # Cortafuegos de tamaño: no bajar respuestas gigantes.
            clen = r.headers.get("content-length")
            if clen and clen.isdigit() and int(clen) > 15 * 1024 * 1024:
                raise HTTPException(400, "El contenido del enlace es demasiado grande")
            ct = (r.headers.get("content-type", "").split(";")[0]).strip().lower()

            # (a) el enlace ES una imagen directa
            if ct in ALLOWED_TYPES:
                return r.content

            # (b) el enlace es HTML -> buscar og:image
            if "html" in ct or ct == "" or "text" in ct:
                html = r.text[:300000]
                m = _OG_A.search(html) or _OG_B.search(html)
                if not m:
                    raise HTTPException(400, "No encontramos una imagen de producto en ese enlace. Sube una foto.")
                img_url = urljoin(str(r.url), m.group(1).replace("&amp;", "&"))
                if not _is_safe_url(img_url):
                    raise HTTPException(400, "La imagen del enlace no es valida")
                ir = await client.get(img_url)
                ir.raise_for_status()
                iclen = ir.headers.get("content-length")
                if iclen and iclen.isdigit() and int(iclen) > 15 * 1024 * 1024:
                    raise HTTPException(400, "La imagen del enlace es demasiado grande")
                return ir.content

            raise HTTPException(400, "Ese enlace no contiene una imagen valida")
    except HTTPException:
        raise
    except httpx.HTTPError:
        raise HTTPException(400, "No pudimos abrir ese enlace. Revisa la URL o sube una foto.")


@router.post("/from-url")
async def upload_from_url(body: UrlIn):
    content = await _fetch_image_from_url((body.url or "").strip())
    return _process_and_save(content)
