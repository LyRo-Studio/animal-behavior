from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.models.cutting_job import CuttingJobOutputStatus, CuttingJobStatus

from cutting_worker.__main__ import requeue_orphaned_jobs
from tests.doubles import build_cutting_job

# Ticket #95's crash-recovery acceptance criterion: `requeue_orphaned_jobs`
# is the cutting-worker's own startup-recovery glue around
# `app.services.cutting_jobs.requeue_stuck_running_cutting_jobs` (already
# covered at the DB-transition level by
# backend/tests/test_cutting_jobs_worker_claim.py) — this file covers the
# piece specific to the cutting-worker process: removing a recovered job's
# now-orphaned output-scratch directory. Mirrors
# worker/tests/test_startup_recovery.py's own shape and fixtures.


def _create_running_job(db_session, uploads_root, *, test_id="T001"):
    job = build_cutting_job(db_session, test_id=test_id, uploads_root=uploads_root)
    job.status = CuttingJobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_requeue_orphaned_jobs_requeues_a_stuck_job_and_removes_its_temp_dir(
    db_session, work_root, uploads_root
):
    job = _create_running_job(db_session, uploads_root)
    job_dir = work_root / str(job.id)
    (job_dir / "output").mkdir(parents=True)
    (job_dir / "output" / "T001_C1_ME_F1.mp4").write_bytes(b"leftover")

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.QUEUED
    assert job.outputs[0].status == CuttingJobOutputStatus.PENDING
    assert not job_dir.exists()


def test_requeue_orphaned_jobs_never_touches_the_source_upload(db_session, work_root, uploads_root):
    # Unlike work_root (this process's own output scratch space), a
    # CuttingJob's source upload lives outside work_root entirely and must
    # survive a requeue untouched — it's exactly what makes the retry safe.
    job = _create_running_job(db_session, uploads_root)

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.QUEUED
    assert Path(job.c1_source_path).is_file()


def test_requeue_orphaned_jobs_handles_a_stuck_job_with_no_temp_dir_on_disk(
    db_session, work_root, uploads_root
):
    job = _create_running_job(db_session, uploads_root)

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.QUEUED


def test_requeue_orphaned_jobs_handles_a_work_root_that_was_never_created(
    db_session, tmp_path, uploads_root
):
    never_created_work_root = tmp_path / "does-not-exist-yet"
    job = _create_running_job(db_session, uploads_root)
    assert not never_created_work_root.exists()

    requeue_orphaned_jobs(db_session, work_root=never_created_work_root)

    db_session.refresh(job)
    assert job.status == CuttingJobStatus.QUEUED
    assert not never_created_work_root.exists()


def test_requeue_orphaned_jobs_requeues_every_stuck_job(db_session, work_root, uploads_root):
    first = _create_running_job(db_session, uploads_root, test_id="T001")
    second = _create_running_job(db_session, uploads_root, test_id="T002")
    (work_root / str(first.id)).mkdir(parents=True)
    (work_root / str(second.id)).mkdir(parents=True)

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.status == CuttingJobStatus.QUEUED
    assert second.status == CuttingJobStatus.QUEUED
    assert not (work_root / str(first.id)).exists()
    assert not (work_root / str(second.id)).exists()


@pytest.mark.parametrize("status", [CuttingJobStatus.QUEUED, CuttingJobStatus.SUCCEEDED])
def test_requeue_orphaned_jobs_never_touches_a_non_running_jobs_dir(
    db_session, work_root, uploads_root, status
):
    job = build_cutting_job(db_session, uploads_root=uploads_root)
    job.status = status
    db_session.add(job)
    db_session.commit()
    job_dir = work_root / str(job.id)
    job_dir.mkdir(parents=True)

    requeue_orphaned_jobs(db_session, work_root=work_root)

    db_session.refresh(job)
    assert job.status == status
    assert job_dir.exists()
