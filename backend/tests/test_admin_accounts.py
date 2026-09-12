from app.models.account import AccountRole
from tests.helpers import create_account, login_headers


def _admin_headers(client, db_session):
    create_account(db_session, email="admin.person@vives.be", role=AccountRole.ADMIN)
    return login_headers(client, "admin.person@vives.be")


def test_non_admin_is_rejected_from_creating_an_account(client, db_session):
    create_account(db_session, email="jan.peeters@vives.be", role=AccountRole.USER)
    headers = login_headers(client, "jan.peeters@vives.be")

    response = client.post(
        "/admin/accounts", json={"email": "new.student@vives.be"}, headers=headers
    )

    assert response.status_code == 403


def test_non_admin_is_rejected_from_listing_accounts(client, db_session):
    create_account(db_session, email="jan.peeters@vives.be", role=AccountRole.USER)
    headers = login_headers(client, "jan.peeters@vives.be")

    response = client.get("/admin/accounts", headers=headers)

    assert response.status_code == 403


def test_unauthenticated_is_rejected_from_admin_endpoints(client, db_session):
    assert client.get("/admin/accounts").status_code == 401
    assert client.post("/admin/accounts", json={"email": "x@vives.be"}).status_code == 401


def test_admin_creates_an_account_and_it_receives_an_invite_email(
    client, db_session, mail_transport
):
    headers = _admin_headers(client, db_session)

    response = client.post(
        "/admin/accounts", json={"email": "Jan.Peeters@Student.VIVES.be"}, headers=headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "jan.peeters@student.vives.be"
    assert body["display_name"] == "Jan"
    assert body["is_active"] is True
    assert "password_hash" not in body

    assert len(mail_transport.sent) == 1
    message = mail_transport.sent[0]
    assert message.to == "jan.peeters@student.vives.be"
    assert "/set-password?token=" in message.text_body


def test_admin_create_rejects_a_disallowed_email_domain(client, db_session, mail_transport):
    headers = _admin_headers(client, db_session)

    response = client.post(
        "/admin/accounts", json={"email": "someone@example.com"}, headers=headers
    )

    assert response.status_code == 422
    assert mail_transport.sent == []


def test_admin_create_rejects_a_duplicate_active_email(client, db_session, mail_transport):
    headers = _admin_headers(client, db_session)
    create_account(db_session, email="existing@vives.be")

    response = client.post("/admin/accounts", json={"email": "existing@vives.be"}, headers=headers)

    assert response.status_code == 409
    assert mail_transport.sent == []


def test_admin_create_allows_reusing_a_deactivated_accounts_email(
    client, db_session, mail_transport
):
    headers = _admin_headers(client, db_session)
    create_account(db_session, email="rehired@vives.be", is_active=False)

    response = client.post("/admin/accounts", json={"email": "rehired@vives.be"}, headers=headers)

    assert response.status_code == 201


def test_admin_lists_all_accounts(client, db_session):
    headers = _admin_headers(client, db_session)
    create_account(db_session, email="jan.peeters@vives.be", display_name="Jan")
    create_account(db_session, email="inactive@vives.be", is_active=False)

    response = client.get("/admin/accounts", headers=headers)

    assert response.status_code == 200
    emails = {row["email"] for row in response.json()}
    assert {"admin.person@vives.be", "jan.peeters@vives.be", "inactive@vives.be"} <= emails
    inactive_row = next(row for row in response.json() if row["email"] == "inactive@vives.be")
    assert inactive_row["is_active"] is False
