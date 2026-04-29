from app.services.media_probe import _extract_mp4_duration_seconds, expected_video_duration_seconds, is_multi_step_video_service


def _box(box_type: bytes, payload: bytes) -> bytes:
    size = (8 + len(payload)).to_bytes(4, "big")
    return size + box_type + payload


def test_expected_video_duration_mapping():
    assert expected_video_duration_seconds("video_15s") == 15.0
    assert expected_video_duration_seconds("video_30s") == 30.0
    assert expected_video_duration_seconds("image") is None
    assert is_multi_step_video_service("video_15s") is True
    assert is_multi_step_video_service("video_8s") is False


def test_extract_mp4_duration_seconds_from_mvhd_box():
    mvhd_payload = (
        b"\x00\x00\x00\x00"  # version + flags
        + (0).to_bytes(4, "big")  # creation_time
        + (0).to_bytes(4, "big")  # modification_time
        + (1000).to_bytes(4, "big")  # timescale
        + (15000).to_bytes(4, "big")  # duration
        + b"\x00" * 40
    )
    data = _box(b"moov", _box(b"mvhd", mvhd_payload))
    assert _extract_mp4_duration_seconds(data) == 15.0
