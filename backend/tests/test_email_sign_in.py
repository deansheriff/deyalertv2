import json
import time
import uuid

import httpx
import pytest
from fastapi import Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.clients import get_redis
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.security import AuthUser, get_auth_user
from app.main import app
from app.services import app_settings, mailer
from tests.test_admin import signed_in, web  # noqa: F401
from tests.test_api_incidents import FakeRedis, db, user  # noqa: F401
from tests.test_worker_and_sos import SECRET, SECRET_BYTES, hook_headers

SMTP = mailer.SmtpConfig(
    host="smtp.example.test",
    port=587,
    username="mailer",
    password="smtp-secret",
    from_email="alerts@example.test",
)


@pytest.fixture
async def server(database_url, migrated, monkeypatch):
    """The API with a real database, fake Redis, and outgoing email captured."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    redis = FakeRedis()
    sent = []
    signed_in_as = {}

    async def session_override():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def redis_override():
        yield redis

    async def auth(authorization: str = Header(default="")):
        return signed_in_as["user"]

    async def capture(config, to, subject, body, html=None):
        if config.host == "down.example.test":
            raise mailer.MailError("Could not connect")
        sent.append((to, subject, body))

    monkeypatch.setattr(mailer, "send", capture)
    settings = Settings(
        database_url=database_url,
        supabase_email_hook_secret=SECRET,
        email_per_address_hourly_cap=2,
    )
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_redis] = redis_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_auth_user] = auth
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.sent = sent
        client.signed_in_as = signed_in_as
        client.engine = engine
        yield client
    app.dependency_overrides.clear()
    async with AsyncSession(engine) as session:
        await session.execute(text("DELETE FROM app_settings"))
        await session.commit()
    await engine.dispose()


async def store(client, key, value):
    async with AsyncSession(client.engine) as session:
        await app_settings.put(session, key, value, None)
        await session.commit()


async def save_smtp(client, config=SMTP):
    async with AsyncSession(client.engine) as session:
        await mailer.save(session, config, None)
        await session.commit()


async def email_hook(client, payload):
    body = json.dumps(payload).encode()
    return await client.post(
        "/v1/auth/email-hook", content=body, headers=hook_headers(SECRET_BYTES, body)
    )


def code_for(address, action="magiclink", code="654321"):
    return {"user": {"email": address}, "email_data": {"token": code, "email_action_type": action}}


async def test_app_offers_email_only_until_phone_is_switched_on(server):
    assert (await server.get("/v1/config")).json() == {"sign_in_methods": ["email"]}
    await store(server, app_settings.PHONE_SIGN_IN, True)
    assert (await server.get("/v1/config")).json() == {"sign_in_methods": ["email", "phone"]}


async def test_email_only_account_gets_a_profile(server, db):  # noqa: F811
    user_id = uuid.uuid4()
    await db.execute("INSERT INTO auth.users (id, email) VALUES ($1, 'ada@example.test')", user_id)
    try:
        server.signed_in_as["user"] = AuthUser(id=user_id, phone=None, email="ada@example.test")
        response = await server.get("/v1/me", headers={"Authorization": "Bearer x"})
        assert response.status_code == 200
        assert response.json()["email"] == "ada@example.test"
        assert response.json()["phone"] is None
    finally:
        await db.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def test_account_without_email_or_phone_is_refused(server):
    server.signed_in_as["user"] = AuthUser(id=uuid.uuid4(), phone=None, email=None)
    response = await server.get("/v1/me", headers={"Authorization": "Bearer x"})
    assert response.status_code == 403


async def test_email_hook_sends_the_code_through_smtp(server):
    await save_smtp(server)
    response = await email_hook(server, code_for("Ada@Example.test"))
    assert response.status_code == 200
    to, subject, body = server.sent[0]
    assert to == "ada@example.test"
    assert "654321" in body
    assert "sign-in code" in subject


async def test_email_change_confirms_both_addresses(server):
    await save_smtp(server)
    payload = {
        "user": {"email": "old@example.test", "new_email": "new@example.test"},
        "email_data": {"token": "111111", "token_new": "222222", "email_action_type": "email_change"},
    }
    assert (await email_hook(server, payload)).status_code == 200
    assert [(to, "111111" in body or "222222" in body) for to, _, body in server.sent] == [
        ("old@example.test", True),
        ("new@example.test", True),
    ]
    assert "222222" in server.sent[1][2]


async def test_email_hook_limits_codes_per_address(server):
    await save_smtp(server)
    assert (await email_hook(server, code_for("ada@example.test"))).status_code == 200
    assert (await email_hook(server, code_for("ada@example.test"))).status_code == 200
    assert (await email_hook(server, code_for("ada@example.test"))).status_code == 429
    assert len(server.sent) == 2


async def test_email_hook_needs_smtp_settings(server):
    response = await email_hook(server, code_for("ada@example.test"))
    assert response.status_code == 503
    assert "SMTP" in response.json()["error"]["message"]


async def test_email_hook_reports_a_failed_send(server):
    await save_smtp(server, mailer.SmtpConfig(**{**SMTP.__dict__, "host": "down.example.test"}))
    assert (await email_hook(server, code_for("ada@example.test"))).status_code == 502


@pytest.mark.parametrize("secret", ["", SECRET])
async def test_email_hook_rejects_unsigned_requests(server, secret):
    app.dependency_overrides[get_settings] = lambda: Settings(supabase_email_hook_secret=secret)
    body = json.dumps(code_for("ada@example.test")).encode()
    headers = {
        "webhook-id": "x",
        "webhook-timestamp": str(int(time.time())),
        "webhook-signature": "v1,bad",
    }
    response = await server.post("/v1/auth/email-hook", content=body, headers=headers)
    assert response.status_code == 401
    assert server.sent == []


async def test_smtp_password_is_encrypted_at_rest(server, db):  # noqa: F811
    await save_smtp(server)
    stored = await db.fetchval("SELECT value::text FROM app_settings WHERE key = 'smtp'")
    assert "smtp-secret" not in stored
    async with AsyncSession(server.engine) as session:
        assert (await mailer.load(session)).password == "smtp-secret"


def test_panel_summary_never_includes_the_password():
    shown = mailer.summary({"host": "h", "password": "encrypted"})
    assert "password" not in shown
    assert shown["has_password"] is True


def test_code_email_escapes_html():
    _, _, html = mailer.code_email("signup", "<b>1</b>")
    assert "<b>1</b>" not in html


# Admin panel


async def admin_session(web, db):  # noqa: F811
    return signed_in(web, await user(db, role="admin"))


SMTP_FORM = {
    "host": "smtp.example.test",
    "port": "587",
    "security": "starttls",
    "username": "mailer",
    "password": "smtp-secret",
    "from_email": "alerts@example.test",
    "from_name": "Dey Alert",
}


@pytest.fixture
async def panel(web, db, monkeypatch):  # noqa: F811
    sent = []

    async def capture(config, to, subject, body, html=None):
        if config.host == "down.example.test":
            raise mailer.MailError("Could not connect to down.example.test:587")
        sent.append((config, to))

    monkeypatch.setattr(mailer, "send", capture)
    web.sent = sent
    web.csrf = await admin_session(web, db)
    yield web
    await db.execute("DELETE FROM app_settings")


async def test_admin_saves_smtp_and_sends_a_test(panel):
    saved = await panel.post("/admin/settings/smtp", data={**SMTP_FORM, "csrf": panel.csrf})
    assert saved.status_code == 200
    assert "smtp-secret" not in saved.text

    # A blank password keeps the saved one.
    changed = {**SMTP_FORM, "password": "", "host": "smtp2.example.test", "csrf": panel.csrf}
    await panel.post("/admin/settings/smtp", data=changed)
    tested = await panel.post(
        "/admin/settings/smtp/test", data={"to": "me@example.test", "csrf": panel.csrf}
    )
    assert "Test email sent" in tested.text
    config, to = panel.sent[0]
    assert (config.host, config.password, to) == ("smtp2.example.test", "smtp-secret", "me@example.test")


async def test_failed_test_email_shows_the_reason(panel):
    form = {**SMTP_FORM, "host": "down.example.test", "csrf": panel.csrf}
    await panel.post("/admin/settings/smtp", data=form)
    tested = await panel.post(
        "/admin/settings/smtp/test", data={"to": "me@example.test", "csrf": panel.csrf}
    )
    assert tested.status_code == 502
    assert "Could not connect" in tested.text


async def test_test_email_needs_saved_settings(panel):
    tested = await panel.post(
        "/admin/settings/smtp/test", data={"to": "me@example.test", "csrf": panel.csrf}
    )
    assert tested.status_code == 400


async def test_moderators_cannot_change_smtp(web, db):  # noqa: F811
    csrf = signed_in(web, await user(db, role="moderator"))
    response = await web.post("/admin/settings/smtp", data={**SMTP_FORM, "csrf": csrf})
    assert response.status_code == 403
    assert await db.fetchval("SELECT count(*) FROM app_settings WHERE key = 'smtp'") == 0


async def test_admin_switches_phone_sign_in(panel, db):  # noqa: F811
    await panel.post("/admin/settings/sign-in", data={"enable": "yes", "csrf": panel.csrf})
    assert await db.fetchval("SELECT value::text FROM app_settings WHERE key = 'phone_sign_in'") == "true"
    page = await panel.get("/admin/settings")
    assert "Turn off phone sign-in" in page.text
