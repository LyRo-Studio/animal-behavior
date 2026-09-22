from sqlalchemy import select

from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, AnalysisJobVideoStatus
from app.models.audit_log import AuditAction, AuditLog
from tests.helpers import identity_headers

# Ticket #49's acceptance criteria: exercised through the HTTP API against
# the fake S3 client (the `s3_client` fixture) — no worker, no real bucket.
# A job's status/report_s3_prefix and its videos' statuses are set directly
# on the rows via `db_session`, standing in for what the worker (ticket #47)
# would otherwise leave behind once a job reaches a terminal state.


def _create_job(client, headers=None, *, test_id="T001", cut="cuts/T001/T001_C2_ME_F1.mp4"):
    response = client.post(
        "/api/analyses", json={"test_ids": [test_id], "cuts": [cut]}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _finish_job(
    db_session,
    analysis_id: int,
    *,
    status: AnalysisJobStatus,
    report_s3_prefix: str | None,
    video_status: AnalysisJobVideoStatus = AnalysisJobVideoStatus.SUCCEEDED,
) -> None:
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = status
    job.report_s3_prefix = report_s3_prefix
    for video in job.videos:
        video.status = video_status
    db_session.add(job)
    db_session.commit()


def test_download_report_for_completed_job_returns_the_xlsx_bytes(client, db_session, s3_client):
    analysis_id = _create_job(client)
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}casiop_report.xlsx"] = b"fake-xlsx-bytes"
    s3_client.objects[f"{prefix}track_report.csv"] = b"not exposed"
    _finish_job(
        db_session, analysis_id, status=AnalysisJobStatus.COMPLETED, report_s3_prefix=prefix
    )

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 200
    assert response.content == b"fake-xlsx-bytes"
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="casiop_report.xlsx"' in response.headers["content-disposition"]


def test_download_report_writes_a_report_downloaded_audit_row(client, db_session, s3_client):
    """Ticket #85 / issue #79: a successful download writes REPORT_DOWNLOADED,
    attributed via `get_verified_identity` — mirrors ticket #82/#83's own
    ANALYSIS_STARTED/ANALYSIS_CANCELLED coverage. `_create_job` already
    writes its own ANALYSIS_STARTED row, so this filters by action rather
    than assuming this is the only row."""
    analysis_id = _create_job(client)
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}casiop_report.xlsx"] = b"fake-xlsx-bytes"
    _finish_job(
        db_session, analysis_id, status=AnalysisJobStatus.COMPLETED, report_s3_prefix=prefix
    )

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 200
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.REPORT_DOWNLOADED)
    )
    assert row is not None
    assert row.target_type == "analysis_job"
    assert row.target == str(analysis_id)
    assert row.identity_verified is False


def test_download_report_for_a_rejected_request_does_not_write_an_audit_row(
    client, db_session, s3_client
):
    analysis_id = _create_job(client)

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 409
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.action == AuditAction.REPORT_DOWNLOADED))
        is None
    )


def test_download_report_for_completed_with_errors_job_returns_the_xlsx_bytes(
    client, db_session, s3_client
):
    analysis_id = _create_job(client)
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}casiop_report.xlsx"] = b"fake-xlsx-bytes"
    _finish_job(
        db_session,
        analysis_id,
        status=AnalysisJobStatus.COMPLETED_WITH_ERRORS,
        report_s3_prefix=prefix,
    )

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 200
    assert response.content == b"fake-xlsx-bytes"


def test_download_report_for_a_queued_job_is_rejected(client, db_session, s3_client):
    analysis_id = _create_job(client)

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 409


def test_download_report_for_a_running_job_is_rejected(client, db_session, s3_client):
    analysis_id = _create_job(client)
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = AnalysisJobStatus.RUNNING
    db_session.add(job)
    db_session.commit()

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 409


def test_download_report_for_a_failed_job_is_rejected(client, db_session, s3_client):
    analysis_id = _create_job(client)
    _finish_job(
        db_session,
        analysis_id,
        status=AnalysisJobStatus.FAILED,
        report_s3_prefix=None,
        video_status=AnalysisJobVideoStatus.FAILED,
    )

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 409


def test_download_report_for_a_cancelled_job_is_rejected(client, db_session, s3_client):
    analysis_id = _create_job(client)
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = AnalysisJobStatus.CANCELLED
    db_session.add(job)
    db_session.commit()

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 409


def test_download_report_for_a_job_run_by_another_identity_succeeds(client, db_session, s3_client):
    """Reports are fully shared (ticket #72): whoever ran the analysis."""
    analysis_id = _create_job(client, identity_headers("owner@vives.be"))
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}casiop_report.xlsx"] = b"fake-xlsx-bytes"
    _finish_job(
        db_session, analysis_id, status=AnalysisJobStatus.COMPLETED, report_s3_prefix=prefix
    )

    response = client.get(
        f"/api/analyses/{analysis_id}/report", headers=identity_headers("other@vives.be")
    )

    assert response.status_code == 200
    assert response.content == b"fake-xlsx-bytes"


def test_download_report_for_an_unknown_job_is_not_found(client, s3_client):
    response = client.get("/api/analyses/999999/report")

    assert response.status_code == 404


def test_download_report_missing_from_s3_is_not_found(client, db_session, s3_client):
    analysis_id = _create_job(client)
    prefix = f"reports/T001/{analysis_id}/"
    # Deliberately never seeded into s3_client.objects.
    _finish_job(
        db_session, analysis_id, status=AnalysisJobStatus.COMPLETED, report_s3_prefix=prefix
    )

    response = client.get(f"/api/analyses/{analysis_id}/report")

    assert response.status_code == 404
