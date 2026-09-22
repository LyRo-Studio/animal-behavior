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

    # Ticket #72: the app no longer authenticates anyone itself — Mechatronics
    # does (docs/adr/0004-trust-mega-tronics-remove-application-auth.md) — and
    # forwards the person's identity in an HTTP header, which the backend
    # reads purely for attribution (never authorization). The real header
    # name is unknown until `dogtrace-app` is live behind it, so it's
    # configuration, not code; unset (or empty — `.env` may carry
    # `IDENTITY_HEADER_NAME=` with nothing after it) means "read no identity
    # at all", which is also what local development (no Mechatronics in
    # front) runs with.
    identity_header_name: str | None = None

    # Signs the short-lived Cut streaming/download tokens (ADR-0002) — the
    # only thing left the backend signs. Same "safe placeholder for
    # standalone pytest/ruff" spirit as database_url above; docker-compose.yml
    # requires it explicitly via `:?` and never falls back to this value in a
    # deployed environment.
    media_token_secret_key: str = "dev-only-insecure-secret-do-not-use-in-production"

    # Ticket #18 (S3 access foundation, part of #17's media browser): the
    # bucket holding Tests/Cuts/Datasets — see CONTEXT.md's "Media browser"
    # decisions and app/services/s3_client.py. Unlike database_url/cors_origins
    # above, these have no placeholder default: an unset value means "S3 not
    # configured", which `get_s3_client` treats as a hard failure rather than
    # falling through to boto3's ambient credential chain against whatever
    # bucket/endpoint ends up resolved — there's no safe "log instead of"
    # fallback for object storage.
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
    # limited per identity (CONTEXT.md's "Media browser — abuse
    # protection" decision), reusing app/services/rate_limit.py.
    media_token_expire_minutes: int = 15
    media_token_rate_limit_max_attempts_per_identity: int = 30
    media_token_rate_limit_window_seconds: int = 300

    # Ticket #22 (Inspect probed media info for a Cut): the `ffprobe`
    # binary used to compute duration/resolution/codec — see
    # app/services/media_prober.py. A bare command name (resolved via
    # PATH) is a safe default since ffprobe ships in the backend Docker
    # image; override only if it's installed somewhere non-standard.
    ffprobe_path: str = "ffprobe"
    ffprobe_timeout_seconds: int = 30

    # A cache miss here does a full S3 download plus an ffprobe subprocess
    # call — the same class of "expensive operation" media_token issuance
    # above is rate limited for, and ENGINEERING-STANDARDS.md's DoS section
    # requires guarding against. Reuses the same rate_limit.py machinery,
    # keyed per identity like the token endpoint.
    cut_info_rate_limit_max_attempts_per_identity: int = 30
    cut_info_rate_limit_window_seconds: int = 300

    # Ticket #89 (Multi-Test job foundation, part of issue #88's Feature B):
    # POST /analyses is now a more expensive thing to trigger repeatedly
    # than a plain single-Test submission was (up to 10 Tests/300 videos
    # wholesale-derived per call) — rate limited per identity, same
    # rate_limit.py machinery as media-token issuance and /media/cuts/info.
    # A generous budget: sized not to affect normal single-job usage
    # (CONTEXT.md's Feature B decision), unlike those two which guard a
    # much cheaper-to-legitimately-repeat action.
    create_analysis_rate_limit_max_attempts_per_identity: int = 10
    create_analysis_rate_limit_window_seconds: int = 300

    # Ticket #47 (Analysis worker, part of #44): polling interval and the
    # job-scoped temp-file root for the separate `worker` Docker service —
    # see CONTEXT.md's "Analysis worker" decisions. Settings is reused as-is
    # by the worker (no FastAPI dependency of its own), same as
    # database_url/s3_* above; these two are meaningless to the backend
    # process itself. The default lives under `/data` — the one directory
    # the `lynndelaere/dogtrace:1.0.0` base image's own non-root `dogtrace`
    # user (uid 10001, which the worker container runs as) already owns;
    # `/var/lib/...` or similar would need an extra chown step to be
    # writable by that user at all.
    analysis_worker_poll_interval_seconds: float = 5.0
    analysis_worker_work_dir: str = "/data/analysis-worker"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS_ORIGINS as a comma-separated env var, split into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
