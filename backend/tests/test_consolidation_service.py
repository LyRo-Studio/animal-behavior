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
from consolidation.observer_import import normaliseer_kop

from app.services.consolidation import ConsolidationInputError, ObserverConsolidationRunner
from tests.observer_exports import ColumnValue, write_observer_export

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
        "availability",
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
        # Every exported behaviour/modifier column is kept, per level,
        # except the 35 Event duration columns (ticket #150): 538 - 35,
        # plus test_id and dog_id.
        assert len(rows["T902"]) == 505


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


def _synthetic_export_without(tmp_path: Path, headers: list[str]) -> Path:
    """The synthetic export with `headers` removed from all three sheets."""
    workbook = openpyxl.load_workbook(_SYNTHETIC_EXPORT)
    for sheet in ("Results (2)", "Results", "Results (REL)"):
        worksheet = workbook[sheet]
        for column in sorted((c.column for c in worksheet[1] if c.value in headers), reverse=True):
            worksheet.delete_cols(column)
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)
    return input_path


def test_removing_former_boundary_and_other_columns_still_consolidates(
    tmp_path: Path, synthetic_result: Path
) -> None:
    """Lying down sternally head up and Other tail used to mark where their
    group started and ended; now any behaviour column is optional."""
    removed = [
        f"Total {statistic} {behaviour} <No Modifier>"
        for statistic in ("duration", "number")
        for behaviour in ("Lying down sternally head up", "Other tail", "Panting")
    ]
    output_path = tmp_path / "result.xlsx"

    ObserverConsolidationRunner().run(
        input_path=_synthetic_export_without(tmp_path, removed), output_path=output_path
    )

    for sheet in _RESULT_SHEETS:
        full = _result_rows(synthetic_result, sheet)
        for test_id, row in _result_rows(output_path, sheet).items():
            # Every other behaviour keeps its group and Out of Sight: its
            # value is exactly what the full export gives.
            assert row == {k: v for k, v in full[test_id].items() if k not in removed}
    status = {a["behaviour"]: a["status"] for a in _sheet_rows(output_path, "availability")}
    assert status["Other tail"] == "not_exported"
    assert status["Panting"] == "not_exported"
    # Its modifier columns are still exported.
    assert status["Lying down sternally head up"] == "exported_nonzero"


def test_every_header_in_the_real_export_layout_resolves_to_one_definition_entry(
    synthetic_result: Path,
) -> None:
    source = openpyxl.load_workbook(_SYNTHETIC_EXPORT, read_only=True)["Results (2)"]
    headers = [
        normaliseer_kop(value)
        for value in next(source.iter_rows(max_row=1, values_only=True))
        if str(value).startswith("Total ")
    ]

    used = [v["gedrag"] for v in _sheet_rows(synthetic_result, "variables")]
    excluded = [e["bronkop"] for e in _sheet_rows(synthetic_result, "excluded")]
    assert sorted(used + excluded) == sorted(headers)
    assert not [
        w for w in _sheet_rows(synthetic_result, "warnings") if w["type"] == "unknown_modifier"
    ]


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
    # actually requires ("Results (2)", "Results", "Results (REL)"). Since
    # ticket #150 lees_observer names the missing sheet itself, rather
    # than the adapter turning a raw KeyError into a message.
    workbook = openpyxl.Workbook()
    workbook.active.title = "SomeOtherSheet"
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)

    with pytest.raises(ConsolidationInputError, match="^Missing sheet: Results \\(2\\)$"):
        ObserverConsolidationRunner().run(
            input_path=input_path,
            output_path=tmp_path / "result.xlsx",
        )


def test_missing_required_column_fails_naming_the_column(tmp_path: Path) -> None:
    input_path = _synthetic_export_without(tmp_path, ["Duration"])

    with pytest.raises(
        ConsolidationInputError, match="^Missing column in Results \\(2\\): Duration$"
    ):
        ObserverConsolidationRunner().run(
            input_path=input_path, output_path=tmp_path / "result.xlsx"
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
    """The OSError/BadZipFile/InvalidFileException catches are scoped to
    lees_observer's own workbook-reading step only (found in review: they
    originally wrapped the whole pipeline), and a KeyError is never
    mapped (ticket #150) — a KeyError from a genuine bug (e.g. inside
    bereken_observer's own pandas merges) must propagate uncaught, not be
    silently reported to the user as "your file's shape is wrong"."""
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


def _consolidate(tmp_path: Path, columns: dict[str, ColumnValue]) -> Path:
    """Consolidate a small synthetic export (tests/observer_exports.py)."""
    input_path = write_observer_export(tmp_path / "input.xlsx", columns)
    output_path = tmp_path / "result.xlsx"
    ObserverConsolidationRunner().run(input_path=input_path, output_path=output_path)
    return output_path


def test_stiffening_up_is_corrected_by_the_exploration_out_of_sight(tmp_path: Path) -> None:
    """The Ethogram puts Stiffening up in the exploration/self-maintenance
    group, whatever group its column sits next to in the export."""
    result = _consolidate(
        tmp_path,
        {
            "Total duration Staring <No Modifier>": 15,
            "Total duration Stiffening up <No Modifier>": 20,
            "Total duration Out of sight expl self maint beh dog <No Modifier>": 20,
            "Total duration Out of sight stress-related <No Modifier>": 40,
        },
    )

    row = _result_rows(result, "with_owner")["T901"]
    # 20 / (100 - 20), not 20 / (100 - 40).
    assert row["Total duration Stiffening up <No Modifier>"] == pytest.approx(0.25)
    assert row["Total duration Staring <No Modifier>"] == pytest.approx(0.25)


def test_a_column_missing_from_raw_results_fails_naming_it(tmp_path: Path) -> None:
    input_path = write_observer_export(
        tmp_path / "input.xlsx", {"Total duration Panting <No Modifier>": 20}
    )
    workbook = openpyxl.load_workbook(input_path)
    raw = workbook["Results"]
    raw.delete_cols(next(c.column for c in raw[1] if c.value == "Duration"))
    workbook.save(input_path)

    with pytest.raises(ConsolidationInputError, match="^Missing column in Results: Duration$"):
        ObserverConsolidationRunner().run(
            input_path=input_path, output_path=tmp_path / "result.xlsx"
        )


def test_a_missing_required_out_of_sight_fails_naming_the_group(tmp_path: Path) -> None:
    with pytest.raises(
        ConsolidationInputError, match="Missing Out of Sight column for Tail position"
    ):
        _consolidate(tmp_path, {"Total duration Tail tucked <No Modifier>": 20})


def test_an_out_of_sight_without_any_exported_behaviour_of_its_group_is_ignored(
    tmp_path: Path,
) -> None:
    result = _consolidate(
        tmp_path,
        {
            # No Vocalisation Out of Sight exists; Panting uses the full Duration.
            "Total duration Panting <No Modifier>": 20,
            "Total duration Out of sight attention <No Modifier>": 100,
        },
    )

    row = _result_rows(result, "with_owner")["T901"]
    assert row["Total duration Panting <No Modifier>"] == pytest.approx(0.20)
    reasons = {e["bronkop"]: e["reden"] for e in _sheet_rows(result, "excluded")}
    assert "ignored" in reasons["Total duration Out of sight attention <No Modifier>"]


def test_an_unknown_behaviour_fails_naming_the_column(tmp_path: Path) -> None:
    with pytest.raises(
        ConsolidationInputError,
        match=r"Unknown behaviour in column H \(Total duration Sniffing air <No Modifier>\)",
    ):
        _consolidate(tmp_path, {"Total duration Sniffing air <No Modifier>": 20})


def test_an_unknown_modifier_warns_and_is_kept(tmp_path: Path) -> None:
    result = _consolidate(tmp_path, {"Total duration Panting Test person": 20})

    row = _result_rows(result, "with_owner")["T901"]
    assert row["Total duration Panting Test person"] == pytest.approx(0.20)
    warned = {
        w["variabele"] for w in _sheet_rows(result, "warnings") if w["type"] == "unknown_modifier"
    }
    assert warned == {"Total duration Panting Test person"}


def test_event_durations_are_excluded_and_only_their_frequency_reported(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            "Total duration Yawning <No Modifier>": 1,
            "Total number Yawning <No Modifier>": 2,
            "Total duration Out of sight stress-related <No Modifier>": 20,
        },
    )

    row = _result_rows(result, "with_owner")["T901"]
    assert "Total duration Yawning <No Modifier>" not in row
    # 2 per phase / 80 visible seconds per phase.
    assert row["Total number Yawning <No Modifier>"] == pytest.approx(0.025)
    excluded = {e["bronkop"] for e in _sheet_rows(result, "excluded")}
    assert "Total duration Yawning <No Modifier>" in excluded
    warnings = [w["variabele"] for w in _sheet_rows(result, "warnings")]
    assert "Total duration Yawning <No Modifier>" in warnings


def test_modifier_variants_stay_separate_and_are_never_summed(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            "Total duration Barking <No Modifier>": 0,
            "Total duration Barking Test person": 10,
            "Total duration Barking Familiar persoon": 30,
        },
    )

    row = _result_rows(result, "with_owner")["T901"]
    assert row["Total duration Barking <No Modifier>"] == 0
    assert row["Total duration Barking Test person"] == pytest.approx(0.10)
    assert row["Total duration Barking Familiar persoon"] == pytest.approx(0.30)
    assert not [w for w in _sheet_rows(result, "warnings") if w["type"] == "unknown_modifier"]


def test_first_contact_and_protocol_behaviours_are_excluded_by_name(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            "Total number First contact with TP <No Modifier>": 1,
            "Total duration Standing bij TP <No Modifier>": 50,
            "Total duration Panting <No Modifier>": 20,
        },
    )

    assert list(_result_rows(result, "with_owner")["T901"]) == [
        "test_id",
        "dog_id",
        "Total duration Panting <No Modifier>",
    ]
    reasons = {e["bronkop"]: e["reden"] for e in _sheet_rows(result, "excluded")}
    assert "first-contact" in reasons["Total number First contact with TP <No Modifier>"]
    assert "protocol" in reasons["Total duration Standing bij TP <No Modifier>"]


def test_groups_needing_the_scoring_plan_are_excluded_with_a_warning(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            "Total duration Distance TP zero <No Modifier>": 5,
            "Total duration Panting <No Modifier>": 20,
        },
    )

    assert (
        "Total duration Distance TP zero <No Modifier>"
        not in (_result_rows(result, "with_owner")["T901"])
    )
    reasons = {e["bronkop"]: e["reden"] for e in _sheet_rows(result, "excluded")}
    assert "Not yet supported" in reasons["Total duration Distance TP zero <No Modifier>"]
    warned = {
        w["variabele"] for w in _sheet_rows(result, "warnings") if w["type"] == "not_yet_supported"
    }
    assert warned == {"Distance of the dog to TP"}


def test_not_exported_and_measured_zero_are_told_apart(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            "Total duration Tail tucked <No Modifier>": 20,
            "Total duration Tail not tucked <No Modifier>": 0,
            "Total duration Out of sight tail <No Modifier>": 20,
        },
    )

    row = _result_rows(result, "with_owner")["T901"]
    assert row["Total duration Tail not tucked <No Modifier>"] == 0
    assert "Total duration Tail hanging down <No Modifier>" not in row
    status = {a["behaviour"]: a["status"] for a in _sheet_rows(result, "availability")}
    assert status["Tail tucked"] == "exported_nonzero"
    assert status["Tail not tucked"] == "exported_all_zero"
    assert status["Tail hanging down"] == "not_exported"
    # Every dog behaviour in the Ethogram is listed, protocol behaviours aren't.
    assert "Out of sight tail" in status
    assert "Standing by TP" not in status


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
