"""Consolidation request orchestration and the adapter around
consolidation/'s domain code (ticket #115, part of issue #113's Excel
consolidation feature).

Two deliberately separate things, mirroring app/services/media_prober.py's
MediaProber Protocol / FakeMediaProber split:

- `ConsolidationRunner` / `ObserverConsolidationRunner`: the adapter that
  calls consolidation/observer_import.py's `lees_observer` ->
  `bereken_observer` -> `schrijf_resultaat` pipeline unmodified — issue
  #113: "Do NOT rewrite the consolidation algorithm... prefer an
  adapter/service layer". Only the hardcoded file-path arguments are
  replaced with a request-scoped temp path. Tests inject
  `FakeConsolidationRunner` (tests/fakes.py) instead of running this real,
  slower, fixture-dependent pipeline.
- `start_consolidation` / `run_consolidation`: HTTP-free orchestration —
  a `processing` row first, then temp workspace, calling the injected
  runner, uploading the result, and moving the row to its terminal status
  — independent of which runner (real or fake) it's given.
"""

import logging
import shutil
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile

from consolidation.observer_import import bereken_observer, lees_observer, schrijf_resultaat
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.consolidation import (
    FAILURE_REASON_MAX_LENGTH,
    Consolidation,
    ConsolidationCondition,
    ConsolidationStatus,
)
from app.services.s3_client import S3Client

logger = logging.getLogger(__name__)

# Confirmed 1:1 during issue #113's spec work by actually running the
# pipeline: deel "1" writes "...deel1_met_eigenaar..." (ME), deel "2"
# writes "...deel2_zonder_eigenaar..." (ZE), "1+2" is both conditions
# combined.
_CONDITION_TO_DEEL = {
    ConsolidationCondition.ME: "1",
    ConsolidationCondition.ZE: "2",
    ConsolidationCondition.ME_ZE: "1+2",
}


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


class ConsolidationRunner(Protocol):
    def run(
        self, *, input_path: Path, output_path: Path, condition: ConsolidationCondition
    ) -> None:
        """Consolidate the Excel workbook at `input_path` for `condition`,
        writing the result to `output_path`. Raises ConsolidationInputError
        for anything wrong with the input file; never raises for a
        downstream (storage/DB) problem, since it never touches either."""
        ...


class ObserverConsolidationRunner:
    """The real ConsolidationRunner. Wraps `lees_observer` ->
    `bereken_observer` -> `schrijf_resultaat` (consolidation/
    observer_import.py) exactly as reviewed in ticket #114 — no change to
    that module's own logic, only path plumbing here.
    """

    def run(
        self, *, input_path: Path, output_path: Path, condition: ConsolidationCondition
    ) -> None:
        deel = _CONDITION_TO_DEEL[condition]

        # KeyError/BadZipFile/InvalidFileException/OSError are scoped to
        # *reading* the workbook only, not the whole pipeline (caught in
        # review): bereken_observer does its own pandas merges/groupby/
        # pivot, which could in principle raise a KeyError of its own for a
        # genuine bug unrelated to a missing sheet — catching that here
        # too would silently misreport a real defect as a user input
        # problem, with nothing logged anywhere to diagnose it.
        try:
            bron = lees_observer(str(input_path), deel=deel)
        except ValueError as exc:
            raise ConsolidationInputError(str(exc)) from exc
        except KeyError as exc:
            # lees_observer indexes required sheets via e.g.
            # wb["Results (2)"] with no try/except -> ValueError translation
            # of its own for a *missing* sheet specifically — confirmed
            # during ticket #114's review by actually triggering this path
            # (a raw KeyError, not one of the module's own ValueErrors).
            # Same "your file's shape is wrong" class of problem as every
            # other validation failure here.
            raise ConsolidationInputError(f"Missing expected sheet: {exc}") from exc
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
            # the KeyError/OSError catches above with it.
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


def start_consolidation(
    db: Session,
    *,
    requested_by_identity: str | None,
    original_filename: str,
    condition: ConsolidationCondition,
    input_size_bytes: int,
) -> Consolidation:
    """Create and commit a `processing` Consolidation — before the runner
    starts, so the caller can audit CONSOLIDATION_STARTED against its id
    (ticket #115)."""
    consolidation = Consolidation(
        original_filename=original_filename,
        condition=condition,
        status=ConsolidationStatus.PROCESSING,
        requested_by_identity=requested_by_identity,
        input_size_bytes=input_size_bytes,
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
    `processing`.

    The temp workspace is always removed afterward (`finally`), regardless
    of outcome — cleanup tests verify this directly.
    """
    job_id = uuid.uuid4().hex
    workspace = work_root / job_id
    workspace.mkdir(parents=True, exist_ok=True)
    input_path = workspace / "input.xlsx"
    output_path = workspace / "result.xlsx"

    def _finish(
        status: ConsolidationStatus,
        *,
        failure_reason: str | None = None,
        result_storage_key: str | None = None,
        result_size_bytes: int | None = None,
    ) -> Consolidation:
        consolidation.status = status
        consolidation.completed_at = datetime.now(UTC)
        consolidation.failure_reason = failure_reason
        consolidation.result_storage_key = result_storage_key
        consolidation.result_size_bytes = result_size_bytes
        db.commit()
        db.refresh(consolidation)
        return consolidation

    try:
        try:
            input_path.write_bytes(file_bytes)
            runner.run(
                input_path=input_path, output_path=output_path, condition=consolidation.condition
            )
            result_size_bytes = output_path.stat().st_size
            key = f"consolidations/{job_id}/result.xlsx"
            s3.upload_file(output_path, key)
        except ConsolidationInputError as exc:
            failure_reason = str(exc)[:FAILURE_REASON_MAX_LENGTH]
            logger.info("Consolidation %s failed: %s", consolidation.id, failure_reason)
            return _finish(ConsolidationStatus.FAILED, failure_reason=failure_reason)
        except Exception:
            logger.exception("Consolidation %s failed unexpectedly", consolidation.id)
            return _finish(ConsolidationStatus.FAILED, failure_reason=GENERIC_FAILURE_REASON)

        return _finish(
            ConsolidationStatus.COMPLETED,
            result_storage_key=key,
            result_size_bytes=result_size_bytes,
        )
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
