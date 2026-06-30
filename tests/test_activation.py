from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import activation
from app.services.activation import (
    _kind_for_age, _parse_ts, run_activation_reminders,
)
from app.services.gmail import build_reminder_email, build_winback_email


def _iso_days_ago(d: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


# ── Pure helpers ─────────────────────────────────────────────────────────────
def test_kind_for_age_windows():
    assert _kind_for_age(0.5) is None      # < 1 dia: el correo de compra ya salio
    assert _kind_for_age(1.0) == "reminder_d1"
    assert _kind_for_age(2.9) == "reminder_d1"
    assert _kind_for_age(3.0) == "reminder_d3"
    assert _kind_for_age(6.9) == "reminder_d3"
    assert _kind_for_age(7.0) == "reminder_d7"
    assert _kind_for_age(13.9) == "reminder_d7"
    assert _kind_for_age(14.0) == "winback"
    assert _kind_for_age(67.0) == "winback"   # los compradores historicos


def test_parse_ts_handles_z_and_offset_and_null():
    assert _parse_ts("2026-01-01T00:00:00Z") is not None
    assert _parse_ts("2026-01-01T00:00:00+00:00") is not None
    assert _parse_ts(None) is None
    assert _parse_ts("basura") is None


# ── Templates (estilo Apple) ─────────────────────────────────────────────────
def test_reminder_templates_have_credits_cta_and_apple_base():
    for day, cta in [(1, "Crear mi primer video"), (3, "Crear mi video"), (7, "Usar mis créditos")]:
        subject, html = build_reminder_email("x@y.com", 20, day)
        assert "20" in html
        assert cta in html
        assert "app.phinodia.com/videos" in html
        assert "Ley 1581" in html          # viene del _apple_email_base


def test_winback_template():
    subject, html = build_winback_email("x@y.com", 50)
    assert "50" in html
    assert "Crear mi video ahora" in html
    assert "Aún tienes" in subject or "créditos" in subject
    assert "app.phinodia.com/videos" in html


# ── run_activation_reminders ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sends_winback_to_old_unactivated_user():
    users = [{"id": "u1", "email": "buyer@x.com", "credits": 20, "created_at": _iso_days_ago(67)}]
    jobs = []  # nadie tiene job 'completed'
    send = MagicMock()
    with patch.object(activation.db, "select", AsyncMock(side_effect=[[], users, jobs, []])), \
         patch.object(activation.db, "insert", AsyncMock(return_value={})) as ins, \
         patch.object(activation, "_send_sync", send):
        sent = await run_activation_reminders()
    assert sent == 1
    ins.assert_awaited_once()
    assert ins.await_args.args[1] == {"user_id": "u1", "kind": "winback"}
    send.assert_called_once()
    # destinatario y asunto correctos
    assert send.call_args.args[0] == "buyer@x.com"


@pytest.mark.asyncio
async def test_skips_activated_users():
    users = [{"id": "u1", "email": "a@x.com", "credits": 20, "created_at": _iso_days_ago(67)}]
    jobs = [{"user_id": "u1"}]  # u1 SI tiene un job completado -> no elegible
    send = MagicMock()
    with patch.object(activation.db, "select", AsyncMock(side_effect=[[], users, jobs, []])), \
         patch.object(activation.db, "insert", AsyncMock(return_value={})) as ins, \
         patch.object(activation, "_send_sync", send):
        sent = await run_activation_reminders()
    assert sent == 0
    ins.assert_not_awaited()
    send.assert_not_called()


@pytest.mark.asyncio
async def test_skips_recent_user_under_one_day():
    users = [{"id": "u1", "email": "a@x.com", "credits": 20, "created_at": _iso_days_ago(0.2)}]
    send = MagicMock()
    with patch.object(activation.db, "select", AsyncMock(side_effect=[[], users, [], []])), \
         patch.object(activation.db, "insert", AsyncMock(return_value={})) as ins, \
         patch.object(activation, "_send_sync", send):
        sent = await run_activation_reminders()
    assert sent == 0
    ins.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_claimed_does_not_resend():
    users = [{"id": "u1", "email": "a@x.com", "credits": 20, "created_at": _iso_days_ago(67)}]
    send = MagicMock()
    # insert lanza (conflicto UNIQUE = ya enviado) -> no se envia
    with patch.object(activation.db, "select", AsyncMock(side_effect=[[], users, [], []])), \
         patch.object(activation.db, "insert", AsyncMock(side_effect=Exception("409 conflict"))), \
         patch.object(activation, "_send_sync", send):
        sent = await run_activation_reminders()
    assert sent == 0
    send.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_flag_short_circuits(monkeypatch):
    monkeypatch.setattr(activation.settings, "activation_reminders_enabled", False)
    sel = AsyncMock()
    with patch.object(activation.db, "select", sel):
        sent = await run_activation_reminders()
    assert sent == 0
    sel.assert_not_awaited()  # ni siquiera consulta la DB


@pytest.mark.asyncio
async def test_send_failure_reverts_claim():
    users = [{"id": "u1", "email": "a@x.com", "credits": 20, "created_at": _iso_days_ago(67)}]
    send = MagicMock(side_effect=RuntimeError("gmail down"))
    with patch.object(activation.db, "select", AsyncMock(side_effect=[[], users, [], []])), \
         patch.object(activation.db, "insert", AsyncMock(return_value={})), \
         patch.object(activation.db, "delete", AsyncMock(return_value=[])) as dele, \
         patch.object(activation, "_send_sync", send):
        sent = await run_activation_reminders()
    assert sent == 0
    # el reclamo se revierte para reintentar el proximo ciclo
    dele.assert_awaited_once()
    assert dele.await_args.args[1] == {"user_id": "eq.u1", "kind": "eq.winback"}


@pytest.mark.asyncio
async def test_respects_per_cycle_cap():
    many = [
        {"id": f"u{i}", "email": f"u{i}@x.com", "credits": 20, "created_at": _iso_days_ago(67)}
        for i in range(activation.MAX_PER_CYCLE + 10)
    ]
    send = MagicMock()
    with patch.object(activation.db, "select", AsyncMock(side_effect=[[], many, [], []])), \
         patch.object(activation.db, "insert", AsyncMock(return_value={})), \
         patch.object(activation, "_send_sync", send):
        sent = await run_activation_reminders()
    assert sent == activation.MAX_PER_CYCLE


@pytest.mark.asyncio
async def test_optout_is_respected():
    # Quien se dio de baja (email en email_optout) NUNCA recibe correo.
    users = [{"id": "u1", "email": "baja@x.com", "credits": 20, "created_at": _iso_days_ago(67)}]
    send = MagicMock()
    # selects en orden: cap, users, jobs-chunk, email_optout
    with patch.object(activation.db, "select",
                      AsyncMock(side_effect=[[], users, [], [{"email": "baja@x.com"}]])), \
         patch.object(activation.db, "insert", AsyncMock(return_value={})) as ins, \
         patch.object(activation, "_send_sync", send):
        sent = await run_activation_reminders()
    assert sent == 0
    ins.assert_not_awaited()
    send.assert_not_called()


def test_unsubscribe_url_firmado():
    import hmac, hashlib
    from urllib.parse import urlparse, parse_qs
    from app.config import get_settings
    from app.services.gmail import unsubscribe_url
    url = unsubscribe_url("  Test@X.com ")
    q = parse_qs(urlparse(url).query)
    assert "/api/v1/unsubscribe" in url
    assert q["e"][0] == "test@x.com"          # normalizado (trim + minusculas)
    expected = hmac.new(get_settings().wompi_integrity_secret.encode(), b"test@x.com", hashlib.sha256).hexdigest()[:32]
    assert q["t"][0] == expected               # token HMAC valido para ESE correo
