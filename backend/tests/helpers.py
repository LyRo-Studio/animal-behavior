"""Shared test helpers.

`IDENTITY_HEADER` is what `conftest.py`'s autouse `_identity_header_configured`
fixture points `settings.identity_header_name` at for every test, standing in
for whatever header Mechatronics really forwards (unknown until `dogtrace-app`
is live behind it — ticket #72).
"""

IDENTITY_HEADER = "X-Test-Identity"


def identity_headers(identity: str) -> dict[str, str]:
    """Request headers as if Mechatronics had authenticated `identity`."""
    return {IDENTITY_HEADER: identity}
