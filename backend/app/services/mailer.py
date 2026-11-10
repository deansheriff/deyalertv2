"""Sends email through the SMTP server an admin set up in the panel."""

import asyncio
import base64
import hashlib
import smtplib
import ssl
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services import app_settings

SECURITY_MODES = ("starttls", "ssl", "none")
TIMEOUT_SECONDS = 10
_templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent.parent / "templates" / "email"),
    autoescape=select_autoescape(["html"]),
)


class MailError(Exception):
    """The email could not be sent. The message is safe to show an admin."""


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    from_name: str = "Dey Alert"
    security: str = "starttls"


def _fernet() -> Fernet:
    # Changing ADMIN_SESSION_SECRET makes the saved SMTP password unreadable; enter it again.
    digest = hashlib.sha256(b"smtp:" + get_settings().admin_session_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str | None:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


async def load(session: AsyncSession) -> SmtpConfig | None:
    stored = await app_settings.get(session, app_settings.SMTP)
    if not stored or not stored.get("host"):
        return None
    password = decrypt(stored["password"]) if stored.get("password") else ""
    if password is None:
        return None
    return SmtpConfig(**{**stored, "password": password})


async def save(session: AsyncSession, config: SmtpConfig, by: Any) -> None:
    stored = asdict(config)
    stored["password"] = encrypt(config.password) if config.password else ""
    await app_settings.put(session, app_settings.SMTP, stored, by)


def summary(stored: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the panel shows: everything except the password."""
    if not stored:
        return None
    shown = {key: value for key, value in stored.items() if key != "password"}
    return {**shown, "has_password": bool(stored.get("password"))}


def _send_sync(config: SmtpConfig, to: str, subject: str, body: str, html: str | None) -> None:
    message = EmailMessage()
    message["From"] = formataddr((config.from_name, config.from_email))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    try:
        if config.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                config.host, config.port, timeout=TIMEOUT_SECONDS, context=context
            )
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
        with server:
            if config.security == "starttls":
                server.starttls(context=context)
            if config.username:
                server.login(config.username, config.password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise MailError("The SMTP server rejected the username or password.") from error
    except smtplib.SMTPException as error:
        raise MailError(f"The SMTP server refused the message: {error}") from error
    except (OSError, TimeoutError) as error:
        raise MailError(f"Could not connect to {config.host}:{config.port}: {error}") from error


async def send(
    config: SmtpConfig, to: str, subject: str, body: str, html: str | None = None
) -> None:
    await asyncio.to_thread(_send_sync, config, to, subject, body, html)


SUBJECTS = {
    "signup": "Your Dey Alert sign-in code",
    "magiclink": "Your Dey Alert sign-in code",
    "reauthentication": "Your Dey Alert confirmation code",
    "recovery": "Your Dey Alert account recovery code",
    "email_change": "Confirm your new Dey Alert email",
    "invite": "You have been invited to Dey Alert",
}


def code_email(action: str, code: str) -> tuple[str, str, str]:
    """Subject, plain text and HTML for a Supabase sign-in code."""
    subject = SUBJECTS.get(action, "Your Dey Alert code")
    text = (
        f"Your Dey Alert code is {code}.\n\n"
        "Enter it in the app to continue. It expires soon. Do not share it with anyone, "
        "including people who say they work for Dey Alert.\n\n"
        "If you did not ask for this code, you can ignore this email."
    )
    html = _templates.get_template("code.html").render(subject=subject, code=code)
    return subject, text, html
