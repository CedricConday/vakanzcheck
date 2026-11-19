"""The feasibility verdict: three explicit rules and how they combine.

Each rule reads one measured number and gives `gruen`, `gelb`, `rot` or
`keine Daten`. The cut-offs are choices, written down here so they can be
argued with and changed; they are not findings.

S1  Offer vs. regional pay (Entgeltatlas, Land level, incl. one-off payments)
    below the lower quartile -> rot; between lower quartile and median -> gelb;
    at or above the median -> gruen.
S2  Offer vs. pay stated in competing ads for the same occupation in the radius.
    Each ad is compared on its own unit's footing (hourly wage vs. the offer's
    base pay, annual salary vs. the offer's pay incl. one-off payments; see
    salary.py), and S2 counts the share of ads that pay more than the offer:
    at least 75 % -> rot; at least 50 % -> gelb; else gruen. That is the offer
    below the ads' lower quartile, below their median, or at/above it.
    Basis: direct employers' ads when MIN_STATED of them state pay, else all ads
    (ad_basis). Fewer than MIN_STATED ads -> keine Daten. If the basis is
    mostly staffing agencies, the grade is shown with a caveat and the verdict
    treats S2 as keine Daten.
W1  Competition: open postings for the same occupation in the radius
    at least HIGH_COMPETITION -> rot; at least MEDIUM_COMPETITION -> gelb; else gruen.

Verdict (a rule carrying a caveat counts as keine Daten here)
    schwer besetzbar        S1 rot; or S1 gelb and W1 rot; or S2 rot and W1 rot
    besetzbar mit Aufwand   any other rot or gelb
    gut besetzbar           no rot and no gelb (a rule without data counts as neither)
    nicht bewertbar         neither S1 nor S2 has data
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .salary import AdPay, Offer

MIN_STATED = 5
HIGH_COMPETITION = 100
MEDIUM_COMPETITION = 30

GREEN, AMBER, RED, NODATA = "gruen", "gelb", "rot", "keine Daten"


@dataclass
class Finding:
    rule: str
    grade: str
    reason: str
    caveat: str = ""


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


def ad_basis(direct: list[AdPay], every: list[AdPay]) -> tuple[list[AdPay], bool]:
    """Which stated salaries S2 compares against, and whether that is the direct-only set.

    Direct employers' ads when at least MIN_STATED of them state pay, otherwise all
    ads. Staffing agencies state pay far more often and would otherwise set the
    benchmark a direct employer is measured against.
    """
    if len(direct) >= MIN_STATED:
        return direct, True
    return every, False


def salary_vs_ads(offer: Offer, pays: list[AdPay], direct_only: bool,
                  agency_in_basis: int) -> Finding:
    basis = "Anzeigen ohne Zeitarbeit" if direct_only else "Anzeigen (inkl. Zeitarbeit)"
    n = len(pays)
    if n < MIN_STATED:
        return Finding("S2", NODATA, f"nur {n} {basis} mit Gehaltsangabe "
                                     f"(mindestens {MIN_STATED} nötig)")
    above = sum(p.above(offer) for p in pays)
    share = above / n
    if share >= 0.75:
        grade = RED
    elif share >= 0.5:
        grade = AMBER
    else:
        grade = GREEN
    reason = (f"{above} von {n} {basis} mit Gehaltsangabe zahlen mehr als das Angebot "
              f"({100 * share:.0f} %; Stundenlöhne gegen Grundgehalt "
              f"{_fmt(offer.base)}, Jahresgehälter gegen Gesamtbezüge {_fmt(offer.total)}/Monat)")
    caveat = ""
    if agency_in_basis * 2 > n:
        caveat = (f"unsicher: {agency_in_basis} von {n} Vergleichsanzeigen sind Zeitarbeit; "
                  "fließt nicht in die Einschätzung ein")
    return Finding("S2", grade, reason, caveat)


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
    findings = [asdict(f) for f in (s1, s2, w1)]
    s1, s2, w1 = (f if not f.caveat else Finding(f.rule, NODATA, f.reason) for f in (s1, s2, w1))
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
    return {"label": label, "findings": findings}
