from tests.helpers import identity_headers

# Ticket #19's acceptance criteria: exercised entirely through the HTTP API
# against the fake S3 client (the `s3_client` fixture) — no real bucket
# access anywhere in this file.


def test_list_tests_returns_every_test_id(client, db_session, s3_client):
    s3_client.objects["cuts/T002/T002_C1_ME_F1.mp4"] = b"a"
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"b"
    # A key outside cuts/ must never surface as a Test.
    s3_client.objects["source/T003/raw.mp4"] = b"c"

    response = client.get("/api/media/tests")

    assert response.status_code == 200
    assert response.json() == ["T001", "T002"]


def test_list_tests_excludes_a_malformed_folder_name(client, db_session, s3_client):
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"a"
    # A stray, non-Test-shaped folder must never surface as a pickable Test
    # id — selecting it would just fail list_cuts_for_test's own check.
    s3_client.objects["cuts/T001_backup/file.mp4"] = b"b"

    response = client.get("/api/media/tests")

    assert response.status_code == 200
    assert response.json() == ["T001"]


def test_list_cuts_parses_camera_condition_phase_and_metadata(client, db_session, s3_client):
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"12345"

    response = client.get("/api/media/tests/T001/cuts")

    assert response.status_code == 200
    [cut] = response.json()
    assert cut["key"] == "cuts/T001/T001_C1_ME_F1.mp4"
    assert cut["filename"] == "T001_C1_ME_F1.mp4"
    assert cut["camera"] == "C1"
    assert cut["condition"] == "ME"
    assert cut["phase"] == "F1"
    assert cut["size"] == 5
    assert cut["last_modified"]


def test_list_cuts_falls_back_to_raw_filename_for_an_unparseable_name(
    client, db_session, s3_client
):
    s3_client.objects["cuts/T001/some-legacy-export.mp4"] = b"data"
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"12345"

    response = client.get("/api/media/tests/T001/cuts")

    assert response.status_code == 200
    by_filename = {cut["filename"]: cut for cut in response.json()}
    assert set(by_filename) == {"some-legacy-export.mp4", "T001_C1_ME_F1.mp4"}
    unparsed = by_filename["some-legacy-export.mp4"]
    assert unparsed["camera"] is None
    assert unparsed["condition"] is None
    assert unparsed["phase"] is None


def test_list_cuts_excludes_nested_objects_and_other_tests(client, db_session, s3_client):
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"a"
    s3_client.objects["cuts/T001/sub/extra.mp4"] = b"b"
    s3_client.objects["cuts/T002/T002_C1_ME_F1.mp4"] = b"c"

    response = client.get("/api/media/tests/T001/cuts")

    assert response.status_code == 200
    assert [cut["filename"] for cut in response.json()] == ["T001_C1_ME_F1.mp4"]


def test_searching_an_unknown_test_id_returns_not_found(client, db_session, s3_client):
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"a"

    response = client.get("/api/media/tests/T999/cuts")

    assert response.status_code == 404


def test_a_malformed_test_id_never_resolves_outside_the_cuts_prefix(client, db_session, s3_client):
    s3_client.objects["source/secret.mp4"] = b"top-secret"
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"a"

    for test_id in ["..", "cuts", "T001%2F..%2F..%2Fsource", "T-1", "not-a-test"]:
        response = client.get(f"/api/media/tests/{test_id}/cuts")
        assert response.status_code == 404
        assert b"secret" not in response.content


def test_media_browser_needs_no_login_and_ignores_identity(client, s3_client):
    """No login and no per-role access since ticket #72 — everyone past
    Mechatronics gets the same view, identified or not."""
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"a"

    anonymous = client.get("/api/media/tests/T001/cuts")
    identified = client.get(
        "/api/media/tests/T001/cuts", headers=identity_headers("jan.peeters@vives.be")
    )

    assert anonymous.status_code == 200
    assert identified.status_code == 200
    assert anonymous.json() == identified.json()
    assert client.get("/api/media/tests").status_code == 200
