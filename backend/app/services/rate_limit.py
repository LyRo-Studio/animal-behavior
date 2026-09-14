"""In-memory request-rate limiting for the unauthenticated auth endpoints
(ticket #7 — see ENGINEERING-STANDARDS.md's Brute-Force and
Denial-of-Service sections).

Fixed-window counters keyed by however each endpoint chooses to identify a
caller — IP and/or the email in the request body; see app/api/auth.py for
the actual key choices and the reasoning behind them per endpoint.

In-memory, per-process state: acceptable at this app's scale (a single
backend instance — see docker-compose.yml's `backend` service, which runs
no replicas) and far simpler than adding Redis or another shared store for
one small internal tool. Revisit if the app is ever deployed with multiple
backend replicas, where each process would otherwise enforce its own
independent limit.
"""

import threading
import time
from dataclasses import dataclass, field

# Every this-many `hit`/`peek` calls, sweep out windows that have fully
# expired. Bounds _windows' memory to roughly one window's worth of
# distinct keys even against a flood of always-unique keys (e.g. attacker-
# chosen emails on /auth/forgot-password) — without this, that dict would
# grow forever, turning the throttle itself into a memory-exhaustion DoS
# vector.
_PRUNE_EVERY_N_CALLS = 256


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: float


@dataclass
class RateLimiter:
    """A simple fixed-window rate limiter, keyed by an arbitrary string.

    Thread-safe: FastAPI runs synchronous `def` endpoint functions (as
    every endpoint using this does) in a real OS-thread threadpool, so
    concurrent requests genuinely execute `hit`/`peek` at the same time,
    not just cooperatively-interleaved coroutines. All state access below
    is guarded by `_lock`.
    """

    _windows: dict[str, tuple[float, int, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _calls_since_prune: int = 0

    def peek(self, key: str, *, limit: int, window_seconds: float) -> RateLimitResult:
        """A dry run of `hit`: report the verdict recording one more
        attempt for `key` *would* get, without actually recording it.

        Used to reject an already-exhausted caller before paying for an
        expensive operation (e.g. password verification) whose outcome
        isn't known yet and that it would fail anyway. Simulating the
        post-increment count (rather than checking the current one) keeps
        this in exact lockstep with `hit` — it's the attempt that *would*
        push the count past `limit` that gets rejected here, the same one
        `hit` would reject.
        """
        with self._lock:
            now = time.monotonic()
            self._prune_if_due(now)
            window_start, count, _ = self._windows.get(key, (now, 0, window_seconds))
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            return self._result(count + 1, limit, window_start, window_seconds, now)

    def hit(self, key: str, *, limit: int, window_seconds: float) -> RateLimitResult:
        """Record one attempt for `key` and report whether it's still
        within `limit` attempts inside the current `window_seconds`-long
        window.

        The attempt is recorded even when it's the one that exceeds the
        limit, so a client that keeps retrying while throttled can't
        "use up" the reset by continuing to hammer the endpoint.
        """
        with self._lock:
            now = time.monotonic()
            self._prune_if_due(now)
            window_start, count, _ = self._windows.get(key, (now, 0, window_seconds))

            if now - window_start >= window_seconds:
                window_start, count = now, 0

            count += 1
            self._windows[key] = (window_start, count, window_seconds)

            return self._result(count, limit, window_start, window_seconds, now)

    @staticmethod
    def _result(
        count: int, limit: int, window_start: float, window_seconds: float, now: float
    ) -> RateLimitResult:
        retry_after = max(0.0, window_seconds - (now - window_start))
        return RateLimitResult(allowed=count <= limit, retry_after_seconds=retry_after)

    def _prune_if_due(self, now: float) -> None:
        """Must be called with `_lock` held. Sweeps out fully-expired
        entries every `_PRUNE_EVERY_N_CALLS` calls (amortizing the O(n)
        scan) rather than on every single call."""
        self._calls_since_prune += 1
        if self._calls_since_prune < _PRUNE_EVERY_N_CALLS:
            return
        self._calls_since_prune = 0

        expired = [
            key
            for key, (window_start, _count, window_seconds) in self._windows.items()
            if now - window_start >= window_seconds
        ]
        for key in expired:
            del self._windows[key]


def enforce_all(*results: RateLimitResult) -> RateLimitResult | None:
    """Return the most-restrictive disallowed result among `results`, or
    None if all were allowed. A caller checking/recording multiple
    dimensions at once (e.g. login's per-IP *and* per-email budgets) uses
    this to decide whether — and for how long — to reject the request.
    """
    exceeded = [result for result in results if not result.allowed]
    if not exceeded:
        return None
    return max(exceeded, key=lambda result: result.retry_after_seconds)


# A process-lifetime singleton: the limiter's state has to persist across
# requests to mean anything, unlike e.g. get_mail_transport's fresh
# transport per call. Tests override get_rate_limiter with a fresh
# RateLimiter() per test (see tests/conftest.py) so attempt counts never
# leak between them.
_shared_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _shared_limiter
