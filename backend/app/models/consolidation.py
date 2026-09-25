import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.analysis_job import REQUESTED_BY_IDENTITY_MAX_LENGTH

# Shared with app.api.consolidations, which rejects a too-long filename
# before ever creating a Consolidation (caught in review: an unchecked
# 256+ character filename previously got as far as a completed S3 upload
# before failing on this column's own length constraint, leaving an
# orphaned S3 object with no row to reference it).
ORIGINAL_FILENAME_MAX_LENGTH = 255
# Shared with app.services.consolidation, which truncates to it.
FAILURE_REASON_MAX_LENGTH = 500
# Shared with app.schemas.consolidations, which rejects a longer rename
# (ticket #117) before it ever reaches this column's own constraint.
DISPLAY_NAME_MAX_LENGTH = 255


class ConsolidationStatus(str, enum.Enum):
    """`processing` -> `completed` | `failed`. Consolidation still runs
    synchronously inside the request (issue #113's "simplest reliable
    architecture": no worker/queue), but the row is created as
    `processing` before the runner starts so CONSOLIDATION_STARTED has an
    id to attribute to, and so an attempt whose process dies mid-run
    still leaves a trace. Such a row is later moved to `failed` by
    app.services.consolidation.reconcile_stale_consolidations (ticket
    #121)."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Consolidation(Base):
    """One upload -> consolidate -> persist request against
    consolidation/observer_import.py's Observer-export pipeline (ticket
    #115, part of issue #113). Its one result holds every Consolidation
    level, so there is no Condition to choose (ticket #149). Fully shared
    visibility, no ownership, mirroring AnalysisJob/CuttingJob (ADR-0004) —
    requested_by_identity is attribution only, never an access check.

    Created as `processing` and moved to its terminal status exactly once
    (app.services.consolidation.run_consolidation never marks a row
    `completed` before its result is confirmed uploaded).
    """

    __tablename__ = "consolidations"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(
        String(ORIGINAL_FILENAME_MAX_LENGTH), nullable=False
    )
    # Optional, user-settable label (ticket #117) — falls back to
    # original_filename when unset (frontend's job). Kept as a separate
    # column, never overwriting original_filename, so a rename never
    # destroys the source file's provenance.
    display_name: Mapped[str | None] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH), nullable=True)
    status: Mapped[ConsolidationStatus] = mapped_column(
        Enum(
            ConsolidationStatus,
            name="consolidation_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    requested_by_identity: Mapped[str | None] = mapped_column(
        String(REQUESTED_BY_IDENTITY_MAX_LENGTH), nullable=True
    )
    # Short, user-safe string only — never a raw exception/stack trace, same
    # bounding rule as AnalysisJobVideo.failure_reason. For a domain
    # validation failure from consolidation/'s own code, this is that
    # message near-verbatim (issue #113's error-handling decision); for
    # anything else, a generic message (the real exception is only logged).
    failure_reason: Mapped[str | None] = mapped_column(
        String(FAILURE_REASON_MAX_LENGTH), nullable=True
    )
    # Where the result is (or would be) stored. Recorded at creation since
    # ticket #121, not at completion, so a result uploaded by a run that
    # never finished can still be found and removed. Only a `completed` row
    # is guaranteed to have an object there. Null only on rows created
    # before #121 that never completed.
    result_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    input_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    result_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set for both terminal outcomes, null while `processing` (mirrors
    # AnalysisJob.finished_at) — "when this attempt finished", not "when it
    # succeeded".
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
