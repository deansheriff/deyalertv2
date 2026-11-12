"""Who may do what in the admin panel.

Super admins hold every permission and are the only ones who see the audit log, manage
admins and edit this grid. The other panel roles start from DEFAULTS, which a super admin
can change in Roles & permissions.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import app_settings

ROLE_PERMISSIONS_KEY = "role_permissions"
PANEL_ROLES = ("super_admin", "admin", "moderator", "verifier", "partner")
GRANTABLE_ROLES = ("admin", "moderator", "verifier", "partner")
STAFF_ROLES_ADMIN_MAY_ASSIGN = ("moderator", "verifier", "partner")
ROLE_LABELS = {
    "super_admin": "Super admin",
    "admin": "Admin",
    "moderator": "Moderator",
    "verifier": "Verifier",
    "partner": "Partner",
    "member": "Member",
}


@dataclass(frozen=True)
class Permission:
    key: str
    group: str
    label: str
    sensitive: bool = False


PERMISSIONS = [
    Permission("view_dashboard", "Overview", "View dashboard"),
    Permission("view_map", "Overview", "Live map"),
    Permission("view_analytics", "Overview", "Analytics and reports"),
    Permission("view_incidents", "Moderation", "View incidents"),
    Permission("view_queue", "Moderation", "View moderation queue"),
    Permission("act_on_incidents", "Moderation", "Act on incidents"),
    Permission("moderate_media", "Moderation", "Moderate photos"),
    Permission("bulk_actions", "Moderation", "Bulk actions"),
    Permission("merge_incidents", "Moderation", "Merge duplicates"),
    Permission("add_notes", "Moderation", "Internal notes and verifier updates"),
    Permission("view_users", "People", "View user details", sensitive=True),
    Permission("sanction_users", "People", "Suspend users and decide appeals"),
    Permission("adjust_reputation", "People", "Adjust reputation"),
    Permission("manage_verifiers", "People", "Manage verifiers and applications"),
    Permission("manage_staff", "People", "Manage staff (not admins)"),
    Permission("support_inbox", "People", "Support inbox"),
    Permission("reveal_location", "Privacy", "Reveal exact location", sensitive=True),
    Permission("lookup_reporter", "Privacy", "Look up reporter", sensitive=True),
    Permission("privacy_requests", "Privacy", "Handle privacy requests", sensitive=True),
    Permission("write_broadcasts", "Communication", "Write broadcasts"),
    Permission("approve_broadcasts", "Communication", "Approve broadcasts", sensitive=True),
    Permission("edit_templates", "Communication", "Edit message templates"),
    Permission("post_announcements", "Communication", "Post announcements"),
    Permission("manage_partners", "Platform", "Partners and webhooks"),
    Permission("manage_areas", "Platform", "Areas and boundaries"),
    Permission("manage_incident_types", "Platform", "Incident types and word filter"),
    Permission("manage_settings", "Platform", "Settings, kill switches and limits", sensitive=True),
    Permission("view_health", "Platform", "System health"),
]
ALL = frozenset(permission.key for permission in PERMISSIONS)

DEFAULTS: dict[str, frozenset[str]] = {
    "admin": ALL,
    "moderator": frozenset(
        {
            "view_dashboard",
            "view_map",
            "view_analytics",
            "view_incidents",
            "view_queue",
            "act_on_incidents",
            "moderate_media",
            "bulk_actions",
            "merge_incidents",
            "add_notes",
            "view_users",
            "reveal_location",
            "sanction_users",
            "support_inbox",
            "write_broadcasts",
            "view_health",
        }
    ),
    "verifier": frozenset({"view_dashboard", "view_map", "view_incidents", "add_notes"}),
    "partner": frozenset({"view_dashboard", "view_map", "view_analytics", "view_incidents"}),
}


async def grid(session: AsyncSession) -> dict[str, frozenset[str]]:
    stored = await app_settings.get(session, ROLE_PERMISSIONS_KEY, {}) or {}
    return {
        role: frozenset(stored[role]) & ALL if role in stored else DEFAULTS[role]
        for role in GRANTABLE_ROLES
    }


async def permissions_for(session: AsyncSession, role: str) -> frozenset[str]:
    if role == "super_admin":
        return ALL
    if role not in GRANTABLE_ROLES:
        return frozenset()
    return (await grid(session))[role]


def groups() -> list[tuple[str, list[Permission]]]:
    ordered: dict[str, list[Permission]] = {}
    for permission in PERMISSIONS:
        ordered.setdefault(permission.group, []).append(permission)
    return list(ordered.items())


def may_assign(actor_role: str, role: str) -> bool:
    """Only super admins create or change admins and super admins."""
    if actor_role == "super_admin":
        return role in (*PANEL_ROLES, "member")
    return role in (*STAFF_ROLES_ADMIN_MAY_ASSIGN, "member")
