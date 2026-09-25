"""Adapter-seam tests for app/services/consolidation.py (ticket #115, part
of issue #113). Calls ObserverConsolidationRunner directly — no DB, no S3,
no FastAPI — exercising the real consolidation/observer_import.py pipeline
itself, independent of the request-orchestration tests in
test_consolidations_api.py (which fake this runner entirely).
"""

import os
import re
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import openpyxl
import pytest

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


def _phase_rows(path: Path) -> dict[tuple[str, int], dict[str, object]]:
    """The per_phase sheet as {(test_id, observer_phase): {header: value}}."""
    return {(row["test_id"], row["observer_phase"]): row for row in _sheet_rows(path, "per_phase")}


def test_one_upload_produces_every_consolidation_level(synthetic_result: Path) -> None:
    workbook = openpyxl.load_workbook(synthetic_result, read_only=True)
    assert workbook.sheetnames == [
        "per_phase",
        *_RESULT_SHEETS,
        "README",
        "details",
        "denominators",
        "warnings",
        "variables",
        "availability",
        "phase_selection",
        "excluded",
    ]
    for sheet in _RESULT_SHEETS:
        assert list(_result_rows(synthetic_result, sheet)) == ["T901", "T902"]
    # One row per test and Observer phase, F8 never.
    assert list(_phase_rows(synthetic_result)) == [
        (test_id, phase) for test_id in ("T901", "T902") for phase in (*range(1, 8), *range(9, 16))
    ]


def test_each_level_sums_its_own_phases_before_dividing(synthetic_result: Path) -> None:
    """Worked out by hand from the fixture's cells (`-` read as 0): every
    phase lasts 120 s with 10 s Out of sight tail and 10 s Out of sight
    stress-related, so seven phases leave 770 visible seconds."""
    tail_tucked = "Total duration Tail tucked <No Modifier>"
    yawning = "Total number Yawning <No Modifier>"
    expected = {
        "with_owner": {"T901": (21.25 / 770, 12 / 770)},
        "without_owner": {"T901": (59 / 770, 9 / 770)},
        "combined": {"T901": (80.25 / 1540, 21 / 1540)},
    }
    for sheet, by_test in expected.items():
        rows = _result_rows(synthetic_result, sheet)
        for test_id, (duration_fraction, count_per_second) in by_test.items():
            assert rows[test_id]["dog_id"] == "D901"
            assert rows[test_id][tail_tucked] == pytest.approx(duration_fraction)
            assert rows[test_id][yawning] == pytest.approx(count_per_second)
        # Every exported behaviour/modifier column is kept, per level,
        # except the 35 Event duration columns (ticket #150): 538 - 35,
        # after test_id, dog_id, phases_used, missing_phases and status.
        assert len(rows["T902"]) == 508
        assert rows["T902"]["status"] == "ok"
        assert rows["T902"]["missing_phases"] is None


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
    assert levels_by_phase[("T901", 1)] == "per_phase, with_owner, combined"
    assert levels_by_phase[("T901", 9)] == "per_phase, without_owner, combined"
    # Owner departure (F8) is listed, but never used.
    assert levels_by_phase[("T901", 8)] is None
    phase_nine = next(r for r in _sheet_rows(synthetic_result, "phase_selection") if r["fase"] == 9)
    assert (phase_nine["condition"], phase_nine["phase"]) == ("ZE", "F1")


def _synthetic_export_without(tmp_path: Path, headers: list[str]) -> Path:
    """The synthetic export with `headers` removed."""
    workbook = openpyxl.load_workbook(_SYNTHETIC_EXPORT)
    results = workbook["Results"]
    for column in sorted((c.column for c in results[1] if c.value in headers), reverse=True):
        results.delete_cols(column)
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
    full_phases = _phase_rows(synthetic_result)
    for key, row in _phase_rows(output_path).items():
        assert row == {k: v for k, v in full_phases[key].items() if k not in removed}
    status = {a["behaviour"]: a["status"] for a in _sheet_rows(output_path, "availability")}
    assert status["Other tail"] == "not_exported"
    assert status["Panting"] == "not_exported"
    # Its modifier columns are still exported.
    assert status["Lying down sternally head up"] == "exported_nonzero"


def test_every_header_in_the_real_export_layout_resolves_to_one_definition_entry(
    synthetic_result: Path,
) -> None:
    results = openpyxl.load_workbook(_SYNTHETIC_EXPORT, read_only=True)["Results"]
    headers = [
        value
        for value in next(results.iter_rows(max_row=1, values_only=True))
        if str(value).startswith("Total ")
    ]

    used = [v["gedrag"] for v in _sheet_rows(synthetic_result, "variables")]
    excluded = [e["bronkop"] for e in _sheet_rows(synthetic_result, "excluded")]
    assert sorted(used + excluded) == sorted(headers)
    assert not [
        w for w in _sheet_rows(synthetic_result, "warnings") if w["type"] == "unknown_modifier"
    ]


def test_the_synthetic_export_is_the_raw_results_sheet_only() -> None:
    """Observer's export exactly as it produces it (ticket #151): no
    hand-made working copies, and many zeros written as `-`."""
    workbook = openpyxl.load_workbook(_SYNTHETIC_EXPORT, read_only=True)
    assert workbook.sheetnames == ["Results"]
    values = [v for row in workbook["Results"].iter_rows(min_row=2, values_only=True) for v in row]
    assert "-" in values


def test_a_text_cell_in_the_real_layout_raises_the_domain_message(tmp_path: Path) -> None:
    workbook = openpyxl.load_workbook(_SYNTHETIC_EXPORT)
    results = workbook["Results"]
    duration_column = next(c for c in results[1] if c.value == "Duration").column_letter
    results[f"{duration_column}2"] = "n/a"
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)

    with pytest.raises(
        ConsolidationInputError, match=f"^Not a number in cell Results!{duration_column}2 "
    ):
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
    # A structurally valid .xlsx, but without the raw `Results` sheet, the
    # only one lees_observer reads (ticket #151). Since ticket #150
    # lees_observer names the missing sheet itself, rather than the
    # adapter turning a raw KeyError into a message.
    workbook = openpyxl.Workbook()
    workbook.active.title = "SomeOtherSheet"
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)

    with pytest.raises(ConsolidationInputError, match="^Missing sheet: Results$"):
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
    """The OSError/BadZipFile/InvalidFileException catches are scoped to
    lees_observer's own workbook-reading step only (found in review: they
    originally wrapped the whole pipeline), and a KeyError is never
    mapped (ticket #150) — a KeyError from a genuine bug (e.g. inside
    bereken_observer's own pandas merges) must propagate uncaught, not be
    silently reported to the user as "your file's shape is wrong"."""
    workbook = openpyxl.Workbook()
    workbook.active.title = "Results"
    input_path = tmp_path / "input.xlsx"
    workbook.save(input_path)

    with (
        patch("app.services.consolidation.lees_observer", return_value={}),
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


def _consolidate(tmp_path: Path, columns: dict[str, ColumnValue], **export: Any) -> Path:
    """Consolidate a small synthetic export (tests/observer_exports.py);
    `export` passes on its `tests`, `duration` and `phases` options."""
    input_path = write_observer_export(tmp_path / "input.xlsx", columns, **export)
    output_path = tmp_path / "result.xlsx"
    ObserverConsolidationRunner().run(input_path=input_path, output_path=output_path)
    return output_path


# Per-phase results and no-visible-time handling (ticket #152).
_TAIL = "Total duration Tail tucked <No Modifier>"
_TAIL_COUNT = "Total number Tail tucked <No Modifier>"
_OOS_TAIL = "Total duration Out of sight tail <No Modifier>"
_PANTING = "Total duration Panting <No Modifier>"
_ME = ", ".join(f"ME F{phase}" for phase in range(1, 8))
_ZE = ", ".join(f"ZE F{phase}" for phase in range(1, 8))


def _details(path: Path, level: str, behaviour: str) -> dict[object, dict[str, object]]:
    """The `details` rows of one level and behaviour, by Observer phase
    (per_phase) or as the level's single row (key None)."""
    return {
        row["fase"]: row
        for row in _sheet_rows(path, "details")
        if row["level"] == level and row["gedrag"] == behaviour
    }


def test_per_phase_rows_name_the_observer_phase_condition_and_phase(tmp_path: Path) -> None:
    result = _consolidate(tmp_path, {_PANTING: 20})

    rows = _phase_rows(result)
    assert list(rows[("T901", 12)]) == [
        "test_id",
        "dog_id",
        "observer_phase",
        "condition",
        "phase",
        _PANTING,
    ]
    assert (rows[("T901", 3)]["condition"], rows[("T901", 3)]["phase"]) == ("ME", "F3")
    assert (rows[("T901", 12)]["condition"], rows[("T901", 12)]["phase"]) == ("ZE", "F4")
    assert ("T901", 8) not in rows


def test_a_phase_is_divided_by_its_own_visible_time(tmp_path: Path) -> None:
    result = _consolidate(tmp_path, {_TAIL: 20, _OOS_TAIL: 20, _PANTING: 20})

    row = _phase_rows(result)[("T901", 1)]
    # Duration 100, behaviour 20, Out of Sight 20.
    assert row[_TAIL] == pytest.approx(0.25)
    # Vocalisation has no Out of Sight: Duration 100, behaviour 20.
    assert row[_PANTING] == pytest.approx(0.20)


def test_a_phase_without_visible_time_is_blank_never_zero(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            _TAIL: lambda phase: 0 if phase == 2 else 20,
            _TAIL_COUNT: lambda phase: 0 if phase == 2 else 1,
            _OOS_TAIL: lambda phase: 100 if phase == 2 else 20,
        },
    )

    rows = _phase_rows(result)
    assert rows[("T901", 2)][_TAIL] is None
    assert rows[("T901", 2)][_TAIL_COUNT] is None
    assert rows[("T901", 1)][_TAIL] == pytest.approx(0.25)
    assert _details(result, "per_phase", _TAIL)[2]["status"] == "no_visible_time"
    assert _details(result, "per_phase", _TAIL)[1]["status"] == "ok"
    # ME still sums all seven phases: 120 / (700 - 220).
    assert _result_rows(result, "with_owner")["T901"][_TAIL] == pytest.approx(0.25)


def test_out_of_sight_just_within_the_tolerance_above_duration_is_no_visible_time(
    tmp_path: Path,
) -> None:
    result = _consolidate(tmp_path, {_TAIL: 0, _OOS_TAIL: lambda p: 100.0005 if p == 2 else 20})

    assert _phase_rows(result)[("T901", 2)][_TAIL] is None
    assert _details(result, "per_phase", _TAIL)[2]["status"] == "no_visible_time"
    rounding = [w for w in _sheet_rows(result, "warnings") if w["type"] == "rounding"]
    assert [(w["test_id"], w["fase"], w["variabele"]) for w in rounding] == [
        ("T901", 2, "Tail position")
    ]


def test_out_of_sight_beyond_the_tolerance_above_duration_fails_naming_test_phase_and_group(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ConsolidationInputError,
        match=r"^Out of Sight exceeds Duration for test T901, Observer phase 3 \(ME F3\), "
        r"Tail position: 101(\.0)? s against 100(\.0)? s\.$",
    ):
        _consolidate(tmp_path, {_TAIL: 0, _OOS_TAIL: lambda p: 101 if p == 3 else 20})


def test_a_behaviour_longer_than_its_visible_time_fails_naming_it(tmp_path: Path) -> None:
    with pytest.raises(
        ConsolidationInputError,
        match=rf"^{re.escape(_TAIL)} lasts longer than its visible time for test T901, "
        r"Observer phase 3 \(ME F3\): ",
    ):
        _consolidate(tmp_path, {_TAIL: lambda p: 90 if p == 3 else 20, _OOS_TAIL: 20})


def test_a_count_without_visible_time_fails_naming_it(tmp_path: Path) -> None:
    with pytest.raises(
        ConsolidationInputError,
        match=rf"^{re.escape(_TAIL_COUNT)} is above 0 without visible time for test T901, "
        r"Observer phase 3 \(ME F3\)",
    ):
        _consolidate(
            tmp_path,
            {_TAIL_COUNT: lambda p: 2 if p == 3 else 0, _OOS_TAIL: lambda p: 100 if p == 3 else 20},
        )


def test_a_level_sums_before_it_divides(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {_TAIL: lambda p: {1: 20, 2: 30}[p], _OOS_TAIL: lambda p: {1: 10, 2: 50}[p]},
        duration=lambda p: {1: 100, 2: 200}[p],
        phases=[1, 2],
    )

    value = _result_rows(result, "with_owner")["T901"][_TAIL]
    assert value == pytest.approx(50 / 240)
    # Not the mean of the two phase ratios.
    assert value != pytest.approx((20 / 90 + 30 / 150) / 2)


def test_each_level_uses_only_its_own_phases_and_never_f8(tmp_path: Path) -> None:
    # 10 s in every ME phase, 30 s in every ZE phase, and all of F8.
    result = _consolidate(tmp_path, {_PANTING: lambda p: 100 if p == 8 else 10 if p <= 7 else 30})

    expected = {
        "with_owner": (0.10, _ME),
        "without_owner": (0.30, _ZE),
        "combined": (0.20, f"{_ME}, {_ZE}"),
    }
    for sheet, (value, phases_used) in expected.items():
        row = _result_rows(result, sheet)["T901"]
        assert row[_PANTING] == pytest.approx(value)
        assert (row["phases_used"], row["missing_phases"], row["status"]) == (
            phases_used,
            None,
            "ok",
        )


@pytest.mark.parametrize("out_of_sight", [100, 99.9999])
def test_a_level_without_visible_time_is_blank_even_with_behaviour_zero(
    tmp_path: Path, out_of_sight: float
) -> None:
    """Summed Out of Sight equal to summed Duration, or leaving at most
    0.001 s (7 x 0.0001 s), is no visible time."""
    result = _consolidate(
        tmp_path,
        {_TAIL: 0, _PANTING: 20, _OOS_TAIL: lambda p: out_of_sight if p <= 7 else 20},
    )

    row = _result_rows(result, "with_owner")["T901"]
    assert row[_TAIL] is None
    assert _details(result, "with_owner", _TAIL)[None]["status"] == "no_visible_time"
    # Only the tail group is affected, and only where it was out of sight.
    assert row[_PANTING] == pytest.approx(0.20)
    assert _result_rows(result, "without_owner")["T901"][_TAIL] == 0


def test_missing_phases_are_listed_and_a_level_without_phases_has_no_row(tmp_path: Path) -> None:
    result = _consolidate(tmp_path, {_PANTING: 20}, phases=[1, 2, 4, 5, 6, 7])

    with_owner = _result_rows(result, "with_owner")["T901"]
    assert with_owner[_PANTING] == pytest.approx(0.20)
    assert (with_owner["missing_phases"], with_owner["status"]) == ("ME F3", "incomplete")
    assert with_owner["phases_used"] == "ME F1, ME F2, ME F4, ME F5, ME F6, ME F7"
    assert _result_rows(result, "without_owner") == {}
    combined = _result_rows(result, "combined")["T901"]
    assert (combined["missing_phases"], combined["status"]) == (f"ME F3, {_ZE}", "incomplete")
    no_phases = [w for w in _sheet_rows(result, "warnings") if w["type"] == "no_phases"]
    assert [(w["test_id"], w["variabele"]) for w in no_phases] == [("T901", "without_owner")]


def test_a_test_with_only_owner_departure_gets_no_rows_but_warnings(tmp_path: Path) -> None:
    input_path = write_observer_export(
        tmp_path / "input.xlsx", {_PANTING: 20}, tests={"T901": "D901", "T902": "D902"}
    )
    workbook = openpyxl.load_workbook(input_path)
    results = workbook["Results"]
    # Rows 17-31 are T902's phases 1-15: keep only its F8 (row 24).
    for row in sorted([*range(17, 24), *range(25, 32)], reverse=True):
        results.delete_rows(row)
    workbook.save(input_path)
    output_path = tmp_path / "result.xlsx"

    ObserverConsolidationRunner().run(input_path=input_path, output_path=output_path)

    for sheet in _RESULT_SHEETS:
        assert list(_result_rows(output_path, sheet)) == ["T901"]
    assert {test_id for test_id, _ in _phase_rows(output_path)} == {"T901"}
    no_phases = [w for w in _sheet_rows(output_path, "warnings") if w["type"] == "no_phases"]
    assert [(w["test_id"], w["variabele"]) for w in no_phases] == [
        ("T902", sheet) for sheet in _RESULT_SHEETS
    ]


def test_denominators_explain_every_level_phase_and_group(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            _TAIL: lambda p: 0 if p == 2 else 20,
            _OOS_TAIL: lambda p: 100 if p == 2 else 20,
            _PANTING: 20,
        },
        phases=[1, 2, 9],
    )

    rows = _sheet_rows(result, "denominators")
    assert list(rows[0]) == [
        "level",
        "test_id",
        "observer_phase",
        "group",
        "duration_s",
        "out_of_sight_s",
        "visible_s",
        "phases_used",
        "status",
    ]
    by_key = {(r["level"], r["observer_phase"], r["group"]): r for r in rows}
    # Three phases and three aggregate levels, for two groups.
    assert len(by_key) == len(rows) == 12
    tail = "Tail position"
    assert [
        by_key[key][c]
        for key in [("per_phase", 2, tail), ("with_owner", None, tail)]
        for c in ("duration_s", "out_of_sight_s", "visible_s", "phases_used", "status")
    ] == [100, 100, 0, "ME F2", "no_visible_time", 200, 120, 80, "ME F1, ME F2", "ok"]
    vocalisation = by_key[("combined", None, "Vocalisation by the dog")]
    assert (vocalisation["duration_s"], vocalisation["out_of_sight_s"]) == (300, 0)
    assert vocalisation["phases_used"] == "ME F1, ME F2, ZE F1"


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


def test_an_export_with_only_the_raw_results_sheet_consolidates(tmp_path: Path) -> None:
    result = _consolidate(tmp_path, {"Total duration Panting <No Modifier>": 20})

    row = _result_rows(result, "with_owner")["T901"]
    assert row["Total duration Panting <No Modifier>"] == pytest.approx(0.20)


def test_a_dash_is_a_measured_zero(tmp_path: Path) -> None:
    result = _consolidate(
        tmp_path,
        {
            "Total duration Panting <No Modifier>": lambda phase: 20 if phase in (2, 4) else "-",
            "Total number Panting <No Modifier>": "-",
        },
    )

    row = _result_rows(result, "with_owner")["T901"]
    # 20 s in two of the seven ME phases: 40 / 700.
    assert row["Total duration Panting <No Modifier>"] == pytest.approx(40 / 700)
    assert row["Total number Panting <No Modifier>"] == 0
    status = {a["behaviour"]: a["status"] for a in _sheet_rows(result, "availability")}
    assert status["Panting"] == "exported_nonzero"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "Blank cell"),
        ("", "Blank cell"),
        ("n/a", "Not a number in cell"),
        ("12.5", "Not a number in cell"),
    ],
)
def test_a_blank_or_text_cell_fails_naming_the_cell(
    tmp_path: Path, value: str | None, message: str
) -> None:
    # Observer phase 3 of the only test is on row 4, Panting in column J.
    with pytest.raises(
        ConsolidationInputError,
        match=f"^{message} Results!J4 \\(Total duration Panting <No Modifier>\\)",
    ):
        _consolidate(
            tmp_path,
            {"Total duration Panting <No Modifier>": lambda phase: value if phase == 3 else 20},
        )


def test_a_blank_duration_fails_naming_the_cell(tmp_path: Path) -> None:
    input_path = write_observer_export(
        tmp_path / "input.xlsx", {"Total duration Panting <No Modifier>": 20}
    )
    workbook = openpyxl.load_workbook(input_path)
    workbook["Results"]["I2"] = None
    workbook.save(input_path)

    with pytest.raises(ConsolidationInputError, match=r"^Blank cell Results!I2 \(Duration\)"):
        ObserverConsolidationRunner().run(
            input_path=input_path, output_path=tmp_path / "result.xlsx"
        )


@pytest.mark.parametrize(
    ("cell", "header"),
    [("C2", "Observations"), ("D2", "Aanwezigheid FP in fase"), ("H2", "Test ID")],
)
def test_a_blank_structural_cell_fails_naming_the_cell(
    tmp_path: Path, cell: str, header: str
) -> None:
    input_path = write_observer_export(
        tmp_path / "input.xlsx", {"Total duration Panting <No Modifier>": 20}
    )
    workbook = openpyxl.load_workbook(input_path)
    workbook["Results"][cell] = None
    workbook.save(input_path)

    with pytest.raises(
        ConsolidationInputError, match=f"^Blank cell Results!{cell} \\({re.escape(header)}\\)$"
    ):
        ObserverConsolidationRunner().run(
            input_path=input_path, output_path=tmp_path / "result.xlsx"
        )


def test_an_empty_row_is_skipped_and_no_observations_at_all_fails(tmp_path: Path) -> None:
    input_path = write_observer_export(
        tmp_path / "input.xlsx", {"Total duration Panting <No Modifier>": 20}
    )
    workbook = openpyxl.load_workbook(input_path)
    results = workbook["Results"]
    results.insert_rows(3)
    workbook.save(input_path)
    output_path = tmp_path / "result.xlsx"

    ObserverConsolidationRunner().run(input_path=input_path, output_path=output_path)
    row = _result_rows(output_path, "with_owner")["T901"]
    assert row["Total duration Panting <No Modifier>"] == pytest.approx(0.20)

    results.delete_rows(2, results.max_row)
    workbook.save(input_path)
    with pytest.raises(ConsolidationInputError, match="^The Results sheet has no Observations"):
        ObserverConsolidationRunner().run(input_path=input_path, output_path=output_path)


@pytest.mark.parametrize(
    "column", ["Observations", "Test ID", "Dog ID", "Aanwezigheid FP in fase", "Duration"]
)
def test_a_missing_required_column_fails_naming_it(tmp_path: Path, column: str) -> None:
    input_path = write_observer_export(
        tmp_path / "input.xlsx", {"Total duration Panting <No Modifier>": 20}
    )
    workbook = openpyxl.load_workbook(input_path)
    results = workbook["Results"]
    results.delete_cols(next(c.column for c in results[1] if c.value == column))
    workbook.save(input_path)

    with pytest.raises(
        ConsolidationInputError, match=f"^Missing column in Results: {re.escape(column)}$"
    ):
        ObserverConsolidationRunner().run(
            input_path=input_path, output_path=tmp_path / "result.xlsx"
        )


def test_columns_observer_adds_that_are_never_read_do_not_block_an_upload(
    tmp_path: Path,
) -> None:
    """Fase, Geslacht hond and Observer's container columns may be missing
    or hold anything; Out of Sight counts and the owner-departure (F8) rows
    are never read either."""
    input_path = write_observer_export(
        tmp_path / "input.xlsx",
        {
            "Total duration Tail tucked <No Modifier>": lambda phase: "junk" if phase == 8 else 20,
            "Total duration Out of sight tail <No Modifier>": 20,
            "Total number Out of sight tail <No Modifier>": "junk",
        },
    )
    workbook = openpyxl.load_workbook(input_path)
    results = workbook["Results"]
    for header in ("Geslacht hond", "Fase"):
        results.delete_cols(next(c.column for c in results[1] if c.value == header))
    results["B2"] = None
    workbook.save(input_path)
    output_path = tmp_path / "result.xlsx"

    ObserverConsolidationRunner().run(input_path=input_path, output_path=output_path)

    row = _result_rows(output_path, "with_owner")["T901"]
    assert row["Total duration Tail tucked <No Modifier>"] == pytest.approx(0.25)


@pytest.mark.parametrize(
    "header",
    [
        # Corrects no exported group, so it is listed as ignored.
        "Total duration Out of sight attention <No Modifier>",
        "Total duration Standing bij TP <No Modifier>",
        "Total number First contact with TP <No Modifier>",
    ],
)
def test_every_exported_column_is_validated_even_when_it_never_enters_a_result(
    tmp_path: Path, header: str
) -> None:
    """One rule for every exported column (ticket #151): only `Total number
    Out of sight …` is never read."""
    with pytest.raises(
        ConsolidationInputError, match=f"^Blank cell Results!K4 \\({re.escape(header)}\\)"
    ):
        _consolidate(
            tmp_path,
            {
                "Total duration Panting <No Modifier>": 20,
                header: lambda phase: None if phase == 3 else 0,
            },
        )


@pytest.mark.parametrize(
    ("cell", "value", "message"),
    [
        # Row 2 is Observer phase 1, row 3 phase 2.
        ("D2", "False", r"owner-present flag .* contradicts the phase of Observation T901_\w+_F1"),
        ("C3", "T901_synthetic_F1", r"^Duplicate test and phase: T901 phase 1 "),
        ("E3", "D999", r"^Missing or conflicting Dog ID for test T901\.$"),
    ],
)
def test_contradicting_rows_fail_naming_the_test_or_phase(
    tmp_path: Path, cell: str, value: str, message: str
) -> None:
    input_path = write_observer_export(
        tmp_path / "input.xlsx", {"Total duration Panting <No Modifier>": 20}
    )
    workbook = openpyxl.load_workbook(input_path)
    workbook["Results"][cell] = value
    workbook.save(input_path)

    with pytest.raises(ConsolidationInputError, match=message):
        ObserverConsolidationRunner().run(
            input_path=input_path, output_path=tmp_path / "result.xlsx"
        )


def test_a_duplicate_header_fails_naming_the_column(tmp_path: Path) -> None:
    panting = "Total duration Panting <No Modifier>"
    input_path = write_observer_export(tmp_path / "input.xlsx", {panting: 20})
    workbook = openpyxl.load_workbook(input_path)
    results = workbook["Results"]
    # Column J copied into K, header and all.
    for row in range(1, results.max_row + 1):
        results.cell(row, 11).value = results.cell(row, 10).value
    workbook.save(input_path)

    with pytest.raises(
        ConsolidationInputError,
        match=f"^Duplicate column in Results: {re.escape(panting)} \\(columns J, K\\)$",
    ):
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
        match=r"Unknown behaviour in column J \(Total duration Sniffing air <No Modifier>\)",
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
        "phases_used",
        "missing_phases",
        "status",
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
