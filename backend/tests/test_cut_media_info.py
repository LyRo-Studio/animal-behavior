from app.core.config import settings
from app.main import app
from app.models.account import AccountRole
from app.services.media_prober import ProbedMediaInfo
from app.services.s3_client import get_s3_client
from tests.helpers import create_account, login_headers

# Ticket #22's acceptance criteria: exercised entirely through the HTTP API
# against the fake S3 client and fake prober (the `s3_client`/`media_prober`
# fixtures) — no real ffprobe binary or real video bytes anywhere in this
# file.

_CUT_KEY = "cuts/T001/T001_C1_ME_F1.mp4"


def _user_headers(client, db_session, *, role: AccountRole = AccountRole.USER):
    email = "admin.person@vives.be" if role == AccountRole.ADMIN else "jan.peeters@vives.be"
    create_account(db_session, email=email, role=role)
    return login_headers(client, email)


def test_get_info_probes_and_returns_duration_resolution_and_codec(
    client, db_session, s3_client, media_prober
):
    headers = _user_headers(client, db_session)
    s3_client.objects[_CUT_KEY] = b"video-bytes"
    media_prober.result = ProbedMediaInfo(
        duration_seconds=42.5, width=1920, height=1080, codec="h264"
    )

    response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)

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
    headers = _user_headers(client, db_session)
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    first = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)
    second = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # Only the first request should have reached the prober — the second
    # is served entirely from the cache.
    assert len(media_prober.calls) == 1


def test_a_changed_object_at_the_same_key_is_re_probed(client, db_session, s3_client, media_prober):
    headers = _user_headers(client, db_session)
    s3_client.objects[_CUT_KEY] = b"original-bytes"
    client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)
    assert len(media_prober.calls) == 1

    # The object at the same key is replaced — its ETag (content-derived in
    # FakeS3Client) changes, so the cached row must no longer be considered
    # fresh.
    s3_client.objects[_CUT_KEY] = b"replaced-bytes-different-length"
    media_prober.result = ProbedMediaInfo(
        duration_seconds=99.0, width=1280, height=720, codec="vp9"
    )

    response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "duration_seconds": 99.0,
        "width": 1280,
        "height": 720,
        "codec": "vp9",
    }
    assert len(media_prober.calls) == 2


def test_get_info_rejects_a_key_outside_cuts(client, db_session, s3_client, media_prober):
    headers = _user_headers(client, db_session)
    s3_client.objects["source/secret.mp4"] = b"top-secret"

    response = client.get("/media/cuts/info", params={"key": "source/secret.mp4"}, headers=headers)

    assert response.status_code == 404
    assert len(media_prober.calls) == 0


def test_get_info_returns_not_found_for_a_cut_key_that_does_not_exist_in_s3(
    client, db_session, s3_client, media_prober
):
    headers = _user_headers(client, db_session)

    response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)

    assert response.status_code == 404
    assert len(media_prober.calls) == 0


def test_user_and_admin_have_identical_access(client, db_session, s3_client, media_prober):
    s3_client.objects[_CUT_KEY] = b"video-bytes"
    user_headers = _user_headers(client, db_session, role=AccountRole.USER)
    admin_headers = _user_headers(client, db_session, role=AccountRole.ADMIN)

    user_response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=user_headers)
    admin_response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=admin_headers)

    assert user_response.status_code == 200
    assert admin_response.status_code == 200
    assert user_response.json() == admin_response.json()
    # Both Accounts share the same cache row — only one probe total.
    assert len(media_prober.calls) == 1


def test_unauthenticated_is_rejected(client, db_session):
    response = client.get("/media/cuts/info", params={"key": _CUT_KEY})
    assert response.status_code == 401


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
    headers = _user_headers(client, db_session)
    s3_client.objects[key] = b"video-bytes"

    response = client.get("/media/cuts/info", params={"key": key}, headers=headers)

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
    headers = _user_headers(client, db_session)
    app.dependency_overrides[get_s3_client] = lambda: _VanishingS3Client(s3_client)

    response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)

    assert response.status_code == 404
    assert len(media_prober.calls) == 0


def test_get_info_is_rate_limited_per_account(
    client, db_session, s3_client, media_prober, monkeypatch
):
    """A cache miss here does a full S3 download plus an ffprobe subprocess
    call — the same expense class as /cuts/token issuance, which is
    already rate limited (CONTEXT.md's "Media browser — abuse protection"
    decision)."""
    monkeypatch.setattr(settings, "cut_info_rate_limit_max_attempts_per_account", 2)
    headers = _user_headers(client, db_session)
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    for _ in range(2):
        response = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)
        assert response.status_code == 200

    throttled = client.get("/media/cuts/info", params={"key": _CUT_KEY}, headers=headers)
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers
