from app.core.config import settings
from tests.helpers import IDENTITY_HEADER, identity_headers

# Ticket #72: the app no longer authenticates anyone itself — Mechatronics
# does, and forwards the person's identity in an HTTP header that this app
# reads purely for attribution (docs/adr/0004-...). Exercised through the
# one endpoint that echoes it back, the existing endpoint-level seam.


def test_whoami_echoes_the_identity_header(client):
    response = client.get("/whoami", headers=identity_headers("jan.peeters@vives.be"))

    assert response.status_code == 200
    assert response.json() == {"identity": "jan.peeters@vives.be"}


def test_whoami_is_null_when_the_header_is_absent(client):
    """Local dev has no Mechatronics in front of it (user story 9)."""
    response = client.get("/whoami")

    assert response.status_code == 200
    assert response.json() == {"identity": None}


def test_whoami_header_name_is_case_insensitive(client):
    response = client.get("/whoami", headers={IDENTITY_HEADER.upper(): "jan.peeters@vives.be"})

    assert response.json() == {"identity": "jan.peeters@vives.be"}


def test_whoami_is_null_when_no_header_name_is_configured(client, monkeypatch):
    """`IDENTITY_HEADER_NAME` may stay empty until the real header is
    discovered on `dogtrace-app`; nothing is read then, whatever headers
    the caller sends."""
    monkeypatch.setattr(settings, "identity_header_name", None)

    response = client.get("/whoami", headers=identity_headers("jan.peeters@vives.be"))

    assert response.json() == {"identity": None}


def test_whoami_treats_an_empty_header_name_setting_as_unconfigured(client, monkeypatch):
    """`.env` may carry `IDENTITY_HEADER_NAME=` with nothing after it."""
    monkeypatch.setattr(settings, "identity_header_name", "")

    response = client.get("/whoami", headers=identity_headers("jan.peeters@vives.be"))

    assert response.json() == {"identity": None}


def test_whoami_ignores_a_blank_header_value(client):
    response = client.get("/whoami", headers=identity_headers("   "))

    assert response.json() == {"identity": None}


def test_whoami_trims_surrounding_whitespace(client):
    response = client.get("/whoami", headers=identity_headers("  jan.peeters@vives.be "))

    assert response.json() == {"identity": "jan.peeters@vives.be"}


def test_whoami_bounds_an_oversized_header_value(client):
    """The header is untrusted input (ENGINEERING-STANDARDS.md §5) headed for
    a bounded DB column; an oversized one must never reach it unbounded."""
    response = client.get("/whoami", headers=identity_headers("x" * 5000))

    assert response.status_code == 200
    assert response.json() == {"identity": "x" * 320}


def test_whoami_ignores_the_configured_header_when_a_different_one_is_sent(client):
    response = client.get("/whoami", headers={"X-Some-Other-Header": "jan.peeters@vives.be"})

    assert response.json() == {"identity": None}
