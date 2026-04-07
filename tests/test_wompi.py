import hashlib
import pytest


def test_verify_webhook_signature_valid():
    from app.services.wompi import verify_webhook_signature
    properties = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
    event = {
        "event": "transaction.updated",
        "data": {
            "transaction": {
                "id": "123-abc",
                "status": "APPROVED",
                "amount_in_cents": 3999000,
            }
        },
        "signature": {"properties": properties, "checksum": ""},
        "timestamp": 1700000000,
        "sent_at": "2026-04-04T12:00:00.000Z",
    }
    secret = "test_events_secret_123"
    concat = "123-abcAPPROVED39990001700000000" + secret
    expected_checksum = hashlib.sha256(concat.encode()).hexdigest()
    event["signature"]["checksum"] = expected_checksum
    assert verify_webhook_signature(event, secret) is True


def test_verify_webhook_signature_invalid():
    from app.services.wompi import verify_webhook_signature
    event = {
        "event": "transaction.updated",
        "data": {"transaction": {"id": "123-abc", "status": "APPROVED", "amount_in_cents": 3999000}},
        "signature": {
            "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
            "checksum": "definitely_wrong_checksum",
        },
        "timestamp": 1700000000,
    }
    assert verify_webhook_signature(event, "test_secret") is False


def test_resolve_property():
    from app.services.wompi import _resolve_property
    data = {"transaction": {"id": "abc", "nested": {"deep": "value"}}}
    assert _resolve_property(data, "transaction.id") == "abc"
    assert _resolve_property(data, "transaction.nested.deep") == "value"


# Package resolution tests — by amount alone and by sku
PACKAGE_MAP_CASES = [
    # video_8s
    (2499000, None, "video_8s", 5),
    (3499000, None, "video_8s", 10),
    (5999000, "video_8s_20", "video_8s", 20),
    (12999000, None, "video_8s", 50),
    # video_15s
    (3999000, None, "video_15s", 5),
    (5499000, None, "video_15s", 10),
    (9999000, None, "video_15s", 20),
    (21999000, None, "video_15s", 50),
    # video_22s (by sku — some amounts overlap)
    (5999000, "video_22s_5", "video_22s", 5),
    (8499000, None, "video_22s", 10),
    (14999000, None, "video_22s", 20),
    (32999000, None, "video_22s", 50),
    # video_30s
    (7999000, "video_30s_5", "video_30s", 5),
    (10999000, None, "video_30s", 10),
    (19999000, "video_30s_20", "video_30s", 20),
    (44999000, None, "video_30s", 50),
    # images
    (1999000, None, "image", 5),
    (2499000, "image_10", "image", 10),
    (4999000, "image_20", "image", 20),
    (11999000, "image_50", "image", 50),
    # landing pages
    (7999000, "landing_5", "landing_page", 5),
    (15999000, "landing_10", "landing_page", 10),
    (19999000, "landing_20", "landing_page", 20),
    (29999000, None, "landing_page", 50),
]


@pytest.mark.parametrize("amount,sku,service,credits_expected", PACKAGE_MAP_CASES)
def test_resolve_package(amount, sku, service, credits_expected):
    from app.services.wompi import resolve_package
    pkg = resolve_package(amount, sku=sku)
    assert pkg is not None
    assert pkg["service"] == service
    assert pkg["credits"] == credits_expected
