"""API-seam tests for POST /consolidations and GET /consolidations/{id}/download
(ticket #115, part of issue #113). Real HTTP routes, real migrated Postgres,
fake S3, fake consolidation runner (`consolidation_runner` fixture) — never
the real consolidation/ pipeline, which tests/test_consolidation_service.py
exercises directly instead. These tests verify orchestration: upload
validation, temp-file lifecycle, atomicity, audit logging, and error
mapping.
"""

from io import BytesIO

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog
from app.models.consolidation import Consolidation
from app.services.consolidation import ConsolidationInputError
from tests.helpers import identity_headers


def _upload(client, *, condition="ME_ZE", filename="export.xlsx", content=b"fake-excel-bytes"):
    return client.post(
        "/api/consolidations",
        data={"condition": condition},
        files={"file": (filename, BytesIO(content), "application/octet-stream")},
    )


def test_create_consolidation_succeeds_and_persists_the_result(
    client, db_session, s3_client, consolidation_runner
):
    consolidation_runner.result_bytes = b"consolidated-workbook-bytes"

    response = _upload(client, filename="export.xlsx", content=b"input-bytes")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["original_filename"] == "export.xlsx"
    assert body["condition"] == "ME_ZE"
    assert body["failure_reason"] is None
    assert body["input_size_bytes"] == len(b"input-bytes")
    assert body["result_size_bytes"] == len(b"consolidated-workbook-bytes")

    row = db_session.get(Consolidation, body["id"])
    assert row is not None
    assert row.result_storage_key is not None
    assert s3_client.objects[row.result_storage_key] == b"consolidated-workbook-bytes"


def test_create_consolidation_invokes_the_runner_with_the_uploaded_bytes_and_condition(
    client, consolidation_runner
):
    response = _upload(client, condition="ZE", content=b"the-uploaded-bytes")

    assert response.status_code == 201, response.text
    assert len(consolidation_runner.calls) == 1
    input_bytes, condition = consolidation_runner.calls[0]
    assert input_bytes == b"the-uploaded-bytes"
    assert condition.value == "ZE"


def test_create_consolidation_removes_the_temp_workspace_after_success(
    client, consolidation_upload_root
):
    response = _upload(client)

    assert response.status_code == 201, response.text
    assert list(consolidation_upload_root.iterdir()) == []


def test_create_consolidation_with_domain_validation_failure_is_still_a_201(
    client, db_session, consolidation_runner
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

    row = db_session.get(Consolidation, body["id"])
    assert row.result_storage_key is None


def test_create_consolidation_removes_the_temp_workspace_after_a_domain_failure(
    client, consolidation_upload_root, consolidation_runner
):
    consolidation_runner.error = ConsolidationInputError("bad input")

    response = _upload(client)

    assert response.status_code == 201, response.text
    assert list(consolidation_upload_root.iterdir()) == []


def test_create_consolidation_never_persists_a_row_before_the_result_is_uploaded(
    client, db_session, monkeypatch, s3_client, consolidation_upload_root
):
    """Atomicity (issue #113): if persisting the result fails, no
    Consolidation row is ever written at all — never one that claims
    `completed` (or any status) without a real result behind it. The
    generic failure propagates uncaught (a genuine bug/system problem, not
    an expected outcome — see create_consolidation's docstring); the test
    client re-raises it rather than turning it into a response, same as
    any unhandled exception would in a live deployment's absence of a
    catch-all handler. The temp workspace is still cleaned up regardless.
    """

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated storage outage")

    monkeypatch.setattr(s3_client, "upload_file", _raise)

    with pytest.raises(RuntimeError, match="simulated storage outage"):
        _upload(client)

    assert db_session.scalar(select(Consolidation)) is None
    assert list(consolidation_upload_root.iterdir()) == []


def test_create_consolidation_rejects_a_non_xlsx_extension(client, db_session):
    response = client.post(
        "/api/consolidations",
        data={"condition": "ME_ZE"},
        files={"file": ("export.csv", BytesIO(b"not-excel"), "text/csv")},
    )

    assert response.status_code == 400
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
        data={"condition": "ME_ZE"},
        files={"file": ("export.xlsx", BytesIO(b"bytes"), "application/octet-stream")},
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
        data={"condition": "ME_ZE"},
        files={"file": ("export.xlsx", BytesIO(b"bytes"), "application/octet-stream")},
        headers=identity_headers("owner@vives.be"),
    )
    consolidation_id = create_response.json()["id"]

    response = client.get(
        f"/api/consolidations/{consolidation_id}/download",
        headers=identity_headers("someone-else@vives.be"),
    )

    assert response.status_code == 200
