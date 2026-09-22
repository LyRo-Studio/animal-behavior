"""Direct tests of `get_verified_identity` (ticket #82, issue #79,
docs/adr/0005-jwt-verified-identity-for-audit-log-only.md), same boundary
as `core/security.py`'s existing token tests. Exercises real RS256
signing/verification against a real (local, ephemeral) JWKS HTTP endpoint —
`PyJWKClient` itself makes a real HTTP GET, so nothing about its own
fetch/cache behavior is mocked away.
"""

import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request

from app.api.deps import get_verified_identity
from tests.helpers import IDENTITY_HEADER

_AUTHENTIK_EMAIL_HEADER = "X-authentik-email"
_AUTHENTIK_JWT_HEADER = "X-authentik-jwt"
_AUTHENTIK_JWKS_HEADER = "X-authentik-meta-jwks"

_KID = "test-key-1"
_OTHER_KID = "test-key-2"


def _request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    }
    return Request(scope)


def _generate_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk_for(private_key: RSAPrivateKey, kid: str) -> dict:
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


def _sign(
    private_key: RSAPrivateKey,
    kid: str,
    *,
    email: str = "jan.peeters@vives.be",
    exp: datetime | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {"email": email, "iat": now, "exp": exp or now + timedelta(hours=1)}
    return jwt.encode(payload, _pem(private_key), algorithm="RS256", headers={"kid": kid})


def _pem(private_key: RSAPrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


class _JWKSRequestHandler(BaseHTTPRequestHandler):
    jwks_body: bytes = b"{}"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's own naming)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.jwks_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # Silence the default per-request stderr logging.


@pytest.fixture()
def jwks_server() -> Iterator[Callable[[list[dict]], str]]:
    """A factory (`make_jwks_url(jwk_dicts) -> url`) serving a real JWKS
    document over a real local HTTP server, plus an unreachable URL that's
    guaranteed to fail fast (nothing listens on it)."""
    servers: list[HTTPServer] = []

    def make_jwks_url(jwks: list[dict]) -> str:
        handler_cls = type(
            "_Handler", (_JWKSRequestHandler,), {"jwks_body": json.dumps({"keys": jwks}).encode()}
        )
        server = HTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        port = server.server_address[1]
        return f"http://127.0.0.1:{port}/jwks.json"

    yield make_jwks_url

    for server in servers:
        server.shutdown()
        server.server_close()


# `get_identity`'s configured header (tests.helpers.IDENTITY_HEADER) is
# deliberately different from the fixed `X-authentik-*` headers
# `get_verified_identity` reads — proves the two are genuinely independent
# trust paths (ADR-0005), not accidentally reading the same setting.
def test_returns_none_and_unverified_with_no_identity_header_at_all():
    identity, verified = get_verified_identity(_request({}))

    assert (identity, verified) == (None, False)


def test_ignores_the_configurable_get_identity_header(jwks_server):
    identity, verified = get_verified_identity(_request({IDENTITY_HEADER: "someone@vives.be"}))

    assert (identity, verified) == (None, False)


def test_returns_unverified_plain_value_with_no_jwt_or_jwks_headers_sent():
    identity, verified = get_verified_identity(
        _request({_AUTHENTIK_EMAIL_HEADER: "jan.peeters@vives.be"})
    )

    assert (identity, verified) == ("jan.peeters@vives.be", False)


def test_returns_verified_true_for_a_valid_jwt(jwks_server):
    private_key = _generate_key()
    jwks_url = jwks_server([_jwk_for(private_key, _KID)])
    token = _sign(private_key, _KID)

    identity, verified = get_verified_identity(
        _request(
            {
                _AUTHENTIK_EMAIL_HEADER: "jan.peeters@vives.be",
                _AUTHENTIK_JWT_HEADER: token,
                _AUTHENTIK_JWKS_HEADER: jwks_url,
            }
        )
    )

    assert (identity, verified) == ("jan.peeters@vives.be", True)


def test_falls_back_unverified_for_an_expired_jwt(jwks_server):
    private_key = _generate_key()
    jwks_url = jwks_server([_jwk_for(private_key, _KID)])
    token = _sign(private_key, _KID, exp=datetime.now(UTC) - timedelta(minutes=1))

    identity, verified = get_verified_identity(
        _request(
            {
                _AUTHENTIK_EMAIL_HEADER: "jan.peeters@vives.be",
                _AUTHENTIK_JWT_HEADER: token,
                _AUTHENTIK_JWKS_HEADER: jwks_url,
            }
        )
    )

    assert (identity, verified) == ("jan.peeters@vives.be", False)


def test_falls_back_unverified_for_a_bad_signature_jwt(jwks_server):
    """The JWKS serves `_KID`'s real public key, but the token was signed
    by a completely different private key carrying the *same* `kid` — the
    signature must not verify even though the key lookup succeeds."""
    published_key = _generate_key()
    forged_key = _generate_key()
    jwks_url = jwks_server([_jwk_for(published_key, _KID)])
    token = _sign(forged_key, _KID)

    identity, verified = get_verified_identity(
        _request(
            {
                _AUTHENTIK_EMAIL_HEADER: "jan.peeters@vives.be",
                _AUTHENTIK_JWT_HEADER: token,
                _AUTHENTIK_JWKS_HEADER: jwks_url,
            }
        )
    )

    assert (identity, verified) == ("jan.peeters@vives.be", False)


def test_falls_back_unverified_when_the_jwt_kid_is_unknown_to_the_jwks(jwks_server):
    private_key = _generate_key()
    jwks_url = jwks_server([_jwk_for(private_key, _KID)])
    token = _sign(private_key, _OTHER_KID)

    identity, verified = get_verified_identity(
        _request(
            {
                _AUTHENTIK_EMAIL_HEADER: "jan.peeters@vives.be",
                _AUTHENTIK_JWT_HEADER: token,
                _AUTHENTIK_JWKS_HEADER: jwks_url,
            }
        )
    )

    assert (identity, verified) == ("jan.peeters@vives.be", False)


def test_falls_back_unverified_when_the_jwt_email_claim_does_not_match_the_header(jwks_server):
    """A validly-signed JWT for a *different* identity must not be accepted
    as proof of the `X-authentik-email` header's identity — otherwise a
    compromised/misconfigured hop could pair any legitimately-signed token
    with a spoofed email header and have it recorded as verified."""
    private_key = _generate_key()
    jwks_url = jwks_server([_jwk_for(private_key, _KID)])
    token = _sign(private_key, _KID, email="someone-else@vives.be")

    identity, verified = get_verified_identity(
        _request(
            {
                _AUTHENTIK_EMAIL_HEADER: "jan.peeters@vives.be",
                _AUTHENTIK_JWT_HEADER: token,
                _AUTHENTIK_JWKS_HEADER: jwks_url,
            }
        )
    )

    assert (identity, verified) == ("jan.peeters@vives.be", False)


def test_falls_back_unverified_when_the_jwks_endpoint_is_unreachable():
    private_key = _generate_key()
    token = _sign(private_key, _KID)
    # Nothing listens on this port — a real, fast connection failure rather
    # than a slow timeout.
    unreachable_jwks_url = "http://127.0.0.1:1/jwks.json"

    identity, verified = get_verified_identity(
        _request(
            {
                _AUTHENTIK_EMAIL_HEADER: "jan.peeters@vives.be",
                _AUTHENTIK_JWT_HEADER: token,
                _AUTHENTIK_JWKS_HEADER: unreachable_jwks_url,
            }
        )
    )

    assert (identity, verified) == ("jan.peeters@vives.be", False)


def test_trims_and_bounds_the_identity_value_the_same_way_as_get_identity():
    identity, verified = get_verified_identity(_request({_AUTHENTIK_EMAIL_HEADER: "  x" * 200}))

    assert verified is False
    assert identity is not None
    assert len(identity) == 320


def test_blank_email_header_is_treated_as_absent():
    identity, verified = get_verified_identity(_request({_AUTHENTIK_EMAIL_HEADER: "   "}))

    assert (identity, verified) == (None, False)
