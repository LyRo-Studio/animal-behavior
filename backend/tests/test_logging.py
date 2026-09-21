import logging

from app.core.logging import RedactMediaTokenFilter

# ADR 0002: the media-streaming endpoint's token query parameter must never
# sit in plaintext in uvicorn's access log — see app/core/logging.py.


def _access_log_record(full_path: str) -> logging.LogRecord:
    # Mirrors uvicorn's own access-log record shape: args = (client_addr,
    # method, full_path, http_version, status_code).
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", full_path, "1.1", 200),
        exc_info=None,
    )


def test_redacts_the_token_query_parameter():
    record = _access_log_record(
        "/api/media/stream?key=cuts/T001/a.mp4&action=play&token=secret.jwt.value"
    )

    RedactMediaTokenFilter().filter(record)

    full_path = record.args[2]
    assert "secret.jwt.value" not in full_path
    assert "token=[redacted]" in full_path
    assert "key=cuts/T001/a.mp4" in full_path
    assert "action=play" in full_path


def test_leaves_a_request_with_no_token_param_unchanged():
    record = _access_log_record("/api/media/tests")

    RedactMediaTokenFilter().filter(record)

    assert record.args[2] == "/api/media/tests"


def test_filter_always_returns_true_so_the_record_is_still_logged():
    record = _access_log_record("/api/media/stream?token=secret")

    assert RedactMediaTokenFilter().filter(record) is True
