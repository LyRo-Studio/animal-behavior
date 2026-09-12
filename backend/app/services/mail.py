"""Outbound email, behind a small injectable transport.

Per issue #1's testing decisions, tests exercise email-sending through the
real service functions with a fake transport injected (see
`app/api/deps.py`'s `get_mail_transport` and `tests/fakes.py`'s
`FakeMailTransport`) — never by hitting real SMTP or mocking a service
function directly.
"""

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MailMessage:
    to: str
    subject: str
    text_body: str


class MailTransport(Protocol):
    def send(self, message: MailMessage) -> None: ...


class SmtpMailTransport:
    """Sends mail over real SMTP, configured via .env (see .env.example)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        from_email: str,
        use_tls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username.strip() if username else username
        # Gmail displays an App Password grouped into 4-character chunks
        # with spaces (e.g. "abcd efgh ijkl mnop") for readability — a
        # value pasted verbatim into .env otherwise fails SMTP AUTH.
        # Stripping all whitespace is harmless for any other provider's
        # password too, since a real password isn't expected to contain
        # spaces.
        self._password = "".join(password.split()) if password else password
        self._from_email = from_email
        self._use_tls = use_tls

    def send(self, message: MailMessage) -> None:
        email = EmailMessage()
        email["Subject"] = message.subject
        email["From"] = self._from_email
        email["To"] = message.to
        email.set_content(message.text_body)

        with smtplib.SMTP(self._host, self._port) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(email)


class LoggingMailTransport:
    """Dev-only fallback used when no SMTP host is configured.

    Logs instead of sending, so `docker compose up` and standalone
    `pytest`/`ruff` work with no mail server present — same "safe
    placeholder" spirit as the other dev-only defaults in
    app/core/config.py. Never used once SMTP_HOST is set (see
    `get_mail_transport`).

    Deliberately logs only metadata, never `message.text_body`: an invite
    (or, from ticket #5, password-reset) email's body embeds the raw
    action token — a bearer secret authorizing a password change — and
    ENGINEERING-STANDARDS.md's Password Security section is unconditional
    that "tokens[...] must never be written to application logs." Use a
    real (even a local/dev) SMTP server via .env to see actual invite
    links, or the FakeMailTransport tests use to inspect sent messages.
    """

    def send(self, message: MailMessage) -> None:
        logger.info(
            "Mail transport not configured — logging instead of sending '%s' to %s",
            message.subject,
            message.to,
        )


def get_mail_transport() -> MailTransport:
    if not settings.smtp_host:
        return LoggingMailTransport()

    return SmtpMailTransport(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        from_email=settings.smtp_from_email,
        use_tls=settings.smtp_use_tls,
    )
