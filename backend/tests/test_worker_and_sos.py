import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from datetime import time as clock
from io import BytesIO

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.auth_hook import get_hook_sms, verify_signature
from app.core.clients import get_redis
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.main import app
from app.services import app_settings, media, notifications, sos
from tests.test_api_incidents import (  # noqa: F401
    LAT,
    LNG,
    FakeRedis,
    api,
    area,
    bearer,
    db,
    device,
    report,
    user,
)

SETTINGS = Settings(push_hourly_cap=5, sms_fallback_enabled=True, sms_daily_cap=10)


class RecordingPush:
    def __init__(self, invalid=()):
        self.sent = []
        self.invalid = list(invalid)

    async def send(self, tokens, data, high):
        self.sent.append((list(tokens), data, high))
        return self.invalid


class RecordingSms:
    def __init__(self):
        self.sent = []

    async def send(self, to, message):
        self.sent.append((to, message))
        return "id"


@pytest.fixture
async def sessions(database_url, migrated):
    engine = create_async_engine(database_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def subscriber(db, lat=LAT, lng=LNG, radius=5, quiet=None, sms=False):
    person = await user(db)
    await db.execute(
        "UPDATE profiles SET alert_radius_km = $2, quiet_hours_start = $3, quiet_hours_end = $4, "
        "sms_alerts_opt_in = $5 WHERE id = $1",
        person,
        radius,
        quiet[0] if quiet else None,
        quiet[1] if quiet else None,
        sms,
    )
    token = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO devices (user_id, fcm_token, platform, last_location, last_location_at) "
        "VALUES ($1, $2, 'android', ST_SetSRID(ST_MakePoint($3::float8, $4::float8), 4326)::geography, NOW())",
        person,
        token,
        lng,
        lat,
    )
    return person, token


async def set_state(db, incident, status, severity="high"):
    await db.execute(
        "UPDATE incidents SET status = $2, severity = $3, confirmed_by_label = 'Chairman, Ward 4' "
        "WHERE id = $1",
        uuid.UUID(incident["id"]),
        status,
        severity,
    )


async def run(sessions, incident, trigger, push=None, sms=None, now=None):
    push = push or RecordingPush()
    sms = sms or RecordingSms()
    count = await notifications.fan_out(
        sessions, SETTINGS, push, sms, FakeRedis(), uuid.UUID(incident["id"]), trigger, now
    )
    return count, push, sms


@pytest.mark.parametrize(
    "status,severity,trusted,expected",
    [
        ("unconfirmed", "critical", False, None),
        ("unconfirmed", "high", True, ("high", None, False)),
        ("corroborated", "low", False, ("normal", 2, False)),
        ("corroborated", "high", False, ("high", None, False)),
        ("confirmed", "critical", False, ("critical", None, True)),
        ("confirmed", "medium", False, ("normal", 2, False)),
        ("expired", "critical", False, None),
    ],
)
def test_policy_matrix(status, severity, trusted, expected):
    decision = notifications.decide(status, severity, trusted)
    if expected is None:
        assert decision is None
    else:
        assert (decision.priority, decision.max_km, decision.sms) == expected


def test_only_confirmed_critical_bypasses_quiet_hours():
    assert notifications.decide("confirmed", "critical", False).bypass_quiet is True
    assert notifications.decide("confirmed", "high", False).bypass_quiet is False


@pytest.mark.parametrize(
    "start,end,now,expected",
    [
        (clock(22), clock(6), clock(23), True),
        (clock(22), clock(6), clock(5), True),
        (clock(22), clock(6), clock(12), False),
        (clock(9), clock(17), clock(10), True),
        (None, None, clock(10), False),
    ],
)
def test_quiet_hours(start, end, now, expected):
    assert notifications.in_quiet_hours(start, end, now) is expected


async def test_unconfirmed_report_pushes_nobody(api, db, area, sessions):  # noqa: F811
    await subscriber(db)
    incident = await report(api, db)
    count, push, _ = await run(sessions, incident, "created")
    assert count == 0
    assert push.sent == []


async def test_fan_out_targets_radius_excludes_reporter_and_dedupes(api, db, area, sessions):  # noqa: F811
    reporter, reporter_token = await subscriber(db)
    near, near_token = await subscriber(db)
    _, far_token = await subscriber(db, lat=LAT + 0.2)
    incident = await report(api, db, reporter=reporter)
    await set_state(db, incident, "corroborated")

    count, push, _ = await run(sessions, incident, "corroborated")
    tokens = push.sent[0][0]
    assert count >= 1
    assert near_token in tokens
    assert reporter_token not in tokens
    assert far_token not in tokens
    assert push.sent[0][2] is True
    assert (
        await db.fetchval(
            "SELECT count(*) FROM notifications WHERE user_id = $1 AND incident_id = $2",
            near,
            uuid.UUID(incident["id"]),
        )
        == 1
    )

    count, push, _ = await run(sessions, incident, "corroborated")
    assert count == 0
    assert push.sent == []


async def test_quiet_hours_hold_high_alerts(api, db, area, sessions):  # noqa: F811
    _, token = await subscriber(db, quiet=(clock(0), clock(23, 59)))
    incident = await report(api, db)
    await set_state(db, incident, "corroborated")
    _, push, _ = await run(sessions, incident, "corroborated")
    assert all(token not in sent[0] for sent in push.sent)


async def test_confirmed_critical_breaks_quiet_hours_and_sends_opted_in_sms(api, db, area, sessions):  # noqa: F811
    person, token = await subscriber(db, quiet=(clock(0), clock(23, 59)), sms=True)
    incident = await report(api, db)
    await set_state(db, incident, "confirmed", "critical")
    _, push, sms = await run(sessions, incident, "confirmed")
    assert token in push.sent[0][0]
    phone = await db.fetchval("SELECT phone FROM profiles WHERE id = $1", person)
    assert phone in [to for to, _ in sms.sent]


async def test_invalid_tokens_are_deactivated(api, db, area, sessions):  # noqa: F811
    _, token = await subscriber(db)
    incident = await report(api, db)
    await set_state(db, incident, "corroborated")
    await run(sessions, incident, "corroborated", push=RecordingPush(invalid=[token]))
    assert await db.fetchval("SELECT is_active FROM devices WHERE fcm_token = $1", token) is False


async def test_all_clear_goes_only_to_people_already_alerted(api, db, area, sessions):  # noqa: F811
    _, alerted = await subscriber(db)
    incident = await report(api, db)
    await set_state(db, incident, "corroborated")
    await run(sessions, incident, "corroborated")
    _, late = await subscriber(db)
    await set_state(db, incident, "resolved")
    _, push, _ = await run(sessions, incident, "resolved")
    tokens = push.sent[0][0]
    assert alerted in tokens
    assert late not in tokens
    assert push.sent[0][1]["kind"] == "all_clear"


async def test_expiry_flips_status_and_records_event(api, db, area, sessions):  # noqa: F811
    incident = await report(api, db)
    incident_id = uuid.UUID(incident["id"])
    await db.execute(
        "UPDATE incidents SET expires_at = NOW() - INTERVAL '1 minute' WHERE id = $1", incident_id
    )
    async with sessions() as session:
        rows = await notifications.expire_due(session)
    assert incident_id in [row.id for row in rows]
    assert await db.fetchval("SELECT status FROM incidents WHERE id = $1", incident_id) == "expired"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM incident_events WHERE incident_id = $1 AND event_type = 'expired'",
            incident_id,
        )
        == 1
    )


async def test_verifier_extension_is_respected_by_expiry(api, db, area, sessions):  # noqa: F811
    incident = await report(api, db)
    incident_id = uuid.UUID(incident["id"])
    await db.execute(
        "UPDATE incidents SET expires_at = NOW() + INTERVAL '2 hours' WHERE id = $1", incident_id
    )
    async with sessions() as session:
        rows = await notifications.expire_due(session)
    assert incident_id not in [row.id for row in rows]


def test_image_processing_strips_exif_and_resizes():
    source = Image.new("RGB", (3000, 2000), "red")
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    buffer = BytesIO()
    source.save(buffer, "JPEG", exif=exif)
    output, width, height = media.render(buffer.getvalue(), media.MAX_SIDE)
    assert max(width, height) == media.MAX_SIDE
    assert dict(Image.open(BytesIO(output)).getexif()) == {}


def hook_headers(secret_bytes, body, timestamp=None, msg_id="msg_1"):
    timestamp = str(timestamp or int(time.time()))
    signed = f"{msg_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
    return {"webhook-id": msg_id, "webhook-timestamp": timestamp, "webhook-signature": f"v1,{signature}"}


SECRET_BYTES = b"0123456789abcdef0123456789abcdef"
SECRET = "v1,whsec_" + base64.b64encode(SECRET_BYTES).decode()


def test_signature_rejects_tampering_and_old_timestamps():
    body = b'{"a":1}'
    headers = hook_headers(SECRET_BYTES, body)
    args = (SECRET, headers["webhook-id"], headers["webhook-timestamp"])
    assert verify_signature(*args, body, headers["webhook-signature"])
    assert not verify_signature(*args, b'{"a":2}', headers["webhook-signature"])
    old = hook_headers(SECRET_BYTES, body, timestamp=int(time.time()) - 3600)
    assert not verify_signature(SECRET, old["webhook-id"], old["webhook-timestamp"], body, old["webhook-signature"])


@pytest.fixture
async def hook(monkeypatch):
    sms = RecordingSms()
    redis = FakeRedis()
    switch = {"phone": True}

    async def redis_override():
        yield redis

    async def no_session():
        yield None

    async def phone_on(_session):
        return switch["phone"]

    async def unchanged(_session, base=None):
        return base

    async def wording(_session):
        return dict(app_settings.DEFAULT_TEMPLATES)

    monkeypatch.setattr(app_settings, "phone_sign_in_enabled", phone_on)
    monkeypatch.setattr(app_settings, "effective", unchanged)
    monkeypatch.setattr(app_settings, "message_templates", wording)
    app.dependency_overrides[get_session] = no_session

    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_sms_hook_secret=SECRET, otp_per_phone_hourly_cap=2
    )
    app.dependency_overrides[get_hook_sms] = lambda: sms
    app.dependency_overrides[get_redis] = redis_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.sms = sms
        client.switch = switch
        yield client
    app.dependency_overrides.clear()


async def call_hook(client):
    body = json.dumps({"user": {"phone": "2348012345678"}, "sms": {"otp": "123456"}}).encode()
    return await client.post("/v1/auth/sms-hook", content=body, headers=hook_headers(SECRET_BYTES, body))


async def test_hook_sends_otp_and_enforces_per_phone_cap(hook):
    assert (await call_hook(hook)).status_code == 200
    assert "123456" in hook.sms.sent[0][1]
    assert (await call_hook(hook)).status_code == 200
    third = await call_hook(hook)
    assert third.status_code == 429
    assert third.json()["error"]["http_code"] == 429


async def test_hook_refuses_while_phone_sign_in_is_off(hook):
    hook.switch["phone"] = False
    response = await call_hook(hook)
    assert response.status_code == 403
    assert hook.sms.sent == []


async def test_hook_rejects_bad_signature(hook):
    response = await hook.post(
        "/v1/auth/sms-hook",
        content=b"{}",
        headers={"webhook-id": "x", "webhook-timestamp": str(int(time.time())), "webhook-signature": "v1,bad"},
    )
    assert response.status_code == 401
    assert hook.sms.sent == []


async def start_sos(api, person, client_id=None):  # noqa: F811
    return await api.post(
        "/v1/sos",
        json={"client_id": client_id or str(uuid.uuid4()), "lat": LAT, "lng": LNG},
        headers=bearer(person),
    )


async def test_sos_is_idempotent_and_never_creates_an_incident(api, db, area):  # noqa: F811
    person = await user(db)
    client_id = str(uuid.uuid4())
    before = await db.fetchval("SELECT count(*) FROM incidents")
    first = await start_sos(api, person, client_id)
    second = await start_sos(api, person, client_id)
    assert first.json()["id"] == second.json()["id"]
    assert [job[0] for job in api.queue.jobs] == ["send_sos_alerts"]
    assert await db.fetchval("SELECT count(*) FROM incidents") == before


async def test_tracking_page_shows_location_then_ends_when_safe(api, db, area):  # noqa: F811
    person = await user(db)
    sos_id = (await start_sos(api, person)).json()["id"]
    token = api.queue.jobs[-1][2]

    page = await api.get(f"/s/{token}")
    assert page.status_code == 200
    assert "is sharing their location" in page.text
    assert page.headers["cache-control"] == "no-store"

    await api.post(f"/v1/sos/{sos_id}/resolve", headers=bearer(person))
    ended = await api.get(f"/s/{token}")
    assert "Person is safe" in ended.text
    assert "sharing their location" not in ended.text
    assert api.queue.jobs[-1][0] == "send_sos_safe"


async def test_tracking_token_is_stored_hashed(api, db, area):  # noqa: F811
    person = await user(db)
    sos_id = uuid.UUID((await start_sos(api, person)).json()["id"])
    token = api.queue.jobs[-1][2]
    stored = await db.fetchval("SELECT share_token_hash FROM sos_alerts WHERE id = $1", sos_id)
    assert stored != token
    assert stored == sos.hash_token(token)


async def test_unknown_tracking_link_reveals_nothing(api):  # noqa: F811
    response = await api.get("/s/not-a-real-token")
    assert "This link has ended" in response.text


async def test_another_user_cannot_touch_someones_sos(api, db, area):  # noqa: F811
    person = await user(db)
    sos_id = (await start_sos(api, person)).json()["id"]
    response = await api.post(f"/v1/sos/{sos_id}/cancel", headers=bearer(await user(db)))
    assert response.status_code == 404


async def test_sms_goes_only_to_accepted_contacts(api, db, area, sessions):  # noqa: F811
    person = await user(db)
    for phone, status in (("+2348000000101", "accepted"), ("+2348000000102", "pending")):
        await db.execute(
            "INSERT INTO trusted_contacts (user_id, name, phone, consent_status) "
            "VALUES ($1, 'c', $2, $3)",
            person,
            phone,
            status,
        )
    sos_id = uuid.UUID((await start_sos(api, person)).json()["id"])
    async with sessions() as session:
        _, contacts = await sos.contacts_for(session, sos_id)
    assert [contact.phone for contact in contacts] == ["+2348000000101"]


async def test_overdue_checkin_fires_once(api, db, area, sessions):  # noqa: F811
    person = await user(db)
    response = await api.post(
        "/v1/sos/checkins",
        json={"client_id": str(uuid.uuid4()), "minutes": 30, "lat": LAT, "lng": LNG},
        headers=bearer(person),
    )
    sos_id = uuid.UUID(response.json()["id"])
    await db.execute(
        "UPDATE sos_alerts SET checkin_due_at = NOW() - INTERVAL '1 minute' WHERE id = $1", sos_id
    )
    async with sessions() as session:
        first = await sos.fire_overdue(session)
    async with sessions() as session:
        second = await sos.fire_overdue(session)
    assert sos_id in [item[0] for item in first]
    assert sos_id not in [item[0] for item in second]


async def test_checking_in_on_time_sends_nothing(api, db, area):  # noqa: F811
    person = await user(db)
    response = await api.post(
        "/v1/sos/checkins",
        json={"client_id": str(uuid.uuid4()), "minutes": 30, "lat": LAT, "lng": LNG},
        headers=bearer(person),
    )
    api.queue.jobs.clear()
    done = await api.post(f"/v1/sos/{response.json()['id']}/checkin", headers=bearer(person))
    assert done.json()["status"] == "resolved"
    assert api.queue.jobs == []


async def test_contact_invite_consent_flow(api, db, area):  # noqa: F811
    person = await user(db)
    response = await api.post(
        "/v1/trusted-contacts",
        json={"name": "Ada", "phone": "+2348000000103"},
        headers=bearer(person),
    )
    assert response.status_code == 201
    contact_id = uuid.UUID(response.json()["id"])
    token = api.queue.jobs[-1][3]

    page = await api.get(f"/c/{token}")
    assert "trusted contact" in page.text

    await api.post(f"/c/{token}", data={"answer": "accept"})
    assert await db.fetchval("SELECT consent_status FROM trusted_contacts WHERE id = $1", contact_id) == "accepted"
    await api.post(f"/c/{token}", data={"answer": "withdraw"})
    assert await db.fetchval("SELECT consent_status FROM trusted_contacts WHERE id = $1", contact_id) == "revoked"


async def test_retention_purge_removes_old_pings(api, db, area, sessions):  # noqa: F811
    person = await user(db)
    sos_id = uuid.UUID((await start_sos(api, person)).json()["id"])
    await db.execute(
        "UPDATE sos_alerts SET status = 'resolved', resolved_at = $2 WHERE id = $1",
        sos_id,
        datetime.now(UTC) - timedelta(hours=25),
    )
    async with sessions() as session:
        await sos.retention_purge(session)
    assert await db.fetchval("SELECT count(*) FROM sos_location_pings WHERE sos_id = $1", sos_id) == 0


async def test_delete_account_removes_the_user(api, db, area):  # noqa: F811
    person = await user(db)
    await report(api, db, reporter=person)
    response = await api.delete("/v1/me", headers=bearer(person))
    assert response.status_code == 204
    assert await db.fetchval("SELECT count(*) FROM profiles WHERE id = $1", person) == 0
