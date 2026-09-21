from tests.helpers import identity_headers

# Ticket #20's acceptance criteria: exercised entirely through the HTTP API
# against the fake S3 client (the `s3_client` fixture) — no real bucket
# access anywhere in this file. Datasets sit at their own top-level prefix
# in the bucket (e.g. "dataset_pose_v0.1/", "dataset_v0.9/"), sibling to
# "cuts/" and "source/" — not nested under a shared "dataset/" parent (see
# CONTEXT.md's "Dataset" term).


def test_top_level_lists_datasets_and_excludes_cuts_and_source(client, db_session, s3_client):
    s3_client.objects["dataset_v0.9/train/a.png"] = b"a"
    s3_client.objects["dataset_pose_v0.1/train/a.png"] = b"b"
    # Sibling top-level prefixes must never surface as Datasets.
    s3_client.objects["cuts/T001/T001_C1_ME_F1.mp4"] = b"c"
    s3_client.objects["source/T001/raw.mp4"] = b"d"

    response = client.get("/media/datasets")

    assert response.status_code == 200
    entries = response.json()
    assert {e["name"] for e in entries} == {"dataset_v0.9", "dataset_pose_v0.1"}
    assert all(e["is_folder"] for e in entries)


def test_navigating_into_a_dataset_shows_its_immediate_children(client, db_session, s3_client):
    s3_client.objects["dataset_v0.9/train/a.png"] = b"a"
    s3_client.objects["dataset_v0.9/valid/b.png"] = b"b"
    s3_client.objects["dataset_v0.9/README.md"] = b"readme"

    response = client.get("/media/datasets", params={"path": "dataset_v0.9"})

    assert response.status_code == 200
    entries = response.json()
    by_name = {e["name"]: e for e in entries}
    assert set(by_name) == {"train", "valid", "README.md"}
    assert by_name["train"]["is_folder"] is True
    assert by_name["valid"]["is_folder"] is True
    assert by_name["README.md"]["is_folder"] is False
    assert by_name["README.md"]["size"] == len(b"readme")
    assert by_name["README.md"]["last_modified"]
    assert by_name["train"]["key"] == "dataset_v0.9/train/"
    assert by_name["README.md"]["key"] == "dataset_v0.9/README.md"


def test_navigating_deeper_shows_only_that_splits_children(client, db_session, s3_client):
    s3_client.objects["dataset_v0.9/train/img1.png"] = b"a"
    s3_client.objects["dataset_v0.9/train/img2.png"] = b"b"
    s3_client.objects["dataset_v0.9/valid/img3.png"] = b"c"

    response = client.get("/media/datasets", params={"path": "dataset_v0.9/train"})

    assert response.status_code == 200
    entries = response.json()
    assert {e["name"] for e in entries} == {"img1.png", "img2.png"}
    assert all(not e["is_folder"] for e in entries)


def test_browsing_an_unknown_dataset_path_returns_not_found(client, db_session, s3_client):
    s3_client.objects["dataset_v0.9/train/a.png"] = b"a"

    response = client.get("/media/datasets", params={"path": "dataset_v0.9/does-not-exist"})

    assert response.status_code == 404


def test_a_dataset_path_never_resolves_outside_a_dataset_prefix(client, db_session, s3_client):
    s3_client.objects["cuts/secret.mp4"] = b"top-secret"
    s3_client.objects["source/secret.mp4"] = b"top-secret"
    s3_client.objects["dataset_v0.9/train/a.png"] = b"a"

    # Includes the sibling top-level prefixes themselves ("cuts", "source")
    # — a path naming one of those directly must 404 just like a malformed
    # or escaping one.
    for path in [
        "..",
        "cuts",
        "source",
        "cuts/T001",
        "dataset_v0.9/..%2F..%2Fcuts",
        "../cuts",
        "dataset_v0.9/../../cuts",
        ".",
    ]:
        response = client.get("/media/datasets", params={"path": path})
        assert response.status_code == 404
        assert b"secret" not in response.content


def test_dataset_browser_needs_no_login_and_ignores_identity(client, s3_client):
    s3_client.objects["dataset_v0.9/train/a.png"] = b"a"

    anonymous = client.get("/media/datasets", params={"path": "dataset_v0.9"})
    identified = client.get(
        "/media/datasets",
        params={"path": "dataset_v0.9"},
        headers=identity_headers("jan.peeters@vives.be"),
    )

    assert anonymous.status_code == 200
    assert identified.status_code == 200
    assert anonymous.json() == identified.json()
    assert client.get("/media/datasets").status_code == 200
