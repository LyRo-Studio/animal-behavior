from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, AnalysisJobVideoStatus
from tests.helpers import create_account, login_headers

# Ticket #49's acceptance criteria: exercised through the HTTP API against
# the fake S3 client (the `s3_client` fixture) — no worker, no real bucket.
# A job's status/report_s3_prefix and its videos' statuses are set directly
# on the rows via `db_session`, standing in for what the worker (ticket #47)
# would otherwise leave behind once a job reaches a terminal state.


def _user_headers(client, db_session, *, email="jan.peeters@vives.be"):
    create_account(db_session, email=email)
    return login_headers(client, email)


def _create_job(client, headers, *, test_id="T001", cut="cuts/T001/T001_C2_ME_F1.mp4"):
    response = client.post("/analyses", json={"test_id": test_id, "cuts": [cut]}, headers=headers)
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
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}casiop_report.xlsx"] = b"fake-xlsx-bytes"
    s3_client.objects[f"{prefix}track_report.csv"] = b"not exposed"
    _finish_job(
        db_session, analysis_id, status=AnalysisJobStatus.COMPLETED, report_s3_prefix=prefix
    )

    response = client.get(f"/analyses/{analysis_id}/report", headers=headers)

    assert response.status_code == 200
    assert response.content == b"fake-xlsx-bytes"
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="casiop_report.xlsx"' in response.headers["content-disposition"]


def test_download_report_for_completed_with_errors_job_returns_the_xlsx_bytes(
    client, db_session, s3_client
):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}casiop_report.xlsx"] = b"fake-xlsx-bytes"
    _finish_job(
        db_session,
        analysis_id,
        status=AnalysisJobStatus.COMPLETED_WITH_ERRORS,
        report_s3_prefix=prefix,
    )

    response = client.get(f"/analyses/{analysis_id}/report", headers=headers)

    assert response.status_code == 200
    assert response.content == b"fake-xlsx-bytes"


def test_download_report_for_a_queued_job_is_rejected(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)

    response = client.get(f"/analyses/{analysis_id}/report", headers=headers)

    assert response.status_code == 409


def test_download_report_for_a_running_job_is_rejected(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = AnalysisJobStatus.RUNNING
    db_session.add(job)
    db_session.commit()

    response = client.get(f"/analyses/{analysis_id}/report", headers=headers)

    assert response.status_code == 409


def test_download_report_for_a_failed_job_is_rejected(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)
    _finish_job(
        db_session,
        analysis_id,
        status=AnalysisJobStatus.FAILED,
        report_s3_prefix=None,
        video_status=AnalysisJobVideoStatus.FAILED,
    )

    response = client.get(f"/analyses/{analysis_id}/report", headers=headers)

    assert response.status_code == 409


def test_download_report_for_a_cancelled_job_is_rejected(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = AnalysisJobStatus.CANCELLED
    db_session.add(job)
    db_session.commit()

    response = client.get(f"/analyses/{analysis_id}/report", headers=headers)

    assert response.status_code == 409


def test_download_report_for_another_accounts_job_is_not_found(client, db_session, s3_client):
    owner_headers = _user_headers(client, db_session, email="owner@vives.be")
    analysis_id = _create_job(client, owner_headers)
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}casiop_report.xlsx"] = b"fake-xlsx-bytes"
    _finish_job(
        db_session, analysis_id, status=AnalysisJobStatus.COMPLETED, report_s3_prefix=prefix
    )
    other_headers = _user_headers(client, db_session, email="other@vives.be")

    response = client.get(f"/analyses/{analysis_id}/report", headers=other_headers)

    assert response.status_code == 404


def test_download_report_for_an_unknown_job_is_not_found(client, db_session, s3_client):
    headers = _user_headers(client, db_session)

    response = client.get("/analyses/999999/report", headers=headers)

    assert response.status_code == 404


def test_download_report_missing_from_s3_is_not_found(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)
    prefix = f"reports/T001/{analysis_id}/"
    # Deliberately never seeded into s3_client.objects.
    _finish_job(
        db_session, analysis_id, status=AnalysisJobStatus.COMPLETED, report_s3_prefix=prefix
    )

    response = client.get(f"/analyses/{analysis_id}/report", headers=headers)

    assert response.status_code == 404


def test_download_report_unauthenticated_is_rejected(client, db_session):
    assert client.get("/analyses/1/report").status_code == 401
