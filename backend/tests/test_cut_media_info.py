from app.core.config import settings
from app.main import app
from app.services.media_prober import ProbedMediaInfo
from app.services.s3_client import get_s3_client
from tests.helpers import identity_headers

# Ticket #22's acceptance criteria: exercised entirely through the HTTP API
# against the fake S3 client and fake prober (the `s3_client`/`media_prober`
# fixtures) — no real ffprobe binary or real video bytes anywhere in this
# file.

_CUT_KEY = "cuts/T001/T001_C1_ME_F1.mp4"


def test_get_info_probes_and_returns_duration_resolution_and_codec(
    client, db_session, s3_client, media_prober
):
    s3_client.objects[_CUT_KEY] = b"video-bytes"
    media_prober.result = ProbedMediaInfo(
        duration_seconds=42.5, width=1920, height=1080, codec="h264"
    )

    response = client.get("/media/cuts/info", params={"key": _CUT_KEY})

    assert response.status_code == 200
    assert response.json() == {
        "duration_seconds": 42.5,
        "width": 1920,
        "height": 1080,
        "codec": "h264",
    }
    assert len(media_prober.calls) == 1


def test_a_second_request_for_the_same_cut_reuses_the_cached_result(
    client, db_session, s3_client, media_prober
):
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    first = client.get("/media/cuts/info", params={"key": _CUT_KEY})
    second = client.get("/media/cuts/info", params={"key": _CUT_KEY})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # Only the first request should have reached the prober — the second
    # is served entirely from the cache.
    assert len(media_prober.calls) == 1


def test_a_changed_object_at_the_same_key_is_re_probed(client, db_session, s3_client, media_prober):
    s3_client.objects[_CUT_KEY] = b"original-bytes"
    client.get("/media/cuts/info", params={"key": _CUT_KEY})
    assert len(media_prober.calls) == 1

    # The object at the same key is replaced — its ETag (content-derived in
    # FakeS3Client) changes, so the cached row must no longer be considered
    # fresh.
    s3_client.objects[_CUT_KEY] = b"replaced-bytes-different-length"
    media_prober.result = ProbedMediaInfo(
        duration_seconds=99.0, width=1280, height=720, codec="vp9"
    )

    response = client.get("/media/cuts/info", params={"key": _CUT_KEY})

    assert response.status_code == 200
    assert response.json() == {
        "duration_seconds": 99.0,
        "width": 1280,
        "height": 720,
        "codec": "vp9",
    }
    assert len(media_prober.calls) == 2


def test_get_info_rejects_a_key_outside_cuts(client, db_session, s3_client, media_prober):
    s3_client.objects["source/secret.mp4"] = b"top-secret"

    response = client.get("/media/cuts/info", params={"key": "source/secret.mp4"})

    assert response.status_code == 404
    assert len(media_prober.calls) == 0


def test_get_info_returns_not_found_for_a_cut_key_that_does_not_exist_in_s3(
    client, db_session, s3_client, media_prober
):
    response = client.get("/media/cuts/info", params={"key": _CUT_KEY})

    assert response.status_code == 404
    assert len(media_prober.calls) == 0


def test_cut_info_needs_no_login_and_ignores_identity(client, s3_client, media_prober):
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    anonymous = client.get("/media/cuts/info", params={"key": _CUT_KEY})
    identified = client.get(
        "/media/cuts/info",
        params={"key": _CUT_KEY},
        headers=identity_headers("jan.peeters@vives.be"),
    )

    assert anonymous.status_code == 200
    assert identified.status_code == 200
    assert anonymous.json() == identified.json()
    # Both share the same cache row — only one probe total.
    assert len(media_prober.calls) == 1


def test_get_info_never_lets_a_dot_dot_filename_escape_the_temp_download_directory(
    client, db_session, s3_client, media_prober
):
    """`cuts/T001/..` is a well-formed Cut key by shape (no "/" in the
    filename component, same regex ticket #21's key validation uses) even
    though S3 has no directory semantics to make ".." meaningful there —
    the risk is entirely local: naively joining it onto a temp directory
    for probing would resolve outside that directory. This must be probed
    safely (or fail cleanly), never write/read outside the temp dir."""
    key = "cuts/T001/.."
    s3_client.objects[key] = b"video-bytes"

    response = client.get("/media/cuts/info", params={"key": key})

    assert response.status_code == 200
    [probed_path] = media_prober.calls
    assert probed_path.name == "cut"
    assert probed_path.parent.name != "T001"


class _VanishingS3Client:
    """Wraps a FakeS3Client so the object is deleted the moment head_object
    succeeds — simulating it being deleted/replaced in the real gap between
    that freshness check and the download that follows it."""

    def __init__(self, inner):
        self._inner = inner

    def head_object(self, key):
        info = self._inner.head_object(key)
        del self._inner.objects[key]
        return info

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_get_info_returns_not_found_when_the_object_vanishes_between_head_and_download(
    client, db_session, s3_client, media_prober
):
    """Regression test: head_object succeeding is no guarantee the object
    is still there by the time download_file runs — that gap must surface
    as the same 404 a never-existed Cut gets, not an unhandled 500."""
    s3_client.objects[_CUT_KEY] = b"video-bytes"
    app.dependency_overrides[get_s3_client] = lambda: _VanishingS3Client(s3_client)

    response = client.get("/media/cuts/info", params={"key": _CUT_KEY})

    assert response.status_code == 404
    assert len(media_prober.calls) == 0


def test_get_info_is_rate_limited_per_identity(client, s3_client, media_prober, monkeypatch):
    """A cache miss here does a full S3 download plus an ffprobe subprocess
    call — the same expense class as /cuts/token issuance, which is
    already rate limited (CONTEXT.md's "Media browser — abuse protection"
    decision)."""
    monkeypatch.setattr(settings, "cut_info_rate_limit_max_attempts_per_identity", 2)
    s3_client.objects[_CUT_KEY] = b"video-bytes"
    headers = identity_headers("jan.peeters@vives.be")

    for _ in range(2):
        response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)
        assert response.status_code == 200

    throttled = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers

    # Another identity has its own budget.
    other = client.get(
        "/media/cuts/info", params={"key": _CUT_KEY}, headers=identity_headers("other@vives.be")
    )
    assert other.status_code == 200


def test_get_info_rate_limit_still_applies_without_an_identity(
    client, s3_client, media_prober, monkeypatch
):
    """No identity header (local development, or a misconfigured proxy)
    must not silently switch the limit off for an expensive operation —
    every anonymous caller shares one bucket instead."""
    monkeypatch.setattr(settings, "cut_info_rate_limit_max_attempts_per_identity", 1)
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    assert client.get("/media/cuts/info", params={"key": _CUT_KEY}).status_code == 200
    assert client.get("/media/cuts/info", params={"key": _CUT_KEY}).status_code == 429
