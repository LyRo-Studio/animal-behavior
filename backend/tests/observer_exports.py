"""Small synthetic Observer exports for the consolidation runner's tests
(ticket #150): only the columns a test is about, with values chosen so the
expected results can be worked out by hand.

Written the way Observer exports them (ticket #151): a single raw `Results`
sheet, led by Observer's own container columns.
"""

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from openpyxl import Workbook

# Every Observer phase: ME 1-7, owner departure 8, ZE 9-15.
OBSERVER_PHASES = range(1, 16)

ColumnValue = float | str | None | Callable[[int], float | str | None]


def write_observer_export(
    path: Path,
    columns: Mapping[str, ColumnValue],
    *,
    tests: Mapping[str, str] | None = None,
    duration: float | Callable[[int], float] = 100.0,
    phases: Iterable[int] = OBSERVER_PHASES,
) -> Path:
    """Write an export with one row per test and Observer phase.

    `columns` maps each `Total duration …`/`Total number …` header to its
    value in every phase, or to a function of the Observer phase number.
    `tests` maps Test ID to Dog ID (default: T901 -> D901). Every phase
    lasts `duration` seconds (or a function of the phase), and only
    `phases` are exported (default: all 15).
    """
    tests = tests or {"T901": "D901"}
    header = [
        "Independent Variables Statistics Behaviors Modifiers",
        "Result Containers",
        "Observations",
        "Aanwezigheid FP in fase",
        "Dog ID",
        "Fase",
        "Geslacht hond",
        "Test ID",
        "Duration",
        *columns,
    ]
    workbook = Workbook()
    results = workbook.active
    results.title = "Results"
    results.append(header)
    phases = list(phases)
    for test_id, dog_id in tests.items():
        for phase in phases:
            values = [value(phase) if callable(value) else value for value in columns.values()]
            results.append(
                [
                    "",
                    "Results",
                    f"{test_id}_synthetic_F{phase}",
                    "True" if phase <= 7 else "False",
                    dog_id,
                    str(phase if phase <= 8 else phase - 8),
                    "M",
                    test_id,
                    duration(phase) if callable(duration) else duration,
                    *values,
                ]
            )
    workbook.save(path)
    return path
