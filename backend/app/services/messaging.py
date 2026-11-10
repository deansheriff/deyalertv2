import asyncio
import json
from typing import Any, Protocol

import firebase_admin
import httpx
import structlog
from firebase_admin import credentials, messaging

from app.core.config import Settings

log = structlog.get_logger()
FCM_BATCH_SIZE = 500


class SmsProvider(Protocol):
    async def send(self, to: str, message: str) -> str | None: ...


class PushProvider(Protocol):
    async def send(self, tokens: list[str], data: dict[str, str], high: bool) -> list[str]: ...


class TermiiProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    async def send(self, to: str, message: str) -> str | None:
        response = await self.client.post(
            f"{self.settings.termii_base_url}/api/sms/send",
            json={
                "to": to.removeprefix("+"),
                "from": self.settings.termii_sender_id,
                "sms": message,
                "type": "plain",
                "channel": "dnd",
                "api_key": self.settings.termii_api_key,
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("message_id")


class LogOnlySms:
    async def send(self, to: str, message: str) -> str | None:
        log.info("sms_skipped_no_provider", length=len(message))
        return None


class FcmProvider:
    def __init__(self, service_account_json: str) -> None:
        info = json.loads(service_account_json)
        self.app = firebase_admin.initialize_app(
            credentials.Certificate(info), name="dey-alert"
        )

    def _send_batch(self, tokens: list[str], data: dict[str, str], high: bool) -> list[str]:
        message = messaging.MulticastMessage(
            tokens=tokens,
            data=data,
            android=messaging.AndroidConfig(priority="high" if high else "normal"),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10" if high else "5"},
                payload=messaging.APNSPayload(aps=messaging.Aps(content_available=True)),
            ),
        )
        response = messaging.send_each_for_multicast(message, app=self.app)
        invalid = []
        for token, result in zip(tokens, response.responses, strict=True):
            if not result.success and isinstance(
                result.exception, messaging.UnregisteredError | messaging.SenderIdMismatchError
            ):
                invalid.append(token)
        return invalid

    async def send(self, tokens: list[str], data: dict[str, str], high: bool) -> list[str]:
        invalid: list[str] = []
        for start in range(0, len(tokens), FCM_BATCH_SIZE):
            batch = tokens[start : start + FCM_BATCH_SIZE]
            invalid += await asyncio.to_thread(self._send_batch, batch, data, high)
        return invalid


class LogOnlyPush:
    async def send(self, tokens: list[str], data: dict[str, str], high: bool) -> list[str]:
        log.info("push_skipped_no_provider", recipients=len(tokens))
        return []


def build_sms(settings: Settings, client: httpx.AsyncClient) -> SmsProvider:
    if settings.termii_api_key:
        return TermiiProvider(settings, client)
    if settings.environment == "production":
        raise RuntimeError("TERMII_API_KEY is required in production")
    return LogOnlySms()


def build_push(settings: Settings) -> PushProvider:
    if settings.fcm_service_account_json:
        return FcmProvider(settings.fcm_service_account_json)
    if settings.environment == "production":
        raise RuntimeError("FCM_SERVICE_ACCOUNT_JSON is required in production")
    return LogOnlyPush()


async def broadcast(
    settings: Settings, client: httpx.AsyncClient, topics: list[str], payload: dict[str, Any]
) -> None:
    if not settings.supabase_url or not topics:
        return
    response = await client.post(
        f"{settings.supabase_url}/realtime/v1/api/broadcast",
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        },
        json={
            "messages": [
                {"topic": topic, "event": "incident", "payload": payload, "private": False}
                for topic in topics
            ]
        },
        timeout=10,
    )
    response.raise_for_status()
