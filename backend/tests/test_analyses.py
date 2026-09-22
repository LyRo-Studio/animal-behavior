from sqlalchemy import select

from app.models.analysis_job import AnalysisJob
from app.models.audit_log import AuditAction, AuditLog
from tests.helpers import identity_headers

# Ticket #45's acceptance criteria: exercised entirely through the HTTP API
# against the real (Alembic-migrated) database — no worker/S3 access in
# this ticket, jobs just sit `queued`. Since ticket #72 there is no login: a
# request may carry an identity header (attribution only) or none at all.
#
# Ticket #89 / issue #88's Feature B extends the request shape to
# `{test_ids: [...], cuts: [...] | None}` — see the wholesale-derivation and
# limit tests further down.


def test_create_analysis_writes_an_analysis_started_audit_row(client, db_session):
    """Ticket #82 / issue #79: every successful call writes ANALYSIS_STARTED,
    attributed via `get_verified_identity` — independent of the ordinary,
    unverified `requested_by_identity` attribution above (no
    `X-authentik-email` sent here, so it's unverified: `identity=None`)."""
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )
    job_id = response.json()["id"]

    row = db_session.scalar(select(AuditLog))
    assert row is not None
    assert row.action == AuditAction.ANALYSIS_STARTED
    assert row.target_type == "analysis_job"
    assert row.target == str(job_id)
    assert row.identity is None
    assert row.identity_verified is False


def test_create_analysis_audit_row_carries_the_authentik_email_header_unverified(
    client, db_session
):
    """No JWT/JWKS headers sent, so the row is unverified — but the plain
    `X-authentik-email` value is still recorded (ADR-0005: a verification
    failure falls back to the plain header, it never blocks or blanks the
    action being logged)."""
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers={"X-authentik-email": "jan.peeters@vives.be"},
    )
    assert response.status_code == 201

    row = db_session.scalar(select(AuditLog))
    assert row.identity == "jan.peeters@vives.be"
    assert row.identity_verified is False


def test_create_analysis_does_not_write_an_audit_row_on_a_rejected_request(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C1_ME_F1.mp4"]},
    )

    assert response.status_code == 400
    assert db_session.scalar(select(AuditLog)) is None


def test_create_analysis_with_valid_c2_cuts_creates_a_queued_job(client, db_session):
    response = client.post(
        "/api/analyses",
        json={
            "test_ids": ["T001"],
            "cuts": [
                "cuts/T001/T001_C2_ME_F1.mp4",
                "cuts/T001/T001_C2_ME_F2.mp4",
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["test_ids"] == ["T001"]
    assert body["status"] == "queued"
    assert body["dogtrace_version"] is None
    assert body["report_available"] is False
    assert body["started_at"] is None
    assert body["finished_at"] is None
    videos = body["videos"]
    assert [video["cut_key"] for video in videos] == [
        "cuts/T001/T001_C2_ME_F1.mp4",
        "cuts/T001/T001_C2_ME_F2.mp4",
    ]
    assert [video["position"] for video in videos] == [0, 1]
    assert all(video["status"] == "pending" for video in videos)
    assert all(video["failure_reason"] is None for video in videos)


def test_create_analysis_accepts_case_insensitive_condition_and_extension(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/t001_c2_ze_f3.MP4"]},
    )

    assert response.status_code == 201, response.text


def test_create_analysis_rejects_a_c1_cut(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C1_ME_F1.mp4"]},
    )

    assert response.status_code == 400
    assert db_session.query(AnalysisJob).count() == 0


def test_create_analysis_rejects_a_malformed_filename(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/not-a-dogtrace-filename.mp4"]},
    )

    assert response.status_code == 400


def test_create_analysis_rejects_a_cut_from_a_different_test(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 400


def test_create_analysis_rejects_a_key_outside_cuts_prefix(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["source/T001/T001_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 400


def test_create_analysis_rejects_the_whole_request_if_any_cut_is_invalid(client, db_session):
    """One bad Cut in a batch must reject the whole request — no partial job."""

    response = client.post(
        "/api/analyses",
        json={
            "test_ids": ["T001"],
            "cuts": ["cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C1_ME_F2.mp4"],
        },
    )

    assert response.status_code == 400

    list_response = client.get("/api/analyses")
    assert list_response.json() == []


def test_create_analysis_rejects_an_empty_cut_list(client, db_session):
    response = client.post("/api/analyses", json={"test_ids": ["T001"], "cuts": []})

    assert response.status_code == 422


def test_create_analysis_rejects_an_empty_test_ids_list(client, db_session):
    response = client.post(
        "/api/analyses", json={"test_ids": [], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]}
    )

    assert response.status_code == 422


# --- Ticket #89 / issue #88's Feature B: wholesale multi-Test selection ---


def test_create_analysis_with_multiple_test_ids_and_no_cuts_derives_wholesale(
    client, db_session, s3_client
):
    """`cuts` omitted, more than one `test_id`: every C2-eligible Cut across
    every listed Test is derived server-side, non-C2 Cuts excluded."""
    s3_client.objects["cuts/T001/T001_C2_ME_F1.mp4"] = b"video"
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"video"  # not C2 — excluded
    s3_client.objects["cuts/T002/T002_C2_ME_F1.mp4"] = b"video"
    s3_client.objects["cuts/T002/T002_C2_ZE_F1.mp4"] = b"video"

    response = client.post("/api/analyses", json={"test_ids": ["T001", "T002"]})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["test_ids"] == ["T001", "T002"]
    assert sorted(video["cut_key"] for video in body["videos"]) == [
        "cuts/T001/T001_C2_ME_F1.mp4",
        "cuts/T002/T002_C2_ME_F1.mp4",
        "cuts/T002/T002_C2_ZE_F1.mp4",
    ]


def test_create_analysis_with_a_single_test_id_and_no_cuts_derives_wholesale(
    client, db_session, s3_client
):
    """The single-Test case also supports wholesale ("select all Cuts in
    this Test" — CONTEXT.md's Feature B decision), not just multi-Test."""
    s3_client.objects["cuts/T001/T001_C2_ME_F1.mp4"] = b"video"
    s3_client.objects["cuts/T001/T001_C2_ME_F2.mp4"] = b"video"

    response = client.post("/api/analyses", json={"test_ids": ["T001"]})

    assert response.status_code == 201, response.text
    assert sorted(video["cut_key"] for video in response.json()["videos"]) == [
        "cuts/T001/T001_C2_ME_F1.mp4",
        "cuts/T001/T001_C2_ME_F2.mp4",
    ]


def test_create_analysis_wholesale_for_a_test_with_no_c2_cuts_is_rejected(
    client, db_session, s3_client
):
    """A Test that exists but resolves to zero C2-eligible Cuts leaves
    nothing to analyze — same "empty selection" rejection as an explicit
    empty cuts list."""
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"video"

    response = client.post("/api/analyses", json={"test_ids": ["T001"]})

    assert response.status_code == 400


def test_create_analysis_wholesale_for_an_unknown_test_is_rejected(client, db_session, s3_client):
    response = client.post("/api/analyses", json={"test_ids": ["T404"]})

    assert response.status_code == 400


def test_create_analysis_rejects_cuts_alongside_more_than_one_test_id(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001", "T002"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 400
    assert db_session.query(AnalysisJob).count() == 0


def test_create_analysis_allows_cuts_alongside_a_duplicated_test_id(client, db_session):
    """A duplicated test_id dedupes down to a single Test before the
    "cuts needs exactly one Test" check runs — must not be wrongly rejected
    as a multi-Test request."""
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001", "T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["test_ids"] == ["T001"]


def test_create_analysis_rejects_more_than_ten_test_ids(client, db_session):
    response = client.post("/api/analyses", json={"test_ids": [f"T{n:03d}" for n in range(11)]})

    assert response.status_code == 422


def test_create_analysis_rejects_a_wholesale_selection_over_300_videos(
    client, db_session, s3_client
):
    """10 Tests (the max) x 31 C2-eligible Cuts each = 310, over the
    300-video backstop — CONTEXT.md's Feature B limit is a backstop over
    the structural ~16-per-Test maximum, not an expected ceiling, so this
    deliberately fabricates more phases per Test than a real one has."""
    for test_number in range(10):
        for phase in range(31):
            key = f"cuts/T{test_number:03d}/T{test_number:03d}_C2_ME_F{phase}.mp4"
            s3_client.objects[key] = b"video"

    response = client.post(
        "/api/analyses",
        json={"test_ids": [f"T{n:03d}" for n in range(10)]},
    )

    assert response.status_code == 400
    assert db_session.query(AnalysisJob).count() == 0


def test_list_analyses_test_scoped_panel_lists_a_multi_test_job_for_every_test_it_touches(
    client, db_session, s3_client
):
    s3_client.objects["cuts/T001/T001_C2_ME_F1.mp4"] = b"video"
    s3_client.objects["cuts/T002/T002_C2_ME_F1.mp4"] = b"video"
    job_id = client.post("/api/analyses", json={"test_ids": ["T001", "T002"]}).json()["id"]

    for test_id in ("T001", "T002"):
        response = client.get("/api/analyses", params={"test_id": test_id})
        assert response.status_code == 200
        assert [job["id"] for job in response.json()] == [job_id]


# --- Rate limiting (ticket #89), same shape as test_media_playback.py's
# media-token limiter tests. ---


def test_create_analysis_is_rate_limited_per_identity(client, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "create_analysis_rate_limit_max_attempts_per_identity", 2)
    headers = identity_headers("busy@vives.be")

    for _ in range(2):
        response = client.post(
            "/api/analyses",
            json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
            headers=headers,
        )
        assert response.status_code == 201, response.text

    throttled = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=headers,
    )

    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


def test_create_analysis_rate_limit_is_scoped_per_identity(client, db_session, monkeypatch):
    """A different identity's budget is untouched by another's — the
    limiter is keyed per identity, same shape as media-token issuance."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "create_analysis_rate_limit_max_attempts_per_identity", 1)

    exhausted = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("busy@vives.be"),
    )
    assert exhausted.status_code == 201, exhausted.text

    still_ok = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("someone-else@vives.be"),
    )
    assert still_ok.status_code == 201, still_ok.text


def test_get_analysis_returns_status_and_per_video_results(client, db_session):
    create_response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )
    analysis_id = create_response.json()["id"]

    response = client.get(f"/api/analyses/{analysis_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == analysis_id
    assert body["status"] == "queued"
    assert len(body["videos"]) == 1
    assert body["videos"][0]["status"] == "pending"


def test_get_analysis_for_unknown_id_is_not_found(client, db_session):
    response = client.get("/api/analyses/999999")

    assert response.status_code == 404


def test_get_analysis_created_by_another_identity_is_visible(client):
    """Analysis history is fully shared (ticket #72, user story 2): the
    header is for attribution, never for hiding anyone's runs."""
    create_response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("owner@vives.be"),
    )
    analysis_id = create_response.json()["id"]

    response = client.get(
        f"/api/analyses/{analysis_id}", headers=identity_headers("other@vives.be")
    )

    assert response.status_code == 200
    assert response.json()["id"] == analysis_id


def test_list_analyses_returns_every_identitys_jobs(client):
    client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("mine@vives.be"),
    )
    client.post(
        "/api/analyses",
        json={"test_ids": ["T002"], "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
        headers=identity_headers("other@vives.be"),
    )

    response = client.get("/api/analyses", headers=identity_headers("mine@vives.be"))

    assert response.status_code == 200
    assert {job["test_ids"][0] for job in response.json()} == {"T001", "T002"}


def test_list_analyses_filters_by_test_id(client):
    client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )
    client.post(
        "/api/analyses",
        json={"test_ids": ["T002"], "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
    )

    response = client.get("/api/analyses", params={"test_id": "T002"})

    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["test_ids"] == ["T002"]


def test_create_analysis_records_who_ran_it(client, db_session):
    """User story 3: each analysis shows who ran it."""
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("jan.peeters@vives.be"),
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] == "jan.peeters@vives.be"
    job = db_session.get(AnalysisJob, response.json()["id"])
    assert job.requested_by_identity == "jan.peeters@vives.be"


def test_list_and_get_expose_who_ran_each_analysis(client):
    analysis_id = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("jan.peeters@vives.be"),
    ).json()["id"]

    assert client.get(f"/api/analyses/{analysis_id}").json()["requested_by_identity"] == (
        "jan.peeters@vives.be"
    )
    assert [job["requested_by_identity"] for job in client.get("/api/analyses").json()] == [
        "jan.peeters@vives.be"
    ]


def test_create_analysis_without_an_identity_header_is_attributed_to_no_one(client):
    """Local development has no Mechatronics in front of it (user story 9)."""
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] is None


def test_create_analysis_stores_an_oversized_identity_bounded(client, db_session):
    response = client.post(
        "/api/analyses",
        json={"test_ids": ["T001"], "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("x" * 5000),
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] == "x" * 320


def test_analyses_need_no_login(client):
    """No `Authorization` header, no identity header, no 401 anywhere."""
    assert client.get("/api/analyses").status_code == 200
    assert client.get("/api/analyses/999999").status_code == 404
    assert client.post("/api/analyses/999999/cancel").status_code == 404


# --- Direct create_analysis_job tests (ticket #89's testing decisions:
# "extend test_analyses.py's existing direct tests") — bypass the schema
# layer entirely, exercising the service's own defensive re-validation. ---


def test_create_analysis_job_wholesale_derives_every_c2_cut_across_multiple_tests(
    db_session, s3_client
):
    from app.services.analyses import create_analysis_job

    s3_client.objects["cuts/T001/T001_C2_ME_F1.mp4"] = b"video"
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"video"
    s3_client.objects["cuts/T002/T002_C2_ME_F1.mp4"] = b"video"

    job = create_analysis_job(
        db_session, requested_by_identity=None, test_ids=["T001", "T002"], s3=s3_client
    )

    assert job.test_ids == ["T001", "T002"]
    assert sorted(video.cut_key for video in job.videos) == [
        "cuts/T001/T001_C2_ME_F1.mp4",
        "cuts/T002/T002_C2_ME_F1.mp4",
    ]


def test_create_analysis_job_rejects_cuts_alongside_more_than_one_test_id(db_session):
    from app.services.analyses import CutsWithMultipleTestsError, create_analysis_job

    try:
        create_analysis_job(
            db_session,
            requested_by_identity=None,
            test_ids=["T001", "T002"],
            cut_keys=["cuts/T001/T001_C2_ME_F1.mp4"],
        )
        raise AssertionError("expected CutsWithMultipleTestsError")
    except CutsWithMultipleTestsError:
        pass
    assert db_session.query(AnalysisJob).count() == 0


def test_create_analysis_job_rejects_more_than_ten_test_ids(db_session):
    from app.services.analyses import TooManyTestsError, create_analysis_job

    try:
        create_analysis_job(
            db_session,
            requested_by_identity=None,
            test_ids=[f"T{n:03d}" for n in range(11)],
            cut_keys=["cuts/T000/T000_C2_ME_F1.mp4"],
        )
        raise AssertionError("expected TooManyTestsError")
    except TooManyTestsError as exc:
        assert exc.count == 11
    assert db_session.query(AnalysisJob).count() == 0


def test_create_analysis_job_rejects_a_resolved_video_count_over_300(db_session, s3_client):
    from app.services.analyses import TooManyVideosError, create_analysis_job

    for test_number in range(10):
        for phase in range(31):
            key = f"cuts/T{test_number:03d}/T{test_number:03d}_C2_ME_F{phase}.mp4"
            s3_client.objects[key] = b"video"

    try:
        create_analysis_job(
            db_session,
            requested_by_identity=None,
            test_ids=[f"T{n:03d}" for n in range(10)],
            s3=s3_client,
        )
        raise AssertionError("expected TooManyVideosError")
    except TooManyVideosError as exc:
        assert exc.count == 310
    assert db_session.query(AnalysisJob).count() == 0


def test_list_analysis_jobs_is_bounded_to_the_newest_jobs(db_session):
    """Now that every job is visible to everyone (ticket #72), an unscoped
    listing would grow without bound over time — ENGINEERING-STANDARDS.md
    §5 (DoS: avoid unbounded database queries)."""
    from app.services.analyses import create_analysis_job, list_analysis_jobs

    ids = [
        create_analysis_job(
            db_session,
            requested_by_identity=None,
            test_ids=["T001"],
            cut_keys=["cuts/T001/T001_C2_ME_F1.mp4"],
        ).id
        for _ in range(3)
    ]

    listed = list_analysis_jobs(db_session, limit=2)

    assert [job.id for job in listed] == [ids[2], ids[1]]
