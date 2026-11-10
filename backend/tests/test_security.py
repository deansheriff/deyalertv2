import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import security
from app.core.config import Settings
from app.core.errors import ApiError

KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
SECRET = "s" * 40


@pytest.fixture(autouse=True)
def jwks(monkeypatch):
    client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=KEY.public_key())
    )
    monkeypatch.setattr(security, "_jwks_client", client)
    monkeypatch.setattr(security, "get_settings", lambda: Settings(supabase_jwt_secret=""))


@pytest.fixture
def shared_secret(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: Settings(supabase_jwt_secret=SECRET))


def token(key=KEY, algorithm="ES256", **overrides):
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "phone": "2348012345678",
        **overrides,
    }
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm=algorithm)


def hs256(secret=SECRET, **overrides):
    return token(key=secret, algorithm="HS256", **overrides)


async def authenticate(value):
    return await security.get_auth_user(authorization=f"Bearer {value}")


async def test_valid_token_returns_the_user():
    subject = str(uuid.uuid4())
    user = await authenticate(token(sub=subject))
    assert str(user.id) == subject
    assert user.phone == "2348012345678"


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: token(exp=datetime.now(UTC) - timedelta(minutes=1)), id="expired"),
        pytest.param(lambda: token(aud="anon"), id="wrong-audience"),
        pytest.param(lambda: token(key=OTHER_KEY), id="signed-by-another-key"),
        pytest.param(lambda: token(sub="not-a-uuid"), id="bad-subject"),
        pytest.param(lambda: token(exp=None), id="no-expiry"),
        pytest.param(lambda: "garbage", id="not-a-token"),
    ],
)
async def test_invalid_tokens_are_rejected(bad):
    with pytest.raises(ApiError) as caught:
        await authenticate(bad())
    assert caught.value.status == 401


async def test_unsigned_token_is_rejected():
    unsigned = jwt.api_jws.PyJWS().encode(
        b'{"sub":"00000000-0000-0000-0000-000000000000","aud":"authenticated","exp":9999999999}',
        key=None,
        algorithm="none",
    )
    with pytest.raises(ApiError):
        await authenticate(unsigned)


async def test_key_mode_rejects_shared_secret_tokens():
    with pytest.raises(ApiError):
        await authenticate(hs256())


@pytest.mark.parametrize("header", ["", "Bearer", "Basic abc"])
async def test_missing_or_wrong_scheme_is_rejected(header):
    with pytest.raises(ApiError):
        await security.get_auth_user(authorization=header)


async def test_shared_secret_token_is_accepted(shared_secret):
    subject = str(uuid.uuid4())
    user = await authenticate(hs256(sub=subject))
    assert str(user.id) == subject


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: hs256(secret="t" * 40), id="wrong-secret"),
        pytest.param(lambda: hs256(aud="anon"), id="anon-key-is-not-a-session"),
        pytest.param(lambda: hs256(exp=datetime.now(UTC) - timedelta(minutes=1)), id="expired"),
        pytest.param(lambda: token(), id="key-signed-token-in-secret-mode"),
    ],
)
async def test_shared_secret_mode_rejects_bad_tokens(shared_secret, bad):
    with pytest.raises(ApiError) as caught:
        await authenticate(bad())
    assert caught.value.status == 401


async def test_shared_secret_mode_never_reads_the_key_list(shared_secret, monkeypatch):
    def fail(_):
        raise AssertionError("key list should not be used")

    monkeypatch.setattr(
        security, "_jwks_client", SimpleNamespace(get_signing_key_from_jwt=fail)
    )
    assert await authenticate(hs256())
