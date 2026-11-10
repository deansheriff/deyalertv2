import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.admin import auth
from app.core.clients import get_queue, get_redis
from app.core.db import get_session
from app.main import app
from tests.test_api_incidents import (  # noqa: F401
    FakeQueue,
    FakeRedis,
    api,
    area,
    db,
    report,
    user,
)


@pytest.fixture
async def web(database_url, migrated):
    engine = create_async_engine(database_url, poolclass=NullPool)
    queue = FakeQueue()
    redis = FakeRedis()

    async def session_override():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def queue_override():
        return queue

    async def redis_override():
        yield redis

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_queue] = queue_override
    app.dependency_overrides[get_redis] = redis_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        client.redis = redis
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


def signed_in(client, user_id):
    token = auth.session_cookie(user_id)
    client.cookies.set(auth.COOKIE, token, path="/admin")
    return auth.csrf_token(token)


async def held_incident(api, db):  # noqa: F811
    reporter = await user(db)
    incident = await report(api, db, reporter=reporter, description="Those Hausa boys blocked the road")
    return uuid.UUID(incident["id"]), reporter


@pytest.mark.parametrize("path", ["/admin", "/admin/queue", "/admin/users", "/admin/audit"])
async def test_pages_redirect_to_sign_in_without_a_session(web, path):
    response = await web.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


async def test_member_cannot_use_the_panel(web, db):  # noqa: F811
    signed_in(web, await user(db))
    assert (await web.get("/admin")).status_code == 303


async def test_tampered_session_is_rejected(web, db):  # noqa: F811
    moderator = await user(db, role="moderator")
    token = auth.session_cookie(moderator)
    web.cookies.set(auth.COOKIE, token[:-2] + "xx", path="/admin")
    assert (await web.get("/admin")).status_code == 303


async def test_password_change_token_cannot_open_the_panel(web, db):  # noqa: F811
    web.cookies.set(auth.COOKIE, auth.password_cookie(await user(db, role="admin")), path="/admin")
    assert (await web.get("/admin")).status_code == 303


async def test_pages_render_for_a_moderator(web, db, api, area):  # noqa: F811
    await held_incident(api, db)
    signed_in(web, await user(db, role="moderator"))
    for path in ("/admin", "/admin/queue", "/admin/verifiers", "/admin/users", "/admin/audit", "/admin/settings"):
        assert (await web.get(path)).status_code == 200, path


async def test_queue_never_shows_reporter_identity_or_true_location(web, db, api, area):  # noqa: F811
    incident_id, reporter = await held_incident(api, db)
    phone = await db.fetchval("SELECT phone FROM profiles WHERE id = $1", reporter)
    signed_in(web, await user(db, role="moderator"))
    for path in ("/admin/queue", f"/admin/incidents/{incident_id}"):
        assert phone not in (await web.get(path)).text


async def test_action_needs_csrf_token(web, db, api, area):  # noqa: F811
    incident_id, _ = await held_incident(api, db)
    signed_in(web, await user(db, role="moderator"))
    response = await web.post(f"/admin/incidents/{incident_id}/action", data={"action": "restore", "csrf": "wrong"})
    assert response.status_code == 403
    assert await db.fetchval("SELECT status FROM incidents WHERE id = $1", incident_id) == "under_review"


async def test_moderator_restores_with_audit(web, db, api, area):  # noqa: F811
    incident_id, _ = await held_incident(api, db)
    csrf = signed_in(web, await user(db, role="moderator"))
    response = await web.post(f"/admin/incidents/{incident_id}/action", data={"action": "restore", "csrf": csrf})
    assert response.status_code == 303
    assert await db.fetchval("SELECT status FROM incidents WHERE id = $1", incident_id) == "unconfirmed"
    assert await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'verify:restore' AND entity_id = $1", str(incident_id)
    ) == 1


async def test_moderator_cannot_look_up_a_reporter(web, db, api, area):  # noqa: F811
    incident_id, _ = await held_incident(api, db)
    csrf = signed_in(web, await user(db, role="moderator"))
    response = await web.post(
        f"/admin/incidents/{incident_id}/reporter",
        data={"reason": "A long enough reason for this lookup", "csrf": csrf},
    )
    assert response.status_code == 403


async def test_admin_lookup_requires_reason_and_is_audited(web, db, api, area):  # noqa: F811
    incident_id, reporter = await held_incident(api, db)
    phone = await db.fetchval("SELECT phone FROM profiles WHERE id = $1", reporter)
    csrf = signed_in(web, await user(db, role="admin"))
    url = f"/admin/incidents/{incident_id}/reporter"
    assert (await web.post(url, data={"reason": "short", "csrf": csrf})).status_code == 422
    response = await web.post(url, data={"reason": "Repeated false reports from this account", "csrf": csrf})
    assert phone in response.text
    assert await db.fetchval(
        "SELECT reason FROM audit_log WHERE action = 'reporter_lookup' AND entity_id = $1", str(incident_id)
    ) == "Repeated false reports from this account"


async def test_exact_location_reveal_is_logged(web, db, api, area):  # noqa: F811
    incident_id, _ = await held_incident(api, db)
    csrf = signed_in(web, await user(db, role="moderator"))
    response = await web.post(
        f"/admin/incidents/{incident_id}/reveal-location",
        data={"reason": "Checking a duplicate report", "csrf": csrf},
    )
    assert response.status_code == 200
    assert await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'location_reveal' AND entity_id = $1", str(incident_id)
    ) == 1


async def test_only_admin_changes_roles_and_not_their_own(web, db):  # noqa: F811
    member = await user(db)
    moderator = await user(db, role="moderator")
    csrf = signed_in(web, moderator)
    assert (await web.post(f"/admin/users/{member}/role", data={"role": "admin", "csrf": csrf})).status_code == 403

    web.cookies.clear()
    administrator = await user(db, role="admin")
    csrf = signed_in(web, administrator)
    assert (await web.post(f"/admin/users/{administrator}/role", data={"role": "member", "csrf": csrf})).status_code == 403
    assert (await web.post(f"/admin/users/{member}/role", data={"role": "moderator", "csrf": csrf})).status_code == 303
    assert await db.fetchval("SELECT role FROM profiles WHERE id = $1", member) == "moderator"


async def test_user_list_masks_phone_numbers(web, db):  # noqa: F811
    member = await user(db)
    phone = await db.fetchval("SELECT phone FROM profiles WHERE id = $1", member)
    signed_in(web, await user(db, role="moderator"))
    assert phone not in (await web.get("/admin/users", params={"q": phone[-4:]})).text


async def test_sms_kill_switch(web, db):  # noqa: F811
    csrf = signed_in(web, await user(db, role="admin"))
    await web.post("/admin/settings/sms", data={"enable": "no", "csrf": csrf})
    assert await web.redis.get("flag:sms_fallback_off") == "1"
    await web.post("/admin/settings/sms", data={"enable": "yes", "csrf": csrf})
    assert await web.redis.get("flag:sms_fallback_off") is None


def fake_supabase(monkeypatch, user_id, must_change=False):
    calls = {}

    async def sign_in(email, password):
        if password != "right":
            return None
        metadata = {"must_change_password": must_change}
        return {"access_token": "token", "user": {"id": str(user_id), "app_metadata": metadata}}

    async def set_password(uid, password):
        calls["password"] = (uid, password)
        return True

    monkeypatch.setattr(auth, "password_sign_in", sign_in)
    monkeypatch.setattr(auth, "set_password", set_password)
    return calls


async def sign_in(client, password="right"):
    return await client.post("/admin/login", data={"email": "a@example.com", "password": password})


async def test_members_cannot_sign_in_to_the_panel(web, db, monkeypatch):  # noqa: F811
    fake_supabase(monkeypatch, await user(db))
    response = await sign_in(web)
    assert response.status_code == 401
    assert auth.PENDING_COOKIE not in response.cookies


async def test_wrong_password_is_refused(web, db, monkeypatch):  # noqa: F811
    fake_supabase(monkeypatch, await user(db, role="admin"))
    assert (await sign_in(web, password="wrong")).status_code == 401


async def test_staff_password_opens_the_panel(web, db, monkeypatch):  # noqa: F811
    fake_supabase(monkeypatch, await user(db, role="moderator"))
    response = await sign_in(web)
    assert response.headers["location"] == "/admin"
    assert (await web.get("/admin")).status_code == 200


async def test_seeded_admin_must_choose_a_password_first(web, db, monkeypatch):  # noqa: F811
    admin = await user(db, role="admin")
    calls = fake_supabase(monkeypatch, admin, must_change=True)
    response = await sign_in(web)
    assert response.headers["location"] == "/admin/password"
    # The panel stays closed until the new password is saved.
    assert (await web.get("/admin")).status_code == 303
    assert (await web.get("/admin/password")).status_code == 200

    short = await web.post("/admin/password", data={"new_password": "short", "confirm_password": "short"})
    assert short.status_code == 400
    mismatch = await web.post(
        "/admin/password",
        data={"new_password": "a-long-password-1", "confirm_password": "a-long-password-2"},
    )
    assert mismatch.status_code == 400
    assert "password" not in calls

    done = await web.post(
        "/admin/password",
        data={"new_password": "a-long-password-1", "confirm_password": "a-long-password-1"},
    )
    assert done.headers["location"] == "/admin/login?changed=1"
    assert calls["password"] == (admin, "a-long-password-1")
    assert (await web.get("/admin/password")).headers["location"] == "/admin/login"


async def test_email_only_admin_appears_in_the_user_list(web, db):  # noqa: F811
    staff = uuid.uuid4()
    await db.execute("INSERT INTO auth.users (id) VALUES ($1)", staff)
    await db.execute(
        "INSERT INTO profiles (id, role, display_name) VALUES ($1, 'admin', 'Admin')", staff
    )
    try:
        signed_in(web, staff)
        assert "Email sign-in" in (await web.get("/admin/users")).text
    finally:
        # A phone-less profile would block the migration downgrade test.
        await db.execute("DELETE FROM auth.users WHERE id = $1", staff)
