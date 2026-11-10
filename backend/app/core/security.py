import asyncio
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Header

from app.core.config import get_settings
from app.core.errors import ApiError

_jwks_client: jwt.PyJWKClient | None = None
CLAIM_OPTIONS = {"require": ["exp", "sub"]}


@dataclass(frozen=True)
class AuthUser:
    id: UUID
    phone: str | None


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
    return AuthUser(id=UUID(claims["sub"]), phone=claims.get("phone"))


async def get_auth_user(authorization: str = Header(default="")) -> AuthUser:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "unauthorized", "Sign in required")
    try:
        return await asyncio.to_thread(decode_token, token)
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise ApiError(401, "unauthorized", "Invalid session") from exc
