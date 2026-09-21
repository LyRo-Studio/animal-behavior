from datetime import UTC, datetime

import pytest
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, AnalysisJobVideoStatus
from app.services.analyses import create_analysis_job

from worker.__main__ import requeue_orphaned_jobs

# Ticket #50's acceptance criteria: `requeue_orphaned_jobs` is the worker's
# own startup-recovery glue around `app.services.analyses
# .requeue_stuck_running_jobs` (already covered at the DB-transition level
# by backend/tests/test_analyses_worker_claim.py) — this file covers the
# piece that's specific to the worker process: removing a recovered job's
# now-orphaned temp directory. Same fixtures/conventions as
# test_orchestrator.py (real, Alembic-migrated DB; no S3/DogTrace needed
# here at all).


def _create_job(db_session, *, test_id="T001", email="jan.peeters@vives.be") -> AnalysisJob:
    return create_analysis_job(
        db_session,
        requested_by_identity=email,
        test_id=test_id,
        cut_keys=[f"cuts/{test_id}/{test_id}_C2_ME_F1.mp4"],
    )


def _create_running_job(db_session, *, test_id="T001", email="jan.peeters@vives.be") -> AnalysisJob:
    """A job set directly to `running` on the row, rather than routed
    through `claim_next_queued_job` — which claims the globally oldest
    `queued` row across the *whole* table, making it fragile against any
    other job a different test happens to leave behind.
    """
    job = _create_job(db_session, test_id=test_id, email=email)
    job.status = AnalysisJobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    job.dogtrace_version = "1.0.0"
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_requeue_orphaned_jobs_requeues_a_stuck_job_and_removes_its_temp_dir(db_session, work_root):
    job = _create_running_job(db_session)
    job_dir = work_root / str(job.id)
    (job_dir / "input").mkdir(parents=True)
    (job_dir / "input" / "T001_C2_ME_F1.mp4").write_bytes(b"leftover")

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.QUEUED
    assert job.videos[0].status == AnalysisJobVideoStatus.PENDING
    assert not job_dir.exists()


def test_requeue_orphaned_jobs_handles_a_stuck_job_with_no_temp_dir_on_disk(db_session, work_root):
    """A job can be left `running` with no temp dir at all — e.g. the
    process crashed before `_run_claimed_job` ever created one. Recovery
    must not raise just because there's nothing to remove."""
    job = _create_running_job(db_session)

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.QUEUED


def test_requeue_orphaned_jobs_handles_a_work_root_that_was_never_created(db_session, tmp_path):
    """`work_root` itself might not exist yet — e.g. the very first thing a
    freshly deployed worker does is this recovery step, before `main()`'s
    own `work_root.mkdir(...)` has ever run for *this* directory. Unlike
    the "no temp dir" case above (an existing `work_root` with no
    subdirectory for this job), here the whole parent tree is missing —
    `shutil.rmtree(..., ignore_errors=True)` must swallow that too.
    """
    never_created_work_root = tmp_path / "does-not-exist-yet"
    job = _create_running_job(db_session)
    assert not never_created_work_root.exists()

    requeue_orphaned_jobs(db_session, work_root=never_created_work_root)

    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.QUEUED
    assert not never_created_work_root.exists()


def test_requeue_orphaned_jobs_requeues_every_stuck_job(db_session, work_root):
    first = _create_running_job(db_session, test_id="T001")
    second = _create_running_job(db_session, test_id="T002", email="other@vives.be")
    (work_root / str(first.id)).mkdir(parents=True)
    (work_root / str(second.id)).mkdir(parents=True)

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.status == AnalysisJobStatus.QUEUED
    assert second.status == AnalysisJobStatus.QUEUED
    assert not (work_root / str(first.id)).exists()
    assert not (work_root / str(second.id)).exists()


@pytest.mark.parametrize("status", [AnalysisJobStatus.QUEUED, AnalysisJobStatus.COMPLETED])
def test_requeue_orphaned_jobs_never_touches_a_non_running_jobs_dir(db_session, work_root, status):
    """Recovery only ever removes the temp dir of a job it actually
    requeued — a `queued` job (never had one to begin with — issue #44's
    S3 input flow, step 5) and an already-terminal job (its dir, if any,
    is `process_next_job`'s own responsibility, not recovery's) are both
    left alone, even if a directory happens to exist at their id regardless.
    """
    job = _create_job(db_session)
    job.status = status
    db_session.add(job)
    db_session.commit()
    job_dir = work_root / str(job.id)
    job_dir.mkdir(parents=True)

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(job)
    assert job.status == status
    assert job_dir.exists()
