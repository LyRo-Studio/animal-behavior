import pytest

from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from tests.helpers import create_account, login_headers

# Ticket #46's acceptance criteria: exercised through the HTTP API. No
# worker exists yet (ticket #47), so a job is only ever `queued` through
# the API itself — the non-`queued` statuses exercised below are set
# directly on the row via `db_session`, standing in for what the worker
# will do once it exists.


def _user_headers(client, db_session, *, email="jan.peeters@vives.be"):
    create_account(db_session, email=email)
    return login_headers(client, email)


def _create_job(client, headers, *, test_id="T001", cut="cuts/T001/T001_C2_ME_F1.mp4"):
    response = client.post("/analyses", json={"test_id": test_id, "cuts": [cut]}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _set_status(db_session, analysis_id: int, status: AnalysisJobStatus) -> None:
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = status
    db_session.add(job)
    db_session.commit()


def test_cancel_a_queued_job_sets_status_to_cancelled(client, db_session):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)

    response = client.post(f"/analyses/{analysis_id}/cancel", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["finished_at"] is not None

    get_response = client.get(f"/analyses/{analysis_id}", headers=headers)
    assert get_response.json()["status"] == "cancelled"
    assert get_response.json()["finished_at"] is not None


@pytest.mark.parametrize(
    "status",
    [
        AnalysisJobStatus.RUNNING,
        AnalysisJobStatus.COMPLETED,
        AnalysisJobStatus.COMPLETED_WITH_ERRORS,
        AnalysisJobStatus.FAILED,
        AnalysisJobStatus.CANCELLED,
    ],
)
def test_cancel_a_non_queued_job_is_rejected_and_status_unchanged(client, db_session, status):
    headers = _user_headers(client, db_session)
    analysis_id = _create_job(client, headers)
    _set_status(db_session, analysis_id, status)

    response = client.post(f"/analyses/{analysis_id}/cancel", headers=headers)

    assert response.status_code == 409
    get_response = client.get(f"/analyses/{analysis_id}", headers=headers)
    assert get_response.json()["status"] == status.value


def test_cancel_another_accounts_job_is_rejected(client, db_session):
    owner_headers = _user_headers(client, db_session, email="owner@vives.be")
    analysis_id = _create_job(client, owner_headers)
    other_headers = _user_headers(client, db_session, email="other@vives.be")

    response = client.post(f"/analyses/{analysis_id}/cancel", headers=other_headers)

    assert response.status_code == 404
    get_response = client.get(f"/analyses/{analysis_id}", headers=owner_headers)
    assert get_response.json()["status"] == "queued"


def test_cancel_an_unknown_job_is_not_found(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.post("/analyses/999999/cancel", headers=headers)

    assert response.status_code == 404


def test_cancel_unauthenticated_is_rejected(client, db_session):
    assert client.post("/analyses/1/cancel").status_code == 401
