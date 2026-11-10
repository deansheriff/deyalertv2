from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    environment: str = "dev"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:54329/deyalert"
    redis_url: str = "redis://localhost:63790/0"

    public_base_url: str = "http://127.0.0.1:8010"

    supabase_url: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_secret: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_sms_hook_secret: str = ""
    supabase_email_hook_secret: str = ""
    media_quarantine_bucket: str = "quarantine"
    media_public_bucket: str = "incident-media"

    fcm_service_account_json: str = ""
    termii_api_key: str = ""
    termii_sender_id: str = ""
    termii_base_url: str = "https://v3.api.termii.com"
    sentry_dsn: str = ""

    # Each deploy creates this panel admin if the account does not exist yet.
    admin_seed_email: str = ""
    admin_seed_password: str = ""

    corroboration_radius_m: int = 2000
    corroboration_threshold: int = 3
    corroboration_hourly_cap: int = 20
    flag_hide_threshold: float = 5.0
    flag_radius_m: int = 10000
    ttl_override_minutes: int | None = None
    daily_report_cap: int = 20
    new_account_daily_report_cap: int = 3
    otp_daily_cap: int = 2000
    otp_per_phone_hourly_cap: int = 5
    email_daily_cap: int = 2000
    email_per_address_hourly_cap: int = 5
    sms_daily_cap: int = 2000
    sms_fallback_enabled: bool = False
    push_hourly_cap: int = 5
    admin_session_secret: str = "change-me-in-production-" + "x" * 16


REQUIRED_IN_PRODUCTION = (
    "public_base_url",
    "supabase_url",
    "supabase_anon_key",
    "supabase_service_role_key",
    "supabase_email_hook_secret",
    "fcm_service_account_json",
    "termii_api_key",
    "termii_sender_id",
)
MIN_SECRET_LENGTH = 32


def production_problems(settings: Settings) -> list[str]:
    if settings.environment != "production":
        return []
    problems = [
        f"{name.upper()} is required"
        for name in REQUIRED_IN_PRODUCTION
        if not getattr(settings, name)
    ]
    if not settings.supabase_jwks_url and not settings.supabase_jwt_secret:
        problems.append("SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET is required")
    if settings.supabase_jwt_secret and len(settings.supabase_jwt_secret) < MIN_SECRET_LENGTH:
        problems.append(f"SUPABASE_JWT_SECRET must be at least {MIN_SECRET_LENGTH} chars")
    if (
        len(settings.admin_session_secret) < MIN_SECRET_LENGTH
        or settings.admin_session_secret.startswith("change-me-in-production")
    ):
        problems.append(f"ADMIN_SESSION_SECRET must be at least {MIN_SECRET_LENGTH} random chars")
    if not settings.public_base_url.startswith("https://"):
        problems.append("PUBLIC_BASE_URL must use https")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
