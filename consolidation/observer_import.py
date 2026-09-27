"""Import van het ruwe Results-blad van de Observer-export; behoud iedere modifiercombinatie."""
from pathlib import Path
import hashlib
import math
import re
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from consolidation import ethogram_definition
from consolidation.ethogram_definition import normalise
from consolidation.consolidatie import (consolideer, fase_in_conditie, fase_label, fase_labels,
                                        observer_fases)

# Welke Behaviour group en Out of Sight bij een kolom horen, komt uit de
# definitie die uit het ethogram gegenereerd is (ticket #150), nooit uit de
# kolompositie in de export.
DEFINITIE = ethogram_definition.load()
# Het enige blad dat gelezen wordt (ticket #151): de export zoals Observer
# hem maakt. Fase, Geslacht hond en Observers containerkolommen worden nooit
# gelezen.
BLAD = "Results"
EIGENAAR_AANWEZIG = "Aanwezigheid FP in fase"
VERPLICHTE_KOLOMMEN = ["Observations", "Test ID", "Dog ID", EIGENAAR_AANWEZIG, "Duration"]
TOLERANTIE_S = 0.001  # Observer exporteert seconden met een beperkte decimale precisie.
ME, ZE = tuple(range(1, 8)), tuple(range(9, 16))
# De vier Consolidation levels (ticket #152), elk met een eigen resultaatblad.
# Vertrek eigenaar (fase 8) telt in geen enkel niveau.
NIVEAUS = {
    "per_phase": {"fases": ME + ZE, "per_fase": True},
    "with_owner": {"fases": ME},
    "without_owner": {"fases": ZE},
    "combined": {"fases": ME + ZE},
}
GEBRUIKTE_FASES = {fase for niveau in NIVEAUS.values() for fase in niveau["fases"]}


def gescoorde_fases(definitie):
    """{groep: Observer-fases} volgens het Scoring plan: F1-F7 van beide
    Conditions, ME F3 = fase 3 en ZE F3 = fase 11."""
    return {g["name"]: {f for fase in g["scored_phases"] for f in observer_fases(fase)}
            for g in definitie["groups"] if not g["excluded"]}


def fase_uit_observatie(value):
    match = re.fullmatch(r"(T\d+)_.+_F(\d+)", str(value))
    if not match:
        raise ValueError(f"Malformed Observation name {value!r}: expected <Test ID>_..._F<phase>.")
    phase = int(match[2])
    if not 1 <= phase <= 15:
        raise ValueError(f"Phase outside 1-15 in Observation name {value!r}.")
    return match[1], phase


def _bekende_gedragingen(definitie):
    """Iedere ethogramnaam en exportalias (genormaliseerd), langste eerst."""
    per_naam = {b["name"]: b for b in definitie["behaviours"]}
    namen = {normalise(b["name"]): b for b in definitie["behaviours"]}
    namen.update({normalise(alias): per_naam[doel]
                  for alias, doel in definitie["behaviour_aliases"].items()})
    return sorted(namen.items(), key=lambda item: len(item[0]), reverse=True)


def splits_kop(header, bekend):
    """'Total duration Tail tucked <No Modifier>' -> ('duur', gedrag, '<No Modifier>').

    Het gedrag is de langste bekende naam waarmee de kop begint, gevolgd door
    de modifier. None als geen enkele bekende naam past.
    """
    kind = "duur" if header.startswith("Total duration ") else "aantal"
    rest = ethogram_definition.tidy(header.removeprefix("Total duration ").removeprefix("Total number "))
    for naam, gedrag in bekend:
        if rest.casefold().startswith(naam + " "):
            return kind, gedrag, rest[len(naam) + 1:]
    return None


def modifier_toegestaan(modifier, gedrag, definitie):
    """Past iedere modifierwaarde in een eigen, door het ethogram toegestane categorie?"""
    if modifier == "<No Modifier>":
        return True
    categorieen = [{normalise(v) for v in definitie["modifier_categories"][c]}
                   for c in gedrag["modifier_categories"]]
    aliassen = {normalise(k): normalise(v) for k, v in definitie["modifier_aliases"].items()}
    waarden = [aliassen.get(normalise(w), normalise(w)) for w in modifier.split(";")]
    kandidaten = [{i for i, c in enumerate(categorieen) if w in c} for w in waarden]
    if len(waarden) > len(categorieen) or not all(kandidaten):
        return False
    return len(waarden) < 2 or any(a != b for a in kandidaten[0] for b in kandidaten[1])


def _deel_kolommen_in(headers, source, definitie):
    """Deel iedere Total duration/number-kolom in volgens de definitie.

    Een kolom zonder bekend gedrag faalt. De Out of Sight van een groep is
    alleen verplicht wanneer minstens een gedrag van die groep geexporteerd is.
    `ongelezen` zijn de `Total number Out of sight …`-kolommen: de enige
    Total-kolommen waarvan de cellen nooit gelezen (en dus nooit gecontroleerd)
    worden.
    """
    bekend = _bekende_gedragingen(definitie)
    groepen = {g["name"]: g for g in definitie["groups"]}
    mapping, excluded, notes = [], [], []
    oos_kolommen, oos_kandidaten, event_duur, per_gedrag = {}, [], [], {}
    for header, c in headers.items():
        if not header.startswith(("Total duration ", "Total number ")):
            continue
        letter = source.cell(1, c).column_letter
        gesplitst = splits_kop(header, bekend)
        if gesplitst is None:
            raise ValueError(
                f"Unknown behaviour in column {letter} ({header}): it is not in the Ethogram "
                "definition. Add it to the Ethogram and regenerate the definition.")
        kind, gedrag, modifier = gesplitst
        groep = groepen[gedrag["group"]]
        per_gedrag.setdefault(gedrag["name"], []).append(c)
        rij = {"bronkolom": letter, "bronkop": header}
        if gedrag["name"] == groep["out_of_sight"]:
            if kind == "duur" and modifier == "<No Modifier>":
                oos_kolommen[groep["name"]] = c
            oos_kandidaten.append((rij, groep["name"], c, kind))
        elif groep["excluded"] or gedrag["name"] in definitie["excluded_behaviours"]:
            excluded.append({**rij, "reden": groep["excluded"]
                             or definitie["excluded_behaviours"][gedrag["name"]]})
        elif gedrag["kind"] == "Event" and kind == "duur":
            event_duur.append((header, c))
            excluded.append({**rij, "reden": "Event duration: not a meaningful measure; "
                             "only the Event's frequency is reported."})
        else:
            if not modifier_toegestaan(modifier, gedrag, definitie):
                notes.append(_melding("unknown_modifier", header,
                                      f"The Ethogram doesn't allow modifier {modifier!r} for "
                                      f"{gedrag['name']}; its values are kept."))
            mapping.append({"gedrag": header, "basisgedrag": gedrag["name"],
                            "modifier": modifier, "groep": groep["name"], "meettype": kind,
                            "code": gedrag["code"], "state_event": gedrag["kind"],
                            "out_of_sight": groep["out_of_sight"],
                            "gescoorde_fases": _fase_reeks(groep["scored_phases"]),
                            "volgorde": gedrag["order"], "bronkolom": letter, "kolomnummer": c})
    if not mapping:
        raise ValueError("The export has no dog behaviour columns to consolidate.")
    gebruikt = {m["groep"] for m in mapping}
    for naam in sorted(gebruikt):
        oos = groepen[naam]["out_of_sight"]
        if oos and naam not in oos_kolommen:
            raise ValueError(
                f"Missing Out of Sight column for {naam}: the export has behaviours of this "
                f"group but no 'Total duration {oos} <No Modifier>' column.")
    ongelezen = set()
    for rij, groep, c, kind in oos_kandidaten:
        if kind == "aantal":
            reden = "Out of Sight count: never read."
            ongelezen.add(c)
        elif oos_kolommen.get(groep) != c:
            reden = "Out of Sight modifier column: never used."
        elif groep in gebruikt:
            reden = "Out of Sight correction column: its duration corrects the group's denominator."
        else:
            reden = "Out of Sight column ignored: none of its group's behaviours were exported."
        excluded.append({**rij, "reden": reden})
    oos_per_groep = {naam: oos_kolommen.get(naam) for naam in gebruikt}
    for m in mapping:
        oc = oos_per_groep[m["groep"]]
        m["oos_kolom"] = source.cell(1, oc).column_letter if oc else None
    # Ethogramvolgorde, dan de volgorde waarin de modifiers geexporteerd zijn,
    # dan duur voor aantal (ticket #154).
    eerste_kolom = {}
    for m in mapping:
        sleutel = (m["basisgedrag"], m["modifier"])
        eerste_kolom[sleutel] = min(eerste_kolom.get(sleutel, m["kolomnummer"]), m["kolomnummer"])
    mapping.sort(key=lambda m: (m["volgorde"], eerste_kolom[(m["basisgedrag"], m["modifier"])],
                                m["meettype"] != "duur"))
    return mapping, excluded, notes, oos_per_groep, event_duur, per_gedrag, ongelezen


def _fase_reeks(fases):
    """[1, 2, ..., 7] -> 'F1-F7'; [1, 3, 6] -> 'F1, F3, F6'."""
    if fases == ethogram_definition.ALL_PHASES:
        return "F1-F7"
    return ", ".join(f"F{fase}" for fase in fases)


def _beschikbaarheid(per_gedrag, niet_nul, definitie):
    """Ieder hondgedrag uit het ethogram: niet, met alleen nullen of met waarden
    geexporteerd, beoordeeld over de gescoorde fases van zijn groep."""
    groepen = {g["name"]: g for g in definitie["groups"]}
    rijen = []
    for gedrag in definitie["behaviours"]:
        if groepen[gedrag["group"]]["excluded"]:
            continue
        kolommen = per_gedrag.get(gedrag["name"], [])
        status = ("not_exported" if not kolommen else
                  "exported_nonzero" if niet_nul & set(kolommen) else "exported_all_zero")
        rijen.append({"behaviour": gedrag["name"], "code": gedrag["code"],
                      "group": gedrag["group"], "status": status,
                      "exported_columns": len(kolommen)})
    return pd.DataFrame(rijen)


MELDING_KOLOMMEN = ["type", "test_id", "observer_phase", "subject", "message"]


def _melding(soort, variabele, melding, test_id=None, fase=None):
    """Een regel van het blad `warnings`: altijd met een uitleg (`message`)."""
    return dict(zip(MELDING_KOLOMMEN, [soort, test_id, fase, variabele, melding], strict=True))


def _is_meetkolom(header):
    return header == "Duration" or header.startswith(("Total duration ", "Total number "))


def _plaats(cell, header):
    return f"{BLAD}!{cell.coordinate} ({header})"


def _ingevuld(cell, header):
    """De waarde van een verplichte cel; een blanke cel faalt en noemt de cel."""
    value = cell.value
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Blank cell {_plaats(cell, header)}")
    return value


def _meetwaarde(cell, header):
    """Een meetcel als seconden of aantal. Observers '-' is een gemeten nul.

    Een blanke of niet-numerieke cel faalt en noemt de cel.
    """
    value = cell.value
    if isinstance(value, str) and value.strip() == "-":
        return 0.0
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Blank cell {_plaats(cell, header)}: every exported cell must hold "
                         "a number, or '-' for a measured zero.")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Not a number in cell {_plaats(cell, header)}: {str(value)[:40]!r}.")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid value in cell {_plaats(cell, header)}: {value!r}; "
                         "measurements are finite and never negative.")
    return float(value)


def _kolommen(blad):
    """{kop: kolomnummer} van alle kolommen die gelezen worden.

    Een dubbele verplichte of Total-kolom faalt: welke van de twee telt, is
    niet te zeggen. Andere kolommen worden nooit gelezen, dus hun koppen doen
    er niet toe.
    """
    per_kop = {}
    for cell in blad[1]:
        header = "" if cell.value is None else str(cell.value)
        if header in VERPLICHTE_KOLOMMEN or _is_meetkolom(header):
            per_kop.setdefault(header, []).append(cell)
    for header, cells in per_kop.items():
        if len(cells) > 1:
            letters = ", ".join(c.column_letter for c in cells)
            raise ValueError(f"Duplicate column in {BLAD}: {header} (columns {letters})")
    for header in VERPLICHTE_KOLOMMEN:
        if header not in per_kop:
            raise ValueError(f"Missing column in {BLAD}: {header}")
    return {header: cell.column for header, (cell,) in per_kop.items()}


def lees_observer(path):
    """Lees iedere fase van ieder niveau uit het ruwe Results-blad van de Observer-export.

    Alleen dat blad wordt gelezen (ticket #151); handgemaakte werkkopieen zijn
    niet nodig. '-' is een gemeten nul; een blanke of niet-numerieke meetcel
    faalt. Fase wordt uit Observations gehaald: de bronkolom Fase nummert
    ZE opnieuw van 1 tot 7 en wordt nooit gelezen.
    """
    try:
        wb = load_workbook(path, data_only=False, keep_links=False)
    except KeyError as exc:
        # openpyxl meldt sommige ontbrekende OOXML-onderdelen als KeyError.
        raise ValueError("The uploaded file is not a valid Excel workbook.") from exc
    try:
        if BLAD not in wb.sheetnames:
            raise ValueError(f"Missing sheet: {BLAD}")
        source = wb[BLAD]
        headers = _kolommen(source)
        (mapping, excluded, notes, oos_columns, event_duur, per_gedrag,
         ongelezen) = _deel_kolommen_in(headers, source, DEFINITIE)
        # Duration en iedere Total-kolom, behalve de Out of Sight-aantallen.
        te_lezen = {header: c for header, c in headers.items()
                    if _is_meetkolom(header) and c not in ongelezen}
        definitions = pd.DataFrame(mapping)
        phases, behaviors, invisibility, selection = [], [], [], []
        # Een waarde telt voor `availability` alleen in een gescoorde fase van de
        # groep: daarbuiten komt hij in geen enkel resultaat.
        gescoord = gescoorde_fases(DEFINITIE)
        groep_van = {b["name"]: b["group"] for b in DEFINITIE["behaviours"]}
        gescoord_per_kolom = {c: gescoord[groep_van[naam]] for naam, kolommen in per_gedrag.items()
                              for c in kolommen if groep_van[naam] in gescoord}
        niet_nul = set()
        for row in range(2, source.max_row + 1):
            if all(cell.value is None for cell in source[row]):
                continue  # Een lege rij is geen observatie.
            observation, test, owner_flag = (
                _ingevuld(source.cell(row, headers[h]), h)
                for h in ("Observations", "Test ID", EIGENAAR_AANWEZIG))
            label_test, phase = fase_uit_observatie(observation)
            dog = source.cell(row, headers["Dog ID"]).value
            owner_flag = str(owner_flag).lower()
            if owner_flag not in {"true", "false"} or (owner_flag == "true") != (phase <= 7):
                raise ValueError(
                    f"The owner-present flag ({EIGENAAR_AANWEZIG}) contradicts the phase of "
                    f"Observation {observation}: it must be True exactly for phases 1-7.")
            if test != label_test:
                notes.append(_melding("observation_name", observation,
                                      f"The Observation name's Test ID ({label_test}) differs "
                                      f"from the Test ID column; {test} is used.", test, phase))
            condition, phase_in_condition = fase_in_conditie(phase)
            selection.append({"observatie": observation, "test_id": test, "dog_id": dog,
                              "fase": phase, "condition": condition, "phase": phase_in_condition,
                              "met_eigenaar": owner_flag == "true",
                              "opgenomen": phase in GEBRUIKTE_FASES, "bronrij": row})
            if phase not in GEBRUIKTE_FASES:
                continue
            # Controleer iedere gelezen kolom, ook uitgesloten protocolkolommen.
            waarden = {}
            for header, c in te_lezen.items():
                waarden[c] = _meetwaarde(source.cell(row, c), header)
                if waarden[c] > 0 and phase in gescoord_per_kolom.get(c, GEBRUIKTE_FASES):
                    niet_nul.add(c)
            phases.append({"test_id": test, "fase": phase, "duur_s": waarden[headers["Duration"]]})
            for group, oc in oos_columns.items():
                invisibility.append({"test_id": test, "fase": phase, "groep": group,
                                     "duur_s": waarden[oc] if oc else 0.0})
            for m in mapping:
                value = waarden[m["kolomnummer"]]
                behaviors.append({"test_id": test, "fase": phase, "gedrag": m["gedrag"],
                                  "duur_s": value if m["meettype"] == "duur" else float("nan"),
                                  "aantal": value if m["meettype"] == "aantal" else float("nan")})
        for header, c in event_duur:
            if c in niet_nul:
                notes.append(_melding("event_duration", header,
                                      "This Event has a nonzero duration; Observer's coding may "
                                      "have changed. Only its frequency is reported."))
        selected = pd.DataFrame(selection)
        if selected.empty:
            raise ValueError(f"The {BLAD} sheet has no Observations.")
        if not phases:
            raise ValueError(f"The {BLAD} sheet has no Observations in Observer phases 1-7 or 9-15.")
        dubbel = selected[selected.duplicated(["test_id", "fase"])]
        if not dubbel.empty:
            raise ValueError(f"Duplicate test and phase: {dubbel.test_id.iloc[0]} phase "
                             f"{dubbel.fase.iloc[0]} appears more than once.")
        dogs = selected[["test_id", "dog_id"]].drop_duplicates()
        fout = dogs[dogs.test_id.duplicated(keep=False) | dogs.dog_id.isna()].test_id.unique()
        if len(fout):
            raise ValueError(f"Missing or conflicting Dog ID for test {', '.join(map(str, fout))}.")
        return {"invoer": {"fases": pd.DataFrame(phases),
                           "gedrag_groepen": definitions[["gedrag", "groep", "meettype"]],
                           "gedragingen": pd.DataFrame(behaviors),
                           "out_of_sight": pd.DataFrame(invisibility),
                           "gescoorde_fases": gescoorde_fases(DEFINITIE)},
                "definities": definitions, "honden": dogs, "selectie": selected,
                "uitgesloten": pd.DataFrame(excluded),
                "meldingen": pd.DataFrame(notes, columns=MELDING_KOLOMMEN),
                "beschikbaarheid": _beschikbaarheid(per_gedrag, niet_nul, DEFINITIE)}
    finally:
        wb.close()


def _breed(resultaat, sleutel, variabelen):
    """Een rij per `sleutel`; duurkolommen bevatten fracties en aantal-kolommen
    frequenties per seconde, in de volgorde van `variabelen`."""
    if resultaat.empty:
        return pd.DataFrame(columns=[*sleutel, *variabelen])
    wide = resultaat.pivot(index=sleutel, columns="gedrag", values="geconsolideerde_waarde")
    wide = wide.reindex(columns=variabelen).reset_index()
    wide.columns.name = None
    return wide


def bereken_observer(bron):
    """Ieder niveau (NIVEAUS) uit een gelezen export: een breed resultaatblad
    per niveau, plus de lange tabellen en meldingen."""
    resultaat, detail, noemers = consolideer(**bron["invoer"], niveaus=NIVEAUS,
                                            tolerantie_s=TOLERANTIE_S)
    labels = bron["definities"][["gedrag", "basisgedrag", "modifier", "bronkolom", "oos_kolom"]]
    resultaat = resultaat.merge(labels, on="gedrag", validate="many_to_one").merge(
        bron["honden"], on="test_id", validate="many_to_one")
    resultaat["geconsolideerde_waarde"] = resultaat.fractie.where(
        resultaat.meettype == "duur", resultaat.frequentie_per_s)
    notes = bron["meldingen"].to_dict("records")
    for row in detail[~detail.gescoord & ((detail.gedrag_s > 0) | (detail.aantal > 0))].itertuples():
        waarde = f"{row.gedrag_s} s" if row.meettype == "duur" else f"{row.aantal:g} times"
        notes.append(_melding("not_scored", row.gedrag,
                              f"{row.groep} isn't scored in Observer phase {row.fase} "
                              f"({fase_label(row.fase)}) under the Scoring plan, but the export "
                              f"holds {waarde}; the value is left out of every result.",
                              row.test_id, row.fase))
    gescoord = detail[detail.gescoord]
    for row in gescoord[gescoord.gedrag_s > gescoord.zichtbaar_s].itertuples():
        notes.append(_melding("rounding", row.gedrag,
                              f"Behaviour duration {row.gedrag_s} s against visible time "
                              f"{row.zichtbaar_s} s: within the {TOLERANTIE_S} s export tolerance; "
                              "values kept unchanged.", row.test_id, row.fase))
    groepen = detail.drop_duplicates(["test_id", "fase", "groep"])
    for row in groepen[groepen.out_of_sight_s > groepen.fase_duur_s].itertuples():
        notes.append(_melding("rounding", row.groep,
                              f"Out of Sight {row.out_of_sight_s} s against Duration "
                              f"{row.fase_duur_s} s: within the {TOLERANTIE_S} s export tolerance, "
                              "so no visible time.", row.test_id, row.fase))
    for row in noemers[noemers.status == "no_phases"].itertuples():
        notes.append(_melding("no_phases", row.groep,
                              f"{row.test_id} has none of the phases in which {row.groep} is "
                              f"scored on {row.level} ({row.ontbrekende_fases}), so its values "
                              "there are blank.", row.test_id))
    variabelen = list(bron["definities"].gedrag)
    aanwezig = bron["invoer"]["fases"].groupby("test_id").fase.agg(set)
    # Alleen een fase waarin minstens een geexporteerde groep gescoord wordt, telt
    # voor phases_used, missing_phases en status.
    relevante_fases = set().union(*(bron["invoer"]["gescoorde_fases"][groep]
                                    for groep in set(bron["definities"].groep)))
    per_niveau = {}
    for niveau, config in NIVEAUS.items():
        van_niveau = resultaat[resultaat.level == niveau]
        if config.get("per_fase"):
            wide = _breed(van_niveau, ["test_id", "dog_id", "fase"], variabelen).rename(
                columns={"fase": "observer_phase"})
            conditie = wide.observer_phase.map(fase_in_conditie)
            wide.insert(3, "condition", conditie.str[0])
            wide.insert(4, "phase", conditie.str[1])
            per_niveau[niveau] = wide
            continue
        rijen = []
        for test in bron["honden"].itertuples():
            # Een test met alleen F8 heeft geen enkele gebruikte fase.
            gebruikt = aanwezig.get(test.test_id, set()) & set(config["fases"])
            if not gebruikt:
                notes.append(_melding("no_phases", niveau,
                                      f"{test.test_id} has none of the {niveau} phases, so it has "
                                      f"no row on {niveau}.", test.test_id))
                continue
            ontbrekend = set(config["fases"]) & relevante_fases - gebruikt
            rijen.append({"test_id": test.test_id, "dog_id": test.dog_id,
                          "phases_used": fase_labels(gebruikt & relevante_fases) or None,
                          "missing_phases": fase_labels(ontbrekend) or None,
                          "status": "incomplete" if ontbrekend else "ok"})
        kop = pd.DataFrame(rijen, columns=["test_id", "dog_id", "phases_used",
                                            "missing_phases", "status"])
        per_niveau[niveau] = kop.merge(_breed(van_niveau, ["test_id"], variabelen), on="test_id",
                                       how="left", validate="one_to_one")
    noemers = noemers.merge(bron["honden"], on="test_id", validate="many_to_one")
    return {"per_niveau": per_niveau, "details": resultaat, "noemers": noemers,
            "meldingen": pd.DataFrame(notes, columns=MELDING_KOLOMMEN)}


# Iedere Behaviour group krijgt een eigen kleur in de groepsrij van de resultaatbladen.
GROEPSKLEUREN = ["D9EAD3", "CFE2F3", "FFF2CC", "F4CCCC", "D9D2E9", "FCE5CD", "D0E0E3",
                 "EAD1DC", "C9DAF8", "F9CB9C", "E6E6E6"]
LEIDENDE_KOLOMMEN = 5  # test_id, dog_id en drie kolommen per niveau.
STATISTIEK = {"duur": "duration", "aantal": "count"}
WAARDE = {"duur": "fraction of visible time", "aantal": "frequency per visible second"}


def _readme(source_path):
    definitie_sha = hashlib.sha256(ethogram_definition.DEFINITION_PATH.read_bytes()).hexdigest()
    return [
        "CONSOLIDATION RESULT: relative behaviour durations and frequencies per dog, at every level, from one "
        "Observer export.",
        f"Source file: {Path(source_path).name}",
        f"Source SHA-256: {hashlib.sha256(Path(source_path).read_bytes()).hexdigest()}",
        f"Ethogram definition: {ethogram_definition.DEFINITION_PATH.name} (SHA-256 {definitie_sha}), generated "
        f"from {DEFINITIE['ethogram']} (SHA-256 {DEFINITIE['ethogram_sha256']}).",
        "Only the export's raw Results sheet is read, exactly as Observer produces it. The source file is never "
        "changed.",
        "",
        "LEVELS",
        "per_phase: every Observer phase on its own: 1-7 (ME F1-F7) and 9-15 (ZE F1-F7).",
        "with_owner: Observer phases 1-7 together (ME, the owner is present).",
        "without_owner: Observer phases 9-15 together (ZE, the owner is absent).",
        "combined: Observer phases 1-7 and 9-15 together. Never an average of with_owner and without_owner.",
        "Owner departure (Observer phase 8) never counts. The phase comes from the Observation name "
        "(..._F12); the owner-present flag must be True exactly for phases 1-7.",
        "",
        "RESULT SHEETS (per_phase, with_owner, without_owner, combined)",
        "per_phase: one row per test and Observer phase, led by test_id, dog_id, observer_phase, condition (ME/ZE) "
        "and phase (F1-F7).",
        "with_owner, without_owner, combined: one row per test, led by test_id, dog_id, phases_used, missing_phases "
        "and status (ok or incomplete). Only phases in which at least one exported group is scored count there. A "
        "test with none of a level's phases has no row on that level, with a no_phases warning.",
        "Row 1 names each column's Behaviour group; row 2 holds the exact Observer headers. Columns follow the "
        "Ethogram's order, then the export's modifier order, then duration before count.",
        "Every exported behaviour and modifier combination is its own column; none is summed into a base "
        "behaviour, and <No Modifier> is not a total.",
        "Total duration columns: relative duration = summed behaviour duration / visible time, as a fraction "
        "(0.25, not 25%). States only.",
        "Total number columns: frequency = summed count / visible time, per visible second. States and Events; an "
        "Event's duration is not reported.",
        "Visible time = summed Duration - summed Out of Sight duration of the behaviour's own Behaviour group. "
        "Everything is summed over the level's phases first and divided once, never an average of phase values.",
        "Groups without an Out of Sight behaviour (Vocalisation, Distance to TP, Distance to FP, Location, Dog "
        "following the TP) use the plain Duration. Zero states (Distance TP zero, Distance FP zero, Square zero, "
        "Following TP zero) are ordinary behaviours, never subtracted from it.",
        "Behaviour groups, Out of Sight and State/Event come from the Ethogram definition, never from where a "
        "column sits in the export. Stiffening up belongs to exploration/self-maintenance.",
        "",
        "STATUS (row status on the result sheets; value status in details and denominators)",
        "ok: calculated from every phase that counts.",
        "incomplete: calculated from the phases present; missing_phases lists the others.",
        f"no_visible_time: visible time of {TOLERANTIE_S} s or less. The value is blank: never 0 and never a "
        "division.",
        "not_scored: the Scoring plan doesn't score the group in this phase (per_phase only). The value is blank.",
        "no_phases: the test has none of the phases in which the group is scored on this level. The value is "
        "blank.",
        "A result cell is blank only for no_visible_time, not_scored or no_phases; a 0 is always a measured zero.",
        "",
        "SCORING PLAN",
        "Distance to TP, Distance to FP and Location are scored only in F1, F3 and F6 of each Condition; Dog "
        "following the TP only in F2, F4 and F7; every other group in F1-F7 (see variables: scored_phases).",
        "A group's sums use only its scored phases. A nonzero exported value in an unscored phase gives a "
        "not_scored warning and never enters a result.",
        "",
        "NOT EXPORTED, MEASURED ZERO AND BLANK",
        "A behaviour Observer didn't export has no result column. An exported cell holding 0 or Observer's '-' is "
        "a measured zero, and a result of 0 shows 0.",
        "not_exported: (availability) absent from the export, so absent from the results.",
        "exported_all_zero: (availability) exported, and 0 in every phase in which its group is scored.",
        "exported_nonzero: (availability) exported, with a value above 0 in a scored phase.",
        "",
        "CHECKS",
        "The upload fails, naming the column, cell, test or phase, for: a missing required column or sheet, a "
        "blank or non-numeric cell, an unknown behaviour, a missing Out of Sight column for an exported group, a "
        "duplicate test and phase, a malformed Observation name or a phase outside 1-15, an owner-present flag "
        "that contradicts the phase, a missing or conflicting Dog ID, Out of Sight above Duration, a behaviour "
        "longer than its visible time, or a count without visible time (the last two in scored phases only).",
        f"Observer rounds to {TOLERANTIE_S} s: a difference within that tolerance keeps the values and gives a "
        "rounding warning.",
        "Only a warning: rounding, a Test ID typo in the Observation name (the Test ID column is used), an "
        "unknown modifier (its values are kept), a nonzero Event duration, a nonzero value in an unscored phase, "
        "and a test or group without phases on a level.",
        "",
        "DIAGNOSTIC SHEETS",
        "details: every value of every level (level column): numerator (behaviour_duration_s or count), "
        "duration_s, out_of_sight_s, visible_s, fraction, percentage, frequency_per_s, frequency_per_min and "
        "status. observer_phase is set on per_phase rows only.",
        "denominators: duration_s, out_of_sight_s and visible_s per level, test, phase (per_phase only) and "
        "group, with the phases used, the group's scored phases the test lacks (missing_phases) and the status.",
        "variables: every result column: exact Observer header, behaviour, modifier, code, group, its Out of "
        "Sight, State/Event, statistic, what the value measures, scored phases and source columns.",
        "availability: every dog behaviour in the Ethogram, as not_exported, exported_all_zero or "
        "exported_nonzero.",
        "phase_selection: every source row of the export and the levels it was used in (none for phase 8).",
        "warnings: every warning, with its test, Observer phase, subject and message.",
        "excluded: every column that never enters a result, with the reason: Out of Sight columns, Event "
        "durations, First contact with TP and the TP/FP protocol behaviours.",
        "Never read: Fase, Geslacht hond, Observer's container columns and Total number Out of sight columns.",
    ]


def _groepsrij(ws, kolommen, groep_per_variabele):
    """Rij 1 van een resultaatblad: de Behaviour group boven zijn kolommen,
    samengevoegd en gekleurd."""
    kleuren = {g["name"]: GROEPSKLEUREN[i % len(GROEPSKLEUREN)]
               for i, g in enumerate(DEFINITIE["groups"])}
    start = LEIDENDE_KOLOMMEN + 1
    variabelen = list(kolommen)[LEIDENDE_KOLOMMEN:] + [None]
    for i in range(1, len(variabelen)):
        groep = groep_per_variabele[variabelen[i - 1]]
        if variabelen[i] is not None and groep_per_variabele[variabelen[i]] == groep:
            continue
        einde = LEIDENDE_KOLOMMEN + i
        cell = ws.cell(1, start, groep)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        for c in range(start, einde + 1):
            ws.cell(1, c).fill = PatternFill("solid", fgColor=kleuren[groep])
        if einde > start:
            ws.merge_cells(start_row=1, start_column=start, end_row=1, end_column=einde)
        start = einde + 1


def schrijf_resultaat(path, bron, berekend, source_path):
    """Een Engelstalig werkboek met ieder niveau (NIVEAUS) uit een upload.

    `bron` en `berekend` zijn de uitkomst van lees_observer en bereken_observer.
    """
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    definities = bron["definities"]
    volgorde = {gedrag: i for i, gedrag in enumerate(definities.gedrag)}

    variabelen = pd.DataFrame({
        "variable": definities.gedrag, "behaviour": definities.basisgedrag,
        "modifier": definities.modifier, "code": definities.code, "group": definities.groep,
        "out_of_sight": definities.out_of_sight, "state_event": definities.state_event,
        "statistic": definities.meettype.map(STATISTIEK), "value": definities.meettype.map(WAARDE),
        "scored_phases": definities.gescoorde_fases, "source_column": definities.bronkolom,
        "out_of_sight_column": definities.oos_kolom})
    details = berekend["details"].assign(
        meettype=lambda d: d.meettype.map(STATISTIEK),
        _niveau=lambda d: d.level.map({n: i for i, n in enumerate(NIVEAUS)}),
        _volgorde=lambda d: d.gedrag.map(volgorde),
    ).sort_values(["_niveau", "test_id", "fase", "_volgorde"], na_position="first").rename(columns={
        "fase": "observer_phase", "groep": "group", "gedrag": "variable", "basisgedrag": "behaviour",
        "meettype": "statistic", "gedrag_s": "behaviour_duration_s", "aantal": "count",
        "totale_faseduur_s": "duration_s", "zichtbaar_s": "visible_s", "aanwezige_fases": "phases_used",
        "fractie": "fraction", "frequentie_per_s": "frequency_per_s",
        "frequentie_per_min": "frequency_per_min"})[[
        "level", "test_id", "dog_id", "observer_phase", "group", "variable", "behaviour", "modifier",
        "statistic", "behaviour_duration_s", "count", "duration_s", "out_of_sight_s", "visible_s",
        "phases_used", "fraction", "percentage", "frequency_per_s", "frequency_per_min", "status"]]
    noemers = berekend["noemers"].rename(columns={
        "fase": "observer_phase", "groep": "group", "totale_faseduur_s": "duration_s",
        "zichtbaar_s": "visible_s", "aanwezige_fases": "phases_used",
        "ontbrekende_fases": "missing_phases"})[[
        "level", "test_id", "observer_phase", "group", "duration_s", "out_of_sight_s",
        "visible_s", "phases_used", "missing_phases", "status"]]
    selectie = bron["selectie"].rename(columns={
        "observatie": "observation", "fase": "observer_phase", "met_eigenaar": "owner_present",
        "bronrij": "source_row"})
    selectie["levels"] = [", ".join(n for n, c in NIVEAUS.items() if fase in c["fases"]) or None
                          for fase in selectie.observer_phase]
    selectie = selectie[["observation", "test_id", "dog_id", "observer_phase", "condition", "phase",
                         "owner_present", "source_row", "levels"]]
    uitgesloten = bron["uitgesloten"].rename(columns={
        "bronkolom": "source_column", "bronkop": "header", "reden": "reason"})
    tables = {
        **berekend["per_niveau"],
        "README": pd.DataFrame({"README": _readme(source_path)}),
        "details": details,
        "denominators": noemers,
        "variables": variabelen,
        "availability": bron["beschikbaarheid"],
        "phase_selection": selectie,
        "warnings": berekend["meldingen"],
        "excluded": uitgesloten.reindex(columns=["source_column", "header", "reason"]),
    }
    groep_per_variabele = dict(zip(definities.gedrag, definities.groep, strict=True))
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, table in tables.items():
            # Een resultaatblad heeft de groepsrij boven zijn kopregel.
            kopregel = 2 if name in NIVEAUS else 1
            table.to_excel(writer, sheet_name=name, index=False, startrow=kopregel - 1)
            ws = writer.book[name]
            # De leidende kolommen van ieder resultaatblad blijven zichtbaar.
            ws.freeze_panes = (f"{get_column_letter(LEIDENDE_KOLOMMEN + 1)}{kopregel + 1}"
                               if name in NIVEAUS else "A2")
            ws.auto_filter.ref = (f"A{kopregel}:{get_column_letter(ws.max_column)}"
                                  f"{max(ws.max_row, kopregel)}")
            for cell in ws[kopregel]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="245447")
                ws.column_dimensions[cell.column_letter].width = 24
            if name in NIVEAUS:
                _groepsrij(ws, table.columns, groep_per_variabele)
            if name == "README":
                ws.column_dimensions["A"].width = 140
    return path
