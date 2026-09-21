import json

import pytest

from app.core.config import Settings, settings
from app.core.security import create_media_token
from app.services import request_diagnostics
from tests.helpers import IDENTITY_HEADER, identity_headers

# Ticket #72's identity-header discovery: a temporary, off-by-default endpoint
# that lets someone on the hosted site read, from the browser console, which
# headers Mechatronics forwards — and whether a native <video> request (which
# is what ADR-0002's media tokens exist for) carries the identity header too.
# It must never become a way to read a credential back out, hence the
# redaction and names-only tests below.

_DEBUG_URL = "/api/debug/request-headers"
_STREAM_URL = "/api/media/stream"


@pytest.fixture(autouse=True)
def _clean_recent_requests():
    # Module-level state: don't let one test's stream requests show up in
    # another's.
    request_diagnostics.clear_recent_media_requests()
    yield
    request_diagnostics.clear_recent_media_requests()


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "debug_request_headers_enabled", True)


_CUT_KEY = "cuts/T001/T001_C1_ME_F1.mp4"


def _stream(client, *, token=None, headers=None):
    # A validly-signed token is what a real <video> request carries. The Cut
    # itself need not exist: the request is recorded once the token checks
    # out, before S3 is consulted.
    token = token if token is not None else create_media_token(_CUT_KEY, "play")
    return client.get(
        _STREAM_URL,
        params={"key": _CUT_KEY, "action": "play", "token": token},
        headers=headers or {},
    )


def test_is_a_404_unless_explicitly_enabled(client):
    """Off by default — in production it exists only while someone is
    actively discovering the header."""
    assert settings.debug_request_headers_enabled is False

    assert client.get(_DEBUG_URL).status_code == 404


def test_lists_the_headers_the_backend_received(client, enabled):
    response = client.get(
        _DEBUG_URL,
        headers={"X-Forwarded-User": "jan.peeters@vives.be", "X-Something-Else": "value"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["headers"]["x-forwarded-user"] == "jan.peeters@vives.be"
    assert body["headers"]["x-something-else"] == "value"


def test_reports_the_configured_identity_header_and_whether_it_arrived(client, enabled):
    with_header = client.get(_DEBUG_URL, headers=identity_headers("jan.peeters@vives.be")).json()
    without_header = client.get(_DEBUG_URL).json()

    assert with_header["identity_header_name"] == IDENTITY_HEADER
    assert with_header["identity_header_present"] is True
    assert without_header["identity_header_present"] is False


def test_identity_header_present_is_false_when_none_is_configured(client, enabled, monkeypatch):
    monkeypatch.setattr(settings, "identity_header_name", None)

    body = client.get(_DEBUG_URL, headers=identity_headers("jan.peeters@vives.be")).json()

    assert body["identity_header_name"] is None
    assert body["identity_header_present"] is False


@pytest.mark.parametrize(
    "name",
    [
        "Cookie",
        "Authorization",
        "Proxy-Authorization",
        "X-Api-Key",
        "X-Auth-Token",
        "X-Amzn-Oidc-Accesstoken",
        "X-Client-Secret",
        "X-Session-Id",
    ],
)
def test_credential_bearing_headers_are_never_echoed(client, enabled, name):
    response = client.get(_DEBUG_URL, headers={name: "hunter2-super-secret"})

    assert response.json()["headers"][name.lower()] == "[redacted]"
    assert "hunter2-super-secret" not in response.text


def test_headers_that_merely_sound_like_identity_are_not_redacted(client, enabled):
    """The real identity header is likely named something like `X-Auth-User`
    or `X-Forwarded-Email` — redacting on "auth" would hide the very thing
    being looked for."""
    headers = {
        "X-Auth-User": "jan.peeters@vives.be",
        "X-Forwarded-Email": "jan.peeters@vives.be",
        "Remote-User": "jan.peeters",
    }

    shown = client.get(_DEBUG_URL, headers=headers).json()["headers"]

    assert shown["x-auth-user"] == "jan.peeters@vives.be"
    assert shown["x-forwarded-email"] == "jan.peeters@vives.be"
    assert shown["remote-user"] == "jan.peeters"


def test_very_long_values_are_truncated(client, enabled):
    shown = client.get(_DEBUG_URL, headers={"X-Long": "a" * 5000}).json()["headers"]

    assert len(shown["x-long"]) <= 250
    assert shown["x-long"].startswith("a" * 200)


def test_records_names_only_for_media_stream_requests(client, enabled):
    _stream(
        client,
        headers={**identity_headers("jan.peeters@vives.be"), "Sec-Fetch-Dest": "video"},
    )

    recent = client.get(_DEBUG_URL).json()["recent_media_requests"]

    assert len(recent) == 1
    record = recent[0]
    assert record["sec_fetch_dest"] == "video"
    assert IDENTITY_HEADER.lower() in record["header_names"]
    assert record["identity_header_present"] is True
    # Names and a couple of booleans — never a value, never the query string.
    assert "jan.peeters@vives.be" not in json.dumps(recent)
    assert create_media_token(_CUT_KEY, "play")[:20] not in json.dumps(recent)
    assert "token=" not in json.dumps(recent)


def test_a_stream_request_without_the_identity_header_is_recorded_as_such(client, enabled):
    """This is the media-token gate's answer: a native request that did not
    carry the header."""
    _stream(client, headers={"Sec-Fetch-Dest": "video"})

    (record,) = client.get(_DEBUG_URL).json()["recent_media_requests"]

    assert record["identity_header_present"] is False
    assert IDENTITY_HEADER.lower() not in record["header_names"]


def test_nothing_is_recorded_while_disabled(client, monkeypatch):
    _stream(client, headers=identity_headers("jan.peeters@vives.be"))

    monkeypatch.setattr(settings, "debug_request_headers_enabled", True)
    assert client.get(_DEBUG_URL).json()["recent_media_requests"] == []


def test_the_record_is_bounded_and_keeps_the_newest(client, enabled):
    for i in range(request_diagnostics.MAX_RECENT_MEDIA_REQUESTS + 5):
        _stream(client, headers={"Sec-Fetch-Dest": f"dest-{i}"})

    recent = client.get(_DEBUG_URL).json()["recent_media_requests"]

    assert len(recent) == request_diagnostics.MAX_RECENT_MEDIA_REQUESTS
    assert (
        recent[-1]["sec_fetch_dest"] == f"dest-{request_diagnostics.MAX_RECENT_MEDIA_REQUESTS + 4}"
    )


def test_junk_stream_requests_do_not_evict_real_ones(client, enabled):
    """Only a request whose media token checks out is recorded, so a scanner
    (or retries) hammering the endpoint with garbage can't push the real
    <video> request out of the small buffer before it is read."""
    _stream(client, headers={"Sec-Fetch-Dest": "video"})
    for _ in range(request_diagnostics.MAX_RECENT_MEDIA_REQUESTS * 2):
        _stream(client, token="not-a-real-token", headers={"Sec-Fetch-Dest": "image"})

    recent = client.get(_DEBUG_URL).json()["recent_media_requests"]

    assert [r["sec_fetch_dest"] for r in recent] == ["video"]


# Value-based redaction: a credential can sit under a header name the
# name-based rules know nothing about.
_A_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJqYW4ifQ.c2lnbmF0dXJlLWJ5dGVz"


@pytest.mark.parametrize(
    "name, value",
    [
        ("X-Amzn-Oidc-Data", _A_JWT),
        ("X-Forwarded-Assertion", _A_JWT),
        ("X-Something", "Bearer abc.def.ghi"),
        ("X-Something", "basic dXNlcjpwYXNz"),
    ],
)
def test_credential_shaped_values_are_redacted_whatever_the_name(client, enabled, name, value):
    response = client.get(_DEBUG_URL, headers={name: value})

    assert response.json()["headers"][name.lower()] == "[redacted]"
    assert value not in response.text


@pytest.mark.parametrize("name", ["Referer", "X-Original-URI", "X-Forwarded-Uri"])
def test_query_strings_are_stripped_from_every_value(client, enabled, name):
    """`token=` (ADR-0002's media token) lives in a URL's query string, and
    URL-carrying headers such as Referer would echo it."""
    response = client.get(_DEBUG_URL, headers={name: "/api/media/stream?key=k&token=SECRET-TOKEN"})

    shown = response.json()["headers"][name.lower()]
    assert shown.startswith("/api/media/stream")
    assert "SECRET-TOKEN" not in response.text
    assert "token=" not in shown


def test_an_email_style_identity_is_not_mistaken_for_a_credential(client, enabled):
    shown = client.get(_DEBUG_URL, headers={"X-Auth-User": "jan.peeters@student.vives.be"}).json()
    assert shown["headers"]["x-auth-user"] == "jan.peeters@student.vives.be"


def test_repeated_headers_are_all_shown_not_just_the_last(client, enabled):
    """A proxy that appends a second identity value (or a spoofed extra one)
    must be visible — collapsing to the last value would hide it."""
    response = client.get(_DEBUG_URL, headers=[("X-Dup", "first"), ("X-Dup", "second")])

    assert response.json()["headers"]["x-dup"] == "first, second"


def test_an_empty_setting_means_off_rather_than_a_startup_crash():
    """`.env` may carry `DEBUG_REQUEST_HEADERS_ENABLED=` with nothing after
    it, as it may for `IDENTITY_HEADER_NAME=`."""
    assert (
        Settings(_env_file=None, debug_request_headers_enabled="").debug_request_headers_enabled
        is False
    )
    assert (
        Settings(_env_file=None, debug_request_headers_enabled="true").debug_request_headers_enabled
        is True
    )
