import base64
import hashlib
import hmac
import json
import time
import uuid

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.admin import auth, insights, rbac
from app.api import app_config
from app.services import webhooks
from tests.test_admin import held_incident, signed_in, web  # noqa: F401
from tests.test_api_incidents import (  # noqa: F401
    api,
    area,
    bearer,
    db,
    payload,
    report,
    user,
)


@pytest.fixture(autouse=True)
def fresh_switches():
    app_config.clear_cache()
    yield
    app_config.clear_cache()


@pytest.fixture
async def clean_settings(db):  # noqa: F811
    yield
    await db.execute("DELETE FROM app_settings")
    app_config.clear_cache()


# Audit chain


async def test_audit_entries_are_chained_and_append_only(web, db, api, area):  # noqa: F811
    incident_id, _ = await held_incident(api, db)
    csrf = signed_in(web, await user(db, role="super_admin"))
    for reason in ("First lookup for the chain test", "Second lookup for the chain test"):
        await web.post(
            f"/admin/incidents/{incident_id}/reveal-location", data={"reason": reason, "csrf": csrf}
        )
    rows = await db.fetch(
        "SELECT chain_seq, prev_hash, hash FROM audit_log ORDER BY chain_seq DESC LIMIT 2"
    )
    assert rows[0]["prev_hash"] == rows[1]["hash"]
    assert rows[0]["chain_seq"] == rows[1]["chain_seq"] + 1
    assert await db.fetchval(insights.CHAIN_CHECK_SQL) is None

    with pytest.raises(asyncpg.RaiseError):
        await db.execute("UPDATE audit_log SET reason = 'edited' WHERE chain_seq = $1", rows[0]["chain_seq"])
    with pytest.raises(asyncpg.RaiseError):
        await db.execute("DELETE FROM audit_log WHERE chain_seq = $1", rows[0]["chain_seq"])


async def test_tampering_with_the_audit_log_is_detected(db):  # noqa: F811
    await db.execute(
        "INSERT INTO audit_log (action, entity_type, entity_id) VALUES ('tamper_test', 'test', 'x')"
    )
    seq = await db.fetchval("SELECT max(chain_seq) FROM audit_log")
    transaction = db.transaction()
    await transaction.start()
    try:
        # Someone with database access bypasses the protection and edits a row.
        await db.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_protect")
        await db.execute("UPDATE audit_log SET reason = 'covered up' WHERE chain_seq = $1", seq)
        assert await db.fetchval(insights.CHAIN_CHECK_SQL) == seq
    finally:
        await transaction.rollback()
    assert await db.fetchval(insights.CHAIN_CHECK_SQL) is None


async def test_deleting_a_staff_account_keeps_the_chain_valid(db):  # noqa: F811
    staff = await user(db, role="moderator")
    await db.execute(
        "INSERT INTO audit_log (actor_id, action, entity_type) VALUES ($1, 'test', 'test')", staff
    )
    await db.execute("DELETE FROM auth.users WHERE id = $1", staff)
    assert await db.fetchval(insights.CHAIN_CHECK_SQL) is None


# Permissions, scopes and sessions


async def test_super_admin_edits_permissions(web, db, clean_settings):  # noqa: F811
    moderator = await user(db, role="moderator")
    csrf = signed_in(web, await user(db, role="super_admin"))
    response = await web.post("/admin/roles", data={"csrf": csrf, "moderator:view_dashboard": "on"})
    assert response.status_code == 303
    web.cookies.clear()
    signed_in(web, moderator)
    assert (await web.get("/admin")).status_code == 200
    assert (await web.get("/admin/queue")).status_code == 403
    entry = await db.fetchrow(
        "SELECT metadata FROM audit_log WHERE action = 'permissions_change' "
        "ORDER BY chain_seq DESC LIMIT 1"
    )
    assert "view_queue" in json.loads(entry["metadata"])["moderator"]["removed"]


async def test_scoped_moderator_only_sees_their_area(web, db, api, area):  # noqa: F811
    incident_id, _ = await held_incident(api, db)
    moderator = await user(db, role="moderator")
    other_lga = await db.fetchval(
        "INSERT INTO lgas (state, name) VALUES ('ScopeTest', $1) RETURNING id", uuid.uuid4().hex
    )
    await db.execute("INSERT INTO staff_scopes (profile_id, lga_id) VALUES ($1, $2)", moderator, other_lga)
    csrf = signed_in(web, moderator)
    try:
        assert (await web.get(f"/admin/incidents/{incident_id}")).status_code == 303
        assert str(incident_id) not in (await web.get("/admin/queue")).text
        action = await web.post(
            f"/admin/incidents/{incident_id}/action", data={"action": "restore", "csrf": csrf}
        )
        assert action.status_code == 403
    finally:
        await db.execute("DELETE FROM lgas WHERE id = $1", other_lga)


async def test_idle_sessions_end(web, db):  # noqa: F811
    moderator = await user(db, role="moderator")
    stale = auth._sign(
        {"sub": str(moderator), "purpose": "session", "sv": 0, "sid": "s", "started": int(time.time()),
         "seen": int(time.time()) - (auth.IDLE_MINUTES + 1) * 60},
        auth.timedelta(hours=1),
    )
    web.cookies.set(auth.COOKIE, stale, path="/admin")
    assert (await web.get("/admin")).status_code == 303


async def test_sign_out_everywhere_ends_existing_sessions(web, db):  # noqa: F811
    moderator = await user(db, role="moderator")
    signed_in(web, moderator)
    assert (await web.get("/admin")).status_code == 200
    await db.execute("UPDATE profiles SET session_version = session_version + 1 WHERE id = $1", moderator)
    assert (await web.get("/admin")).status_code == 303


async def test_staff_cannot_disable_themselves(web, db):  # noqa: F811
    me = await user(db, role="super_admin")
    csrf = signed_in(web, me)
    response = await web.post(f"/admin/staff/{me}/status", data={"status": "disable", "csrf": csrf})
    assert response.status_code == 403
    assert await db.fetchval("SELECT is_active FROM profiles WHERE id = $1", me)


# Moderation


async def test_bulk_actions_and_claims(web, db, api, area):  # noqa: F811
    first, _ = await held_incident(api, db)
    second, _ = await held_incident(api, db)
    moderator = await user(db, role="moderator")
    csrf = signed_in(web, moderator)
    await web.post(f"/admin/incidents/{first}/claim", data={"csrf": csrf})
    assert await db.fetchval("SELECT claimed_by FROM incidents WHERE id = $1", first) == moderator
    response = await web.post(
        "/admin/queue/bulk",
        data={"csrf": csrf, "action": "restore", "incident_ids": [str(first), str(second)]},
    )
    assert response.status_code == 303
    statuses = await db.fetch("SELECT status FROM incidents WHERE id = ANY($1::uuid[])", [first, second])
    assert all(row["status"] != "under_review" for row in statuses)


async def test_merging_hides_the_duplicate_and_counts_it(web, db, api, area):  # noqa: F811
    target = (await report(api, db))["id"]
    duplicate = (await report(api, db))["id"]
    csrf = signed_in(web, await user(db, role="moderator"))
    response = await web.post(
        f"/admin/incidents/{target}/merge", data={"duplicate_id": duplicate, "csrf": csrf}
    )
    assert response.status_code == 303
    row = await db.fetchrow("SELECT merged_into, is_hidden FROM incidents WHERE id = $1", uuid.UUID(duplicate))
    assert row["merged_into"] == uuid.UUID(target) and row["is_hidden"]
    assert await db.fetchval("SELECT corroboration_count FROM incidents WHERE id = $1", uuid.UUID(target)) == 1


async def test_notes_stay_internal_and_updates_are_public(web, db, api, area):  # noqa: F811
    incident_id = (await report(api, db))["id"]
    csrf = signed_in(web, await user(db, role="moderator"))
    await web.post(f"/admin/incidents/{incident_id}/notes", data={"body": "Called the chairman", "csrf": csrf})
    await web.post(f"/admin/incidents/{incident_id}/updates", data={"body": "Road now clear", "csrf": csrf})
    updates = (await api.get(f"/v1/incidents/{incident_id}/updates", headers=bearer(await user(db)))).json()
    assert [u["body"] for u in updates] == ["Road now clear"]
    assert "Called the chairman" not in json.dumps(updates)


async def test_word_filter_holds_reports(api, db, area, clean_settings):  # noqa: F811
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES ('word_filter', '{\"words\": [\"juju\"], \"hold\": true}')"
    )
    held = await report(api, db, description="Strange juju items by the road")
    assert await db.fetchval("SELECT hold_reason FROM incidents WHERE id = $1", uuid.UUID(held["id"])) == "word filter: juju"
    assert await db.fetchval("SELECT status FROM incidents WHERE id = $1", uuid.UUID(held["id"])) == "under_review"


# Kill switches, limits and sanctions


async def test_kill_switches(api, db, area, clean_settings):  # noqa: F811
    person = await user(db)
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES ('switches', '{\"reports_paused\": true}')"
    )
    paused = await api.post("/v1/incidents", json=payload(), headers=bearer(person))
    assert paused.status_code == 503
    await db.execute(
        "UPDATE app_settings SET value = '{\"maintenance\": true, \"maintenance_message\": \"Back soon\"}' "
        "WHERE key = 'switches'"
    )
    app_config.clear_cache()
    assert (await api.get("/v1/me", headers=bearer(person))).status_code == 503
    config = (await api.get("/v1/config")).json()
    assert config["maintenance"] is True and config["maintenance_message"] == "Back soon"


async def test_panel_limits_override_the_server(api, db, area, clean_settings):  # noqa: F811
    person = await user(db)
    await db.execute("INSERT INTO app_settings (key, value) VALUES ('limits', '{\"daily_report_cap\": 1}')")
    assert (await api.post("/v1/incidents", json=payload(), headers=bearer(person))).status_code == 201
    assert (await api.post("/v1/incidents", json=payload(), headers=bearer(person))).status_code == 429


async def test_sanctions_and_appeals(web, api, db, area):  # noqa: F811
    person = await user(db)
    csrf = signed_in(web, await user(db, role="moderator"))
    await web.post(
        f"/admin/users/{person}/sanction",
        data={"kind": "pause_reporting", "days": "7", "reason": "Repeated false reports", "csrf": csrf},
    )
    blocked = await api.post("/v1/incidents", json=payload(), headers=bearer(person))
    assert blocked.status_code == 403
    sanctions = (await api.get("/v1/me/sanctions", headers=bearer(person))).json()
    appeal = await api.post(
        f"/v1/me/sanctions/{sanctions[0]['id']}/appeal",
        json={"message": "These were genuine reports, please check again"},
        headers=bearer(person),
    )
    assert appeal.status_code == 200
    await web.post(f"/admin/sanctions/{sanctions[0]['id']}/appeal", data={"decision": "accepted", "csrf": csrf})
    assert (await api.post("/v1/incidents", json=payload(), headers=bearer(person))).status_code == 201


async def test_user_profiles_need_a_recorded_reason(web, db):  # noqa: F811
    person = await user(db)
    csrf = signed_in(web, await user(db, role="moderator"))
    gate = await web.get(f"/admin/users/{person}")
    assert "Why do you need to open this profile" in gate.text
    await web.post(f"/admin/users/{person}/view", data={"reason": "Checking false reports", "csrf": csrf})
    assert "Opened for: Checking false reports" in (await web.get(f"/admin/users/{person}")).text


# Broadcasts


async def test_broadcasts_need_a_second_person(web, db):  # noqa: F811
    author = await user(db, role="admin")
    csrf = signed_in(web, author)
    await web.post(
        "/admin/broadcasts",
        data={"title": "Flooding", "body": "Avoid Ikorodu Road", "severity": "high",
              "channels": ["push"], "csrf": csrf},
    )
    broadcast_id = await db.fetchval(
        "SELECT id FROM broadcasts WHERE created_by = $1 ORDER BY created_at DESC LIMIT 1", author
    )
    own = await web.post(f"/admin/broadcasts/{broadcast_id}/decide", data={"decision": "approve", "csrf": csrf})
    assert own.status_code == 403
    web.cookies.clear()
    csrf = signed_in(web, await user(db, role="admin"))
    approved = await web.post(
        f"/admin/broadcasts/{broadcast_id}/decide", data={"decision": "approve", "csrf": csrf}
    )
    assert approved.status_code == 303
    assert await db.fetchval("SELECT status FROM broadcasts WHERE id = $1", broadcast_id) == "approved"
    await db.execute("DELETE FROM broadcasts WHERE id = $1", broadcast_id)


# App features


async def test_watch_zones(api, db):  # noqa: F811
    person = await user(db)
    zone = {"name": "Home", "kind": "home", "lat": 6.52, "lng": 3.37, "radius_m": 1500}
    created = await api.post("/v1/me/zones", json=zone, headers=bearer(person))
    assert created.status_code == 201
    for index in range(4):
        await api.post("/v1/me/zones", json={**zone, "name": f"Z{index}"}, headers=bearer(person))
    assert (await api.post("/v1/me/zones", json=zone, headers=bearer(person))).status_code == 409
    other = await user(db)
    assert (await api.get("/v1/me/zones", headers=bearer(other))).json() == []
    deleted = await api.delete(f"/v1/me/zones/{created.json()['id']}", headers=bearer(other))
    assert deleted.status_code == 204
    assert len((await api.get("/v1/me/zones", headers=bearer(person))).json()) == 5


async def test_support_conversation(web, api, db):  # noqa: F811
    person = await user(db)
    ticket = (
        await api.post(
            "/v1/support", json={"category": "bug", "message": "The map is blank"}, headers=bearer(person)
        )
    ).json()
    csrf = signed_in(web, await user(db, role="moderator"))
    await web.post(f"/admin/support/{ticket['id']}/reply", data={"body": "Checking our logs", "internal": "true", "csrf": csrf})
    await web.post(f"/admin/support/{ticket['id']}/reply", data={"body": "Fixed in the next update", "csrf": csrf})
    mine = (await api.get("/v1/support", headers=bearer(person))).json()[0]
    bodies = [m["body"] for m in mine["messages"]]
    assert bodies == ["The map is blank", "Fixed in the next update"]
    assert mine["status"] == "waiting"


async def test_verifier_application_and_updates(web, api, db, area):  # noqa: F811
    person = await user(db)
    response = await api.post(
        "/v1/verifier-application",
        json={"full_name": "Ada Obi", "community_role": "CDA Chairman", "lga_id": area["lga"],
              "ward_id": area["ward"], "reason": "I chair the CDA and know every street in the ward."},
        headers=bearer(person),
    )
    assert response.status_code == 201
    application = await db.fetchval("SELECT id FROM verifier_applications WHERE user_id = $1", person)
    incident_id = (await report(api, db))["id"]
    denied = await api.post(f"/v1/incidents/{incident_id}/updates", json={"body": "Clear now"}, headers=bearer(person))
    assert denied.status_code == 403
    csrf = signed_in(web, await user(db, role="admin"))
    await web.post(f"/admin/applications/{application}", data={"decision": "approved", "csrf": csrf})
    assert await db.fetchval("SELECT role FROM profiles WHERE id = $1", person) == "verifier"
    posted = await api.post(f"/v1/incidents/{incident_id}/updates", json={"body": "Clear now"}, headers=bearer(person))
    assert posted.status_code == 201
    assert posted.json()["author"] == "CDA Chairman, Ward 4"
    await db.execute("DELETE FROM verifier_applications WHERE user_id = $1", person)


def test_webhook_signatures_follow_standard_webhooks():
    secret = webhooks.new_secret()
    body = b'{"type":"incident.confirmed"}'
    signature = webhooks.sign(secret, "msg_1", 1700000000, body)
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(hmac.new(key, b"msg_1.1700000000." + body, hashlib.sha256).digest()).decode()
    assert signature == f"v1,{expected}"


async def test_webhooks_only_cover_the_partner_area(db, area, database_url):  # noqa: F811
    partner = await db.fetchval(
        "INSERT INTO partners (name) VALUES ($1) RETURNING id", uuid.uuid4().hex
    )
    await db.execute(
        "INSERT INTO staff_scopes (partner_id, lga_id) VALUES ($1, $2)", partner, area["lga"]
    )
    await db.execute(
        "INSERT INTO webhooks (partner_id, url, secret_enc, events) "
        "VALUES ($1, 'https://partner.test/hook', 'x', ARRAY['incident.confirmed'])",
        partner,
    )
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            inside = await webhooks.queue_event(session, "incident.confirmed", {}, area["lga"])
            outside = await webhooks.queue_event(session, "incident.confirmed", {}, -1)
            other_event = await webhooks.queue_event(session, "broadcast.sent", {}, area["lga"])
        assert len(inside) == 1 and outside == [] and other_event == []
    finally:
        await engine.dispose()
        await db.execute("DELETE FROM partners WHERE id = $1", partner)


def test_role_hierarchy_and_default_grants():

    assert rbac.may_assign("admin", "moderator")
    assert not rbac.may_assign("admin", "admin")
    assert rbac.may_assign("super_admin", "admin")
    assert "view_queue" in rbac.DEFAULTS["moderator"]
    assert "manage_staff" not in rbac.DEFAULTS["moderator"]
    assert "view_incidents" in rbac.DEFAULTS["partner"] and "act_on_incidents" not in rbac.DEFAULTS["partner"]


async def test_admin_sign_in_limits_are_per_account(web, db, monkeypatch):  # noqa: F811
    async def wrong(email, password):
        return None

    monkeypatch.setattr(auth, "password_sign_in", wrong)
    for _ in range(10):
        await web.post("/admin/login", data={"email": "typo@example.test", "password": "x"})
    blocked = await web.post("/admin/login", data={"email": "typo@example.test", "password": "x"})
    assert blocked.status_code == 429
    # Someone else behind the same proxy can still sign in.
    other = await web.post("/admin/login", data={"email": "staff@example.test", "password": "x"})
    assert other.status_code == 401


async def test_cloudflare_address_is_recorded(web, db, api, area):  # noqa: F811
    incident_id, _ = await held_incident(api, db)
    csrf = signed_in(web, await user(db, role="super_admin"))
    await web.post(
        f"/admin/incidents/{incident_id}/reveal-location",
        data={"reason": "Checking the visitor address", "csrf": csrf},
        headers={"CF-Connecting-IP": "102.89.1.7"},
    )
    ip = await db.fetchval(
        "SELECT host(ip) FROM audit_log WHERE reason = 'Checking the visitor address'"
    )
    assert ip == "102.89.1.7"

