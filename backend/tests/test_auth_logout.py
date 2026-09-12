from tests.helpers import DEFAULT_PASSWORD, create_account


def test_logout_revokes_the_refresh_token(client, db_session):
    create_account(db_session, email="jan.peeters@vives.be")
    login_response = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
    )
    refresh_token = login_response.json()["refresh_token"]

    logout_response = client.post("/auth/logout", json={"refresh_token": refresh_token})
    assert logout_response.status_code == 204

    refresh_after_logout = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_after_logout.status_code == 401


def test_logout_with_an_unknown_token_is_a_silent_no_op(client, db_session):
    response = client.post("/auth/logout", json={"refresh_token": "not-a-real-token"})

    assert response.status_code == 204
