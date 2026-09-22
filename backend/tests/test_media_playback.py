from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_media_token
from app.models.audit_log import AuditAction, AuditLog
from tests.helpers import identity_headers

# Ticket #21's acceptance criteria: exercised entirely through the HTTP API
# against the fake S3 client (the `s3_client` fixture) — no real bucket, no
# real video file, anywhere in this file.

_CUT_KEY = "cuts/T001/T001_C1_ME_F1.mp4"


# --- POST /media/cuts/token: minting -----------------------------------


def test_mint_token_for_a_well_formed_cut_key_succeeds(client, db_session, s3_client):
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    response = client.post("/api/media/cuts/token", json={"key": _CUT_KEY, "action": "play"})

    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["expires_in"] == settings.media_token_expire_minutes * 60


def test_mint_token_for_play_writes_a_cut_play_requested_audit_row(client, db_session, s3_client):
    """Ticket #86 / issue #79: named "requested," not "played" — minting a
    token isn't proof playback happened, attributed via
    `get_verified_identity` like every other audit call site."""
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    response = client.post("/api/media/cuts/token", json={"key": _CUT_KEY, "action": "play"})

    assert response.status_code == 200
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.CUT_PLAY_REQUESTED)
    )
    assert row is not None
    assert row.target_type == "cut"
    assert row.target == _CUT_KEY
    assert row.identity_verified is False


def test_mint_token_for_download_writes_a_cut_download_requested_audit_row(
    client, db_session, s3_client
):
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    response = client.post("/api/media/cuts/token", json={"key": _CUT_KEY, "action": "download"})

    assert response.status_code == 200
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.CUT_DOWNLOAD_REQUESTED)
    )
    assert row is not None
    assert row.target_type == "cut"
    assert row.target == _CUT_KEY
    assert row.identity_verified is False


def test_mint_token_does_not_write_an_audit_row_for_a_rejected_request(
    client, db_session, s3_client
):
    response = client.post(
        "/api/media/cuts/token", json={"key": "source/secret.mp4", "action": "play"}
    )

    assert response.status_code == 404
    assert db_session.scalar(select(AuditLog).where(AuditLog.target_type == "cut")) is None


def test_mint_token_rejects_a_key_outside_cuts(client, db_session, s3_client):
    s3_client.objects["source/secret.mp4"] = b"top-secret"

    response = client.post(
        "/api/media/cuts/token", json={"key": "source/secret.mp4", "action": "play"}
    )

    assert response.status_code == 404


def test_mint_token_rejects_a_traversal_attempt(client, db_session, s3_client):
    s3_client.objects["cuts/secret.mp4"] = b"top-secret"

    for key in ["cuts/T001/../../source/secret.mp4", "cuts/T001/sub/nested.mp4", "cuts/secret.mp4"]:
        response = client.post("/api/media/cuts/token", json={"key": key, "action": "play"})
        assert response.status_code == 404


def test_mint_token_is_rate_limited_per_identity(client, s3_client, monkeypatch):
    monkeypatch.setattr(settings, "media_token_rate_limit_max_attempts_per_identity", 2)
    s3_client.objects[_CUT_KEY] = b"video-bytes"
    headers = identity_headers("jan.peeters@vives.be")

    for _ in range(2):
        response = client.post(
            "/api/media/cuts/token", json={"key": _CUT_KEY, "action": "play"}, headers=headers
        )
        assert response.status_code == 200

    throttled = client.post(
        "/api/media/cuts/token", json={"key": _CUT_KEY, "action": "play"}, headers=headers
    )
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


def test_mint_token_rate_limit_is_scoped_per_identity(client, s3_client, monkeypatch):
    monkeypatch.setattr(settings, "media_token_rate_limit_max_attempts_per_identity", 1)
    s3_client.objects[_CUT_KEY] = b"video-bytes"

    exhausted = client.post(
        "/api/media/cuts/token",
        json={"key": _CUT_KEY, "action": "play"},
        headers=identity_headers("jan.peeters@vives.be"),
    )
    assert exhausted.status_code == 200

    still_ok = client.post(
        "/api/media/cuts/token",
        json={"key": _CUT_KEY, "action": "play"},
        headers=identity_headers("other@vives.be"),
    )
    assert still_ok.status_code == 200


def test_mint_token_rate_limit_still_applies_without_an_identity(client, s3_client, monkeypatch):
    """No identity header must not silently switch the limit off — token
    issuance is what throttles streaming at all (nothing rate limits the
    stream endpoint itself), so anonymous callers share one bucket."""
    monkeypatch.setattr(settings, "media_token_rate_limit_max_attempts_per_identity", 1)
    s3_client.objects[_CUT_KEY] = b"video-bytes"
    payload = {"key": _CUT_KEY, "action": "play"}

    assert client.post("/api/media/cuts/token", json=payload).status_code == 200
    assert client.post("/api/media/cuts/token", json=payload).status_code == 429


# --- GET /media/stream: serving ------------------------------------------


def test_stream_with_a_play_token_returns_full_content_inline(client, db_session, s3_client):
    s3_client.objects[_CUT_KEY] = b"0123456789"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "play", "token": token}
    )

    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["content-disposition"] == 'inline; filename="T001_C1_ME_F1.mp4"'
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_stream_with_a_download_token_sets_attachment_disposition(client, db_session, s3_client):
    s3_client.objects[_CUT_KEY] = b"0123456789"
    token = create_media_token(cut_key=_CUT_KEY, action="download")

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "download", "token": token}
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="T001_C1_ME_F1.mp4"'


def test_stream_forwards_a_range_request_as_partial_content(client, db_session, s3_client):
    s3_client.objects[_CUT_KEY] = b"0123456789"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream",
        params={"key": _CUT_KEY, "action": "play", "token": token},
        headers={"Range": "bytes=2-4"},
    )

    assert response.status_code == 206
    assert response.content == b"234"
    assert response.headers["content-range"] == "bytes 2-4/10"
    assert response.headers["content-length"] == "3"


def _tracking_read_range(s3_client):
    """Wrap `s3_client.read_range` to record each call's byte-range size,
    without changing its behavior — used to prove a large stream is
    fetched from S3 in several bounded reads, never one huge one."""
    original = s3_client.read_range
    call_sizes: list[int] = []

    def tracking(key, start, end):
        call_sizes.append(end - start + 1)
        return original(key, start, end)

    s3_client.read_range = tracking
    return call_sizes


def test_stream_honors_a_full_open_ended_range_via_several_bounded_s3_reads(
    client, db_session, s3_client
):
    """The client gets the whole remainder of an open-ended range (RFC-
    correct — no artificial truncation), but the backend must never pull
    more than _STREAM_CHUNK_BYTES from S3 in a single call, so a large Cut
    is never materialized in memory all at once."""
    from app.services.s3_client import STREAM_CHUNK_BYTES as _STREAM_CHUNK_BYTES

    total_size = int(_STREAM_CHUNK_BYTES * 2.5)
    s3_client.objects[_CUT_KEY] = b"x" * total_size
    token = create_media_token(cut_key=_CUT_KEY, action="play")
    call_sizes = _tracking_read_range(s3_client)

    response = client.get(
        "/api/media/stream",
        params={"key": _CUT_KEY, "action": "play", "token": token},
        headers={"Range": "bytes=0-"},
    )

    assert response.status_code == 206
    assert len(response.content) == total_size
    assert response.headers["content-range"] == f"bytes 0-{total_size - 1}/{total_size}"
    assert len(call_sizes) > 1
    assert all(size <= _STREAM_CHUNK_BYTES for size in call_sizes)


def test_stream_with_no_range_header_streams_a_large_download_in_bounded_chunks(
    client, db_session, s3_client
):
    """The download flow (a plain <a> click, no Range header) must be just
    as memory-bounded as a Range-based play request — this is the common
    path for "Download", not an edge case."""
    from app.services.s3_client import STREAM_CHUNK_BYTES as _STREAM_CHUNK_BYTES

    total_size = int(_STREAM_CHUNK_BYTES * 2.5)
    s3_client.objects[_CUT_KEY] = b"x" * total_size
    token = create_media_token(cut_key=_CUT_KEY, action="download")
    call_sizes = _tracking_read_range(s3_client)

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "download", "token": token}
    )

    assert response.status_code == 200
    assert len(response.content) == total_size
    assert len(call_sizes) > 1
    assert all(size <= _STREAM_CHUNK_BYTES for size in call_sizes)


def test_stream_suffix_range_streams_in_bounded_chunks(client, db_session, s3_client):
    from app.services.s3_client import STREAM_CHUNK_BYTES as _STREAM_CHUNK_BYTES

    total_size = int(_STREAM_CHUNK_BYTES * 2.5)
    s3_client.objects[_CUT_KEY] = b"x" * total_size
    token = create_media_token(cut_key=_CUT_KEY, action="play")
    call_sizes = _tracking_read_range(s3_client)

    response = client.get(
        "/api/media/stream",
        params={"key": _CUT_KEY, "action": "play", "token": token},
        headers={"Range": f"bytes=-{total_size}"},
    )

    assert response.status_code == 206
    assert len(response.content) == total_size
    assert len(call_sizes) > 1
    assert all(size <= _STREAM_CHUNK_BYTES for size in call_sizes)


def test_stream_treats_a_reversed_range_as_malformed_and_serves_full_content(
    client, db_session, s3_client
):
    """RFC 7233 §2.1: a byte-range-spec with last-byte-pos < first-byte-pos
    is syntactically invalid and must be ignored (served as an ordinary
    GET), not rejected with 416."""
    s3_client.objects[_CUT_KEY] = b"0123456789"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream",
        params={"key": _CUT_KEY, "action": "play", "token": token},
        headers={"Range": "bytes=5-3"},
    )

    assert response.status_code == 200
    assert response.content == b"0123456789"


def test_stream_escapes_a_quote_character_in_the_content_disposition_filename(
    client, db_session, s3_client
):
    key = 'cuts/T001/weird"name.mp4'
    s3_client.objects[key] = b"data"
    token = create_media_token(cut_key=key, action="download")

    response = client.get(
        "/api/media/stream", params={"key": key, "action": "download", "token": token}
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="weird\\"name.mp4"'


def test_stream_rejects_an_unsatisfiable_range(client, db_session, s3_client):
    s3_client.objects[_CUT_KEY] = b"0123456789"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream",
        params={"key": _CUT_KEY, "action": "play", "token": token},
        headers={"Range": "bytes=100-200"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"


def test_stream_rejects_a_token_used_for_a_different_cut(client, db_session, s3_client):
    other_key = "cuts/T002/T002_C1_ME_F1.mp4"
    s3_client.objects[_CUT_KEY] = b"a"
    s3_client.objects[other_key] = b"b"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream", params={"key": other_key, "action": "play", "token": token}
    )

    assert response.status_code == 403


def test_stream_rejects_a_token_used_for_the_other_action(client, db_session, s3_client):
    s3_client.objects[_CUT_KEY] = b"a"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "download", "token": token}
    )

    assert response.status_code == 403


def test_stream_rejects_an_expired_token(client, db_session, s3_client, monkeypatch):
    monkeypatch.setattr(settings, "media_token_expire_minutes", -1)
    s3_client.objects[_CUT_KEY] = b"a"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "play", "token": token}
    )

    assert response.status_code == 401


def test_stream_rejects_a_garbage_token(client, db_session, s3_client):
    s3_client.objects[_CUT_KEY] = b"a"

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "play", "token": "not-a-real-token"}
    )

    assert response.status_code == 401


def test_stream_rejects_a_correctly_signed_token_of_another_type(client, s3_client):
    """Only a token whose "type" claim is "media" is accepted, even when it
    is signed with the right key and carries matching Cut/action claims (see
    create_media_token) — a stray token of any other kind must never open a
    Cut."""
    s3_client.objects[_CUT_KEY] = b"a"
    other_type_token = jwt.encode(
        {
            "type": "access",
            "cut_key": _CUT_KEY,
            "action": "play",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.media_token_secret_key,
        algorithm="HS256",
    )

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "play", "token": other_type_token}
    )

    assert response.status_code == 401


def test_stream_rejects_a_key_outside_cuts_even_with_a_forged_matching_token(
    client, db_session, s3_client
):
    """Defense in depth: even if a token's own cut_key claim were somehow
    outside cuts/, the streaming endpoint independently validates the key
    shape before ever calling into S3 for it."""
    s3_client.objects["source/secret.mp4"] = b"top-secret"
    token = create_media_token(cut_key="source/secret.mp4", action="play")

    response = client.get(
        "/api/media/stream", params={"key": "source/secret.mp4", "action": "play", "token": token}
    )

    assert response.status_code == 404
    assert b"secret" not in response.content


def test_stream_returns_not_found_for_a_cut_key_that_no_longer_exists_in_s3(
    client, db_session, s3_client
):
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "play", "token": token}
    )

    assert response.status_code == 404


def test_stream_requires_no_bearer_authentication(client, db_session, s3_client):
    """The streaming endpoint is reached by the browser's native <video
    src>/download requests — it must work with only the media token: no
    `Authorization` header and no identity header (ADR-0002)."""
    s3_client.objects[_CUT_KEY] = b"a"
    token = create_media_token(cut_key=_CUT_KEY, action="play")

    response = client.get(
        "/api/media/stream", params={"key": _CUT_KEY, "action": "play", "token": token}
    )

    assert response.status_code == 200


def test_media_token_type_claim_rejects_a_forged_access_typed_media_token(monkeypatch):
    """Unit-level guard on decode_media_token itself: a JWT signed with the
    same secret but type="access" (not a media token at all) is rejected —
    exercises the "type" claim check directly rather than only through the
    HTTP layer above."""
    import jwt as pyjwt

    from app.core.security import decode_media_token

    forged = pyjwt.encode(
        {"cut_key": _CUT_KEY, "action": "play", "type": "access"},
        settings.media_token_secret_key,
        algorithm="HS256",
    )

    with pytest.raises(jwt.PyJWTError):
        decode_media_token(forged)
