import pytest

from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, AnalysisJobVideoStatus

# Issue #133: a finished analysis's per-video trace images, exercised through
# the HTTP API against the fake S3 client — same setup as
# test_analyses_report.py, with the job's terminal state set directly on its
# rows in place of the worker.

_F1 = "cuts/T001/T001_C2_ME_F1.mp4"
_F10 = "cuts/T001/T001_C2_ME_F10.mp4"


def _create_job(client, *, cuts=(_F1,)):
    response = client.post("/api/analyses", json={"test_ids": ["T001"], "cuts": list(cuts)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _finish_job(
    db_session,
    analysis_id: int,
    *,
    status: AnalysisJobStatus = AnalysisJobStatus.COMPLETED,
    report_s3_prefix: str | None,
) -> None:
    job = db_session.get(AnalysisJob, analysis_id)
    job.status = status
    job.report_s3_prefix = report_s3_prefix
    for video in job.videos:
        video.status = AnalysisJobVideoStatus.SUCCEEDED
    db_session.add(job)
    db_session.commit()


def _finished_job_with_objects(client, db_session, s3_client, relative_keys, *, cuts=(_F1,)):
    analysis_id = _create_job(client, cuts=cuts)
    prefix = f"reports/{analysis_id}/"
    for relative_key in relative_keys:
        s3_client.objects[f"{prefix}{relative_key}"] = b"fake-jpeg-bytes"
    _finish_job(db_session, analysis_id, report_s3_prefix=prefix)
    return analysis_id


def test_list_traces_groups_each_videos_images_and_skips_other_files(client, db_session, s3_client):
    analysis_id = _finished_job_with_objects(
        client,
        db_session,
        s3_client,
        [
            "casiop_report.xlsx",
            "T001_C2_ME_F1/20260922_06h35/track_report.csv",
            "traces/T001_C2_ME_F1_fp_trace.jpg",
            "traces/T001_C2_ME_F1_dog_trace.jpg",
            "traces/T001_C2_ME_F10_dog_trace.jpg",
        ],
        cuts=(_F1, _F10),
    )

    response = client.get(f"/api/analyses/{analysis_id}/traces")

    assert response.status_code == 200
    assert response.json() == [
        {
            "cut_key": _F1,
            "images": [
                {
                    "path": "traces/T001_C2_ME_F1_dog_trace.jpg",
                    "filename": "T001_C2_ME_F1_dog_trace.jpg",
                    "label": "dog_trace",
                },
                {
                    "path": "traces/T001_C2_ME_F1_fp_trace.jpg",
                    "filename": "T001_C2_ME_F1_fp_trace.jpg",
                    "label": "fp_trace",
                },
            ],
        },
        {
            "cut_key": _F10,
            "images": [
                {
                    "path": "traces/T001_C2_ME_F10_dog_trace.jpg",
                    "filename": "T001_C2_ME_F10_dog_trace.jpg",
                    "label": "dog_trace",
                },
            ],
        },
    ]


def test_get_trace_image_returns_the_jpeg_inline(client, db_session, s3_client):
    analysis_id = _finished_job_with_objects(
        client, db_session, s3_client, ["traces/T001_C2_ME_F1_dog_trace.jpg"]
    )

    response = client.get(f"/api/analyses/{analysis_id}/traces/traces/T001_C2_ME_F1_dog_trace.jpg")

    assert response.status_code == 200
    assert response.content == b"fake-jpeg-bytes"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"] == (
        'inline; filename="T001_C2_ME_F1_dog_trace.jpg"'
    )


def test_get_trace_image_as_download_is_an_attachment(client, db_session, s3_client):
    analysis_id = _finished_job_with_objects(
        client, db_session, s3_client, ["traces/T001_C2_ME_F1_dog_trace.jpg"]
    )

    response = client.get(
        f"/api/analyses/{analysis_id}/traces/traces/T001_C2_ME_F1_dog_trace.jpg",
        params={"download": "true"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="T001_C2_ME_F1_dog_trace.jpg"'
    )


@pytest.mark.parametrize(
    "path",
    [
        # Not an image — and the report must stay behind its own audited endpoint.
        "casiop_report.xlsx",
        "T001_C2_ME_F1/20260922_06h35/track_report.pkl",
        # An image, but not of a video in this job.
        "traces/T002_C2_ME_F1_dog_trace.jpg",
        # Out of the job's own prefix.
        "traces/%2E%2E/%2E%2E/other/traces/T001_C2_ME_F1_dog_trace.jpg",
    ],
)
def test_get_trace_image_serves_only_this_jobs_trace_images(client, db_session, s3_client, path):
    analysis_id = _finished_job_with_objects(
        client,
        db_session,
        s3_client,
        [
            "casiop_report.xlsx",
            "T001_C2_ME_F1/20260922_06h35/track_report.pkl",
            "traces/T002_C2_ME_F1_dog_trace.jpg",
        ],
    )
    s3_client.objects["reports/other/traces/T001_C2_ME_F1_dog_trace.jpg"] = b"another-jobs-image"

    response = client.get(f"/api/analyses/{analysis_id}/traces/{path}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Trace image not found."}


@pytest.mark.parametrize("suffix", ["traces", "traces/traces/T001_C2_ME_F1_dog_trace.jpg"])
def test_traces_for_an_unknown_analysis_are_not_found(client, suffix):
    response = client.get(f"/api/analyses/999999/{suffix}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis not found."}


@pytest.mark.parametrize("suffix", ["traces", "traces/traces/T001_C2_ME_F1_dog_trace.jpg"])
@pytest.mark.parametrize(
    "status", [AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING, AnalysisJobStatus.FAILED]
)
def test_traces_for_a_job_without_output_are_a_conflict(
    client, db_session, s3_client, suffix, status
):
    analysis_id = _create_job(client)
    s3_client.objects[f"reports/{analysis_id}/traces/T001_C2_ME_F1_dog_trace.jpg"] = b"stale"
    _finish_job(db_session, analysis_id, status=status, report_s3_prefix=None)

    response = client.get(f"/api/analyses/{analysis_id}/{suffix}")

    assert response.status_code == 409
    assert response.json() == {"detail": "No trace images are available for this analysis yet."}


def test_list_traces_gives_a_video_without_images_an_empty_list(client, db_session, s3_client):
    analysis_id = _finished_job_with_objects(
        client,
        db_session,
        s3_client,
        ["traces/T001_C2_ME_F1_dog_trace.jpg"],
        cuts=(_F1, "cuts/T001/T001_C2_ME_F2.mp4"),
    )

    response = client.get(f"/api/analyses/{analysis_id}/traces")

    assert [(video["cut_key"], len(video["images"])) for video in response.json()] == [
        (_F1, 1),
        ("cuts/T001/T001_C2_ME_F2.mp4", 0),
    ]


def test_list_traces_leaves_out_an_image_the_image_endpoint_would_refuse(
    client, db_session, s3_client
):
    # Listed only if it can also be served — a folder name outside the
    # allowed path characters would otherwise show a broken thumbnail.
    analysis_id = _finished_job_with_objects(
        client,
        db_session,
        s3_client,
        ["traces/T001_C2_ME_F1_dog_trace.jpg", "odd folder/T001_C2_ME_F1_fp_trace.jpg"],
    )

    response = client.get(f"/api/analyses/{analysis_id}/traces")

    assert [image["path"] for image in response.json()[0]["images"]] == [
        "traces/T001_C2_ME_F1_dog_trace.jpg"
    ]


def test_traces_of_a_multi_test_job_are_listed_and_served_per_video(client, db_session, s3_client):
    s3_client.objects["cuts/T001/T001_C2_ME_F1.mp4"] = b"video"
    s3_client.objects["cuts/T002/T002_C2_ME_F1.mp4"] = b"video"
    response = client.post("/api/analyses", json={"test_ids": ["T001", "T002"]})
    assert response.status_code == 201, response.text
    analysis_id = response.json()["id"]
    prefix = f"reports/{analysis_id}/"
    s3_client.objects[f"{prefix}T002/casiop_report.xlsx"] = b"per-test split"
    s3_client.objects[f"{prefix}traces/T001_C2_ME_F1_dog_trace.jpg"] = b"t001-image"
    s3_client.objects[f"{prefix}traces/T002_C2_ME_F1_dog_trace.jpg"] = b"t002-image"
    _finish_job(db_session, analysis_id, report_s3_prefix=prefix)

    listed = client.get(f"/api/analyses/{analysis_id}/traces").json()
    image = client.get(f"/api/analyses/{analysis_id}/traces/traces/T002_C2_ME_F1_dog_trace.jpg")

    assert [(video["cut_key"], len(video["images"])) for video in listed] == [
        ("cuts/T001/T001_C2_ME_F1.mp4", 1),
        ("cuts/T002/T002_C2_ME_F1.mp4", 1),
    ]
    assert image.content == b"t002-image"


def test_traces_of_a_job_under_the_old_test_first_prefix_are_found(client, db_session, s3_client):
    # Jobs from before ticket #91 keep `reports/<test_id>/<id>/`.
    analysis_id = _create_job(client)
    prefix = f"reports/T001/{analysis_id}/"
    s3_client.objects[f"{prefix}traces/T001_C2_ME_F1_dog_trace.jpg"] = b"old-layout-image"
    _finish_job(db_session, analysis_id, report_s3_prefix=prefix)

    listed = client.get(f"/api/analyses/{analysis_id}/traces").json()
    image = client.get(f"/api/analyses/{analysis_id}/traces/traces/T001_C2_ME_F1_dog_trace.jpg")

    assert [img["path"] for img in listed[0]["images"]] == ["traces/T001_C2_ME_F1_dog_trace.jpg"]
    assert image.content == b"old-layout-image"
