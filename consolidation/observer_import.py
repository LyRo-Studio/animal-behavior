"""Import van de aangeleverde Observer-export; behoud iedere modifiercombinatie."""
from pathlib import Path
import hashlib
import math
import re
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from consolidation import ethogram_definition
from consolidation.ethogram_definition import normalise
from consolidation.consolidatie import consolideer

# Welke Behaviour group en Out of Sight bij een kolom horen, komt uit de
# definitie die uit het ethogram gegenereerd is (ticket #150), nooit uit de
# kolompositie in de export.
DEFINITIE = ethogram_definition.load()
VERPLICHT_IN_BEIDE = ["Observations", "Test ID", "Dog ID", "Duration"]
VERPLICHTE_KOLOMMEN = [*VERPLICHT_IN_BEIDE, "Aanwezigheid FP in fase"]
TOLERANTIE_S = 0.001  # Observer exporteert seconden met een beperkte decimale precisie.
DELEN = {
    "1": {"naam": "Met eigenaar", "fases": tuple(range(1, 8)), "niveau": "with_owner"},
    "2": {"naam": "Zonder eigenaar", "fases": tuple(range(9, 16)), "niveau": "without_owner"},
    "1+2": {"naam": "Met en zonder eigenaar samen", "fases": tuple(range(1, 8)) + tuple(range(9, 16)),
            "niveau": "combined"},
}


def normaliseer_kop(value):
    return str(value).replace("stress0", "stress-").replace("self0", "self-")


def fase_uit_observatie(value):
    match = re.fullmatch(r"(T\d+)_.+_F(\d+)", str(value))
    if not match:
        raise ValueError(f"Onherkenbare observatienaam: {value!r}")
    phase = int(match[2])
    if not 1 <= phase <= 15:
        raise ValueError(f"Ongeldige fase in observatienaam: {value}")
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
    """
    bekend = _bekende_gedragingen(definitie)
    groepen = {g["name"]: g for g in definitie["groups"]}
    mapping, excluded, notes = [], [], []
    oos_kolommen, oos_kandidaten, event_duur, per_gedrag = {}, [], [], {}
    niet_ondersteund = {}
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
            oos_kandidaten.append((rij, groep["name"], c))
        elif groep["excluded"] or gedrag["name"] in definitie["excluded_behaviours"]:
            excluded.append({**rij, "reden": groep["excluded"]
                             or definitie["excluded_behaviours"][gedrag["name"]]})
        elif groep["scored_phases"] != ethogram_definition.ALL_PHASES:
            niet_ondersteund.setdefault(groep["name"], groep["scored_phases"])
            excluded.append({**rij, "reden": "Not yet supported: this group is only scored in "
                             "some phases (Scoring plan)."})
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
                            "bronkolom": letter, "kolomnummer": c})
    if not mapping:
        raise ValueError("The export has no dog behaviour columns to consolidate.")
    gebruikt = {m["groep"] for m in mapping}
    for naam in sorted(gebruikt):
        oos = groepen[naam]["out_of_sight"]
        if oos and naam not in oos_kolommen:
            raise ValueError(
                f"Missing Out of Sight column for {naam}: the export has behaviours of this "
                f"group but no 'Total duration {oos} <No Modifier>' column.")
    for rij, groep, c in oos_kandidaten:
        if oos_kolommen.get(groep) != c:
            reden = "Out of Sight count or modifier column: never used."
        elif groep in gebruikt:
            reden = "Out of Sight correction column: its duration corrects the group's denominator."
        else:
            reden = "Out of Sight column ignored: none of its group's behaviours were exported."
        excluded.append({**rij, "reden": reden})
    for naam, fases in niet_ondersteund.items():
        notes.append(_melding("not_yet_supported", naam,
                              f"{naam} is only scored in phases {fases} of each Condition. "
                              "Its columns are excluded until the Scoring plan is supported."))
    oos_per_groep = {naam: oos_kolommen.get(naam) for naam in gebruikt}
    for m in mapping:
        oc = oos_per_groep[m["groep"]]
        m["oos_kolom"] = source.cell(1, oc).column_letter if oc else "geen"
    return mapping, excluded, notes, oos_per_groep, event_duur, per_gedrag


def _beschikbaarheid(per_gedrag, niet_nul, definitie):
    """Ieder hondgedrag uit het ethogram: niet, met alleen nullen of met waarden geexporteerd."""
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


def _melding(soort, variabele, melding, test_id="", fase=""):
    return {"type": soort, "test_id": test_id, "fase": fase, "variabele": variabele,
            "melding": melding}


def _getal(value, context):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"Ongeldige meetwaarde in {context}: {value!r}")
    return float(value)


def lees_observer(path, deel="1"):
    """Lees deel 1, deel 2 of 1+2 uit Results (2), met controle tegen Results.

    '-' wordt alleen als nul aanvaard wanneer de bewerkte kopie expliciet nul
    bevat. Blanke meetcellen zijn fouten. Fase wordt uit Observations gehaald:
    de bronkolom Fase nummert deel 2 opnieuw van 1 tot 7.
    """
    deel = str(deel)
    if deel not in DELEN:
        raise ValueError("Deel moet 1, 2 of 1+2 zijn.")
    gekozen_fases = DELEN[deel]["fases"]
    try:
        wb = load_workbook(path, data_only=False, keep_links=False)
    except KeyError as exc:
        # openpyxl meldt sommige ontbrekende OOXML-onderdelen als KeyError.
        raise ValueError("The uploaded file is not a valid Excel workbook.") from exc
    try:
        for sheet in ("Results (2)", "Results", "Results (REL)"):
            if sheet not in wb.sheetnames:
                raise ValueError(f"Missing sheet: {sheet}")
        source = wb["Results (2)"]
        raw = wb["Results"]
        rel = wb["Results (REL)"]
        headers = {normaliseer_kop(c.value): c.column for c in source[1]}
        raw_headers = {normaliseer_kop(c.value): c.column for c in raw[1]}
        if len(headers) != source.max_column or len(raw_headers) != raw.max_column:
            raise ValueError("Dubbele kolomkoppen in de Observer-export.")
        for header in VERPLICHTE_KOLOMMEN:
            if header not in headers:
                raise ValueError(f"Missing column in Results (2): {header}")
        for header in [*VERPLICHT_IN_BEIDE, *(h for h in headers if h.startswith("Total "))]:
            if header not in raw_headers:
                raise ValueError(f"Missing column in Results: {header}")
        raw_rows = {raw.cell(r, raw_headers["Observations"]).value: r
                    for r in range(2, raw.max_row + 1)}
        if len(raw_rows) != raw.max_row - 1:
            raise ValueError("Dubbele observaties in ruwe Results.")
        mapping, excluded, notes, oos_columns, event_duur, per_gedrag = _deel_kolommen_in(
            headers, source, DEFINITIE)
        for group, oc in oos_columns.items():
            if oc:
                color = source.cell(1, oc).fill.fgColor
                if color.type != "rgb" or color.rgb[-6:] != "FFFF00":
                    raise ValueError(f"OOS-kolom is niet geel gemarkeerd: {source.cell(1, oc).value}")
        for m in mapping:
            formula = rel.cell(2, m["kolomnummer"]).value
            refs = re.findall(r"\$([A-Z]+)2", str(formula))
            found = sorted(set(refs) - {"G"})
            if found != ([] if m["oos_kolom"] == "geen" else [m["oos_kolom"]]):
                notes.append({"type": "bestaande_formule", "test_id": "", "fase": "",
                              "variabele": m["gedrag"],
                              "melding": f"Results (REL) verwijst naar {found}; gebruikt: {m['oos_kolom']}."})
        definitions = pd.DataFrame(mapping)
        phases, behaviors, invisibility, selection = [], [], [], []
        zero_count = 0
        niet_nul = set()
        for row in range(2, source.max_row + 1):
            observation = source.cell(row, headers["Observations"]).value
            label_test, phase = fase_uit_observatie(observation)
            test = source.cell(row, headers["Test ID"]).value
            owner_flag = str(source.cell(row, headers["Aanwezigheid FP in fase"]).value).lower()
            if owner_flag not in {"true", "false"} or (owner_flag == "true") != (phase <= 7):
                raise ValueError(f"Fase en aanwezigheid eigenaar/FP komen niet overeen: {observation}")
            if observation not in raw_rows:
                raise ValueError(f"Observation {observation} is missing from Results.")
            raw_row = raw_rows[observation]
            if (raw.cell(raw_row, raw_headers["Test ID"]).value != test
                    or raw.cell(raw_row, raw_headers["Dog ID"]).value != source.cell(row, headers["Dog ID"]).value):
                raise ValueError(f"Test/Dog-ID verschillen tussen de twee brontabbladen: {observation}")
            if test != label_test:
                notes.append({"type": "observatienaam", "test_id": test, "fase": phase,
                              "variabele": observation,
                              "melding": "Typfout in observatienaam; Test ID en Dog ID uit beide brontabbladen komen overeen en worden gebruikt."})
            selection.append({"observatie": observation, "test_id": test,
                              "dog_id": source.cell(row, headers["Dog ID"]).value,
                              "fase": phase, "fase_bronkolom": source.cell(row, headers["Fase"]).value if "Fase" in headers else None,
                              "fase_in_deel": phase if phase <= 7 else phase - 8,
                              "deel": 1 if phase <= 7 else 2, "met_eigenaar": owner_flag == "true",
                              "opgenomen": phase in gekozen_fases, "bronrij": row})
            if phase not in gekozen_fases:
                continue
            # Controleer alle statistieken, ook uitgesloten protocolkolommen.
            for header, c in headers.items():
                if not header.startswith(("Total duration ", "Total number ")) and header != "Duration":
                    continue
                actual = _getal(source.cell(row, c).value, f"Results (2)!{source.cell(row, c).coordinate}")
                original = raw.cell(raw_row, raw_headers[header]).value
                if actual > 0:
                    niet_nul.add(c)
                if original == "-" and actual == 0:
                    zero_count += 1
                elif not isinstance(original, (int, float)) or original != actual:
                    raise ValueError(f"Ruwe data wijkt af van kopie: {observation}, {header}")
            duration = _getal(source.cell(row, headers["Duration"]).value, observation)
            phases.append({"test_id": test, "fase": phase, "duur_s": duration})
            for group, oc in oos_columns.items():
                invisibility.append({"test_id": test, "fase": phase, "groep": group,
                                     "duur_s": float(source.cell(row, oc).value) if oc else 0.0})
            for m in mapping:
                value = _getal(source.cell(row, m["kolomnummer"]).value, observation)
                behaviors.append({"test_id": test, "fase": phase, "gedrag": m["gedrag"],
                                  "duur_s": value if m["meettype"] == "duur" else float("nan"),
                                  "aantal": value if m["meettype"] == "aantal" else float("nan")})
        for header, c in event_duur:
            if c in niet_nul:
                notes.append(_melding("event_duration", header,
                                      "This Event has a nonzero duration; Observer's coding may "
                                      "have changed. Only its frequency is reported."))
        selected = pd.DataFrame(selection)
        if selected.duplicated(["test_id", "fase"]).any():
            raise ValueError("Dubbele echte test/fase-combinatie.")
        dogs = selected[["test_id", "dog_id"]].drop_duplicates()
        if dogs.test_id.duplicated().any() or dogs.dog_id.isna().any():
            raise ValueError("Test heeft ontbrekend of tegenstrijdig Dog ID.")
        notes.append({"type": "broncontrole", "test_id": "", "fase": "", "variabele": "",
                      "melding": f"Alle gekozen waarden gelijk aan ruwe Results; {zero_count} '-' expliciet als 0 bevestigd door Results (2)."})
        return {"deel": deel, "invoer": {"fases": pd.DataFrame(phases),
                           "gedrag_groepen": definitions[["gedrag", "groep", "meettype"]],
                           "gedragingen": pd.DataFrame(behaviors),
                           "out_of_sight": pd.DataFrame(invisibility)},
                "definities": definitions, "honden": dogs, "selectie": selected,
                "uitgesloten": pd.DataFrame(excluded), "meldingen": pd.DataFrame(notes),
                "beschikbaarheid": _beschikbaarheid(per_gedrag, niet_nul, DEFINITIE)}
    finally:
        wb.close()


def bereken_observer(bron):
    deel = bron.get("deel", "1")
    resultaat, detail = consolideer(**bron["invoer"], tolerantie_s=TOLERANTIE_S,
                                   geselecteerde_fases=DELEN[deel]["fases"])
    labels = bron["definities"][["gedrag", "basisgedrag", "modifier", "bronkolom", "oos_kolom"]]
    resultaat = resultaat.merge(labels, on="gedrag", validate="many_to_one").merge(
        bron["honden"], on="test_id", validate="many_to_one")
    resultaat["deel"] = int(deel) if deel in {"1", "2"} else deel
    resultaat["met_eigenaar"] = (deel == "1") if deel != "1+2" else None
    resultaat["selectie"] = DELEN[deel]["naam"]
    detail = detail.merge(labels, on="gedrag", validate="many_to_one")
    detail = detail.merge(bron["selectie"].query("opgenomen")[[
        "test_id", "fase", "observatie", "fase_in_deel", "deel", "met_eigenaar", "bronrij"]],
        on=["test_id", "fase"], validate="many_to_one")
    notes = bron["meldingen"].to_dict("records")
    for row in detail[detail.gedrag_s > detail.zichtbaar_s + 1e-9].itertuples():
        notes.append({"type": "afronding", "test_id": row.test_id, "fase": row.fase,
                      "variabele": row.gedrag,
                      "melding": f"Gedragsduur {row.gedrag_s} s; zichtbare tijd {row.zichtbaar_s} s. Binnen {TOLERANTIE_S} s exporttolerantie; waarden niet aangepast."})
    noemers = detail[["test_id", "fase", "groep", "fase_duur_s", "out_of_sight_s", "zichtbaar_s"]].drop_duplicates()
    groep_totalen = noemers.groupby(["test_id", "groep"], as_index=False).agg(
        totale_faseduur_s=("fase_duur_s", "sum"), out_of_sight_s=("out_of_sight_s", "sum"),
        zichtbaar_s=("zichtbaar_s", "sum"), aantal_fases=("fase", "nunique"))
    for row in groep_totalen[groep_totalen.zichtbaar_s == 0].itertuples():
        notes.append({"type": "geen_zichtbare_tijd", "test_id": row.test_id, "fase": str(DELEN[deel]["fases"]),
                      "variabele": row.groep, "melding": "Volledig out of sight: fracties/frequenties blijven leeg."})
    # Brede tabel: een rij per test; duurkolommen bevatten fracties en
    # aantal-kolommen frequenties per seconde, overeenkomstig de opdracht.
    resultaat["geconsolideerde_waarde"] = resultaat.fractie.where(
        resultaat.meettype == "duur", resultaat.frequentie_per_s)
    wide = resultaat.pivot(index=["test_id", "dog_id"], columns="gedrag", values="geconsolideerde_waarde")
    wide = wide.reindex(columns=bron["definities"].gedrag).reset_index()
    wide.columns.name = None
    return {"per_test": wide, "consolidatie": resultaat, "controle_per_fase": detail,
            "noemers_per_groep": groep_totalen, "meldingen": pd.DataFrame(notes)}


def schrijf_resultaat(path, bronnen, berekend, source_path):
    """Een Engelstalig werkboek met ieder niveau (DELEN) uit een upload.

    `bronnen` en `berekend` bevatten per deel de uitkomst van lees_observer en
    bereken_observer. Diagnostische tabellen krijgen een kolom `level`.
    """
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    uitleg = [
        "CONSOLIDATION RESULT - every level from one Observer export.",
        "with_owner: Observer phases 1-7 (ME, the owner is present).",
        "without_owner: Observer phases 9-15 (ZE, the owner is absent).",
        "combined: Observer phases 1-7 and 9-15 together. Owner departure (phase 8) never counts.",
        "Phases come from the Observation name; the owner-present flag is checked against them.",
        "with_owner, without_owner, combined: one row per test; every original behaviour and modifier column kept separately.",
        "Total duration columns: sum of behaviour duration / (sum of phase Duration - sum of the group's Out of Sight duration). Fractions, not percentages.",
        "Total number columns: sum of counts / the same denominator. Unit: occurrences per visible second.",
        "Sums first, then one division: never an average of phase values. combined is never an average of with_owner and without_owner.",
        "Modifier combinations are never summed into a base behaviour; each is only summed over phases.",
        "A group without an Out of Sight behaviour (Vocalisation) uses the full phase Duration.",
        "Out of Sight counts are not time: only Out of Sight durations are subtracted.",
        "Behaviour groups and Out of Sight corrections come from the Ethogram definition, never from column positions. "
        "Stiffening up belongs to exploration/self-maintenance.",
        "A behaviour Observer didn't export has no result column; an exported column holding 0 is a measured zero.",
        "Events report only a frequency; their duration columns are listed in excluded.",
        "TP/FP protocol behaviours and First contact with TP (a separate F1 measure) are recognised by name and listed in excluded.",
        "Distance to TP/FP, Location and Dog following are only scored in some phases: until the Scoring plan is supported, "
        "their columns are listed in excluded, with a warning.",
        "No visible time: blank fraction and frequency with status geen_zichtbare_tijd; never replaced by 0.",
        "Raw Results and Results (2) are compared cell by cell for every selected value. The source file is not changed.",
        "Headers with stress0/self0 are restored to stress-/self-, matching raw Results.",
        f"Maximum tolerance on individual times: {TOLERANTIE_S} s for export rounding; deviations are listed in warnings.",
        "Existing phase percentages from Results (REL) are not summed or averaged. Wrong group references are listed in warnings.",
        "details: every numerator, denominator, percentage, frequency per second and per minute, and status, per level.",
        "phase_details: every value per test and phase; denominators: per test, level and group.",
        "phase_selection: every source row and the levels it was used in; variables: every result column; excluded: columns left out, with the reason.",
        "availability: every dog behaviour in the Ethogram, as not_exported (absent from the export, so absent from the results), "
        "exported_all_zero (a measured zero) or exported_nonzero.",
        "Diagnostic sheets keep the calculation's own column names: fase = Observer phase number, groep = Behaviour group, "
        "gedrag = result column, basisgedrag = behaviour, duur = duration, aantal = count, zichtbaar = visible time, "
        "fractie = fraction, frequentie = frequency.",
        f"Source: {Path(source_path).name}",
        f"SHA256 source: {hashlib.sha256(Path(source_path).read_bytes()).hexdigest()}",
    ]

    niveaus = {d: config["niveau"] for d, config in DELEN.items()}

    def per_niveau(key, weglaten=()):
        tabellen = []
        for d, niveau in niveaus.items():
            tabel = berekend[d][key].drop(columns=list(weglaten))
            tabel.insert(0, "level", niveau)
            tabellen.append(tabel)
        return pd.concat(tabellen, ignore_index=True)

    # Definities, uitsluitingen en de bronrijen zijn voor ieder deel gelijk;
    # alleen `opgenomen` hangt af van het deel.
    alle = bronnen["1+2"]
    gebruikt = {d: set(bronnen[d]["selectie"].query("opgenomen")[["test_id", "fase"]]
                       .itertuples(index=False, name=None)) for d in niveaus}
    selectie = alle["selectie"].drop(columns="opgenomen")
    selectie["levels"] = [", ".join(n for d, n in niveaus.items() if key in gebruikt[d]) or None
                          for key in selectie[["test_id", "fase"]].itertuples(index=False, name=None)]
    tables = {
        **{niveau: berekend[d]["per_test"] for d, niveau in niveaus.items()},
        "README": pd.DataFrame({"README": uitleg}),
        # deel/met_eigenaar/selectie beschrijven per deel hetzelfde als level.
        "details": per_niveau("consolidatie", ["deel", "met_eigenaar", "selectie"]),
        "phase_details": per_niveau("controle_per_fase"),
        "denominators": per_niveau("noemers_per_groep"),
        "warnings": per_niveau("meldingen"),
        "variables": alle["definities"].drop(columns="kolomnummer"),
        "availability": alle["beschikbaarheid"],
        "phase_selection": selectie,
        "excluded": alle["uitgesloten"],
    }
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, table in tables.items():
            table.to_excel(writer, sheet_name=name, index=False)
            ws = writer.book[name]
            ws.freeze_panes = "C2" if name in niveaus.values() else "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="245447")
                ws.column_dimensions[cell.column_letter].width = 24
            if name == "README":
                ws.column_dimensions["A"].width = 140
    return path
