"""Frictionless one-click checkout: GET /comprar/<sku> -> 302 to Wompi."""
import hashlib
from urllib.parse import urlparse, parse_qs

import pytest
from starlette.requests import Request


def _req():
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "https", "path": "/comprar/x", "raw_path": b"/comprar/x",
        "query_string": b"", "headers": [], "client": ("1.2.3.4", 12345),
        "server": ("app.phinodia.com", 443),
    })


@pytest.mark.asyncio
async def test_comprar_redirects_to_wompi_with_valid_integrity():
    from app.main import comprar_directo
    from app.config import get_settings

    resp = await comprar_directo("credits_6", _req())
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("https://checkout.wompi.co/p/?")

    q = parse_qs(urlparse(loc).query)  # parse_qs un-percent-encodes keys too
    assert q["amount-in-cents"] == ["1490000"]   # credits_6 = $14.900
    assert q["currency"] == ["COP"]

    s = get_settings()
    assert q["public-key"] == [s.wompi_public_key]

    ref = q["reference"][0]
    assert ref.startswith("PH-credits_6-")

    # Integrity hash must match the exact formula the working /checkout uses.
    sig = q["signature:integrity"][0]
    expected = hashlib.sha256(
        f"{ref}1490000COP{s.wompi_integrity_secret}".encode()
    ).hexdigest()
    assert sig == expected

    # Thank-you redirect carries the same reference for the Purchase pixel.
    ru = q["redirect-url"][0]
    assert "gracias-video" in ru and ref in ru


@pytest.mark.asyncio
async def test_comprar_all_three_packs_map_to_correct_amounts():
    from app.main import comprar_directo

    expected = {"credits_6": "1490000", "credits_20": "4790000", "credits_50": "10990000"}
    for sku, cents in expected.items():
        resp = await comprar_directo(sku, _req())
        q = parse_qs(urlparse(resp.headers["location"]).query)
        assert q["amount-in-cents"] == [cents]
        assert q["reference"][0].startswith(f"PH-{sku}-")


@pytest.mark.asyncio
async def test_comprar_unknown_sku_falls_back_to_pricing():
    from app.main import comprar_directo

    resp = await comprar_directo("bogus_pack", _req())
    assert resp.status_code == 302
    assert "app.phinodia.com/precios" in resp.headers["location"]
