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


# Unified credit packs (one wallet). Strict resolution: SKU and amount BOTH match.
#
# Los importes se DERIVAN de PACKAGES_BY_SKU, no se copian. Duplicarlos aquí dejó
# la suite en rojo desde el cambio de precios del 2026-07-14 (el test seguía
# esperando $69.900/$159.900 cuando el catálogo ya cobraba $54.900/$119.900), y un
# test que falla por estar desactualizado deja de avisar de las roturas de verdad.
def _pack_cases():
    from app.services.wompi import PACKAGES_BY_SKU
    return [(sku, p["amount"], p["credits"]) for sku, p in PACKAGES_BY_SKU.items()]


@pytest.mark.parametrize("sku,amount,credits", _pack_cases())
def test_resolve_package_strict_match(sku, amount, credits):
    from app.services.wompi import resolve_package
    pkg = resolve_package(amount, sku=sku)
    assert pkg is not None
    assert pkg["credits"] == credits


def test_catalogo_de_paquetes_no_esta_vacio():
    """Red de seguridad: _pack_cases() derivado no debe degradar a cero casos."""
    from app.services.wompi import PACKAGES_BY_SKU
    assert len(PACKAGES_BY_SKU) >= 3
    assert all(p["amount"] > 0 and p["credits"] > 0 for p in PACKAGES_BY_SKU.values())


def test_resolve_package_unknown_sku_rejected():
    from app.services.wompi import resolve_package
    # Unknown SKU even if amount matches a real package
    assert resolve_package(1690000, sku="fake_sku") is None
    assert resolve_package(1690000, sku="credits_99") is None


def test_resolve_package_amount_mismatch_rejected():
    from app.services.wompi import resolve_package
    assert resolve_package(999, sku="credits_6") is None
    assert resolve_package(1690001, sku="credits_6") is None       # off-by-one
    assert resolve_package(1690000, sku="credits_20") is None      # right family, wrong pack


def test_resolve_package_no_sku_rejected():
    from app.services.wompi import resolve_package
    assert resolve_package(1690000, sku=None) is None
    assert resolve_package(1690000, sku="") is None


def test_packages_by_sku_unique_amounts():
    from app.services.wompi import PACKAGES_BY_SKU
    assert len(PACKAGES_BY_SKU) == 3
    amounts = [p["amount"] for p in PACKAGES_BY_SKU.values()]
    assert len(set(amounts)) == len(amounts), "All package amounts must be unique"
    # No per-service field in the unified model.
    assert all("service" not in p for p in PACKAGES_BY_SKU.values())
    assert all(p["credits"] > 0 and p["amount"] > 0 for p in PACKAGES_BY_SKU.values())
