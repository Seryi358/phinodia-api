"""ffmpeg-based video stitching for Gemini-Omni videos longer than 10s.

Omni caps a single generation at 10s, so longer videos are built by generating
N clips of <=10s and concatenating them. To keep the avatar, room and product
consistent across cuts we use a "last-frame relay": the last frame of clip N is
hosted publicly and reused as the first frame (image_urls[0]) of clip N+1. This
module provides the ffmpeg primitives plus the local<->public-URL plumbing the
video worker needs.

ffmpeg is installed in the Docker image (apt-get ffmpeg). Locally, point
FFMPEG_BIN at a static binary (e.g. the one bundled by imageio-ffmpeg).
"""
import asyncio
import logging
import os
import shutil
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
RESULTS_DIR = Path("data/uploads/results")
PUBLIC_PREFIX = "/uploads/results"
WORK_DIR = Path("data/uploads/work")

# Canonical spec every clip is normalized to before concat — identical codec,
# resolution, fps and pixel format so the concat demuxer can `-c copy`
# losslessly instead of producing a janky re-mux. 720x1280 = vertical 9:16 720p.
TARGET_W, TARGET_H, TARGET_FPS = 720, 1280, 30


async def _run(args: list[str], timeout: float = 300) -> bool:
    """Run ffmpeg non-blockingly. Returns True on exit 0."""
    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            logger.warning("ffmpeg failed (%s): %s", args[:3], (stderr or b"")[:300])
            return False
        return True
    except asyncio.TimeoutError:
        logger.warning("ffmpeg timed out: %s", args[:3])
        return False
    except (FileNotFoundError, OSError) as e:
        logger.warning("ffmpeg exec error: %s", e)
        return False


async def extract_last_frame(video_path: str, out_png: str) -> bool:
    """Grab ~the last frame by seeking from end-of-file (cheap, no full decode).

    -sseof -0.3 seeks ~0.3s before EOF; -update 1 -frames:v 1 writes one image.
    Used as the conditioning first frame of the next clip (last-frame relay).
    """
    return await _run(["-sseof", "-0.3", "-i", video_path,
                       "-update", "1", "-frames:v", "1", "-q:v", "2", out_png])


async def normalize_clip(in_path: str, out_path: str) -> bool:
    """Re-encode to the canonical 9:16/720p/30fps/yuv420p/h264+aac spec so all
    clips share identical parameters and a subsequent concat -c copy is valid.

    Letterbox-pads rather than crops so the product is never cut off if a clip
    comes back at a slightly different aspect ratio.
    """
    vf = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
          f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
          f"fps={TARGET_FPS},format=yuv420p")
    return await _run(["-i", in_path, "-vf", vf,
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                       "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
                       "-movflags", "+faststart", out_path])


async def concat_clips(clip_paths: list[str], out_path: str) -> bool:
    """Normalize each clip then losslessly concat them into out_path.

    Single-clip inputs are just normalized+copied (still guarantees the output
    spec). Returns True only if the final file was produced.
    """
    clip_paths = [c for c in clip_paths if c and os.path.exists(c)]
    if not clip_paths:
        return False
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    base = os.path.basename(out_path)
    normed: list[str] = []
    for i, c in enumerate(clip_paths):
        n = str(WORK_DIR / f"norm_{base}_{i}.mp4")
        if await normalize_clip(c, n):
            normed.append(n)
        else:
            logger.warning("normalize failed for clip %d (%s) — skipping", i, c)
    if not normed:
        return False
    try:
        if len(normed) == 1:
            shutil.copyfile(normed[0], out_path)
            ok = True
        else:
            listf = str(WORK_DIR / f"list_{base}.txt")
            with open(listf, "w") as f:
                for p in normed:
                    f.write(f"file '{os.path.abspath(p)}'\n")
            ok = await _run(["-f", "concat", "-safe", "0", "-i", listf,
                             "-c", "copy", "-movflags", "+faststart", out_path])
    finally:
        for p in normed:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.remove(str(WORK_DIR / f"list_{base}.txt"))
        except OSError:
            pass
    return ok


def publish_local(local_path: str, public_name: str) -> str | None:
    """Copy a local file into the public /uploads/results dir and return its
    same-origin public URL (which KIE can fetch for the next clip's first frame).
    """
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        dest = RESULTS_DIR / public_name
        shutil.copyfile(local_path, dest)
        return f"{settings.api_base_url}{PUBLIC_PREFIX}/{public_name}"
    except OSError as e:
        logger.warning("publish_local failed for %s: %s", public_name, e)
        return None


_DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_MAX_BYTES = 80 * 1024 * 1024


async def download_to(url: str, path: str) -> bool:
    """Stream an https URL to a local file. Returns True on success.

    Used to pull each KIE clip down locally so we can extract its last frame
    and feed all clips to ffmpeg for concatenation.
    """
    if not url or not url.startswith("https://"):
        return False
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    logger.warning("download_to: %s returned %d", url, resp.status_code)
                    return False
                total = 0
                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        total += len(chunk)
                        if total > _MAX_BYTES:
                            logger.warning("download_to: %s exceeded max bytes", url)
                            return False
                        f.write(chunk)
        return True
    except (httpx.HTTPError, OSError) as e:
        logger.warning("download_to failed for %s: %s", url, type(e).__name__)
        return False
