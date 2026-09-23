"""Adapter-seam tests for app/services/consolidation.py (ticket #115, part
of issue #113). Calls ObserverConsolidationRunner directly — no DB, no S3,
no FastAPI — exercising the real consolidation/observer_import.py pipeline
itself, independent of the request-orchestration tests in
test_consolidations_api.py (which fake this runner entirely).
"""

import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest

from app.models.consolidation import ConsolidationCondition
from app.services.consolidation import ConsolidationInputError, ObserverConsolidationRunner

# Real Observer-export column layout, generated values only — see
# fixtures/consolidation/build_synthetic_observer_export.py.
_SYNTHETIC_EXPORT = (
    Path(__file__).parent / "fixtures" / "consolidation" / "synthetic_observer_export.xlsx"
)


@pytest.mark.parametrize("condition", list(ConsolidationCondition))
def test_synthetic_observer_export_consolidates_for_every_condition(
    tmp_path: Path, condition: ConsolidationCondition
) -> None:
    output_path = tmp_path / "result.xlsx"

    ObserverConsolidationRunner().run(
        input_path=_SYNTHETIC_EXPORT, output_path=output_path, condition=condition
    )

    result_workbook = openpyxl.load_workbook(output_path, read_only=True)
    assert "consolidatie" in result_workbook.sheetnames
    per_test = result_workbook["per_test"]
    test_ids = [row[0] for row in per_test.iter_rows(min_row=2, values_only=True)]
    assert test_ids == ["T901", "T902"]


def test_raw_and_edited_results_disagreeing_raises_the_domain_message(tmp_path: Path) -> None:
    """A domain ValueError on the real sheet shape: lees_observer checks every
    selected measurement in `Results (2)` against raw `Results`."""
    workbook = openpyxl.load_workbook(_SYNTHETIC_EXPORT)
    source = workbook["Results (2)"]
    duration_column = next(c.column for c in source[1] if c.value == "Duration")
    source.cell(2, duration_column).value = 999.0
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)

    with pytest.raises(ConsolidationInputError, match="Ruwe data wijkt af van kopie"):
        ObserverConsolidationRunner().run(
            input_path=input_path,
            output_path=tmp_path / "result.xlsx",
            condition=ConsolidationCondition.ME,
        )


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


def test_corrupted_but_valid_zip_raises_consolidation_input_error(tmp_path: Path) -> None:
    """A .xlsx that IS a structurally valid ZIP (unlike the garbage-bytes
    case above) but whose internal OOXML parts are missing/corrupted makes
    openpyxl's load_workbook raise a bare OSError, not BadZipFile or
    InvalidFileException — found in review, not caught by the original
    exception mapping."""
    input_path = tmp_path / "input.xlsx"
    with zipfile.ZipFile(input_path, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')

    with pytest.raises(ConsolidationInputError):
        ObserverConsolidationRunner().run(
            input_path=input_path,
            output_path=tmp_path / "result.xlsx",
            condition=ConsolidationCondition.ME_ZE,
        )


def test_a_bug_inside_bereken_observer_is_not_miscategorized_as_input_error(
    tmp_path: Path,
) -> None:
    """The KeyError/OSError/BadZipFile/InvalidFileException catches are
    scoped to lees_observer's own workbook-reading step only (found in
    review: they originally wrapped the whole pipeline) — a KeyError from
    a genuine bug elsewhere (e.g. inside bereken_observer's own pandas
    merges) must propagate uncaught, not be silently reported to the user
    as "your file's shape is wrong"."""
    workbook = openpyxl.Workbook()
    for name in ("Results", "Results (2)", "Results (REL)"):
        workbook.create_sheet(name)
    del workbook["Sheet"]
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)

    with (
        patch("app.services.consolidation.lees_observer", return_value={"deel": "1+2"}),
        patch(
            "app.services.consolidation.bereken_observer",
            side_effect=KeyError("unrelated_bug"),
        ),
        pytest.raises(KeyError, match="unrelated_bug"),
    ):
        ObserverConsolidationRunner().run(
            input_path=input_path,
            output_path=tmp_path / "result.xlsx",
            condition=ConsolidationCondition.ME_ZE,
        )


@pytest.mark.real_consolidation_fixture
def test_real_observer_export_succeeds(tmp_path: Path) -> None:
    """Opt-in: exercises the real pipeline against a real Observer export.

    The synthetic fixture above has the real column layout but generated
    values; this additionally checks real measurements (e.g. Observer's own
    rounding) still pass. A real export was used to verify this pipeline
    during ticket #114's review but was deliberately not committed (real
    research data, not a fixture) — see .gitignore.

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
