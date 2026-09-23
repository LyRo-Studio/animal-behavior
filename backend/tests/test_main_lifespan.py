"""Direct tests of `app.main.lifespan`'s background-task wiring: the audit
log prune (ticket #87) and stale-consolidation reconciliation (ticket #121).

Never goes through `TestClient`/`app.db.session.SessionLocal` — `_audit_log_
pruning_disabled` (conftest.py, autouse) already keeps the real prune task
from ever running during the rest of this suite (caught in review: it has
no per-request `Depends` seam, so it would otherwise fire a real DELETE
against whichever database `DATABASE_URL` points at on every `client`-using
test). These tests exercise `lifespan` itself in isolation, with
`_prune_audit_log_once` stubbed out so nothing here touches a real Session
either, even if the task gets a chance to run before being cancelled.
"""

import asyncio

import app.main as main_module
from app.core.config import settings


def _other_tasks() -> set[asyncio.Task]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


def test_lifespan_starts_no_background_task_when_pruning_disabled(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_prune_enabled", False)

    async def _run() -> set[asyncio.Task]:
        async with main_module.lifespan(None):
            return _other_tasks()

    assert asyncio.run(_run()) == set()


def test_lifespan_starts_and_cleanly_cancels_the_prune_task_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_prune_enabled", True)
    # Defensive: even though the task can't run any of its own code before
    # the assertion below (no await point has yielded control to it yet),
    # stub the real DB call out so this test can never touch a real Session
    # if that timing assumption ever stops holding.
    monkeypatch.setattr(main_module, "_prune_audit_log_once", lambda: 0)

    async def _run() -> set[asyncio.Task]:
        async with main_module.lifespan(None):
            return _other_tasks()

    tasks = asyncio.run(_run())
    assert len(tasks) == 1
    assert "_prune_audit_log_periodically" in tasks.pop().get_coro().__qualname__


def test_lifespan_starts_and_cleanly_cancels_the_reconcile_task_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_prune_enabled", False)
    monkeypatch.setattr(settings, "consolidation_reconcile_enabled", True)
    monkeypatch.setattr(main_module, "_reconcile_stale_consolidations_once", lambda: [])

    async def _run() -> set[asyncio.Task]:
        async with main_module.lifespan(None):
            return _other_tasks()

    tasks = asyncio.run(_run())
    assert len(tasks) == 1
    assert "_reconcile_stale_consolidations_periodically" in tasks.pop().get_coro().__qualname__


def test_lifespan_reconciles_once_immediately_on_startup(monkeypatch):
    """Ticket #121: rows left `processing` by the previous backend process
    (the most common cause: a restart mid-run) are reconciled at startup,
    not only after a full interval."""
    monkeypatch.setattr(settings, "audit_log_prune_enabled", False)
    monkeypatch.setattr(settings, "consolidation_reconcile_enabled", True)
    monkeypatch.setattr(settings, "consolidation_reconcile_interval_seconds", 3600)
    calls = []
    monkeypatch.setattr(
        main_module, "_reconcile_stale_consolidations_once", lambda: calls.append(1) or []
    )

    async def _run() -> None:
        async with main_module.lifespan(None):
            for _ in range(100):
                if calls:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(_run())
    assert calls == [1]


def test_the_reconcile_loop_keeps_running_after_a_failed_run(monkeypatch):
    """Runs again every interval, and one failed run (e.g. the database is
    briefly down) is logged rather than ending the loop."""
    monkeypatch.setattr(settings, "audit_log_prune_enabled", False)
    monkeypatch.setattr(settings, "consolidation_reconcile_enabled", True)
    monkeypatch.setattr(settings, "consolidation_reconcile_interval_seconds", 0.01)
    calls = []

    def _fail_first_time() -> list[int]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated database outage")
        return []

    monkeypatch.setattr(main_module, "_reconcile_stale_consolidations_once", _fail_first_time)

    async def _run() -> None:
        async with main_module.lifespan(None):
            for _ in range(200):
                if len(calls) >= 3:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(_run())
    assert len(calls) >= 3
