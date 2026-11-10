import httpx
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.db import get_session
from app.main import app


async def test_health_needs_no_dependencies():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_checks_the_database(database_url):
    engine = create_async_engine(database_url, poolclass=NullPool)

    async def session_override():
        async with AsyncSession(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
    assert response.status_code == 200
