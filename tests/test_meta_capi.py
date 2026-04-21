"""Meta CAPI service tests.

Coverage focus:
- No-op when env vars missing (deploy-safe before BM is wired)
- Hash + normalize matches Meta spec (lowercase, trim, sha256)
- Email → first/last name extraction
- Payload shape sent to graph.facebook.com
- Errors are swallowed (Meta downtime can't fail a Wompi webhook)
- Test event code routing
"""
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_sha256_norm_normalizes_then_hashes():
    from app.services.meta_capi import _sha256_norm

    # Lowercase + trim per Meta spec
    expected = hashlib.sha256(b"sergio@phinodia.com").hexdigest()
    assert _sha256_norm("  Sergio@Phinodia.COM  ") == expected
    assert _sha256_norm("sergio@phinodia.com") == expected

    # Empty/None returns None so we skip the field (don't ship null hash)
    assert _sha256_norm("") is None
    assert _sha256_norm(None) is None
    assert _sha256_norm("   ") is None


def test_split_name_from_email_basic():
    from app.services.meta_capi import _split_name_from_email

    assert _split_name_from_email("juan.perez@gmail.com") == ("juan", "perez")
    assert _split_name_from_email("juan_perez@gmail.com") == ("juan", "perez")
    assert _split_name_from_email("juan-perez@gmail.com") == ("juan", "perez")
    # +addresses are stripped before split
    assert _split_name_from_email("juan.perez+spam@gmail.com") == ("juan", "perez")
    # Single token = first name only
    assert _split_name_from_email("sergio@phinodia.com") == ("sergio", None)
    # Skip non-alpha tokens (e.g. "user123" → no name)
    assert _split_name_from_email("user123@x.com") == (None, None)
    assert _split_name_from_email("") == (None, None)
    assert _split_name_from_email(None) == (None, None)


def test_new_event_id_unique_uuid_hex():
    from app.services.meta_capi import new_event_id

    a = new_event_id()
    b = new_event_id()
    assert a != b
    assert len(a) == 32  # UUID4 hex
    assert all(c in "0123456789abcdef" for c in a)


@pytest.mark.asyncio
async def test_capi_disabled_when_env_missing():
    """No env vars → enabled=False and send_event becomes a no-op."""
    from app.services.meta_capi import MetaCAPI

    with patch("app.services.meta_capi.get_settings") as MockSettings:
        MockSettings.return_value = MagicMock(
            meta_pixel_id="", meta_capi_access_token="", meta_test_event_code=""
        )
        capi = MetaCAPI()
        assert capi.enabled is False
        result = await capi.send_event("Purchase", value_cop=11990, email="x@y.com")
        assert result is False  # no network call attempted


@pytest.mark.asyncio
async def test_capi_send_event_payload_shape_and_hashing():
    """When enabled, send_event POSTs the right shape to the graph endpoint."""
    from app.services.meta_capi import MetaCAPI

    with patch("app.services.meta_capi.get_settings") as MockSettings:
        MockSettings.return_value = MagicMock(
            meta_pixel_id="123456789",
            meta_capi_access_token="EAA-test-token",
            meta_test_event_code="",
        )
        capi = MetaCAPI()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.services.meta_capi.httpx.AsyncClient") as MockClient:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=False)
            mock_inst.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_inst

            ok = await capi.send_event(
                event_name="Purchase",
                event_id="PH-image_3-1234-abc",
                email="Sergio@PHinodia.com",
                client_ip="190.158.1.1",
                user_agent="Mozilla/5.0",
                fbp="fb.1.1234.567",
                fbc="fb.1.1234.fbclid_xyz",
                value_cop=11990,
                currency="COP",
                content_ids=["image_3"],
            )
            assert ok is True

            call = mock_inst.post.call_args
            url = call.args[0]
            assert "graph.facebook.com" in url
            assert "/123456789/events" in url
            assert call.kwargs["params"]["access_token"] == "EAA-test-token"

            body = call.kwargs["json"]
            assert "data" in body
            ev = body["data"][0]
            assert ev["event_name"] == "Purchase"
            assert ev["event_id"] == "PH-image_3-1234-abc"
            assert ev["action_source"] == "website"

            ud = ev["user_data"]
            # Email lowercased+hashed
            expected_em = hashlib.sha256(b"sergio@phinodia.com").hexdigest()
            assert ud["em"] == [expected_em]
            # First/last name extracted from email + hashed
            assert "fn" in ud and "ln" not in ud  # only "sergio" before @
            assert ud["fn"] == [hashlib.sha256(b"sergio").hexdigest()]
            # Country always sent (we're CO-only)
            assert ud["country"] == [hashlib.sha256(b"co").hexdigest()]
            # Raw signals passed through
            assert ud["client_ip_address"] == "190.158.1.1"
            assert ud["client_user_agent"] == "Mozilla/5.0"
            assert ud["fbp"] == "fb.1.1234.567"
            assert ud["fbc"] == "fb.1.1234.fbclid_xyz"

            cd = ev["custom_data"]
            assert cd["value"] == 11990.0
            assert cd["currency"] == "COP"
            assert cd["content_ids"] == ["image_3"]
            assert cd["content_type"] == "product"

            # No test_event_code in body when env is empty
            assert "test_event_code" not in body


@pytest.mark.asyncio
async def test_capi_test_event_code_routes_to_test_events():
    from app.services.meta_capi import MetaCAPI

    with patch("app.services.meta_capi.get_settings") as MockSettings:
        MockSettings.return_value = MagicMock(
            meta_pixel_id="123456789",
            meta_capi_access_token="EAA-test-token",
            meta_test_event_code="TEST12345",
        )
        capi = MetaCAPI()

        mock_response = MagicMock(status_code=200)
        with patch("app.services.meta_capi.httpx.AsyncClient") as MockClient:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=False)
            mock_inst.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_inst

            await capi.send_event("Purchase", email="x@y.com")
            body = mock_inst.post.call_args.kwargs["json"]
            assert body["test_event_code"] == "TEST12345"


@pytest.mark.asyncio
async def test_capi_swallows_meta_5xx():
    """Meta returning 500 must NOT raise — purchase flow can't fail
    because tracking failed."""
    from app.services.meta_capi import MetaCAPI

    with patch("app.services.meta_capi.get_settings") as MockSettings:
        MockSettings.return_value = MagicMock(
            meta_pixel_id="123456789",
            meta_capi_access_token="EAA-test-token",
            meta_test_event_code="",
        )
        capi = MetaCAPI()

        mock_response = MagicMock(status_code=500, text="meta server error")
        with patch("app.services.meta_capi.httpx.AsyncClient") as MockClient:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=False)
            mock_inst.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_inst

            ok = await capi.send_event("Purchase", email="x@y.com")
            assert ok is False  # logged + swallowed, returns False


@pytest.mark.asyncio
async def test_capi_swallows_network_exception():
    """httpx blowing up (DNS failure, conn reset) must NOT raise."""
    from app.services.meta_capi import MetaCAPI

    with patch("app.services.meta_capi.get_settings") as MockSettings:
        MockSettings.return_value = MagicMock(
            meta_pixel_id="123456789",
            meta_capi_access_token="EAA-test-token",
            meta_test_event_code="",
        )
        capi = MetaCAPI()

        with patch("app.services.meta_capi.httpx.AsyncClient") as MockClient:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=False)
            mock_inst.post = AsyncMock(side_effect=Exception("DNS resolution failed"))
            MockClient.return_value = mock_inst

            ok = await capi.send_event("Purchase", email="x@y.com")
            assert ok is False
