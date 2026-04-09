import httpx
from app.config import Settings

settings = Settings()

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
        except httpx.HTTPStatusError:
            return None

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


db = SupabaseClient()
