from unittest.mock import MagicMock, patch

from app.services.mail import MailMessage, SmtpMailTransport

# SmtpMailTransport is deliberately never exercised through the app's own
# HTTP seam (app/api/deps.py's get_mail_transport is always overridden to
# FakeMailTransport in tests — see conftest.py) since that would mean
# hitting real SMTP. It's still worth a narrow unit test of its own
# contract, mocking only the third-party smtplib it wraps.


def test_strips_whitespace_from_username_and_password():
    """Google displays a Gmail App Password in space-separated groups for
    readability (e.g. "abcd efgh ijkl mnop"); a value pasted verbatim into
    .env must still authenticate."""
    with patch("app.services.mail.smtplib.SMTP") as smtp_class:
        smtp = MagicMock()
        smtp_class.return_value.__enter__.return_value = smtp

        transport = SmtpMailTransport(
            host="smtp.gmail.com",
            port=587,
            username="  someone@gmail.com  ",
            password="abcd efgh ijkl mnop",
            from_email="someone@gmail.com",
            use_tls=True,
        )
        transport.send(MailMessage(to="jan@vives.be", subject="Hi", text_body="Body"))

        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("someone@gmail.com", "abcdefghijklmnop")
        smtp.send_message.assert_called_once()


def test_skips_login_when_no_credentials_are_configured():
    """A local dev SMTP server (e.g. Mailhog) needs neither TLS nor auth."""
    with patch("app.services.mail.smtplib.SMTP") as smtp_class:
        smtp = MagicMock()
        smtp_class.return_value.__enter__.return_value = smtp

        transport = SmtpMailTransport(
            host="mailhog",
            port=1025,
            username=None,
            password=None,
            from_email="no-reply@vives.be",
            use_tls=False,
        )
        transport.send(MailMessage(to="jan@vives.be", subject="Hi", text_body="Body"))

        smtp.starttls.assert_not_called()
        smtp.login.assert_not_called()
        smtp.send_message.assert_called_once()
