import re
from datetime import UTC, datetime, timedelta

from app.core.security import hash_opaque_token
from app.main import app
from app.models.account_action_token import AccountActionToken, AccountActionTokenPurpose
from app.services.mail import get_mail_transport
from tests.fakes import FailingMailTransport
from tests.helpers import create_account


def _request_reset(client, email="jan.peeters@vives.be"):
    return client.post("/auth/forgot-password", json={"email": email})


def _extract_token(mail_transport) -> str:
    message = mail_transport.sent[-1]
    return re.search(r"token=([^\s&]+)", message.text_body).group(1)


def test_forgot_password_for_an_active_account_sends_a_reset_email(
    client, db_session, mail_transport
):
    create_account(db_session, email="jan.peeters@vives.be")

    response = _request_reset(client)

    assert response.status_code == 204
    assert response.content == b""
    assert len(mail_transport.sent) == 1
    message = mail_transport.sent[0]
    assert message.to == "jan.peeters@vives.be"
    assert "/set-password?token=" in message.text_body


def test_forgot_password_for_an_unknown_email_gives_the_same_generic_response(
    client, db_session, mail_transport
):
    response = _request_reset(client, email="nobody@vives.be")

    assert response.status_code == 204
    assert response.content == b""
    assert mail_transport.sent == []


def test_forgot_password_for_a_deactivated_account_gives_the_same_generic_response(
    client, db_session, mail_transport
):
    create_account(db_session, email="left@vives.be", is_active=False)

    response = _request_reset(client, email="left@vives.be")

    assert response.status_code == 204
    assert mail_transport.sent == []


def test_forgot_password_gives_the_same_response_even_when_sending_the_email_fails(
    client, db_session
):
    """Regression test: a mail-transport failure (e.g. SMTP down) must not
    turn into a 500 for a real account while an unknown email still gets
    204 — that status-code split would itself reveal whether the account
    exists, defeating the whole point of the generic response."""
    create_account(db_session, email="jan.peeters@vives.be")
    app.dependency_overrides[get_mail_transport] = lambda: FailingMailTransport()

    response = _request_reset(client)

    assert response.status_code == 204
    assert response.content == b""


def test_forgot_password_issues_a_password_reset_purpose_token(client, db_session, mail_transport):
    create_account(db_session, email="jan.peeters@vives.be")

    _request_reset(client)

    token_row = db_session.query(AccountActionToken).one()
    assert token_row.purpose == AccountActionTokenPurpose.PASSWORD_RESET


def test_full_reset_flow_sets_a_new_password_and_logs_in_with_it(
    client, db_session, mail_transport
):
    create_account(db_session, email="jan.peeters@vives.be")

    _request_reset(client)
    token = _extract_token(mail_transport)

    set_password_response = client.post(
        "/accounts/set-password", json={"token": token, "password": "a-new-strong-password"}
    )
    assert set_password_response.status_code == 204

    login = client.post(
        "/auth/login",
        json={"email": "jan.peeters@vives.be", "password": "a-new-strong-password"},
    )
    assert login.status_code == 200


def test_completing_a_reset_revokes_other_outstanding_sessions(client, db_session, mail_transport):
    account = create_account(db_session, email="jan.peeters@vives.be")
    original_login = client.post(
        "/auth/login",
        json={"email": "jan.peeters@vives.be", "password": "correct-horse-battery-staple"},
    )
    assert original_login.status_code == 200
    original_refresh_token = original_login.json()["refresh_token"]

    _request_reset(client)
    token = _extract_token(mail_transport)
    client.post(
        "/accounts/set-password", json={"token": token, "password": "a-new-strong-password"}
    )

    refresh_response = client.post("/auth/refresh", json={"refresh_token": original_refresh_token})

    assert refresh_response.status_code == 401
    # Sanity: the account referenced above is the one whose session got revoked.
    assert account.email == "jan.peeters@vives.be"


def test_reusing_a_reset_token_is_rejected_without_revealing_the_account(client, db_session):
    account = create_account(db_session, email="jan.peeters@vives.be", password=None)
    raw_token = "a-known-reset-token"
    db_session.add(
        AccountActionToken(
            account_id=account.id,
            token_hash=hash_opaque_token(raw_token),
            purpose=AccountActionTokenPurpose.PASSWORD_RESET,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    first = client.post(
        "/accounts/set-password", json={"token": raw_token, "password": "a-new-strong-password"}
    )
    assert first.status_code == 204

    second = client.post(
        "/accounts/set-password", json={"token": raw_token, "password": "another-password-1"}
    )

    assert second.status_code == 400


def test_an_expired_reset_token_is_rejected(client, db_session):
    account = create_account(db_session, email="jan.peeters@vives.be", password=None)
    raw_token = "an-expired-reset-token"
    db_session.add(
        AccountActionToken(
            account_id=account.id,
            token_hash=hash_opaque_token(raw_token),
            purpose=AccountActionTokenPurpose.PASSWORD_RESET,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db_session.commit()

    response = client.post(
        "/accounts/set-password", json={"token": raw_token, "password": "a-new-password"}
    )

    assert response.status_code == 400
