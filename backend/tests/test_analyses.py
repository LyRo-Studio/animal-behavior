from app.models.account import AccountRole
from app.models.analysis_job import AnalysisJob
from tests.helpers import create_account, login_headers

# Ticket #45's acceptance criteria: exercised entirely through the HTTP API
# against the real (Alembic-migrated) database — no worker/S3 access in
# this ticket, jobs just sit `queued`.


def _user_headers(client, db_session, *, email="jan.peeters@vives.be", role=AccountRole.USER):
    create_account(db_session, email=email, role=role)
    return login_headers(client, email)


def test_create_analysis_with_valid_c2_cuts_creates_a_queued_job(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.post(
        "/analyses",
        json={
            "test_id": "T001",
            "cuts": [
                "cuts/T001/T001_C2_ME_F1.mp4",
                "cuts/T001/T001_C2_ME_F2.mp4",
            ],
        },
        headers=headers,
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
    headers = _user_headers(client, db_session)

    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/t001_c2_ze_f3.MP4"]},
        headers=headers,
    )

    assert response.status_code == 201, response.text


def test_create_analysis_rejects_a_c1_cut(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C1_ME_F1.mp4"]},
        headers=headers,
    )

    assert response.status_code == 400
    assert db_session.query(AnalysisJob).count() == 0


def test_create_analysis_rejects_a_malformed_filename(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/not-a-dogtrace-filename.mp4"]},
        headers=headers,
    )

    assert response.status_code == 400


def test_create_analysis_rejects_a_cut_from_a_different_test(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
        headers=headers,
    )

    assert response.status_code == 400


def test_create_analysis_rejects_a_key_outside_cuts_prefix(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["source/T001/T001_C2_ME_F1.mp4"]},
        headers=headers,
    )

    assert response.status_code == 400


def test_create_analysis_rejects_the_whole_request_if_any_cut_is_invalid(client, db_session):
    """One bad Cut in a batch must reject the whole request — no partial job."""
    headers = _user_headers(client, db_session)

    response = client.post(
        "/analyses",
        json={
            "test_id": "T001",
            "cuts": ["cuts/T001/T001_C2_ME_F1.mp4", "cuts/T001/T001_C1_ME_F2.mp4"],
        },
        headers=headers,
    )

    assert response.status_code == 400

    list_response = client.get("/analyses", headers=headers)
    assert list_response.json() == []


def test_create_analysis_rejects_an_empty_cut_list(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.post("/analyses", json={"test_id": "T001", "cuts": []}, headers=headers)

    assert response.status_code == 422


def test_get_analysis_returns_status_and_per_video_results(client, db_session):
    headers = _user_headers(client, db_session)
    create_response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=headers,
    )
    analysis_id = create_response.json()["id"]

    response = client.get(f"/analyses/{analysis_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == analysis_id
    assert body["status"] == "queued"
    assert len(body["videos"]) == 1
    assert body["videos"][0]["status"] == "pending"


def test_get_analysis_for_unknown_id_is_not_found(client, db_session):
    headers = _user_headers(client, db_session)

    response = client.get("/analyses/999999", headers=headers)

    assert response.status_code == 404


def test_get_analysis_owned_by_another_account_is_not_found(client, db_session):
    owner_headers = _user_headers(client, db_session, email="owner@vives.be")
    create_response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=owner_headers,
    )
    analysis_id = create_response.json()["id"]
    other_headers = _user_headers(client, db_session, email="other@vives.be")

    response = client.get(f"/analyses/{analysis_id}", headers=other_headers)

    assert response.status_code == 404


def test_list_analyses_returns_only_the_current_accounts_own_jobs(client, db_session):
    mine_headers = _user_headers(client, db_session, email="mine@vives.be")
    client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=mine_headers,
    )
    other_headers = _user_headers(client, db_session, email="other@vives.be")
    client.post(
        "/analyses",
        json={"test_id": "T002", "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
        headers=other_headers,
    )

    response = client.get("/analyses", headers=mine_headers)

    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["test_id"] == "T001"


def test_list_analyses_filters_by_test_id(client, db_session):
    headers = _user_headers(client, db_session)
    client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=headers,
    )
    client.post(
        "/analyses",
        json={"test_id": "T002", "cuts": ["cuts/T002/T002_C2_ME_F1.mp4"]},
        headers=headers,
    )

    response = client.get("/analyses", params={"test_id": "T002"}, headers=headers)

    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["test_id"] == "T002"


def test_user_and_admin_have_identical_access(client, db_session):
    user_headers = _user_headers(client, db_session, email="user@vives.be", role=AccountRole.USER)
    admin_headers = _user_headers(
        client, db_session, email="admin.person@vives.be", role=AccountRole.ADMIN
    )

    user_response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=user_headers,
    )
    admin_response = client.post(
        "/analyses",
        json={"test_id": "T001", "cuts": ["cuts/T001/T001_C2_ME_F1.mp4"]},
        headers=admin_headers,
    )

    assert user_response.status_code == 201
    assert admin_response.status_code == 201


def test_unauthenticated_is_rejected(client, db_session):
    assert client.post("/analyses", json={"test_id": "T001", "cuts": ["x"]}).status_code == 401
    assert client.get("/analyses").status_code == 401
    assert client.get("/analyses/1").status_code == 401
