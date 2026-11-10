import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from fastapi import Header
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.clients import get_queue, get_redis
from app.core.db import get_session
from app.core.security import AuthUser, get_auth_user
from app.main import app
from tests.conftest import plain_dsn

LAT, LNG = 8.05, 5.02
LGA = "MULTIPOLYGON(((5.0 8.0,5.1 8.0,5.1 8.1,5.0 8.1,5.0 8.0)))"
WARD = "MULTIPOLYGON(((5.0 8.0,5.05 8.0,5.05 8.1,5.0 8.1,5.0 8.0)))"


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        return True

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args):
        self.jobs.append((name, *args))


@pytest.fixture
async def db(database_url, migrated):
    connection = await asyncpg.connect(plain_dsn(database_url), statement_cache_size=0)
    yield connection
    await connection.close()


@pytest.fixture
async def area(db):
    suffix = uuid.uuid4().hex[:8]
    lga_id = await db.fetchval(
        "INSERT INTO lgas (state, name, geom) VALUES ('ApiTest', $1, ST_GeomFromText($2, 4326)) "
        "RETURNING id",
        f"LGA {suffix}",
        LGA,
    )
    ward_id = await db.fetchval(
        "INSERT INTO wards (lga_id, name, geom) VALUES ($1, 'Ward 4', ST_GeomFromText($2, 4326)) "
        "RETURNING id",
        lga_id,
        WARD,
    )
    yield {"lga": lga_id, "ward": ward_id}
    await db.execute("DELETE FROM incidents WHERE lga_id = $1", lga_id)
    await db.execute("DELETE FROM verifiers WHERE lga_id = $1", lga_id)
    await db.execute(
        "UPDATE profiles SET home_ward_id = NULL, home_lga_id = NULL WHERE home_lga_id = $1", lga_id
    )
    await db.execute("DELETE FROM wards WHERE id = $1", ward_id)
    await db.execute("DELETE FROM lgas WHERE id = $1", lga_id)


@pytest.fixture
async def api(database_url, migrated):
    engine = create_async_engine(database_url, poolclass=NullPool)
    queue = FakeQueue()
    redis = FakeRedis()

    async def session_override():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def auth(authorization: str = Header(default="")):
        user_id = uuid.UUID(authorization.removeprefix("Bearer "))
        return AuthUser(id=user_id, phone=None)

    async def queue_override():
        return queue

    async def redis_override():
        yield redis

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_auth_user] = auth
    app.dependency_overrides[get_queue] = queue_override
    app.dependency_overrides[get_redis] = redis_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.queue = queue
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


async def user(db, age_days=5, role="member", reputation=0):
    user_id = uuid.uuid4()
    phone = f"+234{uuid.uuid4().int % 10**10:010d}"
    await db.execute("INSERT INTO auth.users (id, phone) VALUES ($1, $2)", user_id, phone)
    await db.execute(
        "INSERT INTO profiles (id, phone, role, reputation_score, created_at) "
        "VALUES ($1, $2, $3, $4, NOW() - make_interval(days => $5))",
        user_id,
        phone,
        role,
        reputation,
        age_days,
    )
    return user_id


async def device(db, user_id):
    return await db.fetchval(
        "INSERT INTO devices (user_id, fcm_token, platform) VALUES ($1, $2, 'android') RETURNING id",
        user_id,
        uuid.uuid4().hex,
    )


def bearer(user_id):
    return {"Authorization": f"Bearer {user_id}"}


def payload(**overrides):
    return {
        "client_id": str(uuid.uuid4()),
        "type_code": "roadblock",
        "lat": LAT,
        "lng": LNG,
        "occurred_at": datetime.now(UTC).isoformat(),
        **overrides,
    }


async def report(api, db, reporter=None, **overrides):
    reporter = reporter or await user(db)
    response = await api.post("/v1/incidents", json=payload(**overrides), headers=bearer(reporter))
    assert response.status_code == 201, response.text
    return response.json()


async def corroborate(api, db, incident_id, age_days=5, lat=LAT, lng=LNG):
    person = await user(db, age_days=age_days)
    return await api.post(
        f"/v1/incidents/{incident_id}/corroborate",
        json={"lat": lat, "lng": lng, "device_id": str(await device(db, person))},
        headers=bearer(person),
    )


async def test_replaying_a_report_returns_the_same_incident_without_new_fan_out(api, db, area):
    reporter = await user(db)
    body = payload()
    first = await api.post("/v1/incidents", json=body, headers=bearer(reporter))
    second = await api.post("/v1/incidents", json=body, headers=bearer(reporter))
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(api.queue.jobs) == 1


async def test_another_user_cannot_reuse_a_client_id(api, db, area):
    body = payload()
    assert (await api.post("/v1/incidents", json=body, headers=bearer(await user(db)))).status_code == 201
    other = await api.post("/v1/incidents", json=body, headers=bearer(await user(db)))
    assert other.status_code == 409


async def test_area_is_derived_and_expiry_comes_from_the_type(api, db, area):
    incident = await report(api, db)
    row = await db.fetchrow(
        "SELECT lga_id, ward_id, expires_at - occurred_at AS ttl FROM incidents WHERE id = $1",
        uuid.UUID(incident["id"]),
    )
    assert (row["lga_id"], row["ward_id"]) == (area["lga"], area["ward"])
    assert row["ttl"] == timedelta(minutes=180)


async def test_anonymous_report_is_fuzzed_but_stays_inside_its_ward(api, db, area):
    incident = await report(api, db, is_anonymous=True)
    row = await db.fetchrow(
        "SELECT ST_Distance(location, public_location) AS moved, "
        "ST_Covers((SELECT geom FROM wards WHERE id = $2), public_location::geometry) AS inside "
        "FROM incidents WHERE id = $1",
        uuid.UUID(incident["id"]),
        area["ward"],
    )
    assert row["moved"] > 0
    assert row["inside"] is True


async def test_sensitive_type_is_fuzzed_even_when_not_anonymous(api, db, area):
    incident = await report(api, db, type_code="kidnapping")
    moved = await db.fetchval(
        "SELECT ST_Distance(location, public_location) FROM incidents WHERE id = $1",
        uuid.UUID(incident["id"]),
    )
    assert moved > 0


async def test_old_queued_report_is_history_and_is_not_pushed(api, db, area):
    old = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    incident = await report(api, db, occurred_at=old)
    assert incident["status"] == "expired"
    assert api.queue.jobs == []


async def test_report_naming_a_group_is_held_for_review(api, db, area):
    incident = await report(api, db, description="Those Hausa boys blocked the road")
    assert incident["status"] == "under_review"
    hidden = await db.fetchval(
        "SELECT is_hidden FROM incidents WHERE id = $1", uuid.UUID(incident["id"])
    )
    assert hidden is True
    assert api.queue.jobs == []


async def test_new_account_hits_a_lower_daily_cap(api, db, area):
    newcomer = await user(db, age_days=0)
    for _ in range(3):
        await report(api, db, reporter=newcomer)
    response = await api.post("/v1/incidents", json=payload(), headers=bearer(newcomer))
    assert response.status_code == 429


async def test_three_corroborations_upgrade_once(api, db, area):
    incident = await report(api, db)
    api.queue.jobs.clear()
    for _ in range(2):
        assert (await corroborate(api, db, incident["id"])).json()["status"] == "unconfirmed"
    third = await corroborate(api, db, incident["id"])
    assert third.json()["status"] == "corroborated"
    assert api.queue.jobs == [("fan_out_alert", incident["id"], "corroborated")]


async def test_parallel_corroborations_produce_one_upgrade_and_exact_count(api, db, area):
    incident = await report(api, db)
    api.queue.jobs.clear()
    people = []
    for _ in range(8):
        person = await user(db)
        people.append((person, await device(db, person)))
    url = f"/v1/incidents/{incident['id']}/corroborate"
    responses = await asyncio.gather(
        *[
            api.post(
                url,
                json={"lat": LAT, "lng": LNG, "device_id": str(device_id)},
                headers=bearer(person),
            )
            for person, device_id in people
        ]
    )
    assert [r.status_code for r in responses] == [200] * 8
    row = await db.fetchrow(
        "SELECT status, corroboration_count FROM incidents WHERE id = $1",
        uuid.UUID(incident["id"]),
    )
    assert row["corroboration_count"] == 8
    assert row["status"] == "corroborated"
    assert len(api.queue.jobs) == 1


async def test_corroboration_outside_radius_is_rejected(api, db, area):
    incident = await report(api, db)
    response = await corroborate(api, db, incident["id"], lat=LAT + 0.05)
    assert response.status_code == 403
    assert response.json()["error"] == "too_far"


async def test_new_account_cannot_corroborate(api, db, area):
    incident = await report(api, db)
    response = await corroborate(api, db, incident["id"], age_days=0)
    assert response.json()["error"] == "account_too_new"


async def test_reporter_cannot_corroborate_own_report(api, db, area):
    reporter = await user(db)
    incident = await report(api, db, reporter=reporter)
    response = await api.post(
        f"/v1/incidents/{incident['id']}/corroborate",
        json={"lat": LAT, "lng": LNG, "device_id": str(await device(db, reporter))},
        headers=bearer(reporter),
    )
    assert response.json()["error"] == "own_report"


async def test_same_device_cannot_count_for_two_people(api, db, area):
    incident = await report(api, db)
    person = await user(db)
    shared = await device(db, person)
    url = f"/v1/incidents/{incident['id']}/corroborate"
    body = {"lat": LAT, "lng": LNG, "device_id": str(shared)}
    assert (await api.post(url, json=body, headers=bearer(person))).status_code == 200
    other = await user(db)
    response = await api.post(url, json=body, headers=bearer(other))
    assert response.json()["error"] == "device_not_owned"
    await db.execute("UPDATE devices SET user_id = $1 WHERE id = $2", other, shared)
    response = await api.post(url, json=body, headers=bearer(other))
    assert response.json()["error"] == "device_used"


async def test_scoped_verifier_upgrades_with_one_corroboration(api, db, area):
    incident = await report(api, db)
    verifier = await user(db, role="verifier")
    await db.execute(
        "INSERT INTO verifiers (user_id, lga_id, ward_id, title) "
        "VALUES ($1, $2, $3, 'Community Chairman')",
        verifier,
        area["lga"],
        area["ward"],
    )
    response = await api.post(
        f"/v1/incidents/{incident['id']}/corroborate",
        json={"lat": LAT, "lng": LNG, "device_id": str(await device(db, verifier))},
        headers=bearer(verifier),
    )
    assert response.json()["status"] == "corroborated"


async def flag_as(api, db, incident_id, **kwargs):
    person = await user(db, **kwargs)
    return await api.post(
        f"/v1/incidents/{incident_id}/flag",
        json={"reason": "false_report", "lat": LAT, "lng": LNG},
        headers=bearer(person),
    )


async def test_brigading_from_new_accounts_does_not_hide_a_report(api, db, area):
    incident = await report(api, db)
    for _ in range(10):
        assert (await flag_as(api, db, incident["id"], age_days=0)).status_code == 200
    row = await db.fetchrow(
        "SELECT status, is_hidden FROM incidents WHERE id = $1", uuid.UUID(incident["id"])
    )
    assert row["status"] == "unconfirmed"
    assert row["is_hidden"] is False


async def test_established_flags_hide_and_queue_for_review(api, db, area):
    incident = await report(api, db)
    for _ in range(5):
        response = await flag_as(api, db, incident["id"])
    assert response.json()["status"] == "under_review"


async def test_flag_from_far_away_carries_almost_no_weight(api, db, area):
    incident = await report(api, db)
    person = await user(db)
    response = await api.post(
        f"/v1/incidents/{incident['id']}/flag",
        json={"reason": "spam", "lat": 6.0, "lng": 3.0},
        headers=bearer(person),
    )
    assert response.status_code == 200
    weight = await db.fetchval(
        "SELECT weight FROM incident_flags WHERE incident_id = $1", uuid.UUID(incident["id"])
    )
    assert float(weight) == pytest.approx(0.05)


async def scoped_verifier(db, area):
    verifier = await user(db, role="verifier")
    await db.execute(
        "INSERT INTO verifiers (user_id, lga_id, ward_id, title) "
        "VALUES ($1, $2, $3, 'Community Chairman')",
        verifier,
        area["lga"],
        area["ward"],
    )
    return verifier


async def test_confirm_records_label_audit_and_reputation(api, db, area):
    reporter = await user(db)
    incident = await report(api, db, reporter=reporter)
    verifier = await scoped_verifier(db, area)
    api.queue.jobs.clear()
    response = await api.patch(
        f"/v1/incidents/{incident['id']}/verify",
        json={"action": "confirm"},
        headers=bearer(verifier),
    )
    assert response.json()["status"] == "confirmed"
    label = await db.fetchval(
        "SELECT confirmed_by_label FROM incidents WHERE id = $1", uuid.UUID(incident["id"])
    )
    assert label == "Community Chairman, Ward 4"
    assert await db.fetchval("SELECT reputation_score FROM profiles WHERE id = $1", reporter) == 5
    assert (
        await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE action = 'verify:confirm' AND entity_id = $1",
            incident["id"],
        )
        == 1
    )
    assert api.queue.jobs == [("fan_out_alert", incident["id"], "confirmed")]


async def test_verifier_outside_scope_is_refused(api, db, area):
    incident = await report(api, db)
    outsider = await user(db, role="verifier")
    response = await api.patch(
        f"/v1/incidents/{incident['id']}/verify",
        json={"action": "confirm"},
        headers=bearer(outsider),
    )
    assert response.status_code == 403


async def test_mark_false_hides_and_penalises(api, db, area):
    reporter = await user(db)
    incident = await report(api, db, reporter=reporter)
    verifier = await scoped_verifier(db, area)
    response = await api.patch(
        f"/v1/incidents/{incident['id']}/verify",
        json={"action": "mark_false"},
        headers=bearer(verifier),
    )
    assert response.json()["status"] == "false_report"
    assert await db.fetchval("SELECT reputation_score FROM profiles WHERE id = $1", reporter) == -10


async def test_moderator_restores_a_hidden_report(api, db, area):
    incident = await report(api, db, description="Those Hausa boys blocked the road")
    moderator = await user(db, role="moderator")
    response = await api.patch(
        f"/v1/incidents/{incident['id']}/verify",
        json={"action": "restore"},
        headers=bearer(moderator),
    )
    assert response.json()["status"] == "unconfirmed"


async def test_profile_cannot_set_role_or_reputation(api, db, area):
    person = await user(db)
    response = await api.patch(
        "/v1/me",
        json={"display_name": "Ada", "role": "admin", "reputation_score": 99},
        headers=bearer(person),
    )
    assert response.status_code == 200
    row = await db.fetchrow("SELECT role, reputation_score, display_name FROM profiles WHERE id = $1", person)
    assert tuple(row) == ("member", 0, "Ada")


async def test_setting_home_point_resolves_area(api, db, area):
    person = await user(db)
    response = await api.patch(
        "/v1/me", json={"home_lat": LAT, "home_lng": LNG}, headers=bearer(person)
    )
    assert response.json()["home_ward_id"] == area["ward"]


async def test_media_path_must_be_in_the_reporters_folder(api, db, area):
    reporter = await user(db)
    incident = await report(api, db, reporter=reporter)
    url = f"/v1/incidents/{incident['id']}/media"
    bad = await api.post(url, json={"kind": "image", "storage_path": "other/x.jpg"}, headers=bearer(reporter))
    assert bad.status_code == 422
    good = await api.post(
        url, json={"kind": "image", "storage_path": f"{reporter}/x.jpg"}, headers=bearer(reporter)
    )
    assert good.status_code == 201
