import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients import get_redis
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.services import app_settings, mailer
from app.services.messaging import SmsProvider, build_sms

router = APIRouter(prefix="/v1/auth", tags=["auth"])
MAX_SKEW_SECONDS = 300
_http = httpx.AsyncClient()


def get_hook_sms() -> SmsProvider:
    return build_sms(get_settings(), _http)


def verify_signature(secret: str, msg_id: str, timestamp: str, body: bytes, header: str) -> bool:
    if abs(time.time() - int(timestamp)) > MAX_SKEW_SECONDS:
        return False
    key = base64.b64decode(secret.removeprefix("v1,").removeprefix("whsec_"))
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        hmac.compare_digest(expected, part.split(",", 1)[1])
        for part in header.split()
        if part.startswith("v1,")
    )


def _signed(request: Request, body: bytes, secret: str) -> bool:
    if not secret:
        # An empty key would let anyone compute a valid signature.
        return False
    headers = request.headers
    try:
        return verify_signature(
            secret,
            headers.get("webhook-id", ""),
            headers.get("webhook-timestamp", "0"),
            body,
            headers.get("webhook-signature", ""),
        )
    except (ValueError, IndexError):
        return False


def _hook_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"http_code": status, "message": message}}, status_code=status)


async def _within_cap(redis: Redis, key: str, limit: int, seconds: int) -> bool:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, seconds)
    return count <= limit


@router.post("/sms-hook")
async def sms_hook(
    request: Request,
    redis: Redis = Depends(get_redis),
    sms: SmsProvider = Depends(get_hook_sms),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> Any:
    body = await request.body()
    if not _signed(request, body, settings.supabase_sms_hook_secret):
        return _hook_error(401, "Invalid signature")
    if not await app_settings.phone_sign_in_enabled(session):
        return _hook_error(403, "Phone sign-in is turned off. Use email.")

    settings = await app_settings.effective(session, settings)
    payload = await request.json()
    phone = payload["user"]["phone"]
    otp = payload["sms"]["otp"]
    day = datetime.now(UTC).date()
    if not await _within_cap(redis, f"cap:otp:{day}", settings.otp_daily_cap, 172800):
        return _hook_error(429, "Daily SMS limit reached")
    if not await _within_cap(
        redis, f"cap:otp:{phone}", settings.otp_per_phone_hourly_cap, 3600
    ):
        return _hook_error(429, "Too many codes requested")
    try:
        wording = (await app_settings.message_templates(session))["otp_sms"]
        await sms.send(phone, app_settings.fill(wording, code=otp))
    except httpx.HTTPError:
        return _hook_error(502, "SMS could not be sent")
    return {}


@router.post("/email-hook")
async def email_hook(
    request: Request,
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Supabase's Send Email hook: delivers sign-in codes through the panel's SMTP server."""
    body = await request.body()
    if not _signed(request, body, settings.supabase_email_hook_secret):
        return _hook_error(401, "Invalid signature")

    payload = await request.json()
    user = payload.get("user") or {}
    data = payload.get("email_data") or {}
    action = data.get("email_action_type", "")
    # An email change confirms the new address with token_new and the old one with token.
    messages = [(user.get("email"), data.get("token"))]
    if action == "email_change":
        messages.append((user.get("new_email"), data.get("token_new")))
    messages = [(address.lower(), code) for address, code in messages if address and code]
    if not messages:
        return _hook_error(400, "Nothing to send")

    settings = await app_settings.effective(session, settings)
    config = await mailer.load(session)
    if config is None:
        return _hook_error(503, "Email is not set up yet. An admin must add SMTP settings.")
    day = datetime.now(UTC).date()
    for address, code in messages:
        if not await _within_cap(redis, f"cap:email:{day}", settings.email_daily_cap, 172800):
            return _hook_error(429, "Daily email limit reached")
        if not await _within_cap(
            redis, f"cap:email:{address}", settings.email_per_address_hourly_cap, 3600
        ):
            return _hook_error(429, "Too many codes requested")
        subject, text_body, html_body = mailer.code_email(action, code)
        try:
            await mailer.send(config, address, subject, text_body, html_body)
        except mailer.MailError:
            return _hook_error(502, "Email could not be sent")
    return {}
