from dataclasses import dataclass
from pathlib import Path

from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, AnalysisJobVideoStatus
from app.services.analyses import create_analysis_job

from tests.doubles import FakeDogTraceRunner, FakeS3Client
from tests.helpers import create_account
from worker.dogtrace_runner import ProgressCallback
from worker.orchestrator import _make_progress_callback, process_next_job

# Ticket #47's acceptance criteria, exercised end-to-end against a real
# (Alembic-migrated) database plus a fake S3 client and a fake
# DogTraceRunner — never real inference or the real bucket (issue #44's
# testing strategy).


def _create_job(
    db_session, *, test_id="T001", cuts=("cuts/T001/T001_C2_ME_F1.mp4",)
) -> AnalysisJob:
    account = create_account(db_session)
    return create_analysis_job(
        db_session, requested_by=account.id, test_id=test_id, cut_keys=list(cuts)
    )


def _seed_cut(s3_client: FakeS3Client, cut_key: str) -> None:
    s3_client.objects[cut_key] = b"fake-video-bytes"


def test_process_next_job_returns_false_when_queue_empty(db_session, work_root):
    processed = process_next_job(
        db_session,
        s3_client=FakeS3Client(),
        dogtrace_runner=FakeDogTraceRunner(),
        work_root=work_root,
    )

    assert processed is False


def test_process_next_job_all_videos_succeed_completes_and_uploads_report(db_session, work_root):
    job = _create_job(db_session)
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    dogtrace_runner = FakeDogTraceRunner(version="1.2.3")

    processed = process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    assert processed is True
    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.COMPLETED
    assert job.dogtrace_version == "1.2.3"
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.report_s3_prefix == f"reports/T001/{job.id}/"
    assert job.videos[0].status == AnalysisJobVideoStatus.SUCCEEDED
    assert job.videos[0].failure_reason is None

    uploaded_keys = set(s3_client.objects) - {"cuts/T001/T001_C2_ME_F1.mp4"}
    assert f"reports/T001/{job.id}/casiop_report.xlsx" in uploaded_keys
    assert not (work_root / str(job.id)).exists()


def test_process_next_job_partial_failure_completes_with_errors(db_session, work_root):
    job = _create_job(
        db_session,
        cuts=("cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C2_ME_F2.mp4"),
    )
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F2.mp4")
    dogtrace_runner = FakeDogTraceRunner(failing_video_stems=frozenset({"T001_C2_ME_F2"}))

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.COMPLETED_WITH_ERRORS
    videos_by_key = {video.cut_key: video for video in job.videos}
    assert videos_by_key["cuts/T001/T001_C2_ME_F1.mp4"].status == AnalysisJobVideoStatus.SUCCEEDED
    failed_video = videos_by_key["cuts/T001/T001_C2_ME_F2.mp4"]
    assert failed_video.status == AnalysisJobVideoStatus.FAILED
    assert failed_video.failure_reason
    assert "Traceback" not in failed_video.failure_reason
    assert job.report_s3_prefix == f"reports/T001/{job.id}/"


def test_process_next_job_all_videos_fail_pipeline_is_failed_with_no_report(db_session, work_root):
    job = _create_job(db_session)
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    dogtrace_runner = FakeDogTraceRunner(failing_video_stems=frozenset({"T001_C2_ME_F1"}))

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.FAILED
    assert job.videos[0].status == AnalysisJobVideoStatus.FAILED
    assert job.report_s3_prefix is None
    assert not (work_root / str(job.id)).exists()


def test_process_next_job_missing_cut_fails_only_that_video(db_session, work_root):
    job = _create_job(
        db_session,
        cuts=("cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C2_ME_F2.mp4"),
    )
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")  # F2 deliberately never seeded
    dogtrace_runner = FakeDogTraceRunner()

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    videos_by_key = {video.cut_key: video for video in job.videos}
    assert videos_by_key["cuts/T001/T001_C2_ME_F1.mp4"].status == AnalysisJobVideoStatus.SUCCEEDED
    missing_video = videos_by_key["cuts/T001/T001_C2_ME_F2.mp4"]
    assert missing_video.status == AnalysisJobVideoStatus.FAILED
    assert missing_video.failure_reason
    # Only the one downloadable Cut was ever handed to the pipeline.
    assert len(dogtrace_runner.calls) == 1
    assert [path.name for path in dogtrace_runner.calls[0]] == ["T001_C2_ME_F1.mp4"]
    assert job.status == AnalysisJobStatus.COMPLETED_WITH_ERRORS


def test_process_next_job_all_cuts_missing_never_calls_dogtrace(db_session, work_root):
    job = _create_job(db_session)
    s3_client = FakeS3Client()  # nothing seeded
    dogtrace_runner = FakeDogTraceRunner()

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.FAILED
    assert job.videos[0].status == AnalysisJobVideoStatus.FAILED
    assert dogtrace_runner.calls == []
    assert job.report_s3_prefix is None
    assert not (work_root / str(job.id)).exists()


def test_process_next_job_whole_batch_failure_marks_remaining_videos_failed(db_session, work_root):
    job = _create_job(
        db_session,
        cuts=("cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C2_ME_F2.mp4"),
    )
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F2.mp4")
    dogtrace_runner = FakeDogTraceRunner(raises=RuntimeError("model failed to load"))

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.FAILED
    assert all(video.status == AnalysisJobVideoStatus.FAILED for video in job.videos)
    for video in job.videos:
        assert video.failure_reason
        assert "RuntimeError" not in video.failure_reason
        assert "model failed to load" not in video.failure_reason
    assert job.report_s3_prefix is None
    assert not (work_root / str(job.id)).exists()


def test_process_next_job_duplicate_cut_keys_run_dogtrace_only_once(db_session, work_root):
    duplicate_cut = "cuts/T001/T001_C2_ME_F1.mp4"
    job = _create_job(db_session, cuts=(duplicate_cut, duplicate_cut))
    s3_client = FakeS3Client()
    _seed_cut(s3_client, duplicate_cut)
    dogtrace_runner = FakeDogTraceRunner()

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    assert len(dogtrace_runner.calls) == 1
    assert len(dogtrace_runner.calls[0]) == 1
    assert job.status == AnalysisJobStatus.COMPLETED
    assert all(video.status == AnalysisJobVideoStatus.SUCCEEDED for video in job.videos)


class _RaisingUploadS3Client(FakeS3Client):
    """A FakeS3Client whose `upload_file` always raises — simulates a
    transient S3 error surfacing at the one point in `_run_claimed_job` that
    has no dedicated try/except of its own."""

    def upload_file(self, local_path, key) -> None:
        raise RuntimeError("simulated S3 upload failure")


def test_process_next_job_survives_an_unhandled_error_and_fails_only_that_job(
    db_session, work_root
):
    job = _create_job(db_session)
    s3_client = _RaisingUploadS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    dogtrace_runner = FakeDogTraceRunner()

    processed = process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    assert processed is True
    db_session.refresh(job)
    assert job.status == AnalysisJobStatus.FAILED
    assert job.report_s3_prefix is None
    assert job.finished_at is not None
    assert job.videos[0].status == AnalysisJobVideoStatus.FAILED
    assert job.videos[0].failure_reason
    assert "RuntimeError" not in job.videos[0].failure_reason
    assert not (work_root / str(job.id)).exists()


def test_process_next_job_processes_only_one_job_at_a_time(db_session, work_root):
    first_job = _create_job(db_session, test_id="T001")
    account = create_account(db_session, email="other@vives.be", display_name="Other")
    second_job = create_analysis_job(
        db_session,
        requested_by=account.id,
        test_id="T002",
        cut_keys=["cuts/T002/T002_C2_ME_F1.mp4"],
    )
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    _seed_cut(s3_client, "cuts/T002/T002_C2_ME_F1.mp4")
    dogtrace_runner = FakeDogTraceRunner()

    processed = process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    assert processed is True
    db_session.refresh(first_job)
    db_session.refresh(second_job)
    assert first_job.status == AnalysisJobStatus.COMPLETED
    assert second_job.status == AnalysisJobStatus.QUEUED


def test_process_next_job_commits_progress_incrementally(db_session, work_root):
    # Ticket #48's acceptance criterion: status updates in near-real-time,
    # not only once the job reaches a terminal state — asserted here by
    # counting `db_session.commit()` calls rather than just the end state,
    # since a single end-of-job commit would also produce the right final
    # statuses without satisfying "near-real-time".
    _create_job(
        db_session,
        cuts=("cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C2_ME_F2.mp4"),
    )
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F2.mp4")
    dogtrace_runner = FakeDogTraceRunner()

    commit_calls = 0
    original_commit = db_session.commit

    def _counting_commit():
        nonlocal commit_calls
        commit_calls += 1
        return original_commit()

    db_session.commit = _counting_commit

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    # 2 videos * (started + succeeded) = 4 progress-driven commits, on top
    # of claim_next_queued_job's and finalize_analysis_job's own.
    assert commit_calls >= 4 + 2


def test_progress_callback_sets_processing_before_terminal_status(db_session, work_root):
    # Direct unit test of `_make_progress_callback` itself: PROCESSING on
    # "started" is the intermediate state `GET /analyses/{id}` is meant to
    # observe while a video is running, before its terminal status lands.
    job = _create_job(db_session)
    video = job.videos[0]
    callback = _make_progress_callback(db_session, {Path(video.cut_key).stem: [video]})

    callback(Path(video.cut_key), "started")
    assert video.status == AnalysisJobVideoStatus.PROCESSING

    callback(Path(video.cut_key), "succeeded")
    assert video.status == AnalysisJobVideoStatus.SUCCEEDED
    assert video.failure_reason is None


def test_progress_callback_failed_records_a_user_safe_reason(db_session, work_root):
    job = _create_job(db_session)
    video = job.videos[0]
    callback = _make_progress_callback(db_session, {Path(video.cut_key).stem: [video]})

    callback(Path(video.cut_key), "started")
    callback(Path(video.cut_key), "failed")

    assert video.status == AnalysisJobVideoStatus.FAILED
    assert video.failure_reason


def test_progress_callback_updates_every_row_sharing_a_duplicate_cut_key(db_session, work_root):
    duplicate_cut = "cuts/T001/T001_C2_ME_F1.mp4"
    job = _create_job(db_session, cuts=(duplicate_cut, duplicate_cut))
    stem = Path(duplicate_cut).stem
    callback = _make_progress_callback(db_session, {stem: list(job.videos)})

    callback(Path(duplicate_cut), "started")
    assert all(video.status == AnalysisJobVideoStatus.PROCESSING for video in job.videos)

    callback(Path(duplicate_cut), "succeeded")
    assert all(video.status == AnalysisJobVideoStatus.SUCCEEDED for video in job.videos)


def test_progress_callback_never_resurrects_an_already_terminal_video(db_session, work_root):
    # A video that already failed to download (see `_download_video`) must
    # never be touched by a progress event for a same-named video it never
    # actually reached — defensive, since in practice a download failure
    # and a shared stem can't currently co-occur (same Cut key downloads
    # the same way for every row), but the callback shouldn't rely on that.
    job = _create_job(db_session)
    video = job.videos[0]
    video.status = AnalysisJobVideoStatus.FAILED
    video.failure_reason = "The source video could not be retrieved from storage."
    callback = _make_progress_callback(db_session, {Path(video.cut_key).stem: [video]})

    callback(Path(video.cut_key), "started")

    assert video.status == AnalysisJobVideoStatus.FAILED
    assert video.failure_reason == "The source video could not be retrieved from storage."


def test_process_next_job_batch_failure_after_partial_progress_keeps_prior_success(
    db_session, work_root
):
    # Regression test for the except-branch guard in `_run_claimed_job`:
    # a whole-batch failure partway through must fail only videos still
    # `pending`/`processing`, never overwrite one the progress callback
    # already marked `succeeded` before the crash.
    job = _create_job(
        db_session,
        cuts=("cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C2_ME_F2.mp4"),
    )
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F2.mp4")
    dogtrace_runner = FakeDogTraceRunner(
        raises=RuntimeError("crashed after the first video"), raises_after_videos=1
    )

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    videos_by_key = {video.cut_key: video for video in job.videos}
    assert videos_by_key["cuts/T001/T001_C2_ME_F1.mp4"].status == AnalysisJobVideoStatus.SUCCEEDED
    second_video = videos_by_key["cuts/T001/T001_C2_ME_F2.mp4"]
    assert second_video.status == AnalysisJobVideoStatus.FAILED
    assert second_video.failure_reason
    assert job.status == AnalysisJobStatus.COMPLETED_WITH_ERRORS


def test_process_next_job_video_never_terminalized_by_progress_is_failed_as_fallback(
    db_session, work_root
):
    # Regression test for the `else` fallback sweep in `_run_claimed_job`:
    # if `run_reporting` returns without raising but never calls `progress`
    # with a terminal event for some video (a violation of its documented
    # contract), that video must still end up `failed`, not stuck `pending`/
    # `processing` forever under an otherwise-terminal job.
    job = _create_job(db_session)
    s3_client = FakeS3Client()
    _seed_cut(s3_client, "cuts/T001/T001_C2_ME_F1.mp4")
    dogtrace_runner = _SilentDogTraceRunner()

    process_next_job(
        db_session, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
    )

    db_session.refresh(job)
    assert job.videos[0].status == AnalysisJobVideoStatus.FAILED
    assert job.videos[0].failure_reason
    assert job.status == AnalysisJobStatus.FAILED


@dataclass
class _SilentDogTraceRunner:
    """Returns successfully without ever calling `progress` at all —
    simulates a `dogtrace_runner` that violates its documented callback
    contract, exercising `_run_claimed_job`'s fallback sweep.
    """

    version: str = "1.0.0-fake"

    def run_reporting(
        self,
        video_paths: list[Path],
        *,
        output_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        pass
