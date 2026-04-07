import os
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Set test env vars before any app imports
os.environ.update({
    "KIE_API_KEY": "test-kie-key",
    "OPENAI_API_KEY": "test-openai-key",
    "WOMPI_PRIVATE_KEY": "prv_test_abc",
    "WOMPI_PUBLIC_KEY": "pub_test_abc",
    "WOMPI_EVENTS_SECRET": "test_events_secret",
    "WOMPI_INTEGRITY_SECRET": "test_integrity_secret",
    "GMAIL_CLIENT_ID": "test-client-id",
    "GMAIL_CLIENT_SECRET": "test-client-secret",
    "GMAIL_REFRESH_TOKEN": "test-refresh-token",
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
})


@pytest.fixture
def settings():
    from app.config import Settings
    return Settings()


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
def test_client(db_engine):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.database import get_db_session
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")
