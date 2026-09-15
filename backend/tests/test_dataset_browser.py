from app.models.account import AccountRole
from tests.helpers import create_account, login_headers

# Ticket #20's acceptance criteria: exercised entirely through the HTTP API
# against the fake S3 client (the `s3_client` fixture) — no real bucket
# access anywhere in this file.


def _user_headers(client, db_session, *, role: AccountRole = AccountRole.USER):
    email = "admin.person@vives.be" if role == AccountRole.ADMIN else "jan.peeters@vives.be"
    create_account(db_session, email=email, role=role)
    return login_headers(client, email)


def test_top_level_lists_dataset_folders(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    s3_client.objects["dataset/dataset_v1/train/a.png"] = b"a"
    s3_client.objects["dataset/dataset_v2/train/a.png"] = b"b"
    # A key outside dataset/ must never surface here.
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"c"

    response = client.get("/media/datasets", headers=headers)

    assert response.status_code == 200
    entries = response.json()
    assert {e["name"] for e in entries} == {"dataset_v1", "dataset_v2"}
    assert all(e["is_folder"] for e in entries)


def test_navigating_into_a_dataset_shows_its_immediate_children(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    s3_client.objects["dataset/dataset_v1/train/a.png"] = b"a"
    s3_client.objects["dataset/dataset_v1/valid/b.png"] = b"b"
    s3_client.objects["dataset/dataset_v1/README.md"] = b"readme"

    response = client.get("/media/datasets", params={"path": "dataset_v1"}, headers=headers)

    assert response.status_code == 200
    entries = response.json()
    by_name = {e["name"]: e for e in entries}
    assert set(by_name) == {"train", "valid", "README.md"}
    assert by_name["train"]["is_folder"] is True
    assert by_name["valid"]["is_folder"] is True
    assert by_name["README.md"]["is_folder"] is False
    assert by_name["README.md"]["size"] == len(b"readme")
    assert by_name["README.md"]["last_modified"]
    assert by_name["train"]["key"] == "dataset/dataset_v1/train/"
    assert by_name["README.md"]["key"] == "dataset/dataset_v1/README.md"


def test_navigating_deeper_shows_only_that_splits_children(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    s3_client.objects["dataset/dataset_v1/train/img1.png"] = b"a"
    s3_client.objects["dataset/dataset_v1/train/img2.png"] = b"b"
    s3_client.objects["dataset/dataset_v1/valid/img3.png"] = b"c"

    response = client.get(
        "/media/datasets", params={"path": "dataset_v1/train"}, headers=headers
    )

    assert response.status_code == 200
    entries = response.json()
    assert {e["name"] for e in entries} == {"img1.png", "img2.png"}
    assert all(not e["is_folder"] for e in entries)


def test_browsing_an_unknown_dataset_path_returns_not_found(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    s3_client.objects["dataset/dataset_v1/train/a.png"] = b"a"

    response = client.get(
        "/media/datasets", params={"path": "dataset_v1/does-not-exist"}, headers=headers
    )

    assert response.status_code == 404


def test_a_dataset_path_never_resolves_outside_the_dataset_prefix(client, db_session, s3_client):
    headers = _user_headers(client, db_session)
    s3_client.objects["cuts/secret.mp4"] = b"top-secret"
    s3_client.objects["dataset/dataset_v1/train/a.png"] = b"a"

    for path in ["..", "dataset_v1/..%2F..%2Fcuts", "../cuts", "dataset_v1/../../cuts", "."]:
        response = client.get("/media/datasets", params={"path": path}, headers=headers)
        assert response.status_code == 404
        assert b"secret" not in response.content


def test_user_and_admin_have_identical_dataset_access(client, db_session, s3_client):
    s3_client.objects["dataset/dataset_v1/train/a.png"] = b"a"
    user_headers = _user_headers(client, db_session, role=AccountRole.USER)
    admin_headers = _user_headers(client, db_session, role=AccountRole.ADMIN)

    user_response = client.get(
        "/media/datasets", params={"path": "dataset_v1"}, headers=user_headers
    )
    admin_response = client.get(
        "/media/datasets", params={"path": "dataset_v1"}, headers=admin_headers
    )

    assert user_response.status_code == 200
    assert admin_response.status_code == 200
    assert user_response.json() == admin_response.json()


def test_unauthenticated_is_rejected(client, db_session):
    assert client.get("/media/datasets").status_code == 401
