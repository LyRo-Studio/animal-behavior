from dataclasses import dataclass, field

from app.services.mail import MailMessage


@dataclass
class FakeMailTransport:
    """Records sent messages instead of delivering them.

    The injected fake mail transport tests use in place of real SMTP, per
    issue #1's testing decisions ("Email-sending is exercised through this
    same [HTTP API] seam via an injected fake mail transport... rather than
    hitting real SMTP").
    """

    sent: list[MailMessage] = field(default_factory=list)

    def send(self, message: MailMessage) -> None:
        self.sent.append(message)


@dataclass
class FailingMailTransport:
    """Raises on every send — simulates an SMTP outage for tests that
    assert a mail-transport failure never changes an endpoint's response
    (see test_forgot_password.py's account-enumeration regression test)."""

    def send(self, message: MailMessage) -> None:
        raise RuntimeError("simulated SMTP failure")
