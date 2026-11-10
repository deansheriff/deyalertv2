from app.core.config import Settings, production_problems


def complete(**overrides):
    values = {
        "environment": "production",
        "public_base_url": "https://api.example.test",
        "supabase_url": "https://x.supabase.co",
        "supabase_jwks_url": "https://x.supabase.co/jwks",
        "supabase_anon_key": "a",
        "supabase_service_role_key": "b",
        "supabase_sms_hook_secret": "c",
        "fcm_service_account_json": "{}",
        "termii_api_key": "d",
        "termii_sender_id": "DeyAlert",
        "admin_session_secret": "s" * 40,
    }
    return Settings(**{**values, **overrides})


def test_complete_production_config_passes():
    assert production_problems(complete()) == []


def test_development_needs_nothing():
    assert production_problems(Settings(environment="dev")) == []


def test_missing_secret_is_reported():
    assert "TERMII_API_KEY is required" in production_problems(complete(termii_api_key=""))


def test_default_session_secret_is_rejected():
    problems = production_problems(complete(admin_session_secret="change-me-in-production-" + "x" * 16))
    assert any("ADMIN_SESSION_SECRET" in problem for problem in problems)


def test_self_hosted_secret_replaces_the_key_list():
    assert production_problems(complete(supabase_jwks_url="", supabase_jwt_secret="j" * 40)) == []


def test_some_way_to_check_sign_in_is_required():
    problems = production_problems(complete(supabase_jwks_url=""))
    assert "SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET is required" in problems


def test_short_jwt_secret_is_rejected():
    problems = production_problems(complete(supabase_jwt_secret="short"))
    assert any("SUPABASE_JWT_SECRET" in problem for problem in problems)


def test_tracking_links_must_be_https():
    problems = production_problems(complete(public_base_url="http://api.example.test"))
    assert any("https" in problem for problem in problems)
