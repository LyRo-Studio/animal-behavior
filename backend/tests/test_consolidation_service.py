"""Adapter-seam tests for app/services/consolidation.py (ticket #115, part
of issue #113). Calls ObserverConsolidationRunner directly — no DB, no S3,
no FastAPI — exercising the real consolidation/observer_import.py pipeline
itself, independent of the request-orchestration tests in
test_consolidations_api.py (which fake this runner entirely).
"""

import os
from pathlib import Path

import openpyxl
import pytest

from app.models.consolidation import ConsolidationCondition
from app.services.consolidation import ConsolidationInputError, ObserverConsolidationRunner


def test_not_a_workbook_raises_consolidation_input_error(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xlsx"
    input_path.write_bytes(b"not a real xlsx file at all")

    with pytest.raises(ConsolidationInputError):
        ObserverConsolidationRunner().run(
            input_path=input_path,
            output_path=tmp_path / "result.xlsx",
            condition=ConsolidationCondition.ME_ZE,
        )


def test_missing_required_sheet_raises_consolidation_input_error(tmp_path: Path) -> None:
    # A structurally valid .xlsx, but none of the sheets lees_observer
    # actually requires ("Results (2)", "Results", "Results (REL)") —
    # confirmed during ticket #114's review that this raises a raw KeyError
    # from the domain code, not one of its own ValueErrors; the adapter
    # maps both to the same ConsolidationInputError (see
    # ObserverConsolidationRunner.run's docstring).
    workbook = openpyxl.Workbook()
    workbook.active.title = "SomeOtherSheet"
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)

    with pytest.raises(ConsolidationInputError, match="Results \\(2\\)"):
        ObserverConsolidationRunner().run(
            input_path=input_path,
            output_path=tmp_path / "result.xlsx",
            condition=ConsolidationCondition.ME_ZE,
        )


@pytest.mark.real_consolidation_fixture
def test_real_observer_export_succeeds(tmp_path: Path) -> None:
    """Opt-in: exercises the real pipeline against a real Observer export.

    No fixture file can substitute for this — the two .xlsx files checked
    into backend/tests/fixtures/consolidation/ are blank input *templates*
    for consolidatie.py's *other*, generic entry point (see that
    directory's README); they don't have the `Results (2)` / `Results` /
    `Results (REL)` sheet shape this pipeline needs, and a synthetic
    from-scratch fixture would have to hand-replicate real Observer XT
    export formatting (merged cells, yellow out-of-sight fills, exact
    column headers) closely enough to be more liability than signal. A
    real export was used to verify this pipeline during ticket #114's
    review but was deliberately not committed (real research data, not a
    fixture) — see .gitignore.

    Skipped (never failed) unless CONSOLIDATION_REAL_FIXTURE_PATH names a
    real local Observer export on disk — same "clear skip reason" pattern
    as cutting-worker/tests/test_real_assist_integration.py's env-var gate.
    Run for real with:

        CONSOLIDATION_REAL_FIXTURE_PATH=/path/to/real_export.xlsx \\
            .venv/bin/pytest -m real_consolidation_fixture \\
            tests/test_consolidation_service.py
    """
    fixture_path = os.environ.get("CONSOLIDATION_REAL_FIXTURE_PATH")
    if not fixture_path or not Path(fixture_path).is_file():
        pytest.skip("CONSOLIDATION_REAL_FIXTURE_PATH not set to a real Observer export on disk.")

    output_path = tmp_path / "result.xlsx"
    ObserverConsolidationRunner().run(
        input_path=Path(fixture_path),
        output_path=output_path,
        condition=ConsolidationCondition.ME_ZE,
    )

    assert output_path.is_file()
    assert output_path.stat().st_size > 0
    result_workbook = openpyxl.load_workbook(output_path, read_only=True)
    assert "consolidatie" in result_workbook.sheetnames
    assert "per_test" in result_workbook.sheetnames
