import threading

from app.services.rate_limit import RateLimiter

# Ticket #72 removed the login/refresh/forgot-password limits along with the
# endpoints they guarded; what's left of the limiter is the media-token and
# cut-info limits, exercised through their own endpoints in
# test_media_playback.py / test_cut_media_info.py. This file keeps the one
# guarantee that only shows up under real concurrency.


def test_concurrent_hits_never_exceed_the_configured_limit():
    """Regression: RateLimiter must be safe under real concurrent access —
    FastAPI runs sync `def` endpoints (every one that uses this) in a
    threadpool, so concurrent requests execute on genuine OS threads, not
    just cooperatively-interleaved coroutines. An unguarded read-modify-write
    would let concurrent requests under-count and slip more than `limit`
    attempts through.

    Each worker thread's own exceptions never surface on the main test
    thread — they'd otherwise just print to stderr and leave that thread's
    result missing, which could let the final assertion pass "successfully"
    without having genuinely exercised 20 concurrent hits. So every thread's
    outcome, exception included, is captured and checked before the actual
    invariant is.
    """
    limit = 5
    limiter = RateLimiter()
    allowed: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    def attempt() -> None:
        try:
            start.wait()
            result = limiter.hit("key", limit=limit, window_seconds=60)
        except Exception as exc:
            with lock:
                errors.append(exc)
            return
        with lock:
            allowed.append(result.allowed)

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(allowed) == 20
    assert sum(allowed) == limit
