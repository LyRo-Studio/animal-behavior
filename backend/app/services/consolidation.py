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
- `create_consolidation`: HTTP-free orchestration — temp workspace,
  calling the injected runner, uploading the result, and persisting a
  `Consolidation` row — independent of which runner (real or fake) it's
  given.
"""

import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile

from consolidation.observer_import import bereken_observer, lees_observer, schrijf_resultaat
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy.orm import Session

from app.models.consolidation import Consolidation, ConsolidationCondition, ConsolidationStatus
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


def create_consolidation(
    db: Session,
    *,
    requested_by_identity: str | None,
    original_filename: str,
    condition: ConsolidationCondition,
    file_bytes: bytes,
    s3: S3Client,
    runner: ConsolidationRunner,
    work_root: Path,
) -> Consolidation:
    """Run one consolidation synchronously and persist its outcome
    (issue #113: "simplest reliable architecture" — no worker/queue).

    Writes `file_bytes` to a job-scoped temp directory (UUID-named, mirroring
    worker/cutting-worker's own job-dir convention), invokes `runner`, and —
    only if that succeeds — uploads the result to `s3` before writing a
    `completed` Consolidation row with its storage key. If `runner` raises
    ConsolidationInputError, a `failed` row is written instead, with that
    error's message as `failure_reason` (issue #113: shown to the user
    near-verbatim). Either way this always returns a Consolidation — it
    never raises for an expected input problem, since that's a valid, real
    outcome to persist and report, not a failure of this function itself.

    A genuinely unexpected exception (a storage/DB problem, or a bug) is
    deliberately *not* caught here: no row exists yet to update to `failed`
    at that point (this function commits exactly once, right before
    returning), so there is nothing to reconcile — it propagates to the
    caller like any other unhandled exception in this codebase, logged in
    full server-side by the caller and reported as a generic 500 to the
    frontend.

    The temp workspace is always removed afterward (`finally`), regardless
    of outcome — cleanup tests verify this directly.
    """
    job_id = uuid.uuid4().hex
    workspace = work_root / job_id
    workspace.mkdir(parents=True, exist_ok=True)
    input_path = workspace / "input.xlsx"
    output_path = workspace / "result.xlsx"

    def _persist(**outcome_fields: object) -> Consolidation:
        # Shared by both outcomes below (caught in review: previously
        # duplicated field-by-field, risking the two falling out of sync)
        # — original_filename/condition/requested_by_identity/
        # input_size_bytes/completed_at are common to every Consolidation
        # this function ever writes; outcome_fields supplies the rest
        # (status, and whichever of failure_reason/result_storage_key/
        # result_size_bytes apply).
        consolidation = Consolidation(
            original_filename=original_filename,
            condition=condition,
            requested_by_identity=requested_by_identity,
            input_size_bytes=len(file_bytes),
            completed_at=datetime.now(UTC),
            **outcome_fields,
        )
        db.add(consolidation)
        db.commit()
        db.refresh(consolidation)
        return consolidation

    try:
        input_path.write_bytes(file_bytes)

        try:
            runner.run(input_path=input_path, output_path=output_path, condition=condition)
        except ConsolidationInputError as exc:
            failure_reason = str(exc)[:500]
            logger.info("Consolidation failed for %r: %s", original_filename, failure_reason)
            return _persist(status=ConsolidationStatus.FAILED, failure_reason=failure_reason)

        result_size_bytes = output_path.stat().st_size
        key = f"consolidations/{job_id}/result.xlsx"
        s3.upload_file(output_path, key)

        return _persist(
            status=ConsolidationStatus.COMPLETED,
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
