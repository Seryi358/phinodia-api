"""Download external job results to local storage so the user-facing
result_url stops 404'ing once the upstream provider's tempfile expires.

KIE/aiquickdraw URLs return 200 for ~7-30 days then disappear. Without
this, the "Mis generaciones" history breaks for old completed jobs.

We download the bytes synchronously inside the worker that's already
about to write `result_url` to the DB — keeps the read-after-write
consistent and avoids a second background task to manage.
"""
import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

RESULTS_DIR = os.path.join("data", "uploads", "results")
PUBLIC_PREFIX = "/uploads/results"

# Cap downloads — KIE videos are ~5-15MB, images ~1-3MB. 50MB is generous.
MAX_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _is_external(url: str) -> bool:
    """True only for https URLs whose host is NOT us. We never re-download
    a URL that's already on our own origin (would loop forever)."""
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme != "https" or not u.netloc:
        return False
    api_host = urlparse(_settings.api_base_url).netloc
    return u.netloc.lower() != api_host.lower()


async def persist_external_url(url: str, job_id: str, ext: str) -> str:
    """Download `url` and serve it from our own /uploads/results/.

    Returns a same-origin URL the frontend can render forever, OR the
    original URL on any failure (so the user still sees their result
    today even if archival to our disk failed — they only lose the
    long-term thumbnail later).

    `ext` should be the file extension WITHOUT a dot (e.g. "mp4", "jpg").
    """
    if not url or not _is_external(url):
        return url

    # Sanity-check the extension — never let an attacker-controlled value
    # (e.g. ".php") become part of an on-disk filename.
    safe_ext = (ext or "").lower().strip().lstrip(".")
    if safe_ext not in ("mp4", "jpg", "jpeg", "png", "webp"):
        return url

    os.makedirs(RESULTS_DIR, exist_ok=True)
    filename = f"{job_id}.{safe_ext}"
    path = os.path.join(RESULTS_DIR, filename)

    tmp = path + ".part"
    success = False
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    logger.warning("persist_external_url: %s returned %d for job %s", url, resp.status_code, job_id)
                    return url
                total = 0
                # Write to a temp file then rename — atomic publish so a
                # half-downloaded file can't be served on a worker crash.
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            logger.warning("persist_external_url: %s exceeded MAX_BYTES for job %s", url, job_id)
                            return url
                        f.write(chunk)
                os.replace(tmp, path)
                success = True
    except (httpx.HTTPError, OSError, asyncio.TimeoutError, asyncio.CancelledError) as e:
        logger.warning("persist_external_url failed for job %s (%s): %s — keeping upstream URL", job_id, url, e)
        if isinstance(e, asyncio.CancelledError):
            raise
        return url
    finally:
        # Always clean the .part — earlier code only removed it on the
        # MAX_BYTES branch, so any httpx/OSError mid-stream left the orphan
        # forever. Filling data/uploads/results/ over time.
        if not success:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    return f"{_settings.api_base_url}{PUBLIC_PREFIX}/{filename}"
