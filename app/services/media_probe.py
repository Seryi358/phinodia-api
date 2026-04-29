import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

RESULTS_DIR = Path("data/uploads/results").resolve()
MAX_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

VIDEO_DURATIONS = {
    "video_8s": 8.0,
    "video_15s": 15.0,
    "video_22s": 22.0,
    "video_30s": 30.0,
}
MULTI_STEP_VIDEO_SERVICES = {"video_15s", "video_22s", "video_30s"}
CONTAINER_TYPES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"edts",
    b"udta",
    b"meta",
    b"dinf",
}


def expected_video_duration_seconds(service_type: str | None) -> float | None:
    return VIDEO_DURATIONS.get(service_type or "")


def is_multi_step_video_service(service_type: str | None) -> bool:
    return (service_type or "") in MULTI_STEP_VIDEO_SERVICES


def _local_result_path(value: str | None) -> Path | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        if parsed.path.startswith("/uploads/results/"):
            return RESULTS_DIR / Path(parsed.path).name
        return None
    path = Path(value)
    return path if path.exists() else None


async def _read_video_bytes(value: str) -> bytes | None:
    local = _local_result_path(value)
    if local and local.is_file():
        return local.read_bytes()

    parsed = urlparse(value)
    if parsed.scheme != "https":
        return None

    total = 0
    chunks: list[bytes] = []
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", value) as resp:
                if resp.status_code != 200:
                    return None
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        logger.warning("media_probe: %s exceeded MAX_BYTES", value)
                        return None
                    chunks.append(chunk)
    except (httpx.HTTPError, asyncio.TimeoutError, OSError):
        return None
    return b"".join(chunks)


def _read_box_size(data: bytes, pos: int, end: int) -> tuple[int, int] | None:
    if pos + 8 > end:
        return None
    size = int.from_bytes(data[pos:pos + 4], "big")
    header = 8
    if size == 1:
        if pos + 16 > end:
            return None
        size = int.from_bytes(data[pos + 8:pos + 16], "big")
        header = 16
    elif size == 0:
        size = end - pos
    if size < header or pos + size > end:
        return None
    return size, header


def _parse_mvhd_duration(data: bytes, start: int, end: int) -> float | None:
    if start + 20 > end:
        return None
    version = data[start]
    if version == 1:
        if start + 32 > end:
            return None
        timescale = int.from_bytes(data[start + 20:start + 24], "big")
        duration = int.from_bytes(data[start + 24:start + 32], "big")
    else:
        timescale = int.from_bytes(data[start + 12:start + 16], "big")
        duration = int.from_bytes(data[start + 16:start + 20], "big")
    if timescale <= 0:
        return None
    return duration / timescale


def _extract_mp4_duration_seconds(data: bytes, start: int = 0, end: int | None = None) -> float | None:
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        box = _read_box_size(data, pos, end)
        if not box:
            return None
        size, header = box
        box_type = data[pos + 4:pos + 8]
        payload_start = pos + header
        payload_end = pos + size
        if box_type == b"mvhd":
            return _parse_mvhd_duration(data, payload_start, payload_end)
        if box_type in CONTAINER_TYPES:
            inner = _extract_mp4_duration_seconds(data, payload_start, payload_end)
            if inner is not None:
                return inner
        pos += size
    return None


async def probe_video_duration_seconds(value: str | None) -> float | None:
    if not value:
        return None
    data = await _read_video_bytes(value)
    if not data:
        return None
    return _extract_mp4_duration_seconds(data)


async def is_video_duration_sufficient(
    value: str | None,
    service_type: str | None,
    tolerance_seconds: float = 0.75,
) -> bool:
    expected = expected_video_duration_seconds(service_type)
    if expected is None:
        return False
    duration = await probe_video_duration_seconds(value)
    if duration is None:
        return False
    return duration + tolerance_seconds >= expected
