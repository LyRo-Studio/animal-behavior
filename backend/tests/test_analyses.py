from app.models.analysis_job import AnalysisJob
from tests.helpers import identity_headers

# Ticket #45's acceptance criteria: exercised entirely through the HTTP API
# against the real (Alembic-migrated) database — no worker/S3 access in
# this ticket, jobs just sit `queued`. Since ticket #72 there is no login: a
# request may carry an identity header (attribution only) or none at all.


def test_create_analysis_with_valid_c2_cuts_creates_a_queued_job(client, db_session):
    response = client.post(
        "/analyses",
        json={
            "test_id": "T001",
            "cuts": [
                "cuts/T001/T001_C2_ME_F1.mp4",
                "cuts/T001/T001_C2_ME_F2.mp4",
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["test_id"] == "T001"
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
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/t001_c2_ze_f3.MP4"]},
    )

    assert response.status_code == 201, response.text


def test_create_analysis_rejects_a_c1_cut(client, db_session):
    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C1_ME_F1.mp4"]},
    )

    assert response.status_code == 400
    assert db_session.query(AnalysisJob).count() == 0


def test_create_analysis_rejects_a_malformed_filename(client, db_session):
    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/not-a-dogtrace-filename.mp4"]},
    )

    assert response.status_code == 400


def test_create_analysis_rejects_a_cut_from_a_different_test(client, db_session):
    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 400


def test_create_analysis_rejects_a_key_outside_cuts_prefix(client, db_session):
    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["source/T001/T001_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 400


def test_create_analysis_rejects_the_whole_request_if_any_cut_is_invalid(client, db_session):
    """One bad Cut in a batch must reject the whole request — no partial job."""

    response = client.post(
        "/analyses",
        json={
            "test_id": "T001",
            "cuts": ["cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C1_ME_F2.mp4"],
        },
    )

    assert response.status_code == 400

    list_response = client.get("/analyses")
    assert list_response.json() == []


def test_create_analysis_rejects_an_empty_cut_list(client, db_session):
    response = client.post("/analyses", json={"test_id": "T001", "cuts": []})

    assert response.status_code == 422


def test_get_analysis_returns_status_and_per_video_results(client, db_session):
    create_response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )
    analysis_id = create_response.json()["id"]

    response = client.get(f"/analyses/{analysis_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == analysis_id
    assert body["status"] == "queued"
    assert len(body["videos"]) == 1
    assert body["videos"][0]["status"] == "pending"


def test_get_analysis_for_unknown_id_is_not_found(client, db_session):
    response = client.get("/analyses/999999")

    assert response.status_code == 404


def test_get_analysis_created_by_another_identity_is_visible(client):
    """Analysis history is fully shared (ticket #72, user story 2): the
    header is for attribution, never for hiding anyone's runs."""
    create_response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("owner@vives.be"),
    )
    analysis_id = create_response.json()["id"]

    response = client.get(f"/analyses/{analysis_id}", headers=identity_headers("other@vives.be"))

    assert response.status_code == 200
    assert response.json()["id"] == analysis_id


def test_list_analyses_returns_every_identitys_jobs(client):
    client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("mine@vives.be"),
    )
    client.post(
        "/analyses",
        json={"test_id": "T002", "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
        headers=identity_headers("other@vives.be"),
    )

    response = client.get("/analyses", headers=identity_headers("mine@vives.be"))

    assert response.status_code == 200
    assert {job["test_id"] for job in response.json()} == {"T001", "T002"}


def test_list_analyses_filters_by_test_id(client):
    client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )
    client.post(
        "/analyses",
        json={"test_id": "T002", "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
    )

    response = client.get("/analyses", params={"test_id": "T002"})

    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["test_id"] == "T002"


def test_create_analysis_records_who_ran_it(client, db_session):
    """User story 3: each analysis shows who ran it."""
    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("jan.peeters@vives.be"),
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] == "jan.peeters@vives.be"
    job = db_session.get(AnalysisJob, response.json()["id"])
    assert job.requested_by_identity == "jan.peeters@vives.be"


def test_list_and_get_expose_who_ran_each_analysis(client):
    analysis_id = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("jan.peeters@vives.be"),
    ).json()["id"]

    assert client.get(f"/analyses/{analysis_id}").json()["requested_by_identity"] == (
        "jan.peeters@vives.be"
    )
    assert [job["requested_by_identity"] for job in client.get("/analyses").json()] == [
        "jan.peeters@vives.be"
    ]


def test_create_analysis_without_an_identity_header_is_attributed_to_no_one(client):
    """Local development has no Mechatronics in front of it (user story 9)."""
    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] is None


def test_create_analysis_stores_an_oversized_identity_bounded(client, db_session):
    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=identity_headers("x" * 5000),
    )

    assert response.status_code == 201
    assert response.json()["requested_by_identity"] == "x" * 320


def test_analyses_need_no_login(client):
    """No `Authorization` header, no identity header, no 401 anywhere."""
    assert client.get("/analyses").status_code == 200
    assert client.get("/analyses/999999").status_code == 404
    assert client.post("/analyses/999999/cancel").status_code == 404


def test_list_analysis_jobs_is_bounded_to_the_newest_jobs(db_session):
    """Now that every job is visible to everyone (ticket #72), an unscoped
    listing would grow without bound over time — ENGINEERING-STANDARDS.md
    §5 (DoS: avoid unbounded database queries)."""
    from app.services.analyses import create_analysis_job, list_analysis_jobs

    ids = [
        create_analysis_job(
            db_session,
            requested_by_identity=None,
            test_id="T001",
            cut_keys=["cuts/T001/T001_C2_ME_F1.mp4"],
        ).id
        for _ in range(3)
    ]

    listed = list_analysis_jobs(db_session, limit=2)

    assert [job.id for job in listed] == [ids[2], ids[1]]
