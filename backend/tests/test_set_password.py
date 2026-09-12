import re
from datetime import UTC, datetime, timedelta

from app.core.security import hash_opaque_token
from app.models.account import AccountRole
from app.models.account_action_token import AccountActionToken, AccountActionTokenPurpose
from tests.helpers import create_account, login_headers


def _invite(client, db_session, mail_transport, email="jan.peeters@vives.be"):
    admin = create_account(db_session, email="admin.person@vives.be", role=AccountRole.ADMIN)
    headers = login_headers(client, admin.email)
    response = client.post("/admin/accounts", json={"email": email}, headers=headers)
    assert response.status_code == 201

    message = mail_transport.sent[-1]
    token = re.search(r"token=([^\s&]+)", message.text_body).group(1)
    return token


def test_set_password_with_a_valid_invite_token_allows_login(client, db_session, mail_transport):
    token = _invite(client, db_session, mail_transport)

    response = client.post(
        "/accounts/set-password", json={"token": token, "password": "a-new-strong-password"}
    )
    assert response.status_code == 204

    login = client.post(
        "/auth/login",
        json={"email": "jan.peeters@vives.be", "password": "a-new-strong-password"},
    )
    assert login.status_code == 200


def test_set_password_with_an_already_used_token_is_rejected(client, db_session, mail_transport):
    token = _invite(client, db_session, mail_transport)
    first = client.post(
        "/accounts/set-password", json={"token": token, "password": "a-new-strong-password"}
    )
    assert first.status_code == 204

    second = client.post(
        "/accounts/set-password", json={"token": token, "password": "another-password-1"}
    )

    assert second.status_code == 400


def test_set_password_with_an_unknown_token_is_rejected(client, db_session):
    response = client.post(
        "/accounts/set-password", json={"token": "not-a-real-token", "password": "a-new-password"}
    )

    assert response.status_code == 400


def test_set_password_with_an_expired_token_is_rejected(client, db_session):
    account = create_account(db_session, email="jan.peeters@vives.be", password=None)
    # Store the hash of a *known* raw token (not an arbitrary string) so
    # this actually exercises the expiry check via a successful lookup —
    # not the "unknown token" branch, which a mismatched hash would hit
    # instead, silently passing for the wrong reason.
    raw_token = "an-expired-raw-token"
    db_session.add(
        AccountActionToken(
            account_id=account.id,
            token_hash=hash_opaque_token(raw_token),
            purpose=AccountActionTokenPurpose.INVITE,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db_session.commit()

    response = client.post(
        "/accounts/set-password", json={"token": raw_token, "password": "a-new-password"}
    )

    assert response.status_code == 400


def test_set_password_rejects_a_too_short_password(client, db_session, mail_transport):
    token = _invite(client, db_session, mail_transport)

    response = client.post("/accounts/set-password", json={"token": token, "password": "short"})

    assert response.status_code == 422
