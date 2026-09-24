from pathlib import Path

from pydantic import Field, SecretStr
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
    # Ticket #128: it embeds the DB password, so it's kept out of
    # `repr(settings)` — but stays a plain `str`, not `SecretStr` like the
    # credentials below, since SQLAlchemy (app/db/session.py) and Alembic
    # (alembic/env.py) consume it as one.
    database_url: str = Field(
        default="postgresql+psycopg://animal_behavior:animal_behavior@localhost:5432/animal_behavior",
        repr=False,
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
    # deployed environment. A `SecretStr` (ticket #128) so `repr(settings)`
    # — in an exception message, a log line, a debugger — never shows it;
    # readers unwrap it with `.get_secret_value()` (app/core/security.py).
    media_token_secret_key: SecretStr = SecretStr(
        "dev-only-insecure-secret-do-not-use-in-production"
    )

    # Ticket #18 (S3 access foundation, part of #17's media browser): the
    # bucket holding Tests/Cuts/Datasets — see CONTEXT.md's "Media browser"
    # decisions and app/services/s3_client.py. Unlike database_url/cors_origins
    # above, these have no placeholder default: an unset value means "S3 not
    # configured", which `get_s3_client` treats as a hard failure rather than
    # falling through to boto3's ambient credential chain against whatever
    # bucket/endpoint ends up resolved — there's no safe "log instead of"
    # fallback for object storage.
    # docker-compose.yml requires all four explicitly via `:?`. The two
    # credentials are `SecretStr` for the same reason as
    # media_token_secret_key above, unwrapped only in `get_s3_client`.
    s3_bucket: str | None = None
    s3_endpoint: str | None = None
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
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

    # Ticket #94 (Cutting job foundation, part of issue #93 / Feature C):
    # where uploaded source videos are streamed to before cutting — a
    # scoped local temp directory, never S3 (CONTEXT.md: "Source videos
    # never reach S3 — not even transiently"). Same "/data" placement as
    # analysis_worker_work_dir above, for the same reason (the base image's
    # non-root user already owns it).
    cutting_upload_temp_dir: str = "/data/cutting-uploads"
    # Total bytes retained across every not-yet-consumed upload before a
    # new upload is rejected outright (CONTEXT.md's Feature C "Upload
    # mechanics" decision) — bounds disk usage from abandoned failed jobs.
    # CONTEXT.md's "Further Notes" flagged that the real number needs
    # verifying against dogtrace-app's actual free space, unconfirmed
    # during design; this default is a conservative placeholder pending
    # that check, not a measured value — revisit once real free-space
    # numbers are available.
    cutting_upload_storage_cap_bytes: int = 50 * 1024 * 1024 * 1024  # 50 GiB
    # A single upload's own declared size is also capped, independent of
    # the aggregate cap above (ENGINEERING-STANDARDS.md §5: "limit request
    # body and upload sizes"). Generous for a real multi-GB source video.
    cutting_upload_max_file_size_bytes: int = 20 * 1024 * 1024 * 1024  # 20 GiB

    # Ticket #94: creating a CuttingJob reuses the existing per-identity
    # rate_limit.py machinery, same reasoning as Feature B's POST /analyses
    # limit (CONTEXT.md's Feature C decision) — an upload-triggering
    # endpoint is at least as expensive a thing to trigger repeatedly.
    create_cutting_job_rate_limit_max_attempts_per_identity: int = 10
    create_cutting_job_rate_limit_window_seconds: int = 300
    # Ticket #97: the timestamp Excel sent with a cutting-job submission
    # (ENGINEERING-STANDARDS.md §5: "limit request body and upload sizes") —
    # a sheet of one row per Test is kilobytes, and a batch parses it once
    # per Test, so this is far tighter than consolidation_max_file_size_bytes.
    cutting_job_excel_max_file_size_bytes: int = 5 * 1024 * 1024  # 5 MiB
    # Starting a chunked upload is cheap on its own (a small metadata
    # write), but unbounded repetition would still clutter local disk with
    # empty upload directories independent of the byte-level cap above —
    # ENGINEERING-STANDARDS.md §5's general "expensive operations... cannot
    # be triggered repeatedly" guidance, applied with a more generous
    # budget than job creation itself.
    cutting_upload_init_rate_limit_max_attempts_per_identity: int = 30
    cutting_upload_init_rate_limit_window_seconds: int = 300

    # Ticket #95 (Cutting worker, part of issue #93's Feature C): polling
    # interval and the job-scoped scratch directory for the separate
    # `cutting-worker` Docker service — mirrors analysis_worker_poll_interval_
    # seconds/analysis_worker_work_dir above. Reused as-is by the
    # cutting-worker process (no FastAPI dependency of its own); meaningless
    # to the backend process itself. Only holds each job's *output* Cuts
    # before they're uploaded to S3 — the source video(s) it cuts from are
    # read directly out of cutting_upload_temp_dir above, never copied here
    # (CONTEXT.md's Feature C "Upload mechanics" decision).
    cutting_worker_poll_interval_seconds: float = 5.0
    cutting_worker_work_dir: str = "/data/cutting-worker"

    # Ticket #87 (Audit log retention pruning, issue #79): a lightweight
    # in-process background task in the backend's own FastAPI lifespan
    # (app/main.py) prunes audit_log rows older than a rolling retention
    # window, once per interval — deliberately no cron container, systemd
    # timer, or task queue, matching rate_limit.py's own single-backend-
    # instance assumption (CONTEXT.md's "Retention" decision: 3 months,
    # rolling 90 days).
    # `gt=0` on both (caught in review): a zero/negative retention_days
    # would make prune_old_audit_events's cutoff resolve to now-or-future,
    # silently deleting the *entire* audit_log table on the very next
    # prune — a compliance-relevant table, with no other guard anywhere
    # against that. A zero/negative interval would busy-loop the prune
    # task against the database instead of waiting between runs.
    audit_log_retention_days: int = Field(default=90, gt=0)
    audit_log_prune_interval_seconds: int = Field(default=24 * 60 * 60, gt=0)
    # The prune task has no per-request `Depends` seam to override the way
    # get_db/get_s3_client/... do (it's lifespan-level, opening its own
    # Session directly) — this flag is that seam instead, so
    # backend/tests/conftest.py can disable it for every test. Left enabled
    # it would fire a real DELETE against whichever database `DATABASE_URL`
    # points at every time a test's `TestClient` triggers ASGI lifespan
    # startup, bypassing db_session's per-test SAVEPOINT rollback entirely
    # (caught in review). A test-only seam, not an operator-facing
    # deployment toggle — deliberately not in .env.example, unlike the two
    # settings above.
    audit_log_prune_enabled: bool = True

    # Ticket #115 (issue #113's Excel consolidation feature): where an
    # uploaded Excel file is written for the duration of one synchronous
    # request — a scoped local temp directory, never S3 (only the
    # consolidated *result* goes to S3; see app/services/consolidation.py).
    # Same "/data" placement as cutting_upload_temp_dir/analysis_worker_
    # work_dir above, for the same non-root-user-ownership reason.
    consolidation_upload_temp_dir: str = "/data/consolidation-uploads"
    # A single upload's declared size cap (ENGINEERING-STANDARDS.md §5:
    # "limit request body and upload sizes") — generous relative to real
    # Observer exports (~1-2 MiB seen in practice per issue #113's spec
    # work) without inviting an oversized upload to sit in memory for the
    # duration of one synchronous request (unlike cutting_upload_max_file_
    # size_bytes's multi-GB videos, this is never chunked).
    consolidation_max_file_size_bytes: int = 25 * 1024 * 1024  # 25 MiB
    # Ticket #115: creating a Consolidation reuses the existing per-identity
    # rate_limit.py machinery, same reasoning as POST /analyses and POST
    # /cutting-jobs — an upload-triggering, synchronous-processing endpoint
    # is at least as expensive a thing to trigger repeatedly.
    create_consolidation_rate_limit_max_attempts_per_identity: int = 10
    create_consolidation_rate_limit_window_seconds: int = 300

    # Ticket #121: a Consolidation still `processing` this long after it was
    # created belongs to a run that will never finish (the backend died or
    # restarted mid-run, or the database failed at the final commit), and is
    # moved to `failed` by the reconcile task in app/main.py — same
    # in-process lifespan-task pattern as the audit-log prune above. 30
    # minutes is far above a real run, which takes seconds. A run that does
    # outlive it can't corrupt anything: it keeps the reconciler's `failed`
    # (see run_consolidation). See CONTEXT.md's "Consolidation
    # reconciliation" decision. `gt=0` on both: a zero threshold would fail
    # every run still in progress, and a zero interval would busy-loop.
    consolidation_stale_after_minutes: int = Field(default=30, gt=0)
    consolidation_reconcile_interval_seconds: int = Field(default=10 * 60, gt=0)
    # Test-only seam, same as audit_log_prune_enabled above — deliberately
    # not in .env.example.
    consolidation_reconcile_enabled: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS_ORIGINS as a comma-separated env var, split into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
