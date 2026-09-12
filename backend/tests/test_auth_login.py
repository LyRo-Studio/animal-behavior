from app.models.account import AccountRole
from tests.helpers import DEFAULT_PASSWORD, create_account


def test_login_with_correct_credentials_returns_token_pair(client, db_session):
    create_account(db_session, email="jan.peeters@vives.be")

    response = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_login_is_case_insensitive_on_email(client, db_session):
    create_account(db_session, email="jan.peeters@vives.be")

    response = client.post(
        "/auth/login", json={"email": "Jan.Peeters@VIVES.be", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200


def test_login_with_wrong_password_is_rejected(client, db_session):
    create_account(db_session, email="jan.peeters@vives.be")

    response = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_login_with_unknown_email_is_rejected(client, db_session):
    response = client.post(
        "/auth/login", json={"email": "nobody@vives.be", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_login_with_deactivated_account_is_rejected(client, db_session):
    create_account(db_session, email="jan.peeters@vives.be", is_active=False)

    response = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_login_failure_response_is_identical_across_failure_reasons(client, db_session):
    """Never distinguish wrong-password / unknown-email / deactivated-account."""
    create_account(db_session, email="wrong.password@vives.be")
    create_account(db_session, email="deactivated@vives.be", is_active=False)

    wrong_password = client.post(
        "/auth/login", json={"email": "wrong.password@vives.be", "password": "nope"}
    )
    unknown_email = client.post(
        "/auth/login", json={"email": "unknown@vives.be", "password": DEFAULT_PASSWORD}
    )
    deactivated = client.post(
        "/auth/login", json={"email": "deactivated@vives.be", "password": DEFAULT_PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == deactivated.status_code == 401
    assert wrong_password.json() == unknown_email.json() == deactivated.json()


def test_login_issues_a_role_bearing_access_token_usable_against_accounts_me(client, db_session):
    create_account(db_session, email="admin.person@vives.be", role=AccountRole.ADMIN)

    login_response = client.post(
        "/auth/login", json={"email": "admin.person@vives.be", "password": DEFAULT_PASSWORD}
    )
    access_token = login_response.json()["access_token"]

    me_response = client.get("/accounts/me", headers={"Authorization": f"Bearer {access_token}"})

    assert me_response.status_code == 200
    assert me_response.json()["role"] == "admin"
