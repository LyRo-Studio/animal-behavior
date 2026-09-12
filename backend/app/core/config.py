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

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS_ORIGINS as a comma-separated env var, split into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
