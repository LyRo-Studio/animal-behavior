"""Access-log redaction for the media-streaming endpoint (ticket #21).

ADR 0002 (docs/adr/0002-media-access-tokens-in-url.md) requires the media
token — a bearer credential carried in the URL as a deliberate, narrow
exception to the app's normal auth rules — never sit in plaintext in
ordinary access/proxy logs. uvicorn's default access logger writes the
full request line (path + query string) for every request, so without
this filter every play/download would log its token verbatim.
"""

import logging
import re

_TOKEN_QUERY_RE = re.compile(r"(token=)[^&\s]+")


class RedactMediaTokenFilter(logging.Filter):
    """Redacts the `token` query parameter from uvicorn access-log records.

    uvicorn formats each access-log line with `record.args = (client_addr,
    method, full_path, http_version, status_code)` — `full_path` is the
    only arg that can ever carry a query string, so scanning every string
    arg (rather than hardcoding an index) redacts it regardless of exactly
    which position it lands in.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple):
            return True
        record.args = tuple(
            _TOKEN_QUERY_RE.sub(r"\1[redacted]", arg) if isinstance(arg, str) else arg
            for arg in record.args
        )
        return True


def configure_access_log_redaction() -> None:
    """Attach `RedactMediaTokenFilter` to uvicorn's access logger.

    Safe to call regardless of whether uvicorn has already attached its
    own handlers to this logger — `logging.getLogger` returns the same
    singleton Logger object by name either way, and a Filter added to it
    applies to every subsequent record regardless of when handlers were
    (or will be) attached.
    """
    logging.getLogger("uvicorn.access").addFilter(RedactMediaTokenFilter())
