"""Consolidation request orchestration and the adapter around
consolidation/'s domain code (ticket #115, part of issue #113's Excel
consolidation feature).

Two deliberately separate things, mirroring app/services/media_prober.py's
MediaProber Protocol / FakeMediaProber split:

- `ConsolidationRunner` / `ObserverConsolidationRunner`: the adapter that
  calls consolidation/observer_import.py's `lees_observer` ->
  `bereken_observer` -> `schrijf_resultaat` pipeline once, for the one
  result workbook holding every Consolidation level (tickets #149, #152) — issue
  #113: "Do NOT rewrite the consolidation algorithm... prefer an
  adapter/service layer". Only the hardcoded file-path arguments are
  replaced with a request-scoped temp path. Tests inject
  `FakeConsolidationRunner` (tests/fakes.py) instead of running this real,
  slower, fixture-dependent pipeline.
- `start_consolidation` / `run_consolidation`: HTTP-free orchestration —
  a `processing` row first, then temp workspace, calling the injected
  runner, uploading the result, and moving the row to its terminal status
  — independent of which runner (real or fake) it's given.

`reconcile_stale_consolidations` (ticket #121) fails rows a run never
finished, from the backend's own lifespan task (app/main.py).
"""

import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile

from consolidation.observer_import import bereken_observer, lees_observer, schrijf_resultaat
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction
from app.models.consolidation import (
    FAILURE_REASON_MAX_LENGTH,
    Consolidation,
    ConsolidationStatus,
)
from app.services.audit_log import record_required_audit_event
from app.services.s3_client import S3Client

logger = logging.getLogger(__name__)

# The audit log's `target_type` for every consolidation event — shared with
# app.api.consolidations, which audits every consolidation action except
# delete (see delete_consolidation for why that one is audited here).
AUDIT_TARGET_TYPE = "consolidation"


class ConsolidationInputError(Exception):
    """Anything wrong with the uploaded file itself — a domain `ValueError`
    from consolidation/'s own validation, a missing expected sheet, or a
    file that isn't a readable Excel workbook at all. The message is shown
    to the user near-verbatim (issue #113's error-handling decision: these
    are actionable, not generic technical noise) — kept short enough to fit
    Consolidation.failure_reason regardless (truncated defensively by the
    caller, not trusted to already be short)."""


class ConsolidationNotFoundError(Exception):
    """Raised for a nonexistent Consolidation id."""


class ConsolidationAlreadyReconciledError(Exception):
    """Raised by run_consolidation when its row was already moved to `failed`
    by reconcile_stale_consolidations (ticket #121) before the run finished
    — a run that outlived the stale threshold. That outcome, and its
    CONSOLIDATION_FAILED audit row, stand."""


class ConsolidationStillProcessingError(Exception):
    """Raised when deleting a `processing` Consolidation (ticket #118): it
    may still be running in another request, whose final update would then
    fail and leave its just-uploaded result orphaned in object storage."""


class ConsolidationRunner(Protocol):
    def run(self, *, input_path: Path, output_path: Path) -> None:
        """Consolidate the Excel workbook at `input_path` into one result
        workbook holding every Consolidation level (per_phase, with_owner,
        without_owner, combined), written to `output_path`. Raises
        ConsolidationInputError for anything wrong with the input file;
        never raises for a downstream (storage/DB) problem, since it never
        touches either."""
        ...


class ObserverConsolidationRunner:
    """The real ConsolidationRunner. Wraps `lees_observer` ->
    `bereken_observer` -> `schrijf_resultaat`
    (consolidation/observer_import.py): the export is read once, and one
    calculation produces every Consolidation level (ticket #152). Only
    path plumbing here.
    """

    def run(self, *, input_path: Path, output_path: Path) -> None:
        # BadZipFile/InvalidFileException/OSError are scoped to *reading*
        # the workbook only, not the whole pipeline (caught in review). A
        # KeyError is never mapped (ticket #150): lees_observer checks
        # every sheet and column it needs and says which one is missing,
        # so a KeyError is a genuine bug — logged, and shown only as the
        # generic failure reason, never as a raw message.
        try:
            bron = lees_observer(str(input_path))
        except ValueError as exc:
            raise ConsolidationInputError(str(exc)) from exc
        except (BadZipFile, InvalidFileException, OSError) as exc:
            # OSError alongside the two openpyxl-specific types: a
            # structurally-valid ZIP with corrupted/missing internal OOXML
            # parts makes openpyxl's load_workbook raise a bare OSError
            # ("File contains no valid workbook part") rather than either
            # of those — confirmed by direct reproduction in review, not
            # previously covered.
            raise ConsolidationInputError(
                "The uploaded file is not a valid Excel workbook."
            ) from exc

        try:
            berekend = bereken_observer(bron)
            schrijf_resultaat(str(output_path), bron, berekend, str(input_path))
        except ValueError as exc:
            # consolideer() (called from bereken_observer) raises its own
            # ValueErrors for the same class of validation failure as
            # lees_observer's — still mapped here, just no longer sharing
            # the OSError catches above with it.
            raise ConsolidationInputError(str(exc)) from exc


_consolidation_runner: ConsolidationRunner = ObserverConsolidationRunner()


def get_consolidation_runner() -> ConsolidationRunner:
    return _consolidation_runner


# Shown to the user for any failure that isn't a ConsolidationInputError —
# the real exception is only ever logged server-side (issue #113).
GENERIC_FAILURE_REASON = "Consolidation failed."


class InvalidWorkbookError(Exception):
    """The upload doesn't open as an Excel workbook at all — rejected by the
    endpoint as a web/file validation error before any Consolidation is
    created (ticket #115), never reaching the runner."""


def validate_workbook_opens(file_bytes: bytes) -> None:
    """Raise InvalidWorkbookError unless `file_bytes` opens as a workbook.

    Deliberately catches *any* exception: this is a gate on untrusted
    bytes with none of our own logic inside, and openpyxl signals a
    malformed file in many ways (BadZipFile, InvalidFileException, a bare
    OSError or KeyError for missing OOXML parts, XML parse errors, ...).
    `read_only=True` keeps this to the workbook's structure, without
    loading every sheet's cells.
    """
    try:
        workbook = load_workbook(BytesIO(file_bytes), read_only=True)
    except MemoryError:
        # A resource problem (e.g. a decompression bomb), not a verdict on
        # the file's validity — never masked as a 400.
        raise
    except Exception as exc:
        raise InvalidWorkbookError("The uploaded file is not a valid Excel workbook.") from exc
    workbook.close()


# Every consolidation result is stored under this prefix, and nothing else
# is — see remove_unreferenced_consolidation_results.
RESULT_KEY_PREFIX = "consolidations/"


def _new_result_key() -> str:
    return f"{RESULT_KEY_PREFIX}{uuid.uuid4().hex}/result.xlsx"


def start_consolidation(
    db: Session,
    *,
    requested_by_identity: str | None,
    original_filename: str,
    input_size_bytes: int,
) -> Consolidation:
    """Create and commit a `processing` Consolidation — before the runner
    starts, so the caller can audit CONSOLIDATION_STARTED against its id
    (ticket #115). Its result key is chosen here too (ticket #121), so a
    result uploaded by a run that never finishes can still be cleaned up by
    reconcile_stale_consolidations."""
    consolidation = Consolidation(
        original_filename=original_filename,
        status=ConsolidationStatus.PROCESSING,
        requested_by_identity=requested_by_identity,
        input_size_bytes=input_size_bytes,
        result_storage_key=_new_result_key(),
    )
    db.add(consolidation)
    db.commit()
    db.refresh(consolidation)
    return consolidation


def run_consolidation(
    db: Session,
    consolidation: Consolidation,
    *,
    file_bytes: bytes,
    s3: S3Client,
    runner: ConsolidationRunner,
    work_root: Path,
) -> Consolidation:
    """Run `consolidation` synchronously and move it to its terminal status
    (issue #113: "simplest reliable architecture" — no worker/queue).

    Writes `file_bytes` to a job-scoped temp directory (UUID-named, mirroring
    worker/cutting-worker's own job-dir convention), invokes `runner`, and —
    only if that succeeds — uploads the result to `s3` before marking the
    row `completed` with its storage key. So a row is never `completed`
    without a persisted result behind it.

    Any failure marks the row `failed` and still returns it: a
    ConsolidationInputError's message is used near-verbatim as
    `failure_reason` (issue #113: actionable for the user); anything else
    (storage problem, a bug) is logged in full here and recorded only as
    GENERIC_FAILURE_REASON. If even that final commit fails (the database
    itself is down), the exception propagates and the row stays
    `processing` until reconcile_stale_consolidations fails it.

    Raises ConsolidationAlreadyReconciledError if the reconciler failed the
    row as stale while this run was still going; the row then keeps that
    outcome, and a result this run uploaded is removed again.

    The temp workspace is always removed afterward (`finally`), regardless
    of outcome — cleanup tests verify this directly.
    """
    job_id = uuid.uuid4().hex
    workspace = work_root / job_id
    workspace.mkdir(parents=True, exist_ok=True)
    input_path = workspace / "input.xlsx"
    output_path = workspace / "result.xlsx"
    # Chosen by start_consolidation (ticket #121), the only way a row is
    # created. A row from before #121 never reaches this: runs don't survive
    # a deploy.
    key = consolidation.result_storage_key
    assert key is not None

    def _finish(
        status: ConsolidationStatus,
        *,
        failure_reason: str | None = None,
        result_size_bytes: int | None = None,
    ) -> Consolidation:
        # Only while still `processing` (ticket #121): if the reconciler
        # already failed this row as stale, it keeps that outcome and its
        # CONSOLIDATION_FAILED audit row, instead of being moved a second
        # time.
        transitioned = (
            db.execute(
                update(Consolidation)
                .where(
                    Consolidation.id == consolidation.id,
                    Consolidation.status == ConsolidationStatus.PROCESSING,
                )
                .values(
                    status=status,
                    completed_at=datetime.now(UTC),
                    failure_reason=failure_reason,
                    result_storage_key=key,
                    result_size_bytes=result_size_bytes,
                )
            ).rowcount
            == 1
        )
        db.commit()
        db.refresh(consolidation)
        if not transitioned:
            logger.warning(
                "Consolidation %s was already reconciled as %s before its run finished",
                consolidation.id,
                consolidation.status.value,
            )
            if status == ConsolidationStatus.COMPLETED:
                # Uploaded after the reconciler's own cleanup ran.
                _delete_result_best_effort(s3, key, consolidation_id=consolidation.id)
            raise ConsolidationAlreadyReconciledError(consolidation.id)
        return consolidation

    try:
        try:
            input_path.write_bytes(file_bytes)
            runner.run(input_path=input_path, output_path=output_path)
            result_size_bytes = output_path.stat().st_size
            s3.upload_file(output_path, key)
        except ConsolidationInputError as exc:
            failure_reason = str(exc)[:FAILURE_REASON_MAX_LENGTH]
            logger.info("Consolidation %s failed: %s", consolidation.id, failure_reason)
            return _finish(ConsolidationStatus.FAILED, failure_reason=failure_reason)
        except Exception:
            logger.exception("Consolidation %s failed unexpectedly", consolidation.id)
            return _finish(ConsolidationStatus.FAILED, failure_reason=GENERIC_FAILURE_REASON)

        return _finish(ConsolidationStatus.COMPLETED, result_size_bytes=result_size_bytes)
    finally:
        try:
            shutil.rmtree(workspace)
        except OSError:
            # Logged, not silently swallowed (caught in review) — the
            # docstring above promises the workspace is "always removed",
            # and docker-compose.yml's rationale for not mounting a
            # dedicated volume for this directory explicitly assumes that
            # holds; a failure here means it doesn't, and should be visible.
            logger.warning(
                "Failed to remove consolidation temp workspace %s", workspace, exc_info=True
            )


def get_consolidation(db: Session, *, consolidation_id: int) -> Consolidation:
    consolidation = db.get(Consolidation, consolidation_id)
    if consolidation is None:
        raise ConsolidationNotFoundError(consolidation_id)
    return consolidation


def rename_consolidation(
    db: Session, *, consolidation_id: int, display_name: str | None
) -> Consolidation:
    """Set (or, with None, clear) `consolidation_id`'s display_name (ticket
    #117). Never touches original_filename — the source file's provenance
    survives any rename. Allowed whatever the row's status: it's only a
    label. Raises ConsolidationNotFoundError for a nonexistent id."""
    consolidation = get_consolidation(db, consolidation_id=consolidation_id)
    consolidation.display_name = display_name
    db.commit()
    db.refresh(consolidation)
    return consolidation


def delete_consolidation(
    db: Session,
    *,
    consolidation_id: int,
    s3: S3Client,
    identity: str | None,
    identity_verified: bool,
) -> None:
    """Hard-delete `consolidation_id` (ticket #118): its stored result and
    its row, with no soft-delete flag and no undelete.

    CONSOLIDATION_DELETED is recorded here, and committed *before* anything
    is deleted (ticket #118: "before or as part of the deletion, not after
    — so a failure partway through doesn't lose the record"). That's why
    this one service function takes the audit identity, where the other
    endpoints audit in the API layer. The order is:

    1. commit the audit row;
    2. delete the stored object;
    3. delete the row and commit.

    Unlike every other audited action, the audit write here is not
    best-effort (issue #79's "never block the action" rule): if step 1
    fails, the error propagates and nothing is deleted. After step 1, a
    failure at any step leaves the deletion on record. If step 2 or 3
    fails, the audit row says "deleted" while the consolidation is still
    there (still listed; downloadable if step 2 failed, a clean 404 if step
    3 did). The error reaches the caller, and retrying finishes the job:
    deleting an already-missing object is a no-op, and each retry records
    its own audit row. Committing the audit row together with the row
    deletion instead would avoid that duplicate, but a failed commit after
    step 2 would then lose the record of an object that is really gone.

    Raises ConsolidationNotFoundError for a nonexistent id, and
    ConsolidationStillProcessingError for a `processing` row. Neither
    records anything.
    """
    consolidation = get_consolidation(db, consolidation_id=consolidation_id)
    if consolidation.status == ConsolidationStatus.PROCESSING:
        raise ConsolidationStillProcessingError(consolidation_id)
    result_storage_key = consolidation.result_storage_key

    record_required_audit_event(
        db,
        identity=identity,
        identity_verified=identity_verified,
        action=AuditAction.CONSOLIDATION_DELETED,
        target_type=AUDIT_TARGET_TYPE,
        target=str(consolidation_id),
    )
    db.commit()

    if result_storage_key is not None:
        s3.delete_object(result_storage_key)

    # A bulk DELETE rather than `db.delete(consolidation)`, so a concurrent
    # delete that got here first is a no-op rather than a stale-row error.
    db.execute(delete(Consolidation).where(Consolidation.id == consolidation_id))
    db.commit()


def stale_failure_reason(stale_after: timedelta) -> str:
    """The failure_reason of a row reconcile_stale_consolidations failed —
    says the run was interrupted, not that the input was wrong."""
    minutes = int(stale_after.total_seconds() // 60)
    return f"Consolidation was interrupted: still processing after {minutes} minutes."


def reconcile_stale_consolidations(
    db: Session,
    *,
    s3: S3Client | None,
    stale_after: timedelta,
) -> list[int]:
    """Fail every Consolidation still `processing` more than `stale_after`
    after it was created, and return their ids (ticket #121).

    Such a row belongs to a run that will never finish: the backend died or
    restarted mid-run, or the database failed at the run's final commit.
    A normal run takes seconds, and uploads are capped
    (settings.consolidation_max_file_size_bytes), so a fixed threshold is
    enough; see CONTEXT.md's "Consolidation reconciliation" decision.

    One transaction moves every stale row to `failed` and writes its
    CONSOLIDATION_FAILED audit row (identity None: a system action, not a
    request). The audit write is required, not best-effort: if it fails,
    the error propagates and nothing is committed, so the caller must close
    or roll back `db`. The UPDATE only matches rows still `processing`, so
    a row is never reconciled or audited twice, even by two overlapping
    calls.

    Only after that commit, each reconciled row's result object is removed
    best-effort: the run may have uploaded it before dying. A failure there
    is logged and changes nothing about the reconciled rows. `s3` is None
    when no storage client could be built; the rows are still reconciled,
    and the skipped cleanup is logged.
    """
    cutoff = datetime.now(UTC) - stale_after
    failure_reason = stale_failure_reason(stale_after)
    reconciled = db.execute(
        update(Consolidation)
        .where(
            Consolidation.status == ConsolidationStatus.PROCESSING,
            Consolidation.created_at < cutoff,
        )
        .values(
            status=ConsolidationStatus.FAILED,
            failure_reason=failure_reason,
            completed_at=datetime.now(UTC),
        )
        .returning(Consolidation.id, Consolidation.result_storage_key)
    ).all()
    for consolidation_id, _ in reconciled:
        record_required_audit_event(
            db,
            identity=None,
            identity_verified=False,
            action=AuditAction.CONSOLIDATION_FAILED,
            target_type=AUDIT_TARGET_TYPE,
            target=str(consolidation_id),
            failure_reason=failure_reason,
        )
    db.commit()

    for consolidation_id, result_storage_key in reconciled:
        logger.warning(
            "Reconciled consolidation %s: still processing after %s",
            consolidation_id,
            stale_after,
        )
        if result_storage_key is None:
            continue
        if s3 is None:
            logger.error(
                "No storage client: result %s of consolidation %s was not removed",
                result_storage_key,
                consolidation_id,
            )
            continue
        _delete_result_best_effort(s3, result_storage_key, consolidation_id=consolidation_id)
    return [consolidation_id for consolidation_id, _ in reconciled]


def remove_unreferenced_consolidation_results(db: Session, *, s3: S3Client) -> list[str]:
    """Delete every stored consolidation result that no Consolidation
    references, and return their keys (ticket #149). Only objects under
    RESULT_KEY_PREFIX are ever considered.

    Migration 0016 deletes every Consolidation made under the old
    per-Condition format, but a migration can't reach object storage, so
    this removes their results afterward. Re-runnable: a second run finds
    nothing left to remove. Run once at deploy with
    `python -m app.commands.remove_unreferenced_consolidation_results`.

    Objects are listed *before* the referenced keys are read. A
    Consolidation's key is committed before its result is uploaded
    (start_consolidation), so a result uploaded while this runs is either
    not listed yet or already referenced — never removed.
    """
    stored = [info.key for info in s3.list_objects_info(RESULT_KEY_PREFIX)]
    referenced = set(
        db.scalars(
            select(Consolidation.result_storage_key).where(
                Consolidation.result_storage_key.is_not(None)
            )
        )
    )
    removed = [key for key in stored if key not in referenced]
    for key in removed:
        s3.delete_object(key)
        logger.info("Removed unreferenced consolidation result %s", key)
    return removed


def _delete_result_best_effort(s3: S3Client, key: str, *, consolidation_id: int) -> None:
    """Remove a possibly-orphaned result object; a failure is only logged."""
    try:
        s3.delete_object(key)
    except Exception:
        logger.exception("Failed to remove result %s of consolidation %s", key, consolidation_id)


# Every consolidation is visible to everyone (fully shared, like
# AnalysisJob since ticket #72), so an unscoped listing grows without bound
# as history accumulates — capped to the newest rows, same reasoning and
# ceiling as app.services.analyses.MAX_LISTED_ANALYSIS_JOBS
# (ENGINEERING-STANDARDS.md §5: avoid unbounded database queries).
MAX_LISTED_CONSOLIDATIONS = 500


def list_consolidations(
    db: Session, *, limit: int = MAX_LISTED_CONSOLIDATIONS
) -> list[Consolidation]:
    """The newest Consolidations (up to `limit`), whoever requested them and
    whatever their status — backs the history page (ticket #116). Failed
    rows are deliberately included (issue #113: a failed attempt still
    shows up, with its reason), as are rows stuck `processing` (see
    Consolidation's own docstring — nothing reconciles those)."""
    stmt = (
        select(Consolidation)
        .order_by(Consolidation.created_at.desc(), Consolidation.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))
