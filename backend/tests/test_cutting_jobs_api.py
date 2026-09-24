"""HTTP-level tests for the cutting-job upload/creation endpoints (ticket
#94, part of issue #93's Feature C) — FastAPI TestClient, small in-memory
byte payloads (issue #93's Testing Decisions: no real multi-GB files
needed). Direct service-level tests live in test_cutting_jobs.py and
test_cutting_uploads.py; this file covers the HTTP wiring: chunk
resumption, upload-id resolution, error -> status-code mapping, and rate
limiting.
"""

import json
from datetime import time
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditLog
from app.models.cutting_job import CuttingJob, CuttingJobOutputStatus, CuttingJobStatus
from tests.helpers import identity_headers

_CONDITIONS = ("ME", "ZE")
_PHASE_HEADERS = [f"{condition}_F{n}" for condition in _CONDITIONS for n in range(1, 9)]
_HEADERS = ["Test ID", "Dog ID", "C1/C2", *_PHASE_HEADERS]


def _excel_bytes(
    test_id="T001", *, reference_camera="C1", phases=("ME_F1", "ME_F2"), extra_test_ids=()
) -> bytes:
    """One identical row per Test: `test_id`, then each of `extra_test_ids`."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADERS)
    for row_test_id in (test_id, *extra_test_ids):
        row = [row_test_id, "Rex", reference_camera]
        for index, header in enumerate(_PHASE_HEADERS):
            row.append(time(index + 1, 0, 0) if header in phases else None)
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _start_upload(client, *, test_id="T001", camera="C1", filename=None, size=10):
    filename = filename or f"{test_id}_{camera}_source.mp4"
    response = client.post(
        "/api/cutting-jobs/uploads",
        json={
            "test_id": test_id,
            "camera": camera,
            "filename": filename,
            "total_size_bytes": size,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_all_bytes(client, upload_id, data: bytes):
    response = client.patch(
        f"/api/cutting-jobs/uploads/{upload_id}",
        content=data,
        headers={"Upload-Offset": "0"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- Chunked upload endpoints ---


def test_start_upload_returns_a_zero_offset_upload(client):
    body = _start_upload(client)

    assert body["received_bytes"] == 0
    assert body["complete"] is False


def test_start_upload_rejects_a_filename_not_matching_the_camera(client):
    response = client.post(
        "/api/cutting-jobs/uploads",
        json={
            "test_id": "T001",
            "camera": "C1",
            "filename": "T001_C2_source.mp4",
            "total_size_bytes": 10,
        },
    )

    assert response.status_code == 400


def test_start_upload_is_rate_limited_per_identity(client, monkeypatch):
    monkeypatch.setattr(settings, "cutting_upload_init_rate_limit_max_attempts_per_identity", 1)
    headers = identity_headers("busy@vives.be")

    first = client.post(
        "/api/cutting-jobs/uploads",
        json={
            "test_id": "T001",
            "camera": "C1",
            "filename": "T001_C1_source.mp4",
            "total_size_bytes": 10,
        },
        headers=headers,
    )
    assert first.status_code == 201, first.text

    throttled = client.post(
        "/api/cutting-jobs/uploads",
        json={
            "test_id": "T001",
            "camera": "C1",
            "filename": "T001_C1_source.mp4",
            "total_size_bytes": 10,
        },
        headers=headers,
    )

    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


def test_start_upload_rejects_over_the_storage_cap(client, monkeypatch):
    monkeypatch.setattr(settings, "cutting_upload_storage_cap_bytes", 5)

    response = client.post(
        "/api/cutting-jobs/uploads",
        json={
            "test_id": "T001",
            "camera": "C1",
            "filename": "T001_C1_source.mp4",
            "total_size_bytes": 10,
        },
    )

    assert response.status_code == 507


def test_append_upload_chunk_advances_offset_and_completes(client):
    upload = _start_upload(client, size=10)

    result = _upload_all_bytes(client, upload["upload_id"], b"helloworld")

    assert result["received_bytes"] == 10
    assert result["complete"] is True


def test_upload_can_resume_after_a_partial_chunk(client):
    upload = _start_upload(client, size=10)

    first = client.patch(
        f"/api/cutting-jobs/uploads/{upload['upload_id']}",
        content=b"hello",
        headers={"Upload-Offset": "0"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["received_bytes"] == 5

    status_response = client.get(f"/api/cutting-jobs/uploads/{upload['upload_id']}")
    assert status_response.json()["received_bytes"] == 5

    second = client.patch(
        f"/api/cutting-jobs/uploads/{upload['upload_id']}",
        content=b"world",
        headers={"Upload-Offset": "5"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["complete"] is True


def test_append_upload_chunk_rejects_a_mismatched_offset(client):
    upload = _start_upload(client, size=10)
    client.patch(
        f"/api/cutting-jobs/uploads/{upload['upload_id']}",
        content=b"hello",
        headers={"Upload-Offset": "0"},
    )

    mismatched = client.patch(
        f"/api/cutting-jobs/uploads/{upload['upload_id']}",
        content=b"world",
        headers={"Upload-Offset": "0"},
    )

    assert mismatched.status_code == 409
    assert mismatched.headers["Upload-Offset"] == "5"


def test_append_upload_chunk_requires_the_offset_header(client):
    upload = _start_upload(client, size=10)

    response = client.patch(f"/api/cutting-jobs/uploads/{upload['upload_id']}", content=b"hello")

    assert response.status_code == 400


def test_get_upload_for_unknown_id_is_not_found(client):
    response = client.get("/api/cutting-jobs/uploads/does-not-exist")

    assert response.status_code == 404


def test_append_upload_chunk_for_unknown_id_is_not_found(client):
    response = client.patch(
        "/api/cutting-jobs/uploads/does-not-exist",
        content=b"hello",
        headers={"Upload-Offset": "0"},
    )

    assert response.status_code == 404


# --- Job creation ---


def test_create_cutting_job_with_a_single_matching_camera(client):
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["test_id"] == "T001"
    assert body["status"] == "queued"
    assert body["reference_camera"] == "C1"
    assert len(body["outputs"]) == 2  # ME_F1, ME_F2 per _excel_bytes' default


def test_create_cutting_job_rejects_an_incomplete_upload(client):
    upload = _start_upload(client, size=10)
    client.patch(
        f"/api/cutting-jobs/uploads/{upload['upload_id']}",
        content=b"hello",
        headers={"Upload-Offset": "0"},
    )
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 400


def test_create_cutting_job_rejects_an_unknown_upload_id(client):
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": "does-not-exist"},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 400


def test_create_cutting_job_rejects_with_no_uploads_at_all(client):
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001"},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 400


def test_create_cutting_job_rejects_a_mismatched_reference_camera(client):
    upload = _start_upload(client, camera="C1", size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    excel = _excel_bytes(reference_camera="C2")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 400


def test_create_cutting_job_rejects_a_missing_excel_row(client):
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    excel = _excel_bytes(test_id="T002", reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 400


def test_create_cutting_job_rejects_a_malformed_workbook(client):
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", b"not a real workbook", "application/octet-stream")},
    )

    assert response.status_code == 400


def test_create_cutting_job_rejects_an_undecodable_video(client, media_prober):
    from app.services.media_prober import MediaProbeError

    media_prober.error = MediaProbeError("ffprobe failed")
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 400


def test_create_cutting_job_rejects_an_s3_filename_collision(client, s3_client):
    upload = _start_upload(client, size=4, filename="T001_C1_source.mp4")
    _upload_all_bytes(client, upload["upload_id"], b"data")
    s3_client.objects["source/T001/T001_C1_source.mp4"] = b"already there"
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 409


def test_create_cutting_job_asks_for_confirmation_before_recutting_a_test_with_cuts(
    client, s3_client, db_session
):
    """Ticket #96: a distinct 409 (machine-readable `code`, still a plain
    `detail` string) — not the source-collision 409 above, and not a job."""
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"an earlier cut"
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "cuts_already_exist"
    assert isinstance(body["detail"], str)
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_recuts_once_overwrite_is_confirmed(client, s3_client):
    """The confirmed follow-up reuses the very same upload — the blocked
    first request consumed nothing."""
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"an earlier cut"
    excel = _excel_bytes(reference_camera="C1")
    request = {
        "data": {"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        "files": {"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    }
    blocked = client.post("/api/cutting-jobs", **request)
    assert blocked.status_code == 409

    confirmed = client.post(
        "/api/cutting-jobs",
        data={**request["data"], "confirm_overwrite": "true"},
        files=request["files"],
    )

    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["status"] == "queued"


def test_source_collision_conflict_is_distinguishable_from_the_recut_confirmation(
    client, s3_client
):
    """The upload-time collision is never overridable — `confirm_overwrite`
    doesn't bypass it, and its 409 carries no confirmation `code`."""
    upload = _start_upload(client, size=4, filename="T001_C1_source.mp4")
    _upload_all_bytes(client, upload["upload_id"], b"data")
    s3_client.objects["source/T001/T001_C1_source.mp4"] = b"already there"
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"], "confirm_overwrite": "true"},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 409
    assert "code" not in response.json()


def test_create_cutting_job_records_who_requested_it(client):
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
        headers=identity_headers("jan.peeters@vives.be"),
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] == "jan.peeters@vives.be"


def test_create_cutting_job_is_rate_limited_per_identity(client, monkeypatch):
    monkeypatch.setattr(settings, "create_cutting_job_rate_limit_max_attempts_per_identity", 1)
    headers = identity_headers("busy@vives.be")
    excel = _excel_bytes(reference_camera="C1")

    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    first = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
        headers=headers,
    )
    assert first.status_code == 201, first.text

    throttled = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001"},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
        headers=headers,
    )

    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


def test_create_cutting_job_needs_no_login(client):
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    excel = _excel_bytes(reference_camera="C1")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] is None


# --- POST /cutting-jobs/batch (ticket #97) ---


def _completed_upload_id(client, *, test_id, camera="C1"):
    upload = _start_upload(client, test_id=test_id, camera=camera, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    return upload["upload_id"]


def _post_batch(client, entries, *, excel=None, headers=None):
    excel = excel or _excel_bytes(extra_test_ids=("T002", "T003", "T004", "T005", "T006"))
    return client.post(
        "/api/cutting-jobs/batch",
        data={"tests": json.dumps(entries)},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
        headers=headers,
    )


def test_batch_creates_one_independently_visible_job_per_test(client):
    entries = [
        {"test_id": test_id, "c1_upload_id": _completed_upload_id(client, test_id=test_id)}
        for test_id in ("T001", "T002")
    ]

    response = _post_batch(client, entries)

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [result["test_id"] for result in results] == ["T001", "T002"]
    assert all(result["error"] is None for result in results)
    for result in results:
        fetched = client.get(f"/api/cutting-jobs/{result['job']['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["test_id"] == result["test_id"]
        assert fetched.json()["status"] == "queued"


def test_batch_over_five_tests_is_rejected_with_no_jobs_created(client, db_session):
    entries = [
        {"test_id": test_id, "c1_upload_id": _completed_upload_id(client, test_id=test_id)}
        for test_id in ("T001", "T002", "T003", "T004", "T005", "T006")
    ]

    response = _post_batch(client, entries)

    # A schema-level bound, like POST /analyses' 1-10 Tests (422, not 400).
    assert response.status_code == 422
    assert db_session.query(CuttingJob).count() == 0


def test_batch_rejects_an_empty_tests_list(client):
    assert _post_batch(client, []).status_code == 422


def test_batch_rejects_the_same_test_twice(client, db_session):
    upload_id = _completed_upload_id(client, test_id="T001")

    response = _post_batch(
        client, [{"test_id": "T001", "c1_upload_id": upload_id}, {"test_id": "001"}]
    )

    assert response.status_code == 400
    assert db_session.query(CuttingJob).count() == 0


def test_batch_reports_each_tests_own_failure_without_blocking_the_rest(client, s3_client):
    """T002 names an unknown upload (400) and T003 already has Cuts (#96's
    distinct 409) — each gets exactly the error a single submission would."""
    s3_client.objects["cuts/T003/T003_C1_ME_F1.mp4"] = b"an earlier cut"
    entries = [
        {"test_id": "T001", "c1_upload_id": _completed_upload_id(client, test_id="T001")},
        {"test_id": "T002", "c1_upload_id": "0" * 32},
        {"test_id": "T003", "c1_upload_id": _completed_upload_id(client, test_id="T003")},
    ]

    response = _post_batch(client, entries)

    assert response.status_code == 200, response.text
    first, second, third = response.json()["results"]
    assert first["job"]["test_id"] == "T001"
    assert second["job"] is None
    assert second["error"] == {
        "status_code": 400,
        "detail": "Unknown upload for C1.",
        "code": None,
    }
    assert third["job"] is None
    assert third["error"]["status_code"] == 409
    assert third["error"]["code"] == "cuts_already_exist"


def test_batch_recut_proceeds_once_that_test_is_confirmed(client, s3_client):
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"an earlier cut"
    upload_id = _completed_upload_id(client, test_id="T001")
    blocked = _post_batch(client, [{"test_id": "T001", "c1_upload_id": upload_id}])
    assert blocked.json()["results"][0]["error"]["code"] == "cuts_already_exist"

    confirmed = _post_batch(
        client, [{"test_id": "T001", "c1_upload_id": upload_id, "confirm_overwrite": True}]
    )

    assert confirmed.json()["results"][0]["job"]["status"] == "queued"


def test_batch_rejects_a_malformed_tests_field(client):
    """FastAPI's own structured validation-error list, like every other 422."""
    for tests in (
        "not json",
        json.dumps({"test_id": "T001"}),
        json.dumps([{"test": "T001"}]),
        json.dumps([{"test_id": "T001", "confirmOverwrite": True}]),
        " " * 5000,
    ):
        response = client.post(
            "/api/cutting-jobs/batch",
            data={"tests": tests},
            files={"excel": ("timestamps.xlsx", _excel_bytes(), "application/octet-stream")},
        )

        assert response.status_code == 422, tests
        assert isinstance(response.json()["detail"], list), tests


def test_batch_rejects_an_oversized_timestamp_excel(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "cutting_job_excel_max_file_size_bytes", 100)
    upload_id = _completed_upload_id(client, test_id="T001")

    response = _post_batch(client, [{"test_id": "T001", "c1_upload_id": upload_id}])

    assert response.status_code == 400
    assert db_session.query(CuttingJob).count() == 0


def test_create_cutting_job_rejects_an_oversized_timestamp_excel(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "cutting_job_excel_max_file_size_bytes", 100)
    upload_id = _completed_upload_id(client, test_id="T001")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload_id},
        files={"excel": ("timestamps.xlsx", _excel_bytes(), "application/octet-stream")},
    )

    assert response.status_code == 400
    assert db_session.query(CuttingJob).count() == 0


def test_batch_costs_one_rate_limit_attempt_however_many_tests_it_names(client, monkeypatch):
    monkeypatch.setattr(settings, "create_cutting_job_rate_limit_max_attempts_per_identity", 1)
    headers = identity_headers("busy@vives.be")
    entries = [
        {"test_id": test_id, "c1_upload_id": _completed_upload_id(client, test_id=test_id)}
        for test_id in ("T001", "T002", "T003")
    ]

    first = _post_batch(client, entries, headers=headers)
    assert first.status_code == 200, first.text
    assert all(result["job"] is not None for result in first.json()["results"])

    throttled = _post_batch(client, [{"test_id": "T004"}], headers=headers)

    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


# --- CUTTING_STARTED audit events (ticket #99) ---


def _cutting_audit_rows(db_session) -> list[AuditLog]:
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.target_type == "cutting_job").order_by(AuditLog.id)
        )
    )


def test_create_cutting_job_writes_a_cutting_started_audit_row(client, db_session):
    """Attributed via `get_verified_identity`, like ANALYSIS_STARTED: no JWT
    sent here, so the plain `X-authentik-email` value is recorded
    unverified."""
    upload_id = _completed_upload_id(client, test_id="T001")

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload_id},
        files={"excel": ("timestamps.xlsx", _excel_bytes(), "application/octet-stream")},
        headers={"X-authentik-email": "jan.peeters@vives.be"},
    )
    assert response.status_code == 201, response.text

    (row,) = _cutting_audit_rows(db_session)
    assert row.action == AuditAction.CUTTING_STARTED
    assert row.target == str(response.json()["id"])
    assert row.identity == "jan.peeters@vives.be"
    assert row.identity_verified is False
    assert row.failure_reason is None


def test_create_cutting_job_writes_no_audit_row_when_nothing_is_created(
    client, db_session, s3_client
):
    """#96's confirmation block creates no job, so there's nothing started."""
    upload_id = _completed_upload_id(client, test_id="T001")
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"an earlier cut"

    response = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload_id},
        files={"excel": ("timestamps.xlsx", _excel_bytes(), "application/octet-stream")},
    )

    assert response.status_code == 409
    assert _cutting_audit_rows(db_session) == []


def test_batch_writes_one_cutting_started_row_per_created_job(client, db_session):
    entries = [
        {"test_id": "T001", "c1_upload_id": _completed_upload_id(client, test_id="T001")},
        {"test_id": "T002", "c1_upload_id": "0" * 32},
        {"test_id": "T003", "c1_upload_id": _completed_upload_id(client, test_id="T003")},
    ]

    results = _post_batch(client, entries).json()["results"]

    created_ids = [str(r["job"]["id"]) for r in results if r["job"] is not None]
    assert len(created_ids) == 2
    rows = _cutting_audit_rows(db_session)
    assert [row.action for row in rows] == [AuditAction.CUTTING_STARTED] * 2
    assert [row.target for row in rows] == created_ids


def test_a_failed_audit_write_never_fails_job_creation(client, db_session, monkeypatch):
    """Best-effort, like every other audit event: `record_audit_event`
    swallows the failure, the job is still created and returned."""

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr("app.services.audit_log.record_required_audit_event", _raise)
    upload_id = _completed_upload_id(client, test_id="T001")

    single = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload_id},
        files={"excel": ("timestamps.xlsx", _excel_bytes(), "application/octet-stream")},
    )
    batch = _post_batch(
        client, [{"test_id": "T002", "c1_upload_id": _completed_upload_id(client, test_id="T002")}]
    )

    assert single.status_code == 201, single.text
    assert batch.json()["results"][0]["job"] is not None
    assert db_session.query(CuttingJob).count() == 2
    assert _cutting_audit_rows(db_session) == []


# --- GET /cutting-jobs/{id} ---


def test_get_cutting_job_returns_status(client):
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    excel = _excel_bytes(reference_camera="C1")
    created = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", excel, "application/octet-stream")},
    ).json()

    response = client.get(f"/api/cutting-jobs/{created['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_get_cutting_job_shows_per_phase_progress_while_still_running(client, db_session):
    """Ticket #98: the cutting-worker commits each output as it succeeds,
    before the job itself is done — the status endpoint shows that mix."""
    upload = _start_upload(client, size=4)
    _upload_all_bytes(client, upload["upload_id"], b"data")
    created = client.post(
        "/api/cutting-jobs",
        data={"test_id": "T001", "c1_upload_id": upload["upload_id"]},
        files={"excel": ("timestamps.xlsx", _excel_bytes(), "application/octet-stream")},
    ).json()
    job = db_session.get(CuttingJob, created["id"])
    job.status = CuttingJobStatus.RUNNING
    first_output = next(o for o in job.outputs if (o.condition, o.phase) == ("ME", "F1"))
    first_output.status = CuttingJobOutputStatus.SUCCEEDED
    db_session.commit()

    body = client.get(f"/api/cutting-jobs/{created['id']}").json()

    assert body["status"] == "running"
    assert {(o["condition"], o["phase"]): o["status"] for o in body["outputs"]} == {
        ("ME", "F1"): "succeeded",
        ("ME", "F2"): "pending",
    }


def test_get_cutting_job_for_unknown_id_is_not_found(client):
    response = client.get("/api/cutting-jobs/999999")

    assert response.status_code == 404
