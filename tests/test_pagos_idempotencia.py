"""Dos entregas del MISMO webhook de Wompi no pueden acreditar dos veces.

Contexto: toda la idempotencia de los pagos se apoyaba en un UNIQUE sobre
transactions.wompi_transaction_id. Se comprobo contra la base de produccion el
12-09-2026 y ESE UNIQUE NO EXISTE (dos inserts con el mismo id de transaccion
devolvieron 201 y 201). Wompi reintenta sus webhooks, asi que dos entregas
simultaneas insertaban dos filas PENDING_GRANT, cada una ganaba SU PROPIO CAS
y el cliente recibia el doble de creditos.

El arreglo deriva el id de la fila del id de transaccion, de modo que la CLAVE
PRIMARIA (que si existe) rechaza el segundo insert.

Estas pruebas usan una base de mentira que se comporta como Postgres en lo
unico que importa aqui: la clave primaria rechaza ids repetidos y NO rechaza
wompi_transaction_id repetidos — igual que la tabla real hoy.
"""
import asyncio
import hashlib

import pytest

from app.database import id_idempotente


class BaseDeMentira:
    """Solo hace cumplir la CLAVE PRIMARIA sobre `id`, como la tabla real."""

    def __init__(self):
        self.filas: dict[str, list[dict]] = {"transactions": [], "users": [], "credits": []}
        self.concedidos: list[tuple[str, int]] = []

    async def insert(self, tabla, datos):
        fila = dict(datos)
        pk = fila.get("id")
        if pk and any(f.get("id") == pk for f in self.filas.setdefault(tabla, [])):
            raise RuntimeError("duplicate key value violates unique constraint (pkey)")
        fila.setdefault("id", f"auto-{len(self.filas.setdefault(tabla, []))}")
        self.filas.setdefault(tabla, []).append(fila)
        return fila

    async def select_one(self, tabla, params):
        r = await self.select(tabla, params)
        return r[0] if r else None

    async def select(self, tabla, params=None):
        filas = self.filas.get(tabla, [])
        for k, v in (params or {}).items():
            if k in ("select", "limit", "offset", "order"):
                continue
            if isinstance(v, str) and v.startswith("eq."):
                filas = [f for f in filas if str(f.get(k)) == v[3:]]
        return list(filas)

    async def update(self, tabla, params, datos):
        objetivo = await self.select(tabla, params)
        for f in objetivo:
            f.update(datos)
        return objetivo


def test_el_id_derivado_es_estable_y_distingue():
    a = id_idempotente("wompi:123-abc")
    assert a == id_idempotente("wompi:123-abc"), "el mismo pago debe dar el mismo id"
    assert a != id_idempotente("wompi:123-abd"), "pagos distintos, ids distintos"
    assert len(a) == 36 and a.count("-") == 4


def test_dos_entregas_del_mismo_pago_solo_insertan_una_fila():
    """Reproduce el fallo exacto: sin el id derivado, entraban las dos."""
    bd = BaseDeMentira()
    tx = "1352121-1776904196-29804"

    async def entregar():
        try:
            await bd.insert("transactions", {
                "id": id_idempotente(f"wompi:{tx}"),
                "wompi_transaction_id": tx, "status": "PENDING_GRANT", "credits_added": 3,
            })
            return "insertada"
        except RuntimeError:
            return "rechazada"

    async def ambas():
        return await asyncio.gather(entregar(), entregar())

    r = asyncio.run(ambas())
    assert sorted(r) == ["insertada", "rechazada"]
    assert len(bd.filas["transactions"]) == 1, "el pago se registro dos veces"


def test_sin_el_arreglo_entrarian_las_dos():
    """Prueba de control: demuestra que la base de mentira NO esta siendo
    permisiva de mas. Si esto pasara, la prueba de arriba no probaria nada."""
    bd = BaseDeMentira()

    async def ambas():
        await bd.insert("transactions", {"wompi_transaction_id": "x", "status": "PENDING_GRANT"})
        await bd.insert("transactions", {"wompi_transaction_id": "x", "status": "PENDING_GRANT"})

    asyncio.run(ambas())
    assert len(bd.filas["transactions"]) == 2, (
        "la tabla real NO tiene UNIQUE sobre wompi_transaction_id; si esta base "
        "de mentira lo impidiera, estaria probando un mundo que no existe")


def test_el_bono_de_referido_tampoco_se_duplica():
    bd = BaseDeMentira()
    clave = "refbonus|E1CE5BBD|alguien@ejemplo.com"

    async def dar_bono():
        try:
            await bd.insert("transactions", {
                "id": id_idempotente(clave), "wompi_transaction_id": clave,
                "plan_name": "referral_bonus", "credits_added": 1, "status": "PENDING_BONUS",
            })
            return True
        except RuntimeError:
            return False

    async def dos_veces():
        return await asyncio.gather(dar_bono(), dar_bono())

    assert sorted(asyncio.run(dos_veces())) == [False, True]
    assert len(bd.filas["transactions"]) == 1
