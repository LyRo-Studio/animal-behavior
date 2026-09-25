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

from app.services.consolidation import ConsolidationInputError, ObserverConsolidationRunner

# Real Observer-export column layout, generated values only — see
# fixtures/consolidation/build_synthetic_observer_export.py.
_SYNTHETIC_EXPORT = (
    Path(__file__).parent / "fixtures" / "consolidation" / "synthetic_observer_export.xlsx"
)


_RESULT_SHEETS = ["with_owner", "without_owner", "combined"]


@pytest.fixture(scope="module")
def synthetic_result(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The synthetic export's result workbook — consolidated once for every
    test that only reads it (the real pipeline runs every level)."""
    output_path = tmp_path_factory.mktemp("synthetic") / "result.xlsx"
    ObserverConsolidationRunner().run(input_path=_SYNTHETIC_EXPORT, output_path=output_path)
    return output_path


def _sheet_rows(path: Path, sheet: str) -> list[dict[str, object]]:
    """A sheet's rows as {header: value}."""
    workbook = openpyxl.load_workbook(path, read_only=True)
    header, *rows = workbook[sheet].iter_rows(values_only=True)
    return [dict(zip(header, row, strict=True)) for row in rows]


def _result_rows(path: Path, sheet: str) -> dict[str, dict[str, object]]:
    """A result sheet as {test_id: {header: value}}."""
    return {row["test_id"]: row for row in _sheet_rows(path, sheet)}


def test_one_upload_produces_every_consolidation_level(synthetic_result: Path) -> None:
    workbook = openpyxl.load_workbook(synthetic_result, read_only=True)
    assert workbook.sheetnames == [
        *_RESULT_SHEETS,
        "README",
        "details",
        "phase_details",
        "denominators",
        "warnings",
        "variables",
        "phase_selection",
        "excluded",
    ]
    for sheet in _RESULT_SHEETS:
        assert list(_result_rows(synthetic_result, sheet)) == ["T901", "T902"]


def test_each_level_matches_what_its_old_condition_produced(synthetic_result: Path) -> None:
    """Literal values from the old per-Condition consolidations (ME, ZE and
    ME_ZE) of this same fixture, captured before ticket #149 merged them
    into one upload."""
    tail_tucked = "Total duration Tail tucked <No Modifier>"
    yawning = "Total number Yawning <No Modifier>"
    expected = {
        "with_owner": {"T901": (0.06071428571428571, 0.01168831168831169)},
        "without_owner": {"T901": (0.04350649350649351, 0.005194805194805195)},
        "combined": {"T901": (0.05211038961038961, 0.008441558441558441)},
    }
    for sheet, by_test in expected.items():
        rows = _result_rows(synthetic_result, sheet)
        for test_id, (duration_fraction, count_per_second) in by_test.items():
            assert rows[test_id]["dog_id"] == "D901"
            assert rows[test_id][tail_tucked] == pytest.approx(duration_fraction)
            assert rows[test_id][yawning] == pytest.approx(count_per_second)
        # Every exported behaviour/modifier column is kept, per level.
        assert len(rows["T902"]) == 540


def test_the_readme_is_english_and_names_the_source_file(synthetic_result: Path) -> None:
    readme = "\n".join(str(row["README"]) for row in _sheet_rows(synthetic_result, "README"))
    assert "with_owner: Observer phases 1-7" in readme
    assert "without_owner: Observer phases 9-15" in readme
    assert f"Source: {_SYNTHETIC_EXPORT.name}" in readme


def test_phase_selection_lists_the_levels_each_source_row_was_used_in(
    synthetic_result: Path,
) -> None:
    levels_by_phase = {
        (row["test_id"], row["fase"]): row["levels"]
        for row in _sheet_rows(synthetic_result, "phase_selection")
    }
    assert levels_by_phase[("T901", 1)] == "with_owner, combined"
    assert levels_by_phase[("T901", 9)] == "without_owner, combined"
    # Owner departure (F8) is listed, but never used.
    assert levels_by_phase[("T901", 8)] is None


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
        )


def test_not_a_workbook_raises_consolidation_input_error(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xlsx"
    input_path.write_bytes(b"not a real xlsx file at all")

    with pytest.raises(ConsolidationInputError):
        ObserverConsolidationRunner().run(
            input_path=input_path,
            output_path=tmp_path / "result.xlsx",
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
    )

    assert output_path.is_file()
    assert output_path.stat().st_size > 0
    result_workbook = openpyxl.load_workbook(output_path, read_only=True)
    assert {"with_owner", "without_owner", "combined", "details"} <= set(result_workbook.sheetnames)
