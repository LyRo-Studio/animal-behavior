from app.models.account import AccountRole
from tests.helpers import DEFAULT_PASSWORD, create_account


def test_me_returns_current_account_details(client, db_session):
    create_account(
        db_session,
        email="jan.peeters@vives.be",
        display_name="Jan",
        role=AccountRole.USER,
    )
    login_response = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
    )
    access_token = login_response.json()["access_token"]

    response = client.get("/accounts/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "jan.peeters@vives.be"
    assert body["display_name"] == "Jan"
    assert body["role"] == "user"
    assert "id" in body
    assert "password_hash" not in body


def test_me_without_a_token_is_rejected(client, db_session):
    response = client.get("/accounts/me")

    assert response.status_code == 401


def test_me_with_a_garbage_token_is_rejected(client, db_session):
    response = client.get("/accounts/me", headers={"Authorization": "Bearer not-a-real-jwt"})

    assert response.status_code == 401


def test_me_for_a_deactivated_account_is_rejected(client, db_session):
    account = create_account(db_session, email="jan.peeters@vives.be")
    login_response = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
    )
    access_token = login_response.json()["access_token"]

    account.is_active = False
    db_session.add(account)
    db_session.commit()

    response = client.get("/accounts/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 401
