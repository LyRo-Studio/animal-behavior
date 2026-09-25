"""Import van de aangeleverde Observer-export; behoud iedere modifiercombinatie."""
from pathlib import Path
import hashlib
import math
import re
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from consolidation.consolidatie import consolideer

# De grenzen staan als gedragsnamen in Results (2), niet als vaste kolomnummers.
# De vijf OOS-kolommen zijn geel gemarkeerd. Vocalisatie gebruikt de volledige
# faseduur, zoals ook de formules in Results (REL) expliciet aangeven.
GROUPS = [
    ("General posture/locomotion of the dog", "Lying down sternally head up",
     "Other general posture/locomotion of the dog", "Out of sight position / locomotion"),
    ("Attention of the dog", "Attention to familiar person", "Attention other",
     "Out of sight attention"),
    ("Vocalisation by the dog", "Panting", "No vocalisation", None),
    ("Exploratory and self-maintenance behaviours by the dog", "Pushing snout",
     "Other expl self-maint beh dog", "Out of sight expl self maint beh dog"),
    ("Stress-related behaviours by the dog", "Yawning", "Other stress-rel beh dog",
     "Out of sight stress-related"),
    ("Tail position", "Tail tucked", "Other tail", "Out of sight tail"),
]
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
    wb = load_workbook(path, data_only=False, keep_links=False)
    try:
        source = wb["Results (2)"]
        raw = wb["Results"]
        rel = wb["Results (REL)"]
        headers = {normaliseer_kop(c.value): c.column for c in source[1]}
        raw_headers = {normaliseer_kop(c.value): c.column for c in raw[1]}
        if len(headers) != source.max_column or len(raw_headers) != raw.max_column:
            raise ValueError("Dubbele kolomkoppen in de Observer-export.")
        raw_rows = {raw.cell(r, raw_headers["Observations"]).value: r
                    for r in range(2, raw.max_row + 1)}
        if len(raw_rows) != raw.max_row - 1:
            raise ValueError("Dubbele observaties in ruwe Results.")
        mapping, excluded, notes = [], [], []
        suffix_group = {}
        oos_columns = {}
        for group, first, last, oos in GROUPS:
            start = headers[f"Total duration {first} <No Modifier>"]
            end = headers[f"Total duration {last} <No Modifier>"]
            for c in range(start, end + 1):
                h = normaliseer_kop(source.cell(1, c).value)
                if not h.startswith("Total duration "):
                    raise ValueError("Onverwachte kolom binnen een gedragsgroep.")
                suffix_group[h.removeprefix("Total duration ")] = group
            oc = headers[f"Total duration {oos} <No Modifier>"] if oos else None
            oos_columns[group] = oc
            if oc:
                color = source.cell(1, oc).fill.fgColor
                if color.type != "rgb" or color.rgb[-6:] != "FFFF00":
                    raise ValueError(f"OOS-kolom is niet geel gemarkeerd: {oos}")
        base_names = sorted({s.removesuffix(" <No Modifier>") for s in suffix_group
                             if s.endswith(" <No Modifier>")}, key=len, reverse=True)
        for header, c in headers.items():
            if not header.startswith(("Total duration ", "Total number ")):
                continue
            kind = "duur" if header.startswith("Total duration ") else "aantal"
            suffix = header.removeprefix("Total duration ").removeprefix("Total number ")
            if suffix not in suffix_group:
                if suffix.startswith("Out of sight "):
                    reason = "Correctiekolom; alleen de duur wordt als noemer gebruikt"
                elif suffix.startswith("First contact with TP "):
                    reason = "Afzonderlijke first-contactmaat: volgens ethogram uitsluitend F1 met eigenaar"
                else:
                    protocol_start = headers["Total duration Standing bij TP <No Modifier>"]
                    protocol_count = headers["Total number Standing bij TP <No Modifier>"]
                    if not ((kind == "duur" and protocol_start <= c < headers[
                            "Total number Lying down sternally head up <No Modifier>"])
                            or (kind == "aantal" and c >= protocol_count)):
                        raise ValueError(f"Niet ingedeelde gedragskolom: {header}")
                    reason = "Protocolgedrag van testpersoon/vertrouwde persoon"
                excluded.append({"bronkolom": source.cell(1, c).column_letter,
                                 "bronkop": header, "reden": reason})
                continue
            base = next((b for b in base_names if suffix.startswith(b + " ")), None)
            if base is None:
                raise ValueError(f"Gedragsnaam niet te bepalen: {header}")
            group = suffix_group[suffix]
            oc = oos_columns[group]
            formula = rel.cell(2, c).value
            expected = source.cell(1, oc).column_letter if oc else "geen"
            refs = re.findall(r"\$([A-Z]+)2", str(formula))
            found = sorted(set(refs) - {"G"})
            if found != ([] if oc is None else [expected]):
                notes.append({"type": "bestaande_formule", "test_id": "", "fase": "",
                              "variabele": header,
                              "melding": f"Results (REL) verwijst naar {found}; gebruikt: {expected}."})
            mapping.append({"gedrag": header, "basisgedrag": base,
                            "modifier": suffix[len(base) + 1:], "groep": group,
                            "meettype": kind, "bronkolom": source.cell(1, c).column_letter,
                            "kolomnummer": c, "oos_kolom": expected})
        definitions = pd.DataFrame(mapping)
        phases, behaviors, invisibility, selection = [], [], [], []
        zero_count = 0
        for row in range(2, source.max_row + 1):
            observation = source.cell(row, headers["Observations"]).value
            label_test, phase = fase_uit_observatie(observation)
            test = source.cell(row, headers["Test ID"]).value
            owner_flag = str(source.cell(row, headers["Aanwezigheid FP in fase"]).value).lower()
            if owner_flag not in {"true", "false"} or (owner_flag == "true") != (phase <= 7):
                raise ValueError(f"Fase en aanwezigheid eigenaar/FP komen niet overeen: {observation}")
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
                              "fase": phase, "fase_bronkolom": source.cell(row, headers["Fase"]).value,
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
                "uitgesloten": pd.DataFrame(excluded), "meldingen": pd.DataFrame(notes)}
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
        "Vocalisation has no Out of Sight column: it uses the full phase Duration, as the source formulas state.",
        "Out of Sight counts are not time: only Out of Sight durations are subtracted.",
        "Behaviour groups follow the export's column order in Results (2).",
        "Protocol behaviours are not dog behaviour groups. First contact with TP is a separate F1 measure: see excluded.",
        "No visible time: blank fraction and frequency with status geen_zichtbare_tijd; never replaced by 0.",
        "Raw Results and Results (2) are compared cell by cell for every selected value. The source file is not changed.",
        "Headers with stress0/self0 are restored to stress-/self-, matching raw Results.",
        f"Maximum tolerance on individual times: {TOLERANTIE_S} s for export rounding; deviations are listed in warnings.",
        "Existing phase percentages from Results (REL) are not summed or averaged. Wrong group references are listed in warnings.",
        "details: every numerator, denominator, percentage, frequency per second and per minute, and status, per level.",
        "phase_details: every value per test and phase; denominators: per test, level and group.",
        "phase_selection: every source row and the levels it was used in; variables: every result column; excluded: columns left out, with the reason.",
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
