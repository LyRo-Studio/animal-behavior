import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.analysis_job import REQUESTED_BY_IDENTITY_MAX_LENGTH


class AuditAction(str, enum.Enum):
    """Every event the audit log records this round (issue #79's "Events,
    this round" list — existing functionality only; a future feature's own
    tickets, e.g. cutting-job events, extend this enum separately)."""

    ANALYSIS_STARTED = "analysis_started"
    ANALYSIS_COMPLETED = "analysis_completed"
    ANALYSIS_COMPLETED_WITH_ERRORS = "analysis_completed_with_errors"
    ANALYSIS_FAILED = "analysis_failed"
    ANALYSIS_CANCELLED = "analysis_cancelled"
    REPORT_DOWNLOADED = "report_downloaded"
    CUT_PLAY_REQUESTED = "cut_play_requested"
    CUT_DOWNLOAD_REQUESTED = "cut_download_requested"
    # Ticket #115, part of issue #113's Excel consolidation feature.
    CONSOLIDATION_STARTED = "consolidation_started"
    CONSOLIDATION_COMPLETED = "consolidation_completed"
    CONSOLIDATION_FAILED = "consolidation_failed"
    CONSOLIDATION_DOWNLOADED = "consolidation_downloaded"


class AuditLog(Base):
    """An immutable accountability record of "who did what, to what, when"
    for meaningful application actions (issue #79, ticket #82). Rows are
    never edited or deleted outside of the retention prune (ticket #87).

    Write-only this round — no UI or read endpoint (issue #79's "Out of
    Scope"); reading is direct SQL for now.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Indexed (ticket #87): the daily retention prune's own
    # `DELETE ... WHERE occurred_at < cutoff` (app/services/audit_log.py's
    # prune_old_audit_events) would otherwise be a full table scan on every
    # run, competing with this table's own frequent inserts for the
    # duration (caught in review).
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # Same truncation convention and length as
    # `AnalysisJob.requested_by_identity` — no FK, attribution only. Null
    # when no identity header was present (e.g. local development) or, for
    # a worker-driven event, when the job it describes was itself
    # unattributed.
    identity: Mapped[str | None] = mapped_column(
        String(REQUESTED_BY_IDENTITY_MAX_LENGTH), nullable=True
    )
    # Whether `identity` was cryptographically verified via
    # `app.api.deps.get_verified_identity` (docs/adr/0005-...) — always
    # False for a worker-driven event, which has no live request to verify
    # a JWT against.
    identity_verified: Mapped[bool] = mapped_column(nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        Enum(
            AuditAction,
            name="audit_action",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    # A generic (target_type, target) pair — e.g. ("analysis_job", "42"),
    # ("cut", "cuts/T001/T001_C2_ME_F1.mp4") — rather than a dedicated
    # per-resource FK column, so the audit trail survives independent of
    # whatever it's describing being later changed or deleted (issue #79's
    # "No FK from target" decision).
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Short, user-safe string only — never a raw exception/stack trace, same
    # bounding rule as `AnalysisJobVideo.failure_reason`.
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
