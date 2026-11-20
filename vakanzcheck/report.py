"""One-page Markdown report from the dict `check.run` returns."""

from __future__ import annotations

from . import rules

MARK = {rules.GREEN: "grün", rules.AMBER: "gelb", rules.RED: "rot", rules.NODATA: "keine Daten"}
NAMES = {"S1": "Gehalt vs. Region", "S2": "Gehalt vs. Konkurrenzanzeigen", "W1": "Wettbewerb"}


def eur(x) -> str:
    return "–" if x is None else f"{rules.half_up(x):,.0f} €".replace(",", ".")


def num(x: float) -> str:
    """German number format: two decimals below 100 (hourly wages), none above."""
    text = f"{rules.half_up(x, 2):,.2f}" if x < 100 else f"{rules.half_up(x):,.0f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def cell(row: dict, key: str) -> str:
    if key in (row.get("censored") or ()):
        return "über BBG"
    return eur(row.get(key))


def pct(a: int, b: int) -> str:
    return "–" if not b else f"{100 * a / b:.0f} %"


def _offer_line(o: dict) -> str:
    unit = {"month": "€/Monat", "hour": "€/Stunde", "year": "€/Jahr"}[o["per"]]
    amount = f"{o['amount']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    parts = [f"{amount} {unit}"]
    if o["per"] == "hour":
        parts.append(f"{o['hours']:g} Std./Woche")
    if o["per"] != "year" and o["payments"] != 12:
        parts.append(f"{o['payments']:g} Gehälter")
    if o["extra"]:
        parts.append(f"{eur(o['extra'])} Sonderzahlungen/Jahr")
    return ", ".join(parts)


def render(r: dict) -> str:
    v, occ, sal, comp, ver = (r["vacancy"], r["occupation"], r["salary"], r["competition"],
                              r["verdict"])
    o = v["offer"]
    L = [f"# Vakanzcheck: {v['title']}", ""]
    L += [(f"{v['beruf']} · {v['place'] or v['wo']} + {v['radius_km']} km · "
          f"Angebot {_offer_line(o)} · Stand {r['date']}"), ""]
    L += [f"**Einschätzung: {ver['label']}**", ""]
    L += ["| Regel | Ergebnis | Begründung |", "|---|---|---|"]
    for f in ver["findings"]:
        grade = MARK[f["grade"]] + (" (unsicher)" if f.get("caveat") else "")
        reason = f["reason"] + (f". **{f['caveat']}**" if f.get("caveat") else "")
        L.append(f"| {f['rule']} {NAMES[f['rule']]} | {grade} | {reason} |")
    L += ["", "Die Regeln und ihre Schwellen stehen im README; sie sind gesetzt, nicht gemessen.",
          ""]

    # (a) salary
    L += ["## Gehalt", ""]
    L += [(f"Angebot umgerechnet: {eur(o['monthly_total'])}/Monat inkl. Sonderzahlungen "
          f"(Vergleich mit dem Entgeltatlas), {eur(o['monthly_base'])}/Monat Grundgehalt "
          "(Vergleich mit Stundenlöhnen in Anzeigen)."), ""]
    source = "per --kldb angegeben" if occ["source"] == "given" else occ["source"]
    L += [(f"KldB {occ['kldb']}: {occ['kldb_label'] or 'Bezeichnung unbekannt'} "
          f"(Niveau {occ['level']}; Zuordnung: {source})."), ""]
    L += ["| Region | Branche | Q25 | Median | Q75 | Beschäftigte |", "|---|---|---|---|---|---|"]
    for block in (sal["regional"], sal["germany"]):
        ov = block["overall"]
        if ov:
            L.append(f"| {ov['region']} | alle | {cell(ov, 'q25')} | {cell(ov, 'median')} | "
                     f"{cell(ov, 'q75')} | {ov['employees'] or '–'} |")
        if block is sal["germany"] and sal["region_id"] == 1:
            break
    for b in sal["regional"]["branches"]:
        L.append(f"| {b['region']} | {b['branche']} | {cell(b, 'q25')} | {cell(b, 'median')} | "
                 f"{cell(b, 'q75')} | {b['employees'] or '–'} |")
    region_note = (" Die Region wurde aus dem Bundesland der Anzeigen im Umkreis bestimmt."
                   if sal["region_inferred"] else "")
    if any(b.get("censored") for b in [sal["regional"]["overall"] or {},
                                         sal["germany"]["overall"] or {},
                                         *sal["regional"]["branches"]]):
        L += ["", (f"„über BBG“: Wert liegt an oder über der Beitragsbemessungsgrenze "
                   f"({eur(sal['ceiling'])}); der wahre Wert ist nicht bekannt.")]
    if not sal["stichtag_confirmed"]:
        L += ["", (f"**Achtung:** Die Beitragsbemessungsgrenze in der Antwort "
                   f"({eur(sal['ceiling'])}) passt nicht zum Stichtag {sal['stichtag']}; "
                   "die Daten sind vermutlich neuer. Stichtag in sources.py prüfen.")]
    L += ["", (f"Quelle: BA Entgeltatlas, Stichtag {sal['stichtag']}, Bruttomonatsentgelt "
          "sozialversicherungspflichtig Vollzeitbeschäftigter inkl. Sonderzahlungen. "
          "Feinste Ebene ist das Bundesland (oder eine von elf Großstädten); "
          f"Kreise gibt es nicht.{region_note}"), ""]

    # (b) competition
    sp, smp = comp["stated_pay"], comp["sample"]
    L += ["## Wettbewerb um Bewerber", ""]
    L += [(f"- **{comp['same_beruf']}** offene Stellen im selben Beruf im Umkreis, davon "
          f"{comp['same_beruf_zeitarbeit']} von Zeitarbeitsfirmen "
          f"({pct(comp['same_beruf_zeitarbeit'], comp['same_beruf'])}, laut Zeitarbeit-Filter "
          f"der Jobsuche; gezählt: {comp['count_source']})."),
          (f"- {comp['query_total']} Treffer für die Suche insgesamt, einschließlich "
          "verwandter Berufe."),
          (f"- {sp['stating']} von {sp['ads']} Anzeigen im selben Beruf nennen ein Gehalt "
          f"({pct(sp['stating'], sp['ads'])}), davon {sp['stating_direct']} ohne Zeitarbeit."),
          (f"- {comp['shift_flagged']} von {sp['ads']} sind in der Jobsuche mit dem "
          "Arbeitszeitmerkmal Schicht/Nacht/Wochenende gekennzeichnet (Strukturfeld, nicht "
          "aus dem Anzeigentext).")]
    if smp["truncated"]:
        L.append(f"- **Stichprobe:** {smp['query_total']} Treffer, abrufbar sind "
                 f"{smp['items_read']}. Zeitarbeit-Kennzeichen, Gehälter, Schichtmerkmal und "
                 "Arbeitgeber unten stammen nur aus diesen; die Stellenzahlen aus der Facette. "
                 "Eine Anzeige ohne Zeitarbeit-Kennzeichen, die nicht unter den abrufbaren "
                 "Treffern ohne Kennzeichen ist, kann dabei als Zeitarbeit gelten; bei so "
                 "vielen Treffern den Radius verkleinern.")
    L.append("")
    if sp["by_unit"]:
        unit = {"hour": "€/Stunde", "month": "€/Monat", "year": "€/Jahr"}
        L += ["Genannte Gehälter in der Einheit der Anzeige (Spannen als Mitte):", "",
              "| Einheit | Zeitarbeit-Kennzeichen | Anzeigen | Median | Spanne |", "|---|---|---|---|---|"]
        for row in sp["by_unit"]:
            who = "nein" if row["direct"] else "ja"
            L.append(f"| {unit[row['unit']]} | {who} | {row['n']} | {num(row['median'])} | "
                     f"{num(row['min'])} – {num(row['max'])} |")
        L += ["", (f"S2 vergleicht Stundenlöhne (mit {o['hours']:g} Std./Woche) mit dem "
                   "Grundgehalt und Jahresgehälter mit den Gesamtbezügen inkl. "
                   "Sonderzahlungen, weil Jahresgehälter diese meist enthalten."), ""]
    if comp["top_employers"]:
        L += ["| Meiste Anzeigen | Stellen | Zeitarbeit-Kennzeichen |", "|---|---|---|"]
        for t in comp["top_employers"]:
            L.append(f"| {t['firma']} | {t['stellen']} | {'ja' if t['zeitarbeit'] else 'nein'} |")
        L.append("")
    if comp["related"]:
        L.append("Verwandte Berufe in denselben Treffern: " + ", ".join(
            f"{x['beruf']} ({x['stellen']})" for x in comp["related"]) + ".")
        L.append("")
    L += [f"Quelle: BA Jobsuche, abgerufen {r['date']}, nur Arbeitsstellen (keine Ausbildung).",
          ""]

    # (c) ad text
    ad = r.get("ad_check")
    if ad:
        L += ["## Anzeigentext", "", f"**{ad['label']}.**", ""]
        L += ["| Prüffrage | Ergebnis | Fundstelle |", "|---|---|---|"]
        for c in ad["checks"]:
            quote = c["zitat"].replace("|", "/")
            if c.get("hinweis"):
                quote = f"{quote} *({c['hinweis']})*" if quote else f"*{c['hinweis']}*"
            L.append(f"| {c['frage']} | {c['status']} | {quote} |")
        L += ["", ("Die Prüffragen sind eine eigene Liste der Angaben, nach denen Bewerber in "
              "diesem Beruf sonst fragen müssen, keine Studie.")]
        if ad["backend"] == "subscription":
            m = ad["meta"]
            L.append(f"Modell: {', '.join(m['served_by']) or m['requested_model']}. Jedes Zitat "
                     "wurde gegen den Anzeigentext geprüft.")
        L.append("")
    return "\n".join(L)
