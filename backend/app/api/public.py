from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.services import sos as service

router = APIRouter(tags=["public"], include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _trail_points(pings: list[dict[str, Any]]) -> str:
    lats = [p["lat"] for p in pings]
    lngs = [p["lng"] for p in pings]
    span = max(max(lats) - min(lats), max(lngs) - min(lngs), 0.0005)
    points = []
    for ping in pings:
        x = 20 + (ping["lng"] - min(lngs)) / span * 280
        y = 180 - (ping["lat"] - min(lats)) / span * 160
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


@router.get("/s/{token}", response_class=HTMLResponse)
async def tracking(
    request: Request, token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    view = await service.tracking_view(session, token)
    if view is None or not view["active"]:
        safe = bool(view and view["safe"])
        return templates.TemplateResponse(
            request, "tracking_ended.html", {"safe": safe}, headers=NO_STORE
        )
    last = view["pings"][-1]
    return templates.TemplateResponse(
        request,
        "tracking.html",
        {**view, "last": last, "trail": _trail_points(view["pings"])},
        headers=NO_STORE,
    )


async def _contact(session: AsyncSession, token: str) -> Any:
    return (
        await session.execute(
            text(
                "SELECT c.id, c.consent_status, COALESCE(p.display_name, 'A Dey Alert user') "
                "AS owner FROM trusted_contacts c JOIN profiles p ON p.id = c.user_id "
                "WHERE c.consent_token_hash = :hash"
            ),
            {"hash": service.hash_token(token)},
        )
    ).first()


@router.get("/c/{token}", response_class=HTMLResponse)
async def opt_in(
    request: Request, token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    contact = await _contact(session, token)
    if contact is None:
        return templates.TemplateResponse(
            request, "tracking_ended.html", {"safe": False}, status_code=404, headers=NO_STORE
        )
    return templates.TemplateResponse(
        request,
        "opt_in.html",
        {"owner": contact.owner, "status": contact.consent_status},
        headers=NO_STORE,
    )


@router.post("/c/{token}", response_class=HTMLResponse)
async def opt_in_answer(
    request: Request,
    token: str,
    answer: str = Form(),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    contact = await _contact(session, token)
    if contact is None or answer not in ("accept", "decline", "withdraw"):
        return templates.TemplateResponse(
            request, "tracking_ended.html", {"safe": False}, status_code=404, headers=NO_STORE
        )
    status = {"accept": "accepted", "decline": "declined", "withdraw": "revoked"}[answer]
    await session.execute(
        text(
            "UPDATE trusted_contacts SET consent_status = :status, consent_at = NOW() "
            "WHERE id = :id"
        ),
        {"status": status, "id": contact.id},
    )
    await session.commit()
    return templates.TemplateResponse(
        request, "opt_in.html", {"owner": contact.owner, "status": status}, headers=NO_STORE
    )
