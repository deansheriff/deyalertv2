import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import routes as admin_routes
from app.api import auth_hook, incidents, me, public, sos
from app.core.config import get_settings, production_problems
from app.core.db import get_session
from app.core.errors import install_handlers
from app.core.logging import configure_logging

settings = get_settings()
configure_logging()
problems = production_problems(settings)
if problems:
    raise RuntimeError("Invalid production configuration: " + "; ".join(problems))
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn, environment=settings.environment, send_default_pii=False
    )

is_production = settings.environment == "production"
app = FastAPI(
    title="Dey Alert API",
    version="1.0.0",
    docs_url=None if is_production else "/docs",
    redoc_url=None,
    openapi_url=None if is_production else "/openapi.json",
)
install_handlers(app)
app.include_router(me.router)
app.include_router(incidents.router)
app.include_router(sos.router)
app.include_router(auth_hook.router)
app.include_router(public.router)
admin_routes.install(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}
