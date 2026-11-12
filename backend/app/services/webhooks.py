"""Signed event deliveries to partner systems (Standard Webhooks format)."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import mailer

EVENTS = {
    "incident.confirmed": "Incident confirmed",
    "incident.resolved": "Incident resolved",
    "broadcast.sent": "Broadcast sent",
}
MAX_ATTEMPTS = 5


def new_secret() -> str:
    return "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{msg_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


async def queue_event(
    session: AsyncSession,
    event: str,
    payload: dict[str, Any],
    lga_id: int | None = None,
    ward_id: int | None = None,
) -> list[int]:
    """Creates one delivery per webhook that wants this event and covers the area."""
    rows = (
        (
            await session.execute(
                text(
                    """
                INSERT INTO webhook_deliveries (webhook_id, event, payload)
                SELECT h.id, :event, CAST(:payload AS JSONB)
                FROM webhooks h JOIN partners pa ON pa.id = h.partner_id
                WHERE h.is_active AND pa.is_active AND :event = ANY(h.events)
                  AND (
                    NOT EXISTS (SELECT 1 FROM staff_scopes s WHERE s.partner_id = pa.id)
                    OR EXISTS (SELECT 1 FROM staff_scopes s WHERE s.partner_id = pa.id
                               AND (s.lga_id = CAST(:lga AS INT) OR s.ward_id = CAST(:ward AS INT)))
                  )
                RETURNING id
                """
                ),
                {"event": event, "payload": json.dumps(payload), "lga": lga_id, "ward": ward_id},
            )
        )
        .scalars()
        .all()
    )
    await session.commit()
    return list(rows)


async def deliver(session: AsyncSession, http: httpx.AsyncClient, delivery_id: int) -> bool:
    """Returns True when finished (delivered, or out of attempts)."""
    row = (
        await session.execute(
            text(
                "SELECT d.id, d.event, d.payload, d.attempts, h.url, h.secret_enc "
                "FROM webhook_deliveries d JOIN webhooks h ON h.id = d.webhook_id "
                "WHERE d.id = :id AND d.delivered_at IS NULL"
            ),
            {"id": delivery_id},
        )
    ).first()
    if row is None:
        return True
    secret = mailer.decrypt(row.secret_enc) or ""
    body = json.dumps({"type": row.event, "data": row.payload}).encode()
    msg_id = f"msg_{row.id}"
    timestamp = int(time.time())
    status_code, error = None, None
    try:
        response = await http.post(
            row.url,
            content=body,
            headers={
                "content-type": "application/json",
                "webhook-id": msg_id,
                "webhook-timestamp": str(timestamp),
                "webhook-signature": sign(secret, msg_id, timestamp, body),
            },
            timeout=10,
        )
        status_code = response.status_code
    except httpx.HTTPError as exc:
        error = str(exc)[:300]
    ok = status_code is not None and 200 <= status_code < 300
    await session.execute(
        text(
            "UPDATE webhook_deliveries SET attempts = attempts + 1, status_code = :code, "
            "error = :error, delivered_at = CASE WHEN :ok THEN NOW() END WHERE id = :id"
        ),
        {"code": status_code, "error": error, "ok": ok, "id": delivery_id},
    )
    await session.commit()
    return ok or row.attempts + 1 >= MAX_ATTEMPTS
