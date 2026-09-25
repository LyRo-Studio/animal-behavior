"""Small synthetic Observer exports for the consolidation runner's tests
(ticket #150): only the columns a test is about, with values chosen so the
expected results can be worked out by hand.

Written in the shape the importer reads today: `Results (2)`, a raw
`Results` copy holding the same values, and `Results (REL)`'s header row
(no formulas). Out of Sight duration headers get the yellow fill the
importer checks for.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

# Every Observer phase: ME 1-7, owner departure 8, ZE 9-15.
OBSERVER_PHASES = range(1, 16)

ColumnValue = float | str | Callable[[int], float | str]

_YELLOW = PatternFill("solid", fgColor="FFFF00")


def write_observer_export(
    path: Path,
    columns: Mapping[str, ColumnValue],
    *,
    tests: Mapping[str, str] | None = None,
    duration: float = 100.0,
) -> Path:
    """Write an export with one row per test and Observer phase.

    `columns` maps each `Total duration …`/`Total number …` header to its
    value in every phase, or to a function of the Observer phase number.
    `tests` maps Test ID to Dog ID (default: T901 -> D901). Every phase
    lasts `duration` seconds.
    """
    tests = tests or {"T901": "D901"}
    header = [
        "Observations",
        "Aanwezigheid FP in fase",
        "Dog ID",
        "Fase",
        "Geslacht hond",
        "Test ID",
        "Duration",
        *columns,
    ]
    rows = []
    for test_id, dog_id in tests.items():
        for phase in OBSERVER_PHASES:
            values = [value(phase) if callable(value) else value for value in columns.values()]
            rows.append(
                [
                    f"{test_id}_synthetic_F{phase}",
                    "true" if phase <= 7 else "false",
                    dog_id,
                    phase if phase <= 8 else phase - 8,
                    "M",
                    test_id,
                    duration,
                    *values,
                ]
            )

    workbook = Workbook()
    source = workbook.active
    source.title = "Results (2)"
    raw = workbook.create_sheet("Results")
    rel = workbook.create_sheet("Results (REL)")
    for sheet in (source, raw):
        sheet.append(header)
        for row in rows:
            sheet.append(row)
    rel.append(header)
    for cell in source[1]:
        if str(cell.value).startswith("Total duration Out of sight"):
            cell.fill = _YELLOW
    workbook.save(path)
    return path
