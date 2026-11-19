"""The feasibility verdict: three explicit rules and how they combine.

Each rule reads one measured number and gives `gruen`, `gelb`, `rot` or
`keine Daten`. The cut-offs are choices, written down here so they can be
argued with and changed; they are not findings.

S1  Offer vs. regional pay (Entgeltatlas, Land level, incl. one-off payments)
    below the lower quartile -> rot; between lower quartile and median -> gelb;
    at or above the median -> gruen.
S2  Offer vs. pay stated in competing ads for the same occupation in the radius
    (regular monthly pay; needs at least MIN_STATED ads that state pay)
    below their lower quartile -> rot; below their median -> gelb; else gruen.
W1  Competition: open postings for the same occupation in the radius
    at least HIGH_COMPETITION -> rot; at least MEDIUM_COMPETITION -> gelb; else gruen.

Verdict
    schwer besetzbar        S1 rot; or S1 gelb and W1 rot; or S2 rot and W1 rot
    besetzbar mit Aufwand   any other rot or gelb
    gut besetzbar           no rot and no gelb (a rule without data counts as neither)
    nicht bewertbar         neither S1 nor S2 has data
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

MIN_STATED = 5
HIGH_COMPETITION = 100
MEDIUM_COMPETITION = 30

GREEN, AMBER, RED, NODATA = "gruen", "gelb", "rot", "keine Daten"


@dataclass
class Finding:
    rule: str
    grade: str
    reason: str


def _fmt(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def salary_vs_region(offer_total: float, median: int | None, q25: int | None,
                     region: str) -> Finding:
    if median is None or q25 is None:
        return Finding("S1", NODATA, f"keine veröffentlichten Entgeltwerte für {region}")
    if offer_total < q25:
        grade, where = RED, "unter dem unteren Quartil"
    elif offer_total < median:
        grade, where = AMBER, "zwischen unterem Quartil und Median"
    else:
        grade, where = GREEN, "auf oder über dem Median"
    return Finding("S1", grade, f"Angebot {_fmt(offer_total)} liegt {where} "
                                f"({region}: Q25 {_fmt(q25)}, Median {_fmt(median)})")


def salary_vs_ads(offer_base: float, stated: dict | None) -> Finding:
    if not stated or stated["n"] < MIN_STATED:
        n = stated["n"] if stated else 0
        return Finding("S2", NODATA, f"nur {n} Konkurrenzanzeigen mit Gehaltsangabe "
                                     f"(mindestens {MIN_STATED} nötig)")
    if offer_base < stated["q25"]:
        grade, where = RED, "unter dem unteren Quartil"
    elif offer_base < stated["median"]:
        grade, where = AMBER, "unter dem Median"
    else:
        grade, where = GREEN, "auf oder über dem Median"
    return Finding("S2", grade, f"Grundgehalt {_fmt(offer_base)} liegt {where} der "
                                f"{stated['n']} Anzeigen mit Gehaltsangabe "
                                f"(Median {_fmt(stated['median'])})")


def competition(same_beruf: int, radius_km: int) -> Finding:
    if same_beruf >= HIGH_COMPETITION:
        grade = RED
    elif same_beruf >= MEDIUM_COMPETITION:
        grade = AMBER
    else:
        grade = GREEN
    return Finding("W1", grade, f"{same_beruf} offene Stellen im selben Beruf im Umkreis "
                                f"von {radius_km} km (gelb ab {MEDIUM_COMPETITION}, "
                                f"rot ab {HIGH_COMPETITION})")


def verdict(s1: Finding, s2: Finding, w1: Finding) -> dict:
    grades = {f.rule: f.grade for f in (s1, s2, w1)}
    if s1.grade == NODATA and s2.grade == NODATA:
        label = "nicht bewertbar"
    elif (s1.grade == RED or (s1.grade == AMBER and w1.grade == RED)
          or (s2.grade == RED and w1.grade == RED)):
        label = "schwer besetzbar"
    elif any(g in (RED, AMBER) for g in grades.values()):
        label = "besetzbar mit Aufwand"
    else:
        label = "gut besetzbar"
    return {"label": label, "findings": [asdict(f) for f in (s1, s2, w1)]}
