"""Pruebas de GET /api/v1/payments/status.

Este endpoint existe para cerrar un fallo concreto: el JS de las páginas
`gracias-*` disparaba el Purchase del píxel de Meta leyendo `value` de la URL sin
comprobar el estado del pago. Wompi redirige a la URL de retorno en cualquier
estado terminal, así que una tarjeta rechazada registraba una compra igual, y
cualquiera podía inyectar una compra falsa con ?value=9999999.

Lo que estas pruebas protegen:
  - Un pago NO aprobado nunca debe devolver approved=True.
  - Un fallo de red contra Wompi debe devolver "pending", nunca "declined": si no
    sabemos qué pasó, no podemos ni disparar Purchase ni decirle al cliente que
    su pago falló.
  - El importe debe venir de Wompi, nunca de la petición.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
URL = "/api/v1/payments/status"


def _mock_fetch(monkeypatch, payload):
    """Sustituye el refetch autoritativo. Se parchea en el módulo del router
    porque payments.py lo importó por nombre (`from ... import fetch_transaction`),
    así que parchear app.services.wompi no tendría efecto."""
    async def fake(tx_id, base_url, private_key):
        return payload
    monkeypatch.setattr("app.routers.payments.fetch_transaction", fake)


@pytest.mark.parametrize("bad", ["", "ab", "tiene espacio", "x" * 65, "punto.coma;"])
def test_id_invalido_rechazado(bad):
    r = client.get(URL, params={"id": bad})
    assert r.status_code in (400, 422), r.text


def test_aprobado_devuelve_valor_de_wompi_no_de_la_url(monkeypatch):
    _mock_fetch(monkeypatch, {
        "status": "APPROVED", "amount_in_cents": 5_490_000,
        "currency": "COP", "reference": "PH-credits_20-1-abc",
    })
    r = client.get(URL, params={"id": "1352121-1700000000-1", "value": "9999999"})
    assert r.status_code == 200
    b = r.json()
    assert b["approved"] is True and b["state"] == "approved"
    # 5.490.000 centavos = 54.900 pesos. El 9999999 de la query se ignora.
    assert b["value"] == 54_900
    assert b["currency"] == "COP"


@pytest.mark.parametrize("status", ["DECLINED", "VOIDED", "ERROR"])
def test_rechazado_no_aprueba(monkeypatch, status):
    _mock_fetch(monkeypatch, {"status": status, "amount_in_cents": 5_490_000})
    b = client.get(URL, params={"id": "tx-rechazada-1"}).json()
    assert b["approved"] is False
    assert b["state"] == "declined"
    assert "value" not in b, "no debe emitirse un importe para un pago rechazado"


@pytest.mark.parametrize("status", ["PENDING", "", "LO_QUE_SEA"])
def test_estado_no_terminal_es_pending(monkeypatch, status):
    """PSE puede quedar PENDING. No es un rechazo: el cliente puede acabar
    pagando, así que ni disparamos Purchase ni le decimos que falló."""
    _mock_fetch(monkeypatch, {"status": status})
    b = client.get(URL, params={"id": "tx-pendiente-1"}).json()
    assert b["approved"] is False
    assert b["state"] == "pending"


def test_fallo_de_red_es_pending_no_declined(monkeypatch):
    """fetch_transaction devuelve None cuando no pudo hablar con Wompi."""
    _mock_fetch(monkeypatch, None)
    b = client.get(URL, params={"id": "tx-sin-respuesta"}).json()
    assert b["approved"] is False
    assert b["state"] == "pending", "un fallo de red no es un rechazo"


@pytest.mark.parametrize("raw,esperado", [
    (5_490_000, 54_900),
    ("5490000", 54_900),      # Wompi serializa números como cadena a veces
    (1_690_000, 16_900),
    (None, None),
    (True, None),             # bool es subclase de int: True daría 0 pesos
    ("no-es-numero", None),
])
def test_conversion_de_centavos(raw, esperado):
    from app.routers.payments import _cents_to_pesos
    assert _cents_to_pesos(raw) == esperado


def test_no_filtra_datos_del_comprador(monkeypatch):
    _mock_fetch(monkeypatch, {
        "status": "APPROVED", "amount_in_cents": 1_690_000, "currency": "COP",
        "reference": "PH-credits_6-1-abc",
        "customer_email": "cliente@ejemplo.com",
        "customer_data": {"full_name": "Nombre Apellido", "phone_number": "3001234567"},
        "payment_method": {"extra": {"last_four": "4242"}},
    })
    b = client.get(URL, params={"id": "tx-con-datos"}).json()
    crudo = str(b)
    for secreto in ("cliente@ejemplo.com", "Nombre Apellido", "3001234567", "4242"):
        assert secreto not in crudo, f"el endpoint filtró {secreto!r}"
