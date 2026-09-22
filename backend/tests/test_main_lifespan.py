"""Direct tests of `app.main.lifespan`'s prune-task wiring (ticket #87).

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
