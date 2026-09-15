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
from collections import deque
from dataclasses import dataclass, field


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
    # (expire_at, key) in expiry order, one entry pushed each time `hit`
    # starts a fresh window for a key (see _current_window's `record`).
    # Never pushed to by `peek`, which never actually stores anything for
    # `_prune_expired` to need to evict. A key can be renewed (get a new,
    # further-out expiry) while a stale entry for its previous window is
    # still queued here; _prune_expired below treats that as fine — see
    # its docstring.
    _expiry_queue: deque[tuple[float, str]] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

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
            self._prune_expired(now)
            window_start, count, _ = self._current_window(key, now, window_seconds, record=False)
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
            self._prune_expired(now)
            window_start, count, _ = self._current_window(key, now, window_seconds, record=True)

            count += 1
            self._windows[key] = (window_start, count, window_seconds)

            return self._result(count, limit, window_start, window_seconds, now)

    def _current_window(
        self, key: str, now: float, window_seconds: float, *, record: bool
    ) -> tuple[float, int, float]:
        """The (window_start, count, window_seconds) `key` is currently in.

        Read-only with respect to `_windows` itself either way: `hit` is
        responsible for actually storing the returned window back into it
        after incrementing `count`; `peek` just reads the verdict without
        storing anything.

        `record` controls `_expiry_queue`, not `_windows`: pass `True`
        (from `hit`) when starting a fresh window that a later `hit` call
        on this same key *will* actually store, so `_prune_expired` has an
        entry to evict it by; pass `False` (from `peek`) when just
        computing what the window would be, since nothing gets stored for
        `_prune_expired` to ever need to evict.
        """
        existing = self._windows.get(key)
        if existing is not None and now - existing[0] < existing[2]:
            return existing
        if record:
            self._expiry_queue.append((now + window_seconds, key))
        return now, 0, window_seconds

    @staticmethod
    def _result(
        count: int, limit: int, window_start: float, window_seconds: float, now: float
    ) -> RateLimitResult:
        retry_after = max(0.0, window_seconds - (now - window_start))
        return RateLimitResult(allowed=count <= limit, retry_after_seconds=retry_after)

    def _prune_expired(self, now: float) -> None:
        """Must be called with `_lock` held. Evicts every `_windows` entry
        whose window has fully elapsed, in O(1) amortized time per `hit`/
        `peek` call rather than a periodic full-map scan — the latter's
        cumulative cost is quadratic in a sustained always-unique-key flood
        (e.g. attacker-chosen emails on /auth/forgot-password), since scan
        size grows with the flood while barely anything is yet expired to
        free, and it holds `_lock` for the whole scan, blocking every other
        rate-limit check meanwhile.

        `_expiry_queue` holds one entry per window a key has started, in
        expiry order, so popping from the front while due is always
        correct *or a safe no-op*: if `key`'s window was since renewed
        (`_current_window` started a fresh, further-out one), the queued
        expiry here is stale and `window_start + window_seconds` in
        `_windows` no longer matches it — that's detected below and the
        entry is discarded without touching `_windows`, since a fresh
        queue entry for the renewed window already exists further back.
        """
        while self._expiry_queue and self._expiry_queue[0][0] <= now:
            expire_at, key = self._expiry_queue.popleft()
            entry = self._windows.get(key)
            if entry is None:
                continue
            window_start, _count, window_seconds = entry
            if window_start + window_seconds <= expire_at:
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
