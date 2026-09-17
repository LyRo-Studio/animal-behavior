from app.models.analysis_job import AnalysisJobStatus, AnalysisJobVideoStatus
from app.services.analyses import (
    claim_next_queued_job,
    create_analysis_job,
    finalize_analysis_job,
    requeue_stuck_running_jobs,
)
from tests.helpers import create_account

# Ticket #47's own service-layer additions to app/services/analyses.py —
# the DB-transition primitives the worker (worker/orchestrator.py) calls
# through, exercised here directly against a real (Alembic-migrated)
# database, same as the rest of this module's tests. The worker's own
# end-to-end orchestration (download/run/upload/cleanup) is covered in
# worker/tests instead.


def _create_job(db_session, *, test_id="T001", cut="cuts/T001/T001_C2_ME_F1.mp4"):
    account = create_account(db_session, email="jan.peeters@vives.be")
    return create_analysis_job(db_session, requested_by=account.id, test_id=test_id, cut_keys=[cut])


def test_claim_next_queued_job_claims_oldest_and_sets_running(db_session):
    account = create_account(db_session, email="jan.peeters@vives.be")
    older = create_analysis_job(
        db_session,
        requested_by=account.id,
        test_id="T001",
        cut_keys=["cuts/T001/T001_C2_ME_F1.mp4"],
    )
    create_analysis_job(
        db_session,
        requested_by=account.id,
        test_id="T002",
        cut_keys=["cuts/T002/T002_C2_ME_F1.mp4"],
    )

    claimed = claim_next_queued_job(db_session, dogtrace_version="1.0.0")

    assert claimed is not None
    assert claimed.id == older.id
    assert claimed.status == AnalysisJobStatus.RUNNING
    assert claimed.started_at is not None
    assert claimed.dogtrace_version == "1.0.0"


def test_claim_next_queued_job_returns_none_when_queue_empty(db_session):
    assert claim_next_queued_job(db_session, dogtrace_version="1.0.0") is None


def test_claim_next_queued_job_ignores_non_queued_jobs(db_session):
    job = _create_job(db_session)
    job.status = AnalysisJobStatus.RUNNING
    db_session.add(job)
    db_session.commit()

    assert claim_next_queued_job(db_session, dogtrace_version="1.0.0") is None


def test_finalize_analysis_job_all_succeeded_is_completed(db_session):
    job = _create_job(db_session)
    job.videos[0].status = AnalysisJobVideoStatus.SUCCEEDED
    db_session.add(job)
    db_session.commit()

    finalized = finalize_analysis_job(db_session, job, report_s3_prefix="reports/T001/1/")

    assert finalized.status == AnalysisJobStatus.COMPLETED
    assert finalized.report_s3_prefix == "reports/T001/1/"
    assert finalized.finished_at is not None


def test_finalize_analysis_job_partial_success_is_completed_with_errors(db_session):
    account = create_account(db_session, email="jan.peeters@vives.be")
    job = create_analysis_job(
        db_session,
        requested_by=account.id,
        test_id="T001",
        cut_keys=["cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C2_ME_F2.mp4"],
    )
    job.videos[0].status = AnalysisJobVideoStatus.SUCCEEDED
    job.videos[1].status = AnalysisJobVideoStatus.FAILED
    job.videos[1].failure_reason = "Analysis did not produce a result for this video."
    db_session.add(job)
    db_session.commit()

    finalized = finalize_analysis_job(db_session, job, report_s3_prefix="reports/T001/1/")

    assert finalized.status == AnalysisJobStatus.COMPLETED_WITH_ERRORS


def test_finalize_analysis_job_all_failed_is_failed_with_no_report(db_session):
    job = _create_job(db_session)
    job.videos[0].status = AnalysisJobVideoStatus.FAILED
    job.videos[0].failure_reason = "Analysis did not produce a result for this video."
    db_session.add(job)
    db_session.commit()

    finalized = finalize_analysis_job(db_session, job, report_s3_prefix=None)

    assert finalized.status == AnalysisJobStatus.FAILED
    assert finalized.report_s3_prefix is None


def test_requeue_stuck_running_jobs_resets_job_and_videos(db_session):
    job = _create_job(db_session)
    claimed = claim_next_queued_job(db_session, dogtrace_version="1.0.0")
    assert claimed.id == job.id
    claimed.videos[0].status = AnalysisJobVideoStatus.PROCESSING
    db_session.add(claimed)
    db_session.commit()

    requeued = requeue_stuck_running_jobs(db_session)

    assert [j.id for j in requeued] == [job.id]
    reset_job = requeued[0]
    assert reset_job.status == AnalysisJobStatus.QUEUED
    assert reset_job.started_at is None
    assert reset_job.dogtrace_version is None
    assert reset_job.videos[0].status == AnalysisJobVideoStatus.PENDING
    assert reset_job.videos[0].failure_reason is None


def test_requeue_stuck_running_jobs_leaves_queued_and_terminal_jobs_alone(db_session):
    _create_job(db_session)  # stays queued

    requeued = requeue_stuck_running_jobs(db_session)

    assert requeued == []
