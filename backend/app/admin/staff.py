"""Staff accounts, area scopes and the roles and permissions grid."""

from typing import Any
from uuid import UUID

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth, rbac
from app.admin.auth import Admin, AdminForbidden
from app.admin.common import back, message, page, record, router
from app.core.db import get_session
from app.services import app_settings
from app.services import staff as staff_service

TABS = ("active", "invited", "disabled")


async def _areas(session: AsyncSession) -> tuple[list[Any], list[Any]]:
    lgas = (await session.execute(text("SELECT id, name FROM lgas ORDER BY name"))).all()
    wards = (
        await session.execute(
            text(
                "SELECT w.id, w.name, l.name AS lga FROM wards w JOIN lgas l ON l.id = w.lga_id "
                "ORDER BY l.name, w.name"
            )
        )
    ).all()
    return lgas, wards


@router.get("/staff", response_class=HTMLResponse)
async def staff_page(
    request: Request,
    tab: str = "active",
    admin: Admin = Depends(auth.can("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tab = tab if tab in TABS else "active"
    rows = await staff_service.staff_list(session, tab)
    counts = {name: len(await staff_service.staff_list(session, name)) for name in TABS}
    logins = (
        await session.execute(
            text(
                "SELECT s.created_at, s.email, s.outcome, s.ip, p.display_name FROM staff_logins s "
                "LEFT JOIN profiles p ON p.id = s.profile_id ORDER BY s.created_at DESC LIMIT 25"
            )
        )
    ).all()
    partners = (
        await session.execute(text("SELECT id, name FROM partners WHERE is_active ORDER BY name"))
    ).all()
    lgas, wards = await _areas(session)
    assignable = [role for role in rbac.PANEL_ROLES if rbac.may_assign(admin.role, role)]
    return page(
        request,
        admin,
        "staff.html",
        tab=tab,
        rows=rows,
        counts=counts,
        logins=logins,
        partners=partners,
        lgas=lgas,
        wards=wards,
        assignable=assignable,
    )


@router.post("/staff/invite", response_class=HTMLResponse)
async def invite_staff(
    request: Request,
    email: str = Form(),
    name: str = Form(default=""),
    role: str = Form(),
    partner_id: str = Form(default=""),
    lga_ids: list[int] = Form(default=[]),
    ward_ids: list[int] = Form(default=[]),
    require_two_step: bool = Form(default=False),
    admin: Admin = Depends(auth.act("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if role not in rbac.PANEL_ROLES or not rbac.may_assign(admin.role, role):
        raise AdminForbidden
    partner = int(partner_id) if partner_id.strip() else None
    if role == "partner" and partner is None:
        return message(
            request,
            admin,
            "Choose a partner",
            "Partner accounts must belong to a partner organisation.",
            "/admin/staff",
        )
    try:
        invite = await staff_service.invite(
            session,
            email=email,
            name=name,
            role=role,
            invited_by=admin.profile.id,
            require_two_step=require_two_step,
            partner_id=partner if role == "partner" else None,
            lga_ids=lga_ids,
            ward_ids=ward_ids,
        )
    except staff_service.StaffError as error:
        return message(request, admin, "Could not invite", str(error), "/admin/staff")
    await record(
        session,
        admin,
        request,
        "staff_invite",
        "profile",
        str(invite.user_id),
        details={
            "email": email.strip().lower(),
            "role": role,
            "areas": len(lga_ids) + len(ward_ids),
        },
    )
    await session.commit()
    return page(
        request,
        admin,
        "staff_invited.html",
        email=email.strip().lower(),
        role=role,
        invite=invite,
    )


async def _target(session: AsyncSession, admin: Admin, user_id: UUID) -> Any:
    """Staff may not change themselves, or anyone above what they could assign."""
    row = await auth.staff_row(session, user_id)
    if row is None or row.id == admin.profile.id or not rbac.may_assign(admin.role, row.role):
        raise AdminForbidden
    return row


async def _keeps_a_super_admin(session: AsyncSession, row: Any) -> bool:
    if row.role != "super_admin" or not row.is_active:
        return True
    return await staff_service.active_super_admins(session) > 1


@router.post("/staff/{user_id}/role", response_model=None)
async def change_staff_role(
    request: Request,
    user_id: UUID,
    role: str = Form(),
    admin: Admin = Depends(auth.act("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | HTMLResponse:
    row = await _target(session, admin, user_id)
    if not rbac.may_assign(admin.role, role):
        raise AdminForbidden
    if role != "super_admin" and not await _keeps_a_super_admin(session, row):
        return message(
            request,
            admin,
            "Not changed",
            "There must always be at least one active super admin.",
            "/admin/staff",
        )
    await session.execute(
        text(
            "UPDATE profiles SET role = :role, session_version = session_version + 1 "
            "WHERE id = :id"
        ),
        {"role": role, "id": user_id},
    )
    await record(
        session,
        admin,
        request,
        f"role_change:{role}",
        "profile",
        str(user_id),
        details={"from": row.role, "to": role},
    )
    await session.commit()
    return back("/admin/staff")


@router.post("/staff/{user_id}/scope")
async def change_scope(
    request: Request,
    user_id: UUID,
    lga_ids: list[int] = Form(default=[]),
    ward_ids: list[int] = Form(default=[]),
    admin: Admin = Depends(auth.act("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await _target(session, admin, user_id)
    await staff_service.set_scope(session, user_id, lga_ids, ward_ids)
    await record(
        session,
        admin,
        request,
        "staff_scope",
        "profile",
        str(user_id),
        details={"lgas": lga_ids, "wards": ward_ids},
    )
    await session.commit()
    return back("/admin/staff")


@router.post("/staff/{user_id}/reset", response_class=HTMLResponse)
async def reset_staff_password(
    request: Request,
    user_id: UUID,
    admin: Admin = Depends(auth.act("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    row = await _target(session, admin, user_id)
    try:
        password = await staff_service.reset_password(session, user_id)
    except staff_service.StaffError as error:
        return message(request, admin, "Could not reset", str(error), "/admin/staff")
    await record(session, admin, request, "staff_password_reset", "profile", str(user_id))
    await session.commit()
    return message(
        request,
        admin,
        f"New one-time password for {row.email}",
        f"{password}  ·  They are signed out everywhere and must choose a new password at sign-in. "
        "Share this privately; it is not shown again.",
        "/admin/staff",
    )


@router.post("/staff/{user_id}/sign-out")
async def sign_out_staff(
    request: Request,
    user_id: UUID,
    admin: Admin = Depends(auth.act("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await _target(session, admin, user_id)
    await staff_service.sign_out_everywhere(session, user_id)
    await record(session, admin, request, "staff_sign_out_everywhere", "profile", str(user_id))
    await session.commit()
    return back("/admin/staff")


@router.post("/staff/{user_id}/status", response_model=None)
async def set_staff_status(
    request: Request,
    user_id: UUID,
    status: str = Form(),
    admin: Admin = Depends(auth.act("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | HTMLResponse:
    """disable: blocks sign-in everywhere. enable: undoes it. remove: back to a member account."""
    row = await _target(session, admin, user_id)
    if status not in ("disable", "enable", "remove"):
        raise AdminForbidden
    if status != "enable" and not await _keeps_a_super_admin(session, row):
        return message(
            request,
            admin,
            "Not changed",
            "There must always be at least one active super admin.",
            "/admin/staff",
        )
    sql = {
        "disable": "UPDATE profiles SET is_active = false, "
        "session_version = session_version + 1 WHERE id = :id",
        "enable": "UPDATE profiles SET is_active = true WHERE id = :id",
        "remove": "UPDATE profiles SET role = 'member', partner_id = NULL, "
        "session_version = session_version + 1 WHERE id = :id",
    }[status]
    await session.execute(text(sql), {"id": user_id})
    if status == "remove":
        await staff_service.set_scope(session, user_id, [], [])
    await record(
        session,
        admin,
        request,
        f"staff_{status}",
        "profile",
        str(user_id),
        details={"role": row.role},
    )
    await session.commit()
    return back("/admin/staff?tab=disabled" if status == "disable" else "/admin/staff")


@router.post("/staff/{user_id}/two-step")
async def toggle_two_step(
    request: Request,
    user_id: UUID,
    admin: Admin = Depends(auth.act("manage_staff")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    row = await _target(session, admin, user_id)
    await session.execute(
        text("UPDATE profiles SET require_two_step = NOT require_two_step WHERE id = :id"),
        {"id": user_id},
    )
    await record(
        session,
        admin,
        request,
        f"staff_two_step:{'off' if row.require_two_step else 'on'}",
        "profile",
        str(user_id),
    )
    await session.commit()
    return back("/admin/staff")


# Roles and permissions (super admins only)


@router.get("/roles", response_class=HTMLResponse)
async def roles_page(
    request: Request,
    saved: bool = False,
    admin: Admin = Depends(auth.require_super),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    return page(
        request,
        admin,
        "roles.html",
        grid=await rbac.grid(session),
        groups=rbac.groups(),
        roles=rbac.GRANTABLE_ROLES,
        defaults=rbac.DEFAULTS,
        notice="Permissions saved. They apply on each person's next page load." if saved else None,
    )


@router.post("/roles")
async def save_roles(
    request: Request,
    admin: Admin = Depends(auth.require_super_post),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    form = await request.form()
    before = await rbac.grid(session)
    if form.get("reset") == "defaults":
        stored = {role: sorted(rbac.DEFAULTS[role]) for role in rbac.GRANTABLE_ROLES}
    else:
        stored = {
            role: sorted(key for key in rbac.ALL if form.get(f"{role}:{key}") == "on")
            for role in rbac.GRANTABLE_ROLES
        }
    changes = {
        role: {
            "granted": sorted(set(stored[role]) - before[role]),
            "removed": sorted(before[role] - set(stored[role])),
        }
        for role in rbac.GRANTABLE_ROLES
        if set(stored[role]) != before[role]
    }
    await app_settings.put(session, rbac.ROLE_PERMISSIONS_KEY, stored, admin.profile.id)
    await record(
        session, admin, request, "permissions_change", "settings", "roles", details=changes
    )
    await session.commit()
    return back("/admin/roles?saved=1")
