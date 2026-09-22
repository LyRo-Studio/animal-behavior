import pytest

from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from tests.helpers import identity_headers

# Ticket #46's acceptance criteria: exercised through the HTTP API. No
# worker exists yet (ticket #47), so a job is only ever `queued` through
# the API itself — the non-`queued` statuses exercised below are set
# directly on the row via `db_session`, standing in for what the worker
# will do once it exists.


def _create_job(client, headers=None, *, test_id="T001", cut="cuts/T001/T001_C2_ME_F1.mp4"):
    response = client.post(
        "/api/analyses", json={"test_ids": [test_id], "cuts": [cut]}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _set_status(db_session, analysis_id: int, status: AnalysisJobStatus) -> None:
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = status
    db_session.add(job)
    db_session.commit()


def test_cancel_a_queued_job_sets_status_to_cancelled(client, db_session):
    analysis_id = _create_job(client)

    response = client.post(f"/api/analyses/{analysis_id}/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["finished_at"] is not None

    get_response = client.get(f"/api/analyses/{analysis_id}")
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
    analysis_id = _create_job(client)
    _set_status(db_session, analysis_id, status)

    response = client.post(f"/api/analyses/{analysis_id}/cancel")

    assert response.status_code == 409
    get_response = client.get(f"/api/analyses/{analysis_id}")
    assert get_response.json()["status"] == status.value


def test_cancel_a_job_created_by_another_identity_succeeds(client):
    """Analyses are fully shared (ticket #72): anyone past Mechatronics may
    cancel a queued job, whoever ran it."""
    analysis_id = _create_job(client, identity_headers("owner@vives.be"))

    response = client.post(
        f"/api/analyses/{analysis_id}/cancel", headers=identity_headers("other@vives.be")
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_an_unknown_job_is_not_found(client):
    response = client.post("/api/analyses/999999/cancel")

    assert response.status_code == 404
