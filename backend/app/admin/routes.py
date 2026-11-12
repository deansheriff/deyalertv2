"""Mounts the admin panel. Each feature area lives in its own module on the shared router."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.admin import (  # noqa: F401  (importing registers each module's routes)
    comms,
    core,
    insights,
    moderation,
    network,
    ops,
    people,
    staff,
)
from app.admin.auth import COOKIE, AdminForbidden, AdminRedirect
from app.admin.common import router, set_admin_cookie

FORBIDDEN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not allowed · Dey Alert</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0A0C0A;color:#F2F5F3;
font:15px/1.5 Inter,system-ui,sans-serif}div{max-width:420px;text-align:center;padding:24px}
h1{font-size:24px;margin:0 0 8px}p{color:#B4BCB4}
a{display:inline-block;margin-top:12px;padding:10px 20px;
border-radius:999px;background:#A6EF1F;color:#0A0C0A;font-weight:700;text-decoration:none}</style></head>
<body><div><h1>You don't have access to this</h1>
<p>Your role doesn't include this page or action. Ask a super admin if you need it.</p>
<a href="/admin">Back to the dashboard</a></div></body></html>"""


def install(app: FastAPI) -> None:
    app.include_router(router)

    @app.exception_handler(AdminRedirect)
    async def _sign_in(_: Request, __: AdminRedirect) -> RedirectResponse:
        response = RedirectResponse("/admin/login", status_code=303)
        response.delete_cookie(COOKIE, path="/admin")
        return response

    @app.exception_handler(AdminForbidden)
    async def _forbidden(_: Request, __: AdminForbidden) -> HTMLResponse:
        return HTMLResponse(FORBIDDEN_PAGE, status_code=403)

    @app.middleware("http")
    async def _refresh_admin_cookie(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Slides the idle timeout forward unless the response already set or cleared it."""
        response = await call_next(request)
        token = getattr(request.state, "admin_cookie", None)
        if token and not any(
            header.startswith(f"{COOKIE}=") for header in response.headers.getlist("set-cookie")
        ):
            set_admin_cookie(response, COOKIE, token)
        return response
