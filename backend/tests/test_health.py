def test_health_reports_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_routes_are_only_served_under_the_api_prefix(client):
    # Ticket #71: nginx sends `/` to the frontend and `/api/` to the backend
    # with no path rewriting, so an unprefixed backend route would be
    # unreachable in production and could only ever mask a wiring mistake.
    assert client.get("/health").status_code == 404
    assert client.get("/whoami").status_code == 404


def test_interactive_docs_live_under_the_api_prefix(client):
    # `/docs` on the public origin belongs to the frontend's SPA fallback.
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/docs").status_code == 404


def test_a_trailing_slash_is_a_404_not_an_absolute_http_redirect(client):
    # Behind the TLS-terminating proxy a Starlette slash-redirect would point
    # at `http://<host>/...` and be blocked as mixed content in the browser.
    response = client.get("/api/health/", follow_redirects=False)

    assert response.status_code == 404
