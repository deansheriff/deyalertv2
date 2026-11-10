import uuid

import httpx
import pytest

from app.core.config import Settings
from data import seed_admin
from tests.test_api_incidents import db  # noqa: F401

EMAIL = "admin@example.test"
PASSWORD = "a-one-time-password"


@pytest.fixture
def supabase(monkeypatch, database_url):
    """A fake Supabase admin API that records each call and answers with a chosen user id."""
    calls = []
    state = {"status": 200, "id": None}

    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        return httpx.Response(state["status"], json={"id": str(state["id"])})

    settings = Settings(
        database_url=database_url,
        supabase_url="https://supabase.test",
        supabase_service_role_key="service",
        admin_seed_email=EMAIL,
        admin_seed_password=PASSWORD,
    )
    monkeypatch.setattr(seed_admin, "get_settings", lambda: settings)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        seed_admin.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    state["settings"] = settings
    state["calls"] = calls
    return state


@pytest.fixture
async def account(db):  # noqa: F811
    """An auth user Supabase would create. Deleted afterwards so the phone-less profile
    does not block the migration downgrade test."""
    user_id = uuid.uuid4()
    await db.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    yield user_id
    await db.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def test_deploy_creates_a_missing_admin(supabase, account, db, migrated):  # noqa: F811
    supabase["id"] = account
    await seed_admin.from_env()

    method, path, body = supabase["calls"][0]
    assert (method, path) == ("POST", "/auth/v1/admin/users")
    assert b'"must_change_password":true' in body.replace(b" ", b"")
    assert await db.fetchval("SELECT role FROM profiles WHERE id = $1", account) == "admin"


async def test_deploy_leaves_an_existing_admin_alone(supabase, account, db, migrated):  # noqa: F811
    await db.execute("UPDATE auth.users SET email = $1 WHERE id = $2", EMAIL.upper(), account)
    await db.execute(
        "INSERT INTO profiles (id, role, display_name) VALUES ($1, 'moderator', 'Kept')", account
    )
    await seed_admin.from_env()
    assert supabase["calls"] == []
    assert await db.fetchval("SELECT role FROM profiles WHERE id = $1", account) == "moderator"


async def test_deploy_adds_a_missing_profile_to_a_hand_made_account(supabase, account, db, migrated):  # noqa: F811,E501
    await db.execute("UPDATE auth.users SET email = $1 WHERE id = $2", EMAIL, account)
    await seed_admin.from_env()
    assert supabase["calls"] == []
    assert await db.fetchval("SELECT role FROM profiles WHERE id = $1", account) == "admin"


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [("", PASSWORD, "not set"), (EMAIL, "short", "at least 12 characters")],
)
async def test_deploy_skips_without_usable_settings(supabase, capsys, email, password, message):
    supabase["settings"].admin_seed_email = email
    supabase["settings"].admin_seed_password = password
    await seed_admin.from_env()
    assert supabase["calls"] == []
    assert message in capsys.readouterr().out


async def test_supabase_refusal_does_not_fail_the_deploy(supabase, capsys, migrated):
    supabase["status"] = 422
    supabase["id"] = uuid.uuid4()
    await seed_admin.from_env()
    assert "Admin seed failed" in capsys.readouterr().out
