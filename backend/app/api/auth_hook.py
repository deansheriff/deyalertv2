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

from app.core.clients import get_redis
from app.core.config import Settings, get_settings
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
) -> Any:
    body = await request.body()
    headers = request.headers
    try:
        valid = verify_signature(
            settings.supabase_sms_hook_secret,
            headers.get("webhook-id", ""),
            headers.get("webhook-timestamp", "0"),
            body,
            headers.get("webhook-signature", ""),
        )
    except (ValueError, IndexError):
        valid = False
    if not valid:
        return _hook_error(401, "Invalid signature")

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
        await sms.send(phone, f"Your Dey Alert code is {otp}. It expires soon. Do not share it.")
    except httpx.HTTPError:
        return _hook_error(502, "SMS could not be sent")
    return {}
