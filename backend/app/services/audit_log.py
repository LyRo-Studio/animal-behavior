"""The audit log's single write seam (ticket #82, issue #79) — importable
by both the backend and the worker, same boundary as `analyses.py`/
`s3_client.py` (no FastAPI dependency). Every call site across every
feature (analysis, and eventually cutting) writes through `record_audit_event`
rather than inserting `AuditLog` rows directly.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog

logger = logging.getLogger(__name__)


def record_audit_event(
    db: Session,
    *,
    identity: str | None,
    identity_verified: bool,
    action: AuditAction,
    target_type: str,
    target: str,
    failure_reason: str | None = None,
) -> None:
    """Write one `audit_log` row, best-effort.

    Writes inside its own SAVEPOINT (`db.begin_nested()`) so a failure here
    can never poison the caller's own in-flight transaction. Any failure —
    a constraint violation, a DB error — is caught and logged, never
    raised: the real action being audited (starting an analysis,
    downloading a report, ...) must never fail because this write did
    (issue #79 user story 9). A caller must still `db.commit()` its own
    transaction afterward for this row to actually persist, exactly like
    any other write via `db` — this function only isolates *failure*, it
    doesn't commit on success.
    """
    try:
        with db.begin_nested():
            record_required_audit_event(
                db,
                identity=identity,
                identity_verified=identity_verified,
                action=action,
                target_type=target_type,
                target=target,
                failure_reason=failure_reason,
            )
    except Exception:
        logger.exception("Failed to record audit event %s for %s:%s", action, target_type, target)


def record_required_audit_event(
    db: Session,
    *,
    identity: str | None,
    identity_verified: bool,
    action: AuditAction,
    target_type: str,
    target: str,
    failure_reason: str | None = None,
) -> None:
    """Write one `audit_log` row, and raise if it can't be written.

    The strict form of `record_audit_event`, for an action that must not go
    ahead unrecorded: a consolidation's hard delete (ticket #118, "a
    failure partway through doesn't lose the record") is the only caller.
    Flushes, so a failure surfaces here rather than at the caller's commit.
    Like `record_audit_event`, it never commits.
    """
    db.add(
        AuditLog(
            identity=identity,
            identity_verified=identity_verified,
            action=action,
            target_type=target_type,
            target=target,
            failure_reason=failure_reason,
        )
    )
    db.flush()


def prune_old_audit_events(db: Session, *, retention_days: int) -> int:
    """Delete every `audit_log` row whose `occurred_at` is older than a
    rolling `retention_days` window (ticket #87, issue #79's "Retention: 3
    months, rolling 90 days from occurred_at" decision) — deletion under a
    stated policy, not the kind of edit/rewrite the table's own immutability
    guarantee rules out (CONTEXT.md's Audit log Language entry).

    Commits on success and returns the number of rows deleted, for the
    caller to log. A plain bulk DELETE with no matching rows is a no-op —
    never raises against an empty (or already-pruned) table.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = db.execute(delete(AuditLog).where(AuditLog.occurred_at < cutoff))
    db.commit()
    return result.rowcount
