import uuid

import httpx
from app.config import get_settings

# Espacio de nombres propio para derivar ids de fila deterministas.
# Es un valor fijo: cambiarlo romperia la idempotencia de las filas ya escritas.
_NS_IDEMPOTENCIA = uuid.UUID("6f1b9c2e-3d4a-5b6c-8d9e-0a1b2c3d4e5f")


def id_idempotente(clave: str) -> str:
    """Id de fila derivado de una clave de negocio, para que la CLAVE PRIMARIA
    impida duplicados.

    Toda la idempotencia de los pagos se apoyaba en un UNIQUE sobre
    transactions.wompi_transaction_id. Se comprobo contra la base de produccion
    el 12-09-2026 y ESE UNIQUE NO EXISTE: se insertaron dos filas con el mismo
    id de transaccion y las dos pasaron (HTTP 201 y 201). Wompi reintenta sus
    webhooks de forma agresiva, asi que dos entregas a la vez insertaban dos
    filas PENDING_GRANT, cada una ganaba SU PROPIO CAS y al cliente se le
    acreditaba el pago DOS VECES. Lo mismo con el bono de referidos.

    Sin acceso a DDL no se puede crear el indice, pero la clave primaria SI
    existe y SI rechaza (probado: el segundo insert devuelve 409). Derivando
    el id de la fila de la clave de negocio, dos escrituras simultaneas generan
    el MISMO uuid y solo una entra.

    Cuando se pueda migrar, lo correcto es ademas:
        create unique index concurrently if not exists
          ux_transactions_wompi_tx on transactions (wompi_transaction_id);
    Esto seguira siendo correcto con el indice puesto.
    """
    return str(uuid.uuid5(_NS_IDEMPOTENCIA, clave))

settings = get_settings()

_REST_URL = f"{settings.supabase_url}/rest/v1"
_HEADERS = {
    "apikey": settings.supabase_service_key,
    "Authorization": f"Bearer {settings.supabase_service_key}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


class SupabaseClient:
    """Thin async wrapper around Supabase PostgREST API using httpx."""

    def __init__(self):
        self._client = httpx.AsyncClient(base_url=_REST_URL, headers=_HEADERS, timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def select(self, table: str, params: dict | None = None) -> list[dict]:
        r = await self._client.get(f"/{table}", params=params or {})
        r.raise_for_status()
        return r.json()

    async def select_one(self, table: str, params: dict | None = None) -> dict | None:
        p = dict(params or {})
        p["limit"] = "1"
        try:
            rows = await self.select(table, p)
            return rows[0] if rows else None
        except httpx.HTTPStatusError as e:
            # Only swallow 404 (table missing) — propagate auth failures (401/403)
            # and server errors (5xx) so callers don't silently treat a Supabase
            # outage as "no row exists" and proceed to insert/grant.
            if e.response.status_code == 404:
                return None
            raise

    async def insert(self, table: str, data: dict) -> dict:
        r = await self._client.post(f"/{table}", json=data)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else data

    async def update(self, table: str, params: dict, data: dict) -> list[dict]:
        r = await self._client.patch(f"/{table}", params=params, json=data)
        r.raise_for_status()
        return r.json()

    async def upsert(self, table: str, data: dict) -> dict:
        headers = {"Prefer": "return=representation,resolution=merge-duplicates"}
        r = await self._client.post(f"/{table}", json=data, headers=headers)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else data

    async def delete(self, table: str, params: dict) -> list[dict]:
        """DELETE rows matching `params` (PostgREST filters). Returns deleted
        rows. Requires a non-empty filter — PostgREST refuses an unfiltered
        DELETE, which protects against wiping a whole table by mistake."""
        r = await self._client.delete(f"/{table}", params=params)
        r.raise_for_status()
        return r.json() if r.content else []

    async def rpc(self, fn: str, params: dict | None = None):
        """Call a Postgres function via PostgREST /rpc/{fn}. Returns the JSON body."""
        r = await self._client.post(f"/rpc/{fn}", json=params or {})
        r.raise_for_status()
        return r.json()


db = SupabaseClient()
