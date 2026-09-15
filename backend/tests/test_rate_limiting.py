"""Brute-force throttling on the unauthenticated auth endpoints (ticket
#7). See app/services/rate_limit.py and app/api/auth.py.
"""

import threading

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from tests.helpers import DEFAULT_PASSWORD, create_account


def test_login_is_not_throttled_by_a_couple_of_mistyped_passwords(client, db_session):
    """AC: normal retries (a mistyped password once or twice) must never
    get a legitimate user throttled."""
    create_account(db_session, email="jan.peeters@vives.be")

    for _ in range(2):
        response = client.post(
            "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
        )
        assert response.status_code == 401

    response = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200


def test_successful_logins_never_count_toward_the_failure_limit(client, db_session, monkeypatch):
    """Only *failed* attempts consume the budget — otherwise many
    legitimate users behind one shared IP (a campus NAT, say) succeeding
    normally could lock each other out with zero attacker involvement."""
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_ip", 2)
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_email", 2)
    create_account(db_session, email="jan.peeters@vives.be")

    for _ in range(5):
        response = client.post(
            "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200


def test_login_is_throttled_after_exceeding_the_per_ip_failure_limit(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_ip", 3)
    create_account(db_session, email="jan.peeters@vives.be")

    for _ in range(3):
        response = client.post(
            "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
        )
        assert response.status_code == 401

    throttled = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
    )

    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


def test_login_throttling_is_keyed_per_ip_not_globally(client, db_session, monkeypatch):
    """A different account hitting the same (test-client) IP shares the
    per-IP limit, but not the per-email one — this exercises the IP
    dimension specifically by giving each account its own generous
    per-email budget."""
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_ip", 2)
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_email", 100)
    create_account(db_session, email="jan.peeters@vives.be")
    create_account(db_session, email="other@vives.be")

    client.post("/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"})
    client.post("/auth/login", json={"email": "other@vives.be", "password": "wrong"})

    throttled = client.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
    )

    assert throttled.status_code == 429


def test_login_per_email_limit_catches_attempts_distributed_across_ips(
    client, db_session, monkeypatch
):
    """The gap a pure per-IP limit would leave open: an attacker spreading
    attempts across several source IPs at one victim account. Each
    simulated IP stays comfortably under the per-IP limit; only the shared
    per-email budget trips."""
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_ip", 100)
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_email", 2)
    create_account(db_session, email="jan.peeters@vives.be")

    for ip in ("1.1.1.1", "2.2.2.2"):
        attacker = TestClient(app, client=(ip, 12345))
        response = attacker.post(
            "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
        )
        assert response.status_code == 401

    third_ip = TestClient(app, client=("3.3.3.3", 12345))
    throttled = third_ip.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
    )

    assert throttled.status_code == 429


def test_login_with_correct_password_bypasses_an_exhausted_per_email_budget(
    client, db_session, monkeypatch
):
    """Regression for the account-specific DoS a pre-auth peek on the
    per-email budget would allow: an attacker distributes wrong-password
    guesses for the victim's email across many IPs (each comfortably under
    the per-IP limit) until the shared per-email budget is exhausted. The
    victim's own *correct* login must still succeed — the per-email budget
    is only ever consulted after a confirmed authentication failure, never
    used to reject a request before authentication runs."""
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_ip", 100)
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_email", 2)
    create_account(db_session, email="jan.peeters@vives.be")

    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        attacker = TestClient(app, client=(ip, 12345))
        attacker.post(
            "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
        )

    victim = TestClient(app, client=("9.9.9.9", 12345))
    response = victim.post(
        "/auth/login", json={"email": "jan.peeters@vives.be", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200


def test_concurrent_login_failures_never_exceed_the_configured_limit(
    client, db_session, monkeypatch
):
    """Regression: RateLimiter must be safe under real concurrent access —
    FastAPI runs sync `def` endpoints (login included) in a threadpool, so
    concurrent requests execute on genuine OS threads, not just
    cooperatively-interleaved coroutines. An unguarded read-modify-write
    would let concurrent requests under-count and slip more than `limit`
    failures through.

    Each worker thread's own exceptions never surface on the main test
    thread — they'd otherwise just print to stderr and leave that thread's
    response missing from `statuses`, which could let the final assertion
    pass "successfully" (e.g. `0 <= limit`) without having genuinely
    exercised 20 concurrent requests. So every thread's outcome, exception
    included, is captured and checked before the actual invariant is.
    """
    limit = 5
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_ip", limit)
    create_account(db_session, email="jan.peeters@vives.be")
    statuses: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            response = client.post(
                "/auth/login", json={"email": "jan.peeters@vives.be", "password": "wrong"}
            )
        except Exception as exc:
            with lock:
                errors.append(exc)
            return
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(statuses) == 20
    assert all(code in (401, 429) for code in statuses)

    not_throttled = sum(1 for code in statuses if code != 429)
    assert not_throttled <= limit


def test_refresh_is_not_throttled_by_a_couple_of_bad_tokens(client, db_session):
    for _ in range(2):
        response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
        assert response.status_code == 401


def test_refresh_is_throttled_after_exceeding_the_per_ip_failure_limit(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "refresh_rate_limit_max_failed_attempts_per_ip", 3)

    for _ in range(3):
        response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
        assert response.status_code == 401

    throttled = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})

    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


def test_forgot_password_is_not_throttled_by_a_couple_of_requests(client, db_session):
    for _ in range(2):
        response = client.post("/auth/forgot-password", json={"email": "jan.peeters@vives.be"})
        assert response.status_code == 204


def test_forgot_password_is_throttled_after_exceeding_the_per_ip_limit(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "forgot_password_rate_limit_max_attempts_per_ip", 3)

    for i in range(3):
        response = client.post("/auth/forgot-password", json={"email": f"someone{i}@vives.be"})
        assert response.status_code == 204

    throttled = client.post("/auth/forgot-password", json={"email": "another@vives.be"})

    assert throttled.status_code == 429


def test_forgot_password_is_throttled_after_exceeding_the_per_email_limit(
    client, db_session, monkeypatch
):
    """Distinct from the per-IP limit above: repeated requests for the
    *same* email are throttled well before the (much looser) per-IP limit
    would kick in. Safe to do here — unlike login — because exceeding it
    never blocks logging in, only requesting more reset emails for a while
    (see app/core/config.py)."""
    monkeypatch.setattr(settings, "forgot_password_rate_limit_max_attempts_per_ip", 100)
    monkeypatch.setattr(settings, "forgot_password_rate_limit_max_attempts_per_email", 2)
    create_account(db_session, email="jan.peeters@vives.be")

    for _ in range(2):
        response = client.post("/auth/forgot-password", json={"email": "jan.peeters@vives.be"})
        assert response.status_code == 204

    throttled = client.post("/auth/forgot-password", json={"email": "jan.peeters@vives.be"})

    assert throttled.status_code == 429


def test_forgot_password_per_email_limit_is_case_insensitive(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "forgot_password_rate_limit_max_attempts_per_ip", 100)
    monkeypatch.setattr(settings, "forgot_password_rate_limit_max_attempts_per_email", 1)

    client.post("/auth/forgot-password", json={"email": "Jan.Peeters@VIVES.be"})
    throttled = client.post("/auth/forgot-password", json={"email": "jan.peeters@vives.be"})

    assert throttled.status_code == 429


def test_rate_limits_are_scoped_independently_per_endpoint(client, db_session, monkeypatch):
    """Exhausting login's limit must not throttle forgot-password too."""
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_ip", 1)
    monkeypatch.setattr(settings, "login_rate_limit_max_failed_attempts_per_email", 1)

    client.post("/auth/login", json={"email": "x@vives.be", "password": "wrong"})
    throttled_login = client.post("/auth/login", json={"email": "x@vives.be", "password": "wrong"})
    assert throttled_login.status_code == 429

    still_ok = client.post("/auth/forgot-password", json={"email": "x@vives.be"})
    assert still_ok.status_code == 204
