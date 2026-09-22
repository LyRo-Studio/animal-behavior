"""The audit log's single write seam (ticket #82, issue #79) — importable
by both the backend and the worker, same boundary as `analyses.py`/
`s3_client.py` (no FastAPI dependency). Every call site across every
feature (analysis, and eventually cutting) writes through `record_audit_event`
rather than inserting `AuditLog` rows directly.
"""

import logging

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
    except Exception:
        logger.exception("Failed to record audit event %s for %s:%s", action, target_type, target)
