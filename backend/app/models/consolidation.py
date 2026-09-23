import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.analysis_job import REQUESTED_BY_IDENTITY_MAX_LENGTH

# Shared with app.api.consolidations, which rejects a too-long filename
# before ever calling create_consolidation (caught in review: an unchecked
# 256+ character filename previously reached the point of a completed S3
# upload before failing on this column's own length constraint, breaking
# the "never persist a row before the outcome is known" atomicity guarantee
# by leaving an orphaned S3 object with no row to reference it).
ORIGINAL_FILENAME_MAX_LENGTH = 255


class ConsolidationCondition(str, enum.Enum):
    """Which of consolidation/observer_import.py's `deel` this consolidation
    covers, in this app's own Condition vocabulary (CONTEXT.md's Language
    section: ME/ZE, also used for Cut filenames) rather than the Dutch
    "deel1"/"deel2"/"deel1+2" names the domain code uses internally —
    confirmed 1:1 during issue #113's spec work: deel 1 = met eigenaar =
    ME, deel 2 = zonder eigenaar = ZE. See
    app.services.consolidation._CONDITION_TO_DEEL for the mapping back to
    the domain code's own parameter.
    """

    ME = "ME"
    ZE = "ZE"
    ME_ZE = "ME_ZE"


class ConsolidationStatus(str, enum.Enum):
    """Only two terminal states — ticket #115 runs consolidation
    synchronously inside the request (issue #113's "simplest reliable
    architecture" decision: no worker/queue), so a Consolidation row is
    only ever written once the outcome is already known. There's no
    persisted `processing` state for anything outside the request to ever
    observe."""

    COMPLETED = "completed"
    FAILED = "failed"


class Consolidation(Base):
    """One upload -> consolidate -> persist request against
    consolidation/observer_import.py's Observer-export pipeline (ticket
    #115, part of issue #113). Fully shared visibility, no ownership,
    mirroring AnalysisJob/CuttingJob (ADR-0004) — requested_by_identity is
    attribution only, never an access check.

    Written exactly once, after the outcome is known
    (app.services.consolidation.create_consolidation never marks a row
    `completed` before its result is confirmed uploaded) — never updated
    from one status to the other afterward.
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
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    condition: Mapped[ConsolidationCondition] = mapped_column(
        Enum(
            ConsolidationCondition,
            name="consolidation_condition",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
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
    # anything else, create_consolidation never reaches the point of
    # writing this row at all — see its docstring.
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Null for a failed consolidation — nothing was ever persisted to
    # object storage.
    result_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    input_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    result_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set for both outcomes (mirrors AnalysisJob.finished_at, set for every
    # terminal status including failed) — "when this attempt finished",
    # not "when it succeeded".
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
