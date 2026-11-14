import asyncio
from dataclasses import dataclass
from uuid import UUID

import jwt
import structlog
from fastapi import Header

from app.core.config import get_settings
from app.core.errors import ApiError

log = structlog.get_logger()
_jwks_client: jwt.PyJWKClient | None = None
CLAIM_OPTIONS = {"require": ["exp", "sub"]}


@dataclass(frozen=True)
class AuthUser:
    id: UUID
    phone: str | None
    email: str | None = None


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(get_settings().supabase_jwks_url, cache_keys=True)
    return _jwks_client


def decode_token(token: str) -> AuthUser:
    # Self-hosted Supabase signs with a shared secret; hosted projects publish keys.
    # Each mode accepts only its own algorithms, so a token cannot switch modes.
    secret = get_settings().supabase_jwt_secret
    if secret:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options=CLAIM_OPTIONS,
        )
    else:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            options=CLAIM_OPTIONS,
        )
    return AuthUser(
        id=UUID(claims["sub"]),
        phone=claims.get("phone") or None,
        email=(claims.get("email") or "").lower() or None,
    )


async def get_auth_user(authorization: str = Header(default="")) -> AuthUser:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "unauthorized", "Sign in required")
    try:
        return await asyncio.to_thread(decode_token, token)
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        # The reason, never the token. "Signature verification failed" usually means
        # SUPABASE_JWT_SECRET differs from Supabase's JWT secret.
        log.warning("auth_rejected", reason=f"{type(exc).__name__}: {exc}"[:200])
        raise ApiError(401, "unauthorized", "Invalid session") from exc


def jwt_secret_problem() -> str | None:
    """Checks the configured secret against the anon key, which Supabase signs the same way."""
    settings = get_settings()
    if settings.supabase_jwks_url:
        return None
    if not settings.supabase_jwt_secret:
        return "SUPABASE_JWT_SECRET is not set, so no sign-in can be checked"
    if not settings.supabase_anon_key:
        return "SUPABASE_ANON_KEY is not set, so the secret can't be checked"
    try:
        jwt.decode(
            settings.supabase_anon_key,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False, "verify_exp": False},
        )
    except jwt.InvalidSignatureError:
        return "SUPABASE_JWT_SECRET doesn't match Supabase's JWT secret (SERVICE_PASSWORD_JWT)"
    except jwt.PyJWTError as exc:
        return f"The anon key can't be read: {exc}"
    return None
