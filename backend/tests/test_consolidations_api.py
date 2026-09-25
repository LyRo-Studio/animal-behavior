"""API-seam tests for POST /consolidations and GET /consolidations/{id}/download
(ticket #115, part of issue #113). Real HTTP routes, real migrated Postgres,
fake S3, fake consolidation runner (`consolidation_runner` fixture) — never
the real consolidation/ pipeline, which tests/test_consolidation_service.py
exercises directly instead. These tests verify orchestration: upload
validation, temp-file lifecycle, atomicity, audit logging, and error
mapping.
"""

import logging
from datetime import UTC, datetime, timedelta
from io import BytesIO

import openpyxl
import pytest
from sqlalchemy import select

import app.services.consolidation as consolidation_service
from app.models.audit_log import AuditAction, AuditLog
from app.models.consolidation import Consolidation, ConsolidationStatus
from app.services.consolidation import (
    ConsolidationInputError,
    list_consolidations,
    reconcile_stale_consolidations,
    start_consolidation,
)
from tests.helpers import identity_headers


def _workbook_bytes(title: str = "Results") -> bytes:
    """A minimal real .xlsx — the endpoint checks the upload opens as a
    workbook before ever reaching the (faked) runner."""
    workbook = openpyxl.Workbook()
    workbook.active.title = title
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


_WORKBOOK_BYTES = _workbook_bytes()


def _upload(client, *, filename="export.xlsx", content=_WORKBOOK_BYTES):
    return client.post(
        "/api/consolidations",
        files={"file": (filename, BytesIO(content), "application/octet-stream")},
    )


def test_create_consolidation_succeeds_and_persists_the_result(
    client, db_session, s3_client, consolidation_runner
):
    consolidation_runner.result_bytes = b"consolidated-workbook-bytes"

    response = _upload(client, filename="export.xlsx")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["original_filename"] == "export.xlsx"
    # Ticket #149: one upload produces every Consolidation level.
    assert "condition" not in body
    assert body["failure_reason"] is None
    assert body["input_size_bytes"] == len(_WORKBOOK_BYTES)
    assert body["result_size_bytes"] == len(b"consolidated-workbook-bytes")

    row = db_session.get(Consolidation, body["id"])
    assert row is not None
    assert row.result_storage_key is not None
    assert s3_client.objects[row.result_storage_key] == b"consolidated-workbook-bytes"


def test_create_consolidation_invokes_the_runner_with_the_uploaded_bytes(
    client, consolidation_runner
):
    content = _workbook_bytes(title="the-uploaded-workbook")

    response = _upload(client, content=content)

    assert response.status_code == 201, response.text
    assert consolidation_runner.calls == [content]


def test_create_consolidation_removes_the_temp_workspace_after_success(
    client, consolidation_upload_root
):
    response = _upload(client)

    assert response.status_code == 201, response.text
    assert list(consolidation_upload_root.iterdir()) == []


def test_create_consolidation_with_domain_validation_failure_is_still_a_201(
    client, db_session, s3_client, consolidation_runner
):
    """Issue #113: a failed *consolidation attempt* is a successfully
    handled request (still 201), not an HTTP error — it still needs to
    show up in history with a clear reason."""
    consolidation_runner.error = ConsolidationInputError("fases: ontbrekende kolommen ['duur_s']")

    response = _upload(client)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "fases: ontbrekende kolommen ['duur_s']"
    assert body["result_size_bytes"] is None

    # The key is recorded at creation (ticket #121), but nothing was stored.
    row = db_session.get(Consolidation, body["id"])
    assert row.result_storage_key not in s3_client.objects


def test_create_consolidation_removes_the_temp_workspace_after_a_domain_failure(
    client, consolidation_upload_root, consolidation_runner
):
    consolidation_runner.error = ConsolidationInputError("bad input")

    response = _upload(client)

    assert response.status_code == 201, response.text
    assert list(consolidation_upload_root.iterdir()) == []


def test_create_consolidation_is_processing_with_a_started_audit_row_while_running(
    client, db_session, consolidation_runner
):
    """The row is created (status `processing`) and CONSOLIDATION_STARTED
    committed before the runner is invoked — so the attempt is on record
    even if the process dies mid-run."""
    observed = {}

    def _observe() -> None:
        db_session.expire_all()
        observed["statuses"] = list(db_session.scalars(select(Consolidation.status)))
        observed["started"] = db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.CONSOLIDATION_STARTED)
        )

    consolidation_runner.on_run = _observe

    response = _upload(client)

    assert response.status_code == 201, response.text
    assert observed["statuses"] == [ConsolidationStatus.PROCESSING]
    assert observed["started"] is not None
    assert observed["started"].target_type == "consolidation"
    assert observed["started"].target == str(response.json()["id"])


def test_create_consolidation_with_an_unexpected_runner_error_is_a_generic_failure(
    client, db_session, consolidation_runner, consolidation_upload_root, caplog, monkeypatch
):
    """A non-domain exception is logged in full server-side, but only a
    generic message reaches the user and the row."""
    # alembic/env.py's fileConfig (run by the session-wide migration
    # fixture) disables every logger that already exists, this one included.
    monkeypatch.setattr(logging.getLogger("app.services.consolidation"), "disabled", False)
    consolidation_runner.error = RuntimeError("secret internal detail /srv/path")

    response = _upload(client)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "Consolidation failed."
    assert "secret internal detail" not in response.text
    assert "secret internal detail" in caplog.text
    assert list(consolidation_upload_root.iterdir()) == []

    actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.target == str(body["id"])).order_by(AuditLog.id)
        )
    )
    assert actions == [AuditAction.CONSOLIDATION_STARTED, AuditAction.CONSOLIDATION_FAILED]


def test_create_consolidation_is_never_completed_if_storing_the_result_fails(
    client, db_session, monkeypatch, s3_client, consolidation_upload_root
):
    """Atomicity (issue #113): a row only becomes `completed` once the
    result upload is confirmed — a storage failure leaves it `failed`, with
    nothing stored at its key, and the temp workspace is still cleaned
    up."""

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated storage outage")

    monkeypatch.setattr(s3_client, "upload_file", _raise)

    response = _upload(client)

    assert response.status_code == 201, response.text
    row = db_session.get(Consolidation, response.json()["id"])
    assert row.status == ConsolidationStatus.FAILED
    assert row.failure_reason == "Consolidation failed."
    assert row.result_storage_key not in s3_client.objects
    assert list(consolidation_upload_root.iterdir()) == []
    actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.target == str(row.id)).order_by(AuditLog.id)
        )
    )
    assert actions == [AuditAction.CONSOLIDATION_STARTED, AuditAction.CONSOLIDATION_FAILED]


def test_create_consolidation_rejects_a_non_xlsx_extension(client, db_session):
    response = client.post(
        "/api/consolidations",
        files={"file": ("export.csv", BytesIO(b"not-excel"), "text/csv")},
    )

    assert response.status_code == 400
    assert db_session.scalar(select(Consolidation)) is None


def test_create_consolidation_rejects_a_file_that_is_not_an_openable_workbook(
    client, db_session, consolidation_runner
):
    response = _upload(client, content=b"not really an excel file")

    assert response.status_code == 400
    assert response.json()["detail"] == "The uploaded file is not a valid Excel workbook."
    assert consolidation_runner.calls == []
    assert db_session.scalar(select(Consolidation)) is None


def test_create_consolidation_rejects_an_empty_file(client, db_session):
    response = _upload(client, content=b"")

    assert response.status_code == 400
    assert db_session.scalar(select(Consolidation)) is None


def test_create_consolidation_rejects_a_too_long_filename(client, db_session):
    long_name = "a" * 252 + ".xlsx"  # 257 chars, over ORIGINAL_FILENAME_MAX_LENGTH (255)

    response = _upload(client, filename=long_name)

    assert response.status_code == 400
    assert db_session.scalar(select(Consolidation)) is None


def test_create_consolidation_rejects_an_oversized_file(client, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "consolidation_max_file_size_bytes", 4)

    response = _upload(client, content=b"way-too-big")

    assert response.status_code == 400
    assert db_session.scalar(select(Consolidation)) is None


def test_create_consolidation_writes_a_completed_audit_row(client, db_session):
    response = _upload(client)
    consolidation_id = response.json()["id"]

    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.CONSOLIDATION_COMPLETED)
    )
    assert row is not None
    assert row.target_type == "consolidation"
    assert row.target == str(consolidation_id)


def test_create_consolidation_writes_a_failed_audit_row(client, db_session, consolidation_runner):
    consolidation_runner.error = ConsolidationInputError("bad input")

    response = _upload(client)
    consolidation_id = response.json()["id"]

    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.CONSOLIDATION_FAILED)
    )
    assert row is not None
    assert row.target == str(consolidation_id)
    assert row.failure_reason == "bad input"


def test_create_consolidation_records_the_requesting_identity(client, db_session):
    response = client.post(
        "/api/consolidations",
        files={"file": ("export.xlsx", BytesIO(_WORKBOOK_BYTES), "application/octet-stream")},
        headers=identity_headers("researcher@vives.be"),
    )

    assert response.status_code == 201, response.text
    row = db_session.get(Consolidation, response.json()["id"])
    assert row.requested_by_identity == "researcher@vives.be"


def test_download_consolidation_returns_the_result_bytes(client, s3_client, consolidation_runner):
    consolidation_runner.result_bytes = b"the-consolidated-file"
    create_response = _upload(client)
    consolidation_id = create_response.json()["id"]

    response = client.get(f"/api/consolidations/{consolidation_id}/download")

    assert response.status_code == 200
    assert response.content == b"the-consolidated-file"
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="export.xlsx"' in response.headers["content-disposition"]


def test_download_consolidation_writes_a_downloaded_audit_row(client, db_session):
    create_response = _upload(client)
    consolidation_id = create_response.json()["id"]

    response = client.get(f"/api/consolidations/{consolidation_id}/download")

    assert response.status_code == 200
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.CONSOLIDATION_DOWNLOADED)
    )
    assert row is not None
    assert row.target == str(consolidation_id)


def test_download_consolidation_for_an_unknown_id_is_not_found(client):
    response = client.get("/api/consolidations/999999/download")

    assert response.status_code == 404


def test_download_consolidation_for_a_failed_consolidation_is_rejected(
    client, consolidation_runner
):
    consolidation_runner.error = ConsolidationInputError("bad input")
    create_response = _upload(client)
    consolidation_id = create_response.json()["id"]

    response = client.get(f"/api/consolidations/{consolidation_id}/download")

    assert response.status_code == 409


def test_download_consolidation_still_processing_is_rejected(client, db_session):
    """A `processing` row already has its result key (ticket #121) but no
    result behind it yet — a 409 like a failed one, not a storage 404."""
    consolidation_id = start_consolidation(
        db_session,
        requested_by_identity=None,
        original_filename="running.xlsx",
        input_size_bytes=1,
    ).id

    response = client.get(f"/api/consolidations/{consolidation_id}/download")

    assert response.status_code == 409


def test_download_consolidation_missing_from_s3_is_not_found(client, db_session, s3_client):
    create_response = _upload(client)
    consolidation_id = create_response.json()["id"]
    row = db_session.get(Consolidation, consolidation_id)
    del s3_client.objects[row.result_storage_key]

    response = client.get(f"/api/consolidations/{consolidation_id}/download")

    assert response.status_code == 404


def test_download_consolidation_created_by_another_identity_still_succeeds(client):
    """Fully shared visibility (issue #113, mirroring ticket #72's
    /analyses precedent) — no ownership check on download."""
    create_response = client.post(
        "/api/consolidations",
        files={"file": ("export.xlsx", BytesIO(_WORKBOOK_BYTES), "application/octet-stream")},
        headers=identity_headers("owner@vives.be"),
    )
    consolidation_id = create_response.json()["id"]

    response = client.get(
        f"/api/consolidations/{consolidation_id}/download",
        headers=identity_headers("someone-else@vives.be"),
    )

    assert response.status_code == 200


def test_list_consolidations_returns_every_identitys_consolidations(client):
    """Fully shared visibility (ticket #116, mirroring /analyses since
    ticket #72) — no per-identity filtering of the history list."""
    client.post(
        "/api/consolidations",
        files={"file": ("mine.xlsx", BytesIO(_WORKBOOK_BYTES), "application/octet-stream")},
        headers=identity_headers("mine@vives.be"),
    )
    client.post(
        "/api/consolidations",
        files={"file": ("theirs.xlsx", BytesIO(_WORKBOOK_BYTES), "application/octet-stream")},
        headers=identity_headers("other@vives.be"),
    )

    response = client.get("/api/consolidations", headers=identity_headers("mine@vives.be"))

    assert response.status_code == 200
    listed = {(row["original_filename"], row["requested_by_identity"]) for row in response.json()}
    assert listed == {("mine.xlsx", "mine@vives.be"), ("theirs.xlsx", "other@vives.be")}


def test_list_consolidations_includes_completed_and_failed_rows_newest_first(
    client, consolidation_runner
):
    completed_id = _upload(client, filename="good.xlsx").json()["id"]
    consolidation_runner.error = ConsolidationInputError("fases: ontbrekende kolommen")
    failed_id = _upload(client, filename="bad.xlsx").json()["id"]

    response = client.get("/api/consolidations")

    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body] == [failed_id, completed_id]
    failed, completed = body
    assert failed["status"] == "failed"
    assert failed["failure_reason"] == "fases: ontbrekende kolommen"
    assert failed["original_filename"] == "bad.xlsx"
    assert completed["status"] == "completed"
    assert completed["failure_reason"] is None
    for row in body:
        assert {"original_filename", "display_name", "created_at", "status"} <= row.keys()
        assert "condition" not in row


def test_list_consolidations_is_empty_when_there_are_none(client):
    response = client.get("/api/consolidations")

    assert response.status_code == 200
    assert response.json() == []


def test_list_consolidations_is_bounded_to_the_newest_rows(db_session):
    """Every consolidation is visible to everyone, so an unscoped listing
    would grow without bound — capped like list_analysis_jobs
    (ENGINEERING-STANDARDS.md §5: avoid unbounded database queries)."""
    ids = [
        start_consolidation(
            db_session,
            requested_by_identity=None,
            original_filename=f"export-{index}.xlsx",
            input_size_bytes=1,
        ).id
        for index in range(3)
    ]

    listed = list_consolidations(db_session, limit=2)

    assert [row.id for row in listed] == [ids[2], ids[1]]


def _rename(client, consolidation_id, display_name, *, headers=None):
    return client.patch(
        f"/api/consolidations/{consolidation_id}",
        json={"display_name": display_name},
        headers=headers,
    )


def test_rename_consolidation_updates_display_name_only(client, db_session):
    created = _upload(client, filename="export.xlsx").json()

    response = _rename(client, created["id"], "Pilot dogs, week 3")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Pilot dogs, week 3"
    assert body["original_filename"] == "export.xlsx"
    assert {key: value for key, value in body.items() if key != "display_name"} == {
        key: value for key, value in created.items() if key != "display_name"
    }
    row = db_session.get(Consolidation, created["id"])
    db_session.refresh(row)
    assert row.display_name == "Pilot dogs, week 3"
    assert row.original_filename == "export.xlsx"


def test_rename_consolidation_keeps_the_original_filename_visible_in_the_history_list(client):
    consolidation_id = _upload(client, filename="export.xlsx").json()["id"]
    _rename(client, consolidation_id, "Pilot dogs, week 3")

    listed = client.get("/api/consolidations").json()

    [row] = [row for row in listed if row["id"] == consolidation_id]
    assert row["display_name"] == "Pilot dogs, week 3"
    assert row["original_filename"] == "export.xlsx"


def test_rename_consolidation_does_not_change_the_download_filename(client):
    consolidation_id = _upload(client, filename="export.xlsx").json()["id"]
    _rename(client, consolidation_id, "Pilot dogs, week 3")

    response = client.get(f"/api/consolidations/{consolidation_id}/download")

    assert response.status_code == 200
    assert 'filename="export.xlsx"' in response.headers["content-disposition"]


def test_rename_consolidation_writes_a_renamed_audit_row(client, db_session):
    consolidation_id = _upload(client).json()["id"]

    # The audit log's own identity source (ADR-0005), not the attribution
    # header — unverified here, since no JWT is sent.
    response = _rename(
        client,
        consolidation_id,
        "Pilot dogs, week 3",
        headers={"X-authentik-email": "renamer@vives.be"},
    )

    assert response.status_code == 200, response.text
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.CONSOLIDATION_RENAMED)
    ).all()
    assert len(rows) == 1
    assert rows[0].target_type == "consolidation"
    assert rows[0].target == str(consolidation_id)
    assert rows[0].identity == "renamer@vives.be"
    assert rows[0].identity_verified is False


def test_rename_consolidation_trims_surrounding_whitespace(client):
    consolidation_id = _upload(client).json()["id"]

    response = _rename(client, consolidation_id, "  Pilot dogs  ")

    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Pilot dogs"


def test_rename_consolidation_to_blank_or_null_clears_the_display_name(client):
    """Clearing the label falls back to original_filename (the frontend's
    job) — a blank name is never stored as a label of its own."""
    consolidation_id = _upload(client).json()["id"]
    _rename(client, consolidation_id, "Pilot dogs")

    blank = _rename(client, consolidation_id, "   ")
    assert blank.status_code == 200, blank.text
    assert blank.json()["display_name"] is None

    _rename(client, consolidation_id, "Pilot dogs")
    cleared = _rename(client, consolidation_id, None)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["display_name"] is None


def test_rename_consolidation_rejects_a_too_long_display_name(client, db_session):
    consolidation_id = _upload(client).json()["id"]

    response = _rename(client, consolidation_id, "x" * 256)

    assert response.status_code == 422
    row = db_session.get(Consolidation, consolidation_id)
    db_session.refresh(row)
    assert row.display_name is None


def test_rename_consolidation_requires_the_display_name_field(client):
    consolidation_id = _upload(client).json()["id"]

    response = client.patch(f"/api/consolidations/{consolidation_id}", json={})

    assert response.status_code == 422


def test_rename_consolidation_rejects_an_attempt_to_change_the_original_filename(
    client, db_session
):
    """original_filename is immutable (issue #113) — not even an accepted
    field of the rename request."""
    consolidation_id = _upload(client, filename="export.xlsx").json()["id"]

    response = client.patch(
        f"/api/consolidations/{consolidation_id}",
        json={"display_name": "Renamed", "original_filename": "forged.xlsx"},
    )

    assert response.status_code == 422
    row = db_session.get(Consolidation, consolidation_id)
    db_session.refresh(row)
    assert row.original_filename == "export.xlsx"


def test_rename_consolidation_for_an_unknown_id_is_not_found(client, db_session):
    response = _rename(client, 999999, "Anything")

    assert response.status_code == 404
    assert (
        db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.CONSOLIDATION_RENAMED)
        )
        is None
    )


def test_rename_consolidation_created_by_another_identity_still_succeeds(client):
    """Fully shared (issue #113, ADR-0004) — no ownership check on rename."""
    create_response = client.post(
        "/api/consolidations",
        files={"file": ("export.xlsx", BytesIO(_WORKBOOK_BYTES), "application/octet-stream")},
        headers=identity_headers("owner@vives.be"),
    )

    response = _rename(
        client,
        create_response.json()["id"],
        "Renamed by someone else",
        headers=identity_headers("someone-else@vives.be"),
    )

    assert response.status_code == 200, response.text


def _deleted_audit_rows(db_session):
    return db_session.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.CONSOLIDATION_DELETED)
    ).all()


def test_delete_consolidation_removes_the_stored_result_and_the_row(client, db_session, s3_client):
    consolidation_id = _upload(client).json()["id"]
    key = db_session.get(Consolidation, consolidation_id).result_storage_key
    assert key in s3_client.objects

    response = client.delete(f"/api/consolidations/{consolidation_id}")

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert key not in s3_client.objects
    db_session.expire_all()
    assert db_session.get(Consolidation, consolidation_id) is None


def test_delete_consolidation_then_download_is_not_found(client):
    consolidation_id = _upload(client).json()["id"]

    client.delete(f"/api/consolidations/{consolidation_id}")
    response = client.get(f"/api/consolidations/{consolidation_id}/download")

    assert response.status_code == 404
    assert response.json() == {"detail": "Consolidation not found."}


def test_delete_consolidation_removes_it_from_the_history_list(client):
    kept_id = _upload(client, filename="kept.xlsx").json()["id"]
    deleted_id = _upload(client, filename="deleted.xlsx").json()["id"]

    client.delete(f"/api/consolidations/{deleted_id}")

    assert [row["id"] for row in client.get("/api/consolidations").json()] == [kept_id]


def test_delete_consolidation_writes_a_deleted_audit_row(client, db_session):
    consolidation_id = _upload(client).json()["id"]

    # The audit log's own identity source (ADR-0005), unverified here.
    response = client.delete(
        f"/api/consolidations/{consolidation_id}",
        headers={"X-authentik-email": "deleter@vives.be"},
    )

    assert response.status_code == 204, response.text
    [row] = _deleted_audit_rows(db_session)
    assert row.target_type == "consolidation"
    assert row.target == str(consolidation_id)
    assert row.identity == "deleter@vives.be"
    assert row.identity_verified is False


def test_delete_consolidation_is_on_record_even_if_storage_fails_and_can_be_retried(
    client, db_session, s3_client, monkeypatch
):
    """CONSOLIDATION_DELETED is committed before anything is deleted
    (ticket #118: "not after — so a failure partway through doesn't lose
    the record"). A storage failure therefore leaves the attempt on record
    with the consolidation still intact, and a retry finishes the job."""
    consolidation_id = _upload(client).json()["id"]
    key = db_session.get(Consolidation, consolidation_id).result_storage_key
    real_delete_object = s3_client.delete_object

    def _raise(key):
        raise RuntimeError("simulated storage outage")

    monkeypatch.setattr(s3_client, "delete_object", _raise)

    with pytest.raises(RuntimeError, match="simulated storage outage"):
        client.delete(f"/api/consolidations/{consolidation_id}")

    db_session.expire_all()
    assert db_session.get(Consolidation, consolidation_id) is not None
    assert key in s3_client.objects
    assert len(_deleted_audit_rows(db_session)) == 1

    monkeypatch.setattr(s3_client, "delete_object", real_delete_object)
    retry = client.delete(f"/api/consolidations/{consolidation_id}")

    assert retry.status_code == 204, retry.text
    db_session.expire_all()
    assert db_session.get(Consolidation, consolidation_id) is None
    assert key not in s3_client.objects
    assert len(_deleted_audit_rows(db_session)) == 2


def test_delete_consolidation_is_aborted_if_the_audit_row_cannot_be_written(
    client, db_session, s3_client, monkeypatch
):
    """Unlike every other audited action (issue #79: best-effort, never
    blocks the action), a delete must not go ahead unrecorded (ticket #118:
    "a failure partway through doesn't lose the record")."""
    consolidation_id = _upload(client).json()["id"]
    key = db_session.get(Consolidation, consolidation_id).result_storage_key

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(consolidation_service, "record_required_audit_event", _raise)

    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        client.delete(f"/api/consolidations/{consolidation_id}")

    db_session.expire_all()
    assert db_session.get(Consolidation, consolidation_id) is not None
    assert key in s3_client.objects


def test_delete_consolidation_succeeds_when_the_stored_result_is_already_gone(
    client, db_session, s3_client
):
    """A retry after a half-finished delete (object removed, database commit
    lost) must still be able to finish the job."""
    consolidation_id = _upload(client).json()["id"]
    del s3_client.objects[db_session.get(Consolidation, consolidation_id).result_storage_key]

    response = client.delete(f"/api/consolidations/{consolidation_id}")

    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(Consolidation, consolidation_id) is None


def test_delete_a_failed_consolidation_removes_the_row(client, db_session, consolidation_runner):
    """A failed consolidation never stored a result — only the row goes."""
    consolidation_runner.error = ConsolidationInputError("bad input")
    consolidation_id = _upload(client).json()["id"]

    response = client.delete(f"/api/consolidations/{consolidation_id}")

    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(Consolidation, consolidation_id) is None
    assert len(_deleted_audit_rows(db_session)) == 1


def test_delete_a_processing_consolidation_is_rejected(client, db_session):
    """A `processing` row may still be running in another request, whose
    final update would then fail and orphan its uploaded result — so it
    can't be deleted (rows stuck in `processing` are #121's problem)."""
    consolidation_id = start_consolidation(
        db_session,
        requested_by_identity=None,
        original_filename="running.xlsx",
        input_size_bytes=1,
    ).id

    response = client.delete(f"/api/consolidations/{consolidation_id}")

    assert response.status_code == 409
    assert response.json() == {"detail": "A consolidation still processing can't be deleted."}
    db_session.expire_all()
    assert db_session.get(Consolidation, consolidation_id) is not None
    assert _deleted_audit_rows(db_session) == []


def test_delete_consolidation_for_an_unknown_id_is_not_found(client, db_session):
    response = client.delete("/api/consolidations/999999")

    assert response.status_code == 404
    assert _deleted_audit_rows(db_session) == []


def test_delete_consolidation_created_by_another_identity_still_succeeds(client):
    """Fully shared (issue #113, ADR-0004) — no ownership check on delete."""
    create_response = client.post(
        "/api/consolidations",
        files={"file": ("export.xlsx", BytesIO(_WORKBOOK_BYTES), "application/octet-stream")},
        headers=identity_headers("owner@vives.be"),
    )

    response = client.delete(
        f"/api/consolidations/{create_response.json()['id']}",
        headers=identity_headers("someone-else@vives.be"),
    )

    assert response.status_code == 204, response.text


def test_a_run_finishing_after_it_was_reconciled_as_stale_keeps_the_failed_outcome(
    client, db_session, s3_client, consolidation_runner
):
    """Ticket #121: a run that outlives the stale threshold must not undo
    the reconciler's `failed` (and its CONSOLIDATION_FAILED audit row), nor
    leave behind the result it uploaded afterwards."""

    def _reconcile_mid_run() -> None:
        [row] = db_session.scalars(select(Consolidation))
        row.created_at = datetime.now(UTC) - timedelta(hours=1)
        db_session.commit()
        reconcile_stale_consolidations(db_session, s3=s3_client, stale_after=timedelta(minutes=30))

    consolidation_runner.on_run = _reconcile_mid_run

    response = _upload(client)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == (
        "Consolidation was interrupted: still processing after 30 minutes."
    )
    row = db_session.get(Consolidation, body["id"])
    assert row.result_storage_key not in s3_client.objects
    actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.target == str(row.id)).order_by(AuditLog.id)
        )
    )
    assert actions == [AuditAction.CONSOLIDATION_STARTED, AuditAction.CONSOLIDATION_FAILED]
