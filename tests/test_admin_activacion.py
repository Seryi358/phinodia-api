from unittest.mock import AsyncMock, patch

import pytest

from app.routers import admin


# users → jobs → txs → reminders  (orden en que _fetch_activation_metrics consulta)
_USERS = [{"id": "u1", "credits": 2}, {"id": "u2", "credits": 0}, {"id": "u3", "credits": 10}]
_JOBS = [
    {"user_id": "u1", "status": "completed", "service_type": "video_8s"},  # cuesta 3 créditos
    {"user_id": "u2", "status": "failed", "service_type": "image"},
    {"user_id": "u1", "status": "failed", "service_type": "video_8s"},
]
_TXS = [
    {"user_id": "u1", "credits_added": 6, "status": "APPROVED", "amount_cop": 1690000},
    {"user_id": "u3", "credits_added": 20, "status": "APPROVED", "amount_cop": 6990000},
    {"user_id": "u2", "credits_added": 6, "status": "PENDING_GRANT", "amount_cop": 1690000},
]
_REM = [{"kind": "winback"}, {"kind": "winback"}, {"kind": "reminder_d1"}]


@pytest.mark.asyncio
async def test_fetch_activation_metrics():
    with patch.object(admin.db, "select", AsyncMock(side_effect=[_USERS, _JOBS, _TXS, _REM])):
        m = await admin._fetch_activation_metrics()
    assert m["usuarios"] == 3
    assert m["compradores"] == 2              # u1, u3 (APPROVED); u2 fue PENDING_GRANT
    assert m["con_creditos"] == 2             # u1, u3
    assert m["activados"] == 1                # solo u1 tiene job completed
    assert m["sin_activar"] == 1              # u3 compró pero no activó
    assert round(m["tasa_activacion_pct"]) == 50
    assert m["creditos_vendidos"] == 26       # 6 + 20 (solo APPROVED)
    assert m["creditos_otorgados"] == 26      # sin bonos aquí: otorgados == vendidos
    assert m["creditos_saldo"] == 12          # 2 + 0 + 10
    assert m["creditos_consumidos"] == 3      # 1 job completado video_8s = 3 créditos (no "otorgados−saldo")
    assert round(m["pct_creditos_sin_usar"]) == 88   # (vendidos 26 − consumidos 3)/26, clamp 0-100
    assert m["ingresos_cop"] == 86800         # (1690000 + 6990000) / 100
    assert m["jobs_total"] == 3 and m["jobs_completados"] == 1 and m["jobs_fallidos"] == 2
    assert round(m["tasa_exito_generacion_pct"]) == 33
    assert m["recordatorios_enviados"] == {"winback": 2, "reminder_d1": 1}
    assert m["recordatorios_total"] == 3


@pytest.mark.asyncio
async def test_referral_bonus_counts_as_granted_not_sold():
    # Un bono de referido (REFERRAL_BONUS) entra al saldo pero NO es una venta.
    # Debe contar en "otorgados" (para que "usados" no se subestime), no en "vendidos".
    users = [{"id": "u1", "credits": 5}]
    jobs = []
    txs = [
        {"user_id": "u1", "credits_added": 6, "status": "APPROVED", "amount_cop": 1690000},
        {"user_id": "u1", "credits_added": 1, "status": "REFERRAL_BONUS", "amount_cop": 0},
    ]
    with patch.object(admin.db, "select", AsyncMock(side_effect=[users, jobs, txs, []])):
        m = await admin._fetch_activation_metrics()
    assert m["creditos_vendidos"] == 6        # solo APPROVED
    assert m["creditos_otorgados"] == 7       # APPROVED (6) + REFERRAL_BONUS (1)
    assert m["creditos_consumidos"] == 0      # sin jobs completados
    assert m["ingresos_cop"] == 16900         # el bono no suma ingresos


@pytest.mark.asyncio
async def test_activacion_dashboard_renders_html():
    with patch.object(admin, "_check_token", lambda t: None), \
         patch.object(admin.db, "select", AsyncMock(side_effect=[_USERS, _JOBS, _TXS, _REM])):
        resp = await admin.activacion_dashboard(token="x")
    body = resp.body.decode()
    assert "Activación" in body
    assert "Tasa de activación" in body
    assert "50%" in body                       # tasa de activación
    assert "Funnel" in body
    assert "Win-back" in body                   # etiqueta de recordatorio


@pytest.mark.asyncio
async def test_activacion_json_requires_token():
    from fastapi import HTTPException
    # token vacío + admin no configurado en tests → 404 (no fuga de datos)
    with pytest.raises(HTTPException):
        await admin.activacion_json(token="")
