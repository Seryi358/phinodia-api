import hashlib
import pytest


def test_verify_webhook_signature_valid():
    from app.services.wompi import verify_webhook_signature
    secret = "test_events_secret_123"
    # Wompi only signs id/status/amount_in_cents in production webhooks.
    # Requiring more here would 403 every legit webhook.
    properties = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
    concat = "123-abcAPPROVED39990001700000000" + secret
    expected_checksum = hashlib.sha256(concat.encode()).hexdigest()
    event = {
        "event": "transaction.updated",
        "data": {"transaction": {"id": "123-abc", "status": "APPROVED", "amount_in_cents": 3999000}},
        "signature": {"properties": properties, "checksum": expected_checksum},
        "timestamp": 1700000000,
    }
    assert verify_webhook_signature(event, secret) is True


def test_verify_webhook_signature_invalid():
    from app.services.wompi import verify_webhook_signature
    event = {
        "event": "transaction.updated",
        "data": {"transaction": {"id": "x", "status": "APPROVED", "amount_in_cents": 3999000}},
        "signature": {
            "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
            "checksum": "definitely_wrong",
        },
        "timestamp": 1700000000,
    }
    assert verify_webhook_signature(event, "test_secret") is False


def test_resolve_property_nested():
    from app.services.wompi import _resolve_property
    data = {"transaction": {"id": "abc", "nested": {"deep": "value"}}}
    assert _resolve_property(data, "transaction.id") == "abc"
    assert _resolve_property(data, "transaction.nested.deep") == "value"
    assert _resolve_property(data, "missing.path") is None


# Strict resolution: SKU and amount BOTH must match. Cases derived from the
# 18 SKUs in PACKAGES_BY_SKU.
@pytest.mark.parametrize("sku,amount,service,credits", [
    ("video_8s_3", 6299000, "video_8s", 3),
    ("video_8s_10", 18999000, "video_8s", 10),
    ("video_8s_25", 39999000, "video_8s", 25),
    ("video_15s_3", 11699000, "video_15s", 3),
    ("video_15s_10", 33999000, "video_15s", 10),
    ("video_22s_25", 104999000, "video_22s", 25),
    ("video_30s_3", 21899000, "video_30s", 3),
    ("image_3", 1199000, "image", 3),
    ("image_10", 3999000, "image", 10),
    ("image_25", 9999000, "image", 25),
    ("landing_3", 4499000, "landing_page", 3),
    ("landing_10", 14999000, "landing_page", 10),
    ("landing_25", 37499000, "landing_page", 25),
])
def test_resolve_package_strict_match(sku, amount, service, credits):
    from app.services.wompi import resolve_package
    pkg = resolve_package(amount, sku=sku)
    assert pkg is not None
    assert pkg["service"] == service
    assert pkg["credits"] == credits


def test_resolve_package_unknown_sku_rejected():
    from app.services.wompi import resolve_package
    # Unknown SKU even if amount matches a real package
    assert resolve_package(1199000, sku="fake_sku") is None
    assert resolve_package(1199000, sku="image_99") is None


def test_resolve_package_amount_mismatch_rejected():
    from app.services.wompi import resolve_package
    # Real SKU but wrong amount
    assert resolve_package(999, sku="image_3") is None
    assert resolve_package(1199001, sku="image_3") is None  # off-by-one
    assert resolve_package(1199000, sku="image_10") is None  # right service, wrong tier


def test_resolve_package_no_sku_rejected():
    from app.services.wompi import resolve_package
    # No SKU = no resolution (strict mode after iter 50 fix)
    assert resolve_package(1199000, sku=None) is None
    assert resolve_package(1199000, sku="") is None


def test_packages_by_sku_has_18_unique_amounts():
    from app.services.wompi import PACKAGES_BY_SKU
    assert len(PACKAGES_BY_SKU) == 18
    amounts = [p["amount"] for p in PACKAGES_BY_SKU.values()]
    assert len(set(amounts)) == 18, "All package amounts must be unique"
