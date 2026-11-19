"""One-page Markdown report from the dict `check.run` returns."""

from __future__ import annotations

from . import rules

MARK = {rules.GREEN: "grün", rules.AMBER: "gelb", rules.RED: "rot", rules.NODATA: "keine Daten"}
NAMES = {"S1": "Gehalt vs. Region", "S2": "Gehalt vs. Konkurrenzanzeigen", "W1": "Wettbewerb"}


def eur(x) -> str:
    return "–" if x is None else f"{x:,.0f} €".replace(",", ".")


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
        L.append(f"| {f['rule']} {NAMES[f['rule']]} | {MARK[f['grade']]} | {f['reason']} |")
    L += ["", "Die Regeln und ihre Schwellen stehen im README; sie sind gesetzt, nicht gemessen.",
          ""]

    # (a) salary
    L += ["## Gehalt", ""]
    L += [(f"Angebot umgerechnet: {eur(o['monthly_total'])}/Monat inkl. Sonderzahlungen "
          f"(Vergleich mit dem Entgeltatlas), {eur(o['monthly_base'])}/Monat Grundgehalt "
          "(Vergleich mit Anzeigen)."), ""]
    L += [(f"KldB {occ['kldb']}: {occ['kldb_label'] or 'Bezeichnung unbekannt'} "
          f"(Niveau {occ['level']}; Zuordnung: {occ['source']})."), ""]
    L += ["| Region | Branche | Q25 | Median | Q75 | Beschäftigte |", "|---|---|---|---|---|---|"]
    for block in (sal["regional"], sal["germany"]):
        ov = block["overall"]
        if ov:
            L.append(f"| {ov['region']} | alle | {eur(ov['q25'])} | {eur(ov['median'])} | "
                     f"{eur(ov['q75'])} | {ov['employees'] or '–'} |")
        if block is sal["germany"] and sal["region_id"] == 1:
            break
    for b in sal["regional"]["branches"]:
        L.append(f"| {b['region']} | {b['branche']} | {eur(b['q25'])} | {eur(b['median'])} | "
                 f"{eur(b['q75'])} | {b['employees'] or '–'} |")
    region_note = (" Die Region wurde aus dem Bundesland der Anzeigen im Umkreis bestimmt."
                   if sal["region_inferred"] else "")
    L += ["", ("Quelle: BA Entgeltatlas, Stichtag 31.12.2025, Bruttomonatsentgelt "
          "sozialversicherungspflichtig Vollzeitbeschäftigter inkl. Sonderzahlungen. "
          "Feinste Ebene ist das Bundesland (oder eine von zwölf Großstädten); "
          f"Kreise gibt es nicht.{region_note}"), ""]

    # (b) competition
    sp = comp["stated_pay"]
    L += ["## Wettbewerb um Bewerber", ""]
    L += [(f"- **{comp['same_beruf']}** offene Stellen im selben Beruf im Umkreis, davon "
          f"{comp['same_beruf_zeitarbeit']} von Zeitarbeitsfirmen "
          f"({pct(comp['same_beruf_zeitarbeit'], comp['same_beruf'])})."),
          (f"- {comp['query_total']} Treffer für die Suche insgesamt, einschließlich "
          "verwandter Berufe."),
          (f"- {sp['stating']} von {sp['ads']} Anzeigen im selben Beruf nennen ein Gehalt "
          f"({pct(sp['stating'], sp['ads'])}); "
          f"{comp['shift_ads']} nennen Schicht-, Nacht- oder Wochenendarbeit.")]
    if sp["monthly"]:
        m = sp["monthly"]
        L.append(f"- Genannte Gehälter, auf Monat umgerechnet (Stundenlöhne mit "
                 f"{o['hours']:g} Std./Woche, Spannen als Mitte): Q25 {eur(m['q25'])}, "
                 f"Median {eur(m['median'])}, Q75 {eur(m['q75'])} (n = {m['n']}).")
        d = sp["monthly_direct"]
        L.append(f"- Davon ohne Zeitarbeit: {sp['stating_direct']} Anzeigen"
                 + (f", Median {eur(d['median'])}." if d else "."))
    if comp["truncated"]:
        L.append("- Mehr Treffer als abrufbar; Zählung aus der Facette der Jobsuche.")
    L.append("")
    if comp["top_employers"]:
        L += ["| Meiste Anzeigen | Stellen | Zeitarbeit |", "|---|---|---|"]
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
