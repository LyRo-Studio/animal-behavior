import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Sized for an email address, the shape of identity Mechatronics is expected
# to forward. `app.api.deps.get_identity` truncates to this before anything
# reaches the column; migration 0006 hard-codes the same length (a migration
# must not import application code that can change after it).
REQUESTED_BY_IDENTITY_MAX_LENGTH = 320


class AnalysisJobStatus(str, enum.Enum):
    """An AnalysisJob's lifecycle (ticket #45, part of #44's job state
    machine): `queued` -> `running` -> one of `completed` /
    `completed_with_errors` / `failed`, or `queued` -> `cancelled` (only
    reachable before a worker claims the job — see ticket #46)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisJobVideoStatus(str, enum.Enum):
    """One AnalysisJobVideo's progress through the worker's per-video loop
    (ticket #47) — `pending` until the worker starts it, `processing` while
    it's running (set live from DogTrace's own progress callback, ticket
    #48), then `succeeded` or `failed`."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalysisJob(Base):
    """A request to run the DogTrace/CASOP pipeline on one or more C2 Cuts
    of a Test (issue #44). Created `queued` by `POST /analyses`; ticket #47
    adds the worker that actually claims and runs it — this ticket only
    creates and persists the row.
    """

    __tablename__ = "analysis_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Ticket #72: the raw identity Mechatronics forwarded when the job was
    # requested (docs/adr/0004-...) — attribution only, so a plain nullable
    # string rather than a foreign key to any account/identity table. Null
    # when no identity header was present (e.g. local development).
    requested_by_identity: Mapped[str | None] = mapped_column(
        String(REQUESTED_BY_IDENTITY_MAX_LENGTH), nullable=True
    )
    status: Mapped[AnalysisJobStatus] = mapped_column(
        Enum(
            AnalysisJobStatus,
            name="analysis_job_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=AnalysisJobStatus.QUEUED,
    )
    # Recorded once the job starts (ticket #47) — null while queued, per
    # issue #44's "Reproducibility" decision.
    dogtrace_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Nullable until the worker (ticket #47) uploads the job's artifacts.
    report_s3_prefix: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    videos: Mapped[list["AnalysisJobVideo"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="AnalysisJobVideo.position",
    )

    @property
    def report_available(self) -> bool:
        """Whether `GET /analyses/{id}/report` (ticket #49) has something to
        serve. True once the worker (ticket #47) has uploaded at least one
        artifact for this job — exposing this derived flag rather than
        `report_s3_prefix` itself keeps the API from leaking the internal S3
        layout to the frontend."""
        return self.report_s3_prefix is not None


class AnalysisJobVideo(Base):
    """One selected Cut within an AnalysisJob — gives per-video progress and
    per-video failure detail (issue #44's data model). `position` preserves
    the order Cuts were submitted in, since a plain unordered collection
    would otherwise lose the "2/5 videos, current: ..." sequence the
    frontend (ticket #52) needs to display.
    """

    __tablename__ = "analysis_job_videos"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id", "position", name="uq_analysis_job_videos_analysis_position"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cut_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AnalysisJobVideoStatus] = mapped_column(
        Enum(
            AnalysisJobVideoStatus,
            name="analysis_job_video_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=AnalysisJobVideoStatus.PENDING,
    )
    # Short, user-safe string only — never a raw exception/stack trace (see
    # issue #44's "Failure handling" decision). Bounded generously; the
    # worker (ticket #47) is responsible for keeping what it writes here
    # well within this.
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    job: Mapped["AnalysisJob"] = relationship(back_populates="videos")
