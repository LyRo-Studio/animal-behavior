from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved from this file's own location, not the process's working
# directory — `env_file` in pydantic-settings is otherwise relative to the
# CWD, so running commands from `backend/` vs. the repo root would silently
# pick up a different (or no) `.env`. Repo root first, `backend/.env`
# second (so a backend-local override wins if both exist).
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime configuration, sourced from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # Safe, non-production placeholders (same spirit as .env.example) so
    # `pytest`/`ruff` work standalone with no .env present at all — never
    # used against a real database in Docker, where DATABASE_URL is always
    # supplied explicitly (see docker-compose.yml).
    database_url: str = (
        "postgresql+psycopg://animal_behavior:animal_behavior@localhost:5432/animal_behavior"
    )
    cors_origins: str = "http://localhost:5173"

    # Same "safe placeholder for standalone pytest/ruff" spirit as above —
    # docker-compose.yml requires all four of these explicitly via `:?` and
    # never falls back to these values in a deployed environment.
    jwt_secret_key: str = "dev-only-insecure-secret-do-not-use-in-production"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    first_admin_email: str = "admin@vives.be"
    first_admin_password: str = "dev-only-insecure-password"

    # Ticket #4 (Admin adds a User): invite links, and the mail transport
    # that sends them. SMTP host has no default: an unset SMTP_HOST means
    # "no real mail server configured", which `get_mail_transport` reads as
    # "log instead of sending" — a safe dev/test fallback, not a broken
    # deployment (docker-compose.yml doesn't require these).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "no-reply@vives.be"
    smtp_use_tls: bool = True
    invite_token_expire_hours: int = 72
    # Used to build the invite link emailed to a new User — not itself a
    # secret, so (like cors_origins) it's safe to default to the frontend's
    # local dev origin.
    frontend_base_url: str = "http://localhost:5173"

    # Ticket #5 (Forgot-password reset): shorter-lived than an invite link,
    # since it's requested and used in one sitting rather than waiting on
    # an Admin-invited User to first check their email.
    password_reset_token_expire_hours: int = 1

    # Ticket #7 (Brute-force throttling): fixed-window limits on the
    # unauthenticated auth endpoints — see app/services/rate_limit.py and
    # app/api/auth.py.
    #
    # Login/refresh only count *failed* attempts (a successful login/
    # refresh never consumes the budget) — so many legitimate users behind
    # one shared IP (a campus NAT, say) succeeding normally can never
    # exhaust it; only a run of failures, the actual brute-force signal,
    # does. Login additionally tracks failures per-account (by email), on
    # top of per-IP: safe to do now that only failures count (a real
    # user's occasional typo stays far under the threshold), and it closes
    # the gap a pure per-IP limit leaves open against an attacker
    # distributing attempts across several source IPs at one victim
    # account.
    #
    # Forgot-password counts *every* request (there's no failure/success
    # distinction meaningful to a caller — the response is always the same
    # empty 204). Its per-email limit is safe in a way login's per-account
    # one wouldn't be if it counted every request too: exceeding it never
    # blocks logging in, only requesting more reset emails for a bit.
    login_rate_limit_max_failed_attempts_per_ip: int = 10
    login_rate_limit_max_failed_attempts_per_email: int = 10
    login_rate_limit_window_seconds: int = 300
    refresh_rate_limit_max_failed_attempts_per_ip: int = 30
    refresh_rate_limit_window_seconds: int = 300
    forgot_password_rate_limit_max_attempts_per_ip: int = 10
    forgot_password_rate_limit_max_attempts_per_email: int = 5
    forgot_password_rate_limit_window_seconds: int = 900

    # Ticket #18 (S3 access foundation, part of #17's media browser): the
    # bucket holding Tests/Cuts/Datasets — see CONTEXT.md's "Media browser"
    # decisions and app/services/s3_client.py. Unlike database_url/cors_origins
    # above, these have no placeholder default: an unset value means "S3 not
    # configured", which `get_s3_client` treats as a hard failure rather than
    # falling through to boto3's ambient credential chain against whatever
    # bucket/endpoint ends up resolved — there's no safe "log instead of"
    # fallback for object storage the way there is for SMTP below.
    # docker-compose.yml requires all four explicitly via `:?`.
    s3_bucket: str | None = None
    s3_endpoint: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    s3_addressing_style: str = "path"

    # Ticket #21 (Play/download a Cut): short-lived, single-Cut-scoped,
    # single-action-scoped tokens for the backend-proxied streaming
    # endpoint — see app/core/security.py's create_media_token and
    # docs/adr/0002-media-access-tokens-in-url.md. Issuance is rate
    # limited per Account (CONTEXT.md's "Media browser — abuse
    # protection" decision), reusing app/services/rate_limit.py.
    media_token_expire_minutes: int = 15
    media_token_rate_limit_max_attempts_per_account: int = 30
    media_token_rate_limit_window_seconds: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS_ORIGINS as a comma-separated env var, split into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
