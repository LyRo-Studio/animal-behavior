"""The consolidation definition, generated from the printable Ethogram
(ticket #150).

For every behaviour: its code, Behaviour group, State or Event, allowed
modifier categories and Ethogram order. For every group: its Out of Sight
behaviour (or none) and the phases in which it is scored. Plus the
hand-maintained parts below: export-name aliases, modifier aliases, the
Scoring plan column mapping and the exclusions.

The importer reads only the generated `ethogram_definition.json`; the
Ethogram itself is never read at run time. After changing the Ethogram or
anything hand-maintained here, regenerate from the repository root and
commit both files:

    python -m consolidation.ethogram_definition

A backend test regenerates the definition and fails if it differs from the
committed one. A new Ethogram behaviour therefore always needs a deliberate
regeneration, and every check below runs again on it.
"""

import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

ETHOGRAM_PATH = Path(__file__).with_name("20241218_Printbaar ethogram.xlsx")
DEFINITION_PATH = Path(__file__).with_name("ethogram_definition.json")

# Observer export name -> Ethogram name, where the two differ.
BEHAVIOUR_ALIASES = {
    "Other general posture/locomotion of the dog": "Other",
    "Jumping without contact": "Jumping up without contact",
    "Attention to familiar person": "Attention to FP",
    "Attention to test person": "Attention to TP",
    "Obstructing test person": "Obstructing TP",
    # The Out of Sight name variant.
    "Out of sight position / locomotion": "Out of sight position/locomotion",
    # TP/FP protocol behaviours.
    "Standing bij TP": "Standing by TP",
    "Sitting (by TP or FP)": "Sitting (by TP or VP)",
    "Walking by test person": "Walking by TP",
    "Petting the dog": "Petting dog by TP",
    "Running hand trough hair by TP": "Running hand through hair by TP",
    "Arrival FP": "Arrival FP with dog",
    "Departure familiar person": "Departure FP",
    "Familiar person zero": "FP zero",
    "End of test familiar person": "End of test FP",
}

# Observer export modifier -> Ethogram modifier, where the two differ.
MODIFIER_ALIASES = {
    "Camera person orientation dog": "Orientation body dog to camera person",
    "Familiar person orientation dog": "Orientation body dog to familiar person",
    "Test person orientation dog": "Orientation body dog to test person",
    "Direction in which FP left": "Orientation body dog to direction in which FP left",
    "Familiar persoon": "Familiar person",
    "Familiar person contact": "Familiar person",
    "Test person contact": "Test person",
    "Against nothing": "nothing",
    "Against chair": "chair",
    "Against gate": "gate",
    "Against familiar person": "familiar person",
    "Against test person": "test person",
}

# A behaviour's modifier column on the Behaviours sheet -> the Modifiers
# sheet's name for it, where the two differ.
MODIFIER_CATEGORY_ALIASES = {"Sitting / standing AGAINST": "Against/on empty chair"}

# Blad1's Scoring plan columns for the groups that are only scored in some
# phases. Every other dog group is scored in F1-F7.
SCORING_PLAN_COLUMNS = {
    "localisation": ["Distance of the dog to TP", "Distance of the dog to FP"],
    "inner/outer zone": ["Location of the dog (inner/outer)"],
    "dog following": ["Dog following the TP"],
}
ALL_PHASES = [1, 2, 3, 4, 5, 6, 7]

EXCLUDED_BEHAVIOURS = {
    "First contact with TP": "A separate first-contact measure, only scored in ME F1.",
}
EXCLUDED_GROUPS = {
    "General posture/locomotion / behavior by TP or FP": (
        "TP/FP protocol behaviour, not dog behaviour."
    ),
}


def tidy(value: object) -> str:
    """`value` as text with runs of whitespace collapsed to one space."""
    return " ".join(str(value).split())


def normalise(value: object) -> str:
    """How names and modifiers are compared: whitespace and case ignored."""
    return tidy(value).casefold()


def _read_behaviours(workbook) -> tuple[list[dict], dict[str, str | None]]:
    behaviours, out_of_sight, group = [], {}, None
    for row in workbook["Behaviours"].iter_rows(min_row=2, values_only=True):
        name, code, _, kind, modifier_1, modifier_2 = (row + (None,) * 6)[:6]
        if not name:
            continue
        name = tidy(name)
        if not code:
            group = name
            out_of_sight[group] = None
            continue
        if group is None:
            raise ValueError(f"Ethogram behaviour outside any group: {name}")
        if str(kind).strip() not in {"State", "Event"}:
            raise ValueError(f"Ethogram behaviour is neither State nor Event: {name}")
        if name.startswith("Out of sight"):
            if out_of_sight[group] is not None:
                raise ValueError(
                    f"Ethogram group has two Out of Sight behaviours: {group}"
                )
            out_of_sight[group] = name
        behaviours.append(
            {
                "name": name,
                "code": str(code).strip(),
                "group": group,
                "kind": str(kind).strip(),
                "modifier_categories": [tidy(m) for m in (modifier_1, modifier_2) if m],
                "order": len(behaviours) + 1,
            }
        )
    return behaviours, out_of_sight


def _read_modifier_categories(workbook) -> dict[str, list[str]]:
    categories = {}
    for row in workbook["Modifiers"].iter_rows(min_row=2, values_only=True):
        if row[0] and row[2]:
            categories[tidy(row[0])] = [
                tidy(line.strip().removeprefix("- "))
                for line in str(row[2]).splitlines()
                if line.strip()
            ]
    return categories


def _read_scored_phases(workbook) -> dict[str, list[int]]:
    """Scored F1-F7 per Blad1 column in SCORING_PLAN_COLUMNS, identical for
    both Conditions (F8 never counts)."""
    rows = list(workbook["Blad1"].iter_rows(values_only=True))
    header_index = next(i for i, row in enumerate(rows) if row[0] == "Fases")
    header = [normalise(value) if value else None for value in rows[header_index]]
    scored = {}
    for column in SCORING_PLAN_COLUMNS:
        index = header.index(normalise(column))
        per_condition = {"ME": [], "ZE": []}
        for row in rows[header_index + 1 :]:
            condition, _, phase = str(row[0]).partition("_F")
            if condition in per_condition and phase.isdigit() and int(phase) <= 7:
                if normalise(row[index] or "") == "x":
                    per_condition[condition].append(int(phase))
        if per_condition["ME"] != per_condition["ZE"] or not per_condition["ME"]:
            raise ValueError(f"Blad1 scores {column!r} differently for ME and ZE.")
        scored[column] = per_condition["ME"]
    return scored


def build(ethogram_path: Path) -> dict:
    workbook = load_workbook(ethogram_path, data_only=True)
    try:
        behaviours, out_of_sight = _read_behaviours(workbook)
        categories = _read_modifier_categories(workbook)
        scored = _read_scored_phases(workbook)
    finally:
        workbook.close()

    by_name = {normalise(b["name"]): b for b in behaviours}
    if len(by_name) != len(behaviours):
        raise ValueError("Ethogram behaviour names are not unique.")
    for alias, target in BEHAVIOUR_ALIASES.items():
        if normalise(target) not in by_name:
            raise ValueError(f"Behaviour alias target is not in the Ethogram: {target}")
        if normalise(alias) in by_name:
            raise ValueError(f"Behaviour alias is itself an Ethogram name: {alias}")
    for name in EXCLUDED_BEHAVIOURS:
        if normalise(name) not in by_name:
            raise ValueError(f"Excluded behaviour is not in the Ethogram: {name}")

    category_names = {normalise(name): name for name in categories}
    for alias, target in MODIFIER_CATEGORY_ALIASES.items():
        category_names[normalise(alias)] = category_names[normalise(target)]
    for behaviour in behaviours:
        try:
            behaviour["modifier_categories"] = [
                category_names[normalise(m)] for m in behaviour["modifier_categories"]
            ]
        except KeyError as exc:
            raise ValueError(
                f"Unknown modifier category for {behaviour['name']}: {exc.args[0]}"
            ) from None
    values = {normalise(v) for category in categories.values() for v in category}
    for alias, target in MODIFIER_ALIASES.items():
        if normalise(target) not in values:
            raise ValueError(f"Modifier alias target is not in the Ethogram: {target}")

    phases_by_group = {
        group: scored[column]
        for column, groups in SCORING_PLAN_COLUMNS.items()
        for group in groups
    }
    for group in list(phases_by_group) + list(EXCLUDED_GROUPS):
        if group not in out_of_sight:
            raise ValueError(f"Group is not in the Ethogram: {group}")
    groups = [
        {
            "name": group,
            "out_of_sight": oos,
            "scored_phases": None
            if group in EXCLUDED_GROUPS
            else phases_by_group.get(group, ALL_PHASES),
            "excluded": EXCLUDED_GROUPS.get(group),
        }
        for group, oos in out_of_sight.items()
    ]
    return {
        "ethogram": ethogram_path.name,
        "ethogram_sha256": hashlib.sha256(ethogram_path.read_bytes()).hexdigest(),
        "groups": groups,
        "behaviours": behaviours,
        "modifier_categories": categories,
        "behaviour_aliases": BEHAVIOUR_ALIASES,
        "modifier_aliases": MODIFIER_ALIASES,
        "excluded_behaviours": EXCLUDED_BEHAVIOURS,
    }


def generate(ethogram_path: Path) -> str:
    """The definition file's exact text for `ethogram_path`."""
    return json.dumps(build(ethogram_path), indent=2, ensure_ascii=False) + "\n"


def load() -> dict:
    """The committed definition, as the importer uses it."""
    return json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    DEFINITION_PATH.write_text(generate(ETHOGRAM_PATH), encoding="utf-8")
    print(DEFINITION_PATH)
