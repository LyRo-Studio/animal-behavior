import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.analysis_job import REQUESTED_BY_IDENTITY_MAX_LENGTH


class CuttingJobStatus(str, enum.Enum):
    """A CuttingJob's lifecycle (ticket #94, part of issue #93's Feature C)
    — mirrors AnalysisJobStatus's shape: `queued` -> `running` -> one of
    `succeeded`/`failed`, or `queued` -> `cancelled`. No cutting-worker
    exists yet in this ticket (mirrors ticket #45's own "data model and
    request/status API before the worker exists" scoping for AnalysisJob)
    — nothing here ever transitions a job past `queued`.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CuttingJobOutputStatus(str, enum.Enum):
    """One CuttingJobOutput's progress (issue #93: "pending -> succeeded/
    failed, updated live as each phase's file appears") — mirrors
    AnalysisJobVideoStatus, minus its "processing" state: no cutting-worker
    exists yet to report live progress through."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CuttingJob(Base):
    """A request to cut one Test's uploaded C1/C2 source video(s) into Cuts
    via `assist` (issue #93, ticket #94). Created `queued` by `POST
    /cutting-jobs` only once every ingestion validation rule
    (app/services/cutting_jobs.py) has passed; no cutting-worker exists yet
    to actually claim and run it.
    """

    __tablename__ = "cutting_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Ticket #72's attribution convention, reused as-is (docs/adr/0004-...)
    # — a plain nullable string, never a foreign key.
    requested_by_identity: Mapped[str | None] = mapped_column(
        String(REQUESTED_BY_IDENTITY_MAX_LENGTH), nullable=True
    )
    status: Mapped[CuttingJobStatus] = mapped_column(
        Enum(
            CuttingJobStatus,
            name="cutting_job_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=CuttingJobStatus.QUEUED,
    )
    # The Excel row's own C1/C2 reference-camera value (CONTEXT.md: "assist
    # only computes a real audio-sync offset when both cameras are
    # present") — recorded even when both cameras were uploaded, since a
    # future cutting step still needs to know which one is the reference.
    reference_camera: Mapped[str] = mapped_column(String(10), nullable=False)
    # Elapsed seconds from each uploaded video's start, keyed "ME_F1".."ME_F8"/
    # "ZE_F1".."ZE_F8" (app/services/timestamp_excel.py) — a key is absent
    # if its cell was the sheet's 00:00:00-skip marker. ZE_F8 is stored like
    # any other key (a future cutting step needs it to bound ZE_F7's end)
    # even though it can never itself become an expected CuttingJobOutput.
    phase_timestamps: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    # Local temp-storage paths of the uploaded source video(s) — never S3,
    # not even transiently (CONTEXT.md's Feature C "Upload mechanics"
    # decision). Whichever camera wasn't uploaded stays null. No cleanup
    # happens in this ticket (no cutting-worker exists yet to discard a
    # succeeded job's source, or to know when a failed job was retried/
    # cancelled) — these just point at what's on disk right now.
    c1_source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    c2_source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    outputs: Mapped[list["CuttingJobOutput"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="CuttingJobOutput.id",
    )


class CuttingJobOutput(Base):
    """One expected (camera, condition, phase) Cut a CuttingJob will
    produce (issue #93: "one row per expected (camera, phase) output" —
    mirrors AnalysisJobVideo's per-unit status role). Created alongside its
    CuttingJob, `pending`, from the uploaded camera(s) crossed with the
    Excel row's non-skipped phase timestamps, excluding ZE_F8 (structurally
    impossible to produce — CONTEXT.md's Feature C "ZE_F8" decision).
    """

    __tablename__ = "cutting_job_outputs"
    __table_args__ = (
        UniqueConstraint(
            "cutting_job_id",
            "camera",
            "condition",
            "phase",
            name="uq_cutting_job_outputs_job_camera_condition_phase",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cutting_job_id: Mapped[int] = mapped_column(
        ForeignKey("cutting_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera: Mapped[str] = mapped_column(String(10), nullable=False)
    condition: Mapped[str] = mapped_column(String(10), nullable=False)
    phase: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[CuttingJobOutputStatus] = mapped_column(
        Enum(
            CuttingJobOutputStatus,
            name="cutting_job_output_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=CuttingJobOutputStatus.PENDING,
    )
    # Short, user-safe string only — never a raw exception/stack trace, same
    # bounding rule as AnalysisJobVideo.failure_reason.
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    job: Mapped["CuttingJob"] = relationship(back_populates="outputs")
