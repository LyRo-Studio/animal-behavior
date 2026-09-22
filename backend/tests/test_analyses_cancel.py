import pytest
from sqlalchemy import select

from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from app.models.audit_log import AuditAction, AuditLog
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


def test_cancel_a_queued_job_writes_an_analysis_cancelled_audit_row(client, db_session):
    """Ticket #83 / issue #79: mirrors test_analyses.py's own
    ANALYSIS_STARTED coverage — attributed via `get_verified_identity`,
    independent of the ordinary, unverified attribution on the job itself.
    `_create_job` already writes its own ANALYSIS_STARTED row, so this
    filters by action rather than assuming this is the only row."""
    analysis_id = _create_job(client)

    response = client.post(f"/api/analyses/{analysis_id}/cancel")

    assert response.status_code == 200, response.text
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.ANALYSIS_CANCELLED)
    )
    assert row is not None
    assert row.target_type == "analysis_job"
    assert row.target == str(analysis_id)
    assert row.identity is None
    assert row.identity_verified is False


def test_cancel_audit_row_carries_the_authentik_email_header_unverified(client, db_session):
    """No JWT/JWKS headers sent, so the row is unverified — but the plain
    `X-authentik-email` value is still recorded (ADR-0005), same as
    test_analyses.py's own equivalent coverage for ANALYSIS_STARTED."""
    analysis_id = _create_job(client)

    response = client.post(
        f"/api/analyses/{analysis_id}/cancel",
        headers={"X-authentik-email": "jan.peeters@vives.be"},
    )
    assert response.status_code == 200

    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.ANALYSIS_CANCELLED)
    )
    assert row.identity == "jan.peeters@vives.be"
    assert row.identity_verified is False


def test_cancel_a_non_queued_job_does_not_write_an_analysis_cancelled_audit_row(client, db_session):
    analysis_id = _create_job(client)
    _set_status(db_session, analysis_id, AnalysisJobStatus.RUNNING)

    response = client.post(f"/api/analyses/{analysis_id}/cancel")

    assert response.status_code == 409
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.action == AuditAction.ANALYSIS_CANCELLED))
        is None
    )


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
