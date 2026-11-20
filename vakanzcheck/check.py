"""Run one vacancy through all sources and rules; return everything as one dict."""

from __future__ import annotations

import collections
import datetime as dt
import re
import statistics
from dataclasses import asdict, dataclass

from . import adcheck, rules, sources
from .fetch import Http
from .salary import AdPay, Offer, ad_pay

# Employer names are shown only when they carry a company legal form. A sole
# trader's business name is usually a person's name, so it is not printed.
COMPANY = re.compile(r"\b(?:GmbH|mbH|AG|KG|KGaA|SE|UG|OHG|eG|e\.\s?V\.|Holding|Group|Gruppe)\b"
                     r"|gGmbH|\bInc\b|\bLtd\b|\bB\.V\.")
MASKED = "Einzelunternehmen (Name nicht angezeigt)"


def display_employer(name: str) -> str:
    return name if COMPANY.search(name) else MASKED


@dataclass
class Vacancy:
    beruf: str  # BA occupation name as Jobsuche lists it, e.g. "Mechatroniker/in"
    wo: str  # place or postcode
    radius_km: int
    offer: Offer
    kldb: str | None = None
    region_id: int | None = None  # Entgeltatlas region; inferred from postings if None
    title: str | None = None  # the client's own job title, for the report header
    ad_text: str | None = None


def pay_table(pays: list[tuple[AdPay, bool]]) -> list[dict]:
    """Stated pay per unit and employer type, in the unit the ads use: like with like."""
    out = []
    for unit in ("hour", "month", "year"):
        for direct in (True, False):
            vals = [p.value for p, d in pays if p.unit == unit and d == direct]
            if vals:
                out.append({"unit": unit, "direct": direct, "n": len(vals),
                            "median": statistics.median(vals),
                            "min": min(vals), "max": max(vals)})
    return out


def _salary_block(rows: list[sources.SalaryRow]) -> dict:
    overall = next((r for r in rows if r.branche == "Gesamt"), None)
    branches = [asdict(r) for r in rows if r.branche != "Gesamt" and r.usable]
    return {"overall": asdict(overall) if overall else None, "branches": branches}


def run(v: Vacancy, http: Http, ask=None, *, today: dt.date | None = None) -> dict:
    occ = (sources.Occupation(v.beruf, sources.normalise_kldb(v.kldb), "given")
           if v.kldb else sources.resolve_kldb(http, v.beruf))
    level = sources.LEVELS.get(occ.kldb[-1], "?")

    every = sources.search_postings(http, v.beruf, v.wo, v.radius_km)
    direct = sources.search_postings(http, v.beruf, v.wo, v.radius_km, zeitarbeit=False)
    same = [x for x in every.items if sources.lists_beruf(x, v.beruf)]
    sources.check_beruf_form(v.beruf, every, len(same))
    if not same:
        sources.check_beruf_known(http, v.beruf, every)
    direct_refs = {x.get("referenznummer") for x in direct.items}
    truncated = every.truncated or direct.truncated
    if not truncated:
        count_every = len(same)
        count_direct = sum(sources.lists_beruf(x, v.beruf) for x in direct.items)
        count_source = "Einzelabruf aller Anzeigen"
    else:
        # More hits than can be paged. Counts come from the facets, which cover all
        # hits; everything per posting (agency flag, pay, employers) comes from the
        # first pages only and is marked as a sample.
        count_every = sources.facet_count(every, v.beruf)
        count_direct = sources.facet_count(direct, v.beruf)
        count_source = "Facette der Jobsuche"
        if count_every is None or count_direct is None:
            raise LookupError(f"{v.beruf!r} not among the occupations the Jobsuche facet lists "
                              "for this search; narrow the radius")
    sample = {"truncated": truncated, "items_read": len(every.items),
              "query_total": every.total}

    agency_names = {x.get("firma") for x in same if x.get("referenznummer") not in direct_refs}
    employers = collections.Counter(x.get("firma") for x in same if x.get("firma"))
    top = [{"firma": display_employer(" ".join(f.split())), "stellen": n,
            "zeitarbeit": f in agency_names} for f, n in employers.most_common(5)]
    related = [{"beruf": b, "stellen": n} for b, n in
               sorted(every.facet.items(), key=lambda kv: -kv[1]) if b != v.beruf][:5]
    pays = []  # (AdPay, is_direct)
    for x in same:
        pay = ad_pay(x, v.offer.hours)
        if pay is not None:
            pays.append((pay, x.get("referenznummer") in direct_refs))
    stated_direct = [p for p, d in pays if d]
    stated_all = [p for p, _ in pays]
    shift = sum(bool(x.get("arbeitszeitSchichtNachtWochenende")) for x in same)

    region_id = v.region_id or sources.region_from_postings(every.items)
    if region_id is None:
        raise LookupError("cannot infer the Land from the postings; pass --ega-region")
    regional = sources.salary_rows(http, occ.kldb, region_id)
    national = sources.salary_rows(http, occ.kldb, 1) if region_id != 1 else regional
    sal_region, sal_de = _salary_block(regional), _salary_block(national)
    ov = sal_region["overall"] or {}

    s1 = rules.salary_vs_region(v.offer.total, ov.get("median"), ov.get("q25"),
                                sources.EGA_REGIONS.get(region_id, str(region_id)))
    basis, direct_only = rules.ad_basis(stated_direct, stated_all)
    s2 = rules.salary_vs_ads(v.offer, basis, direct_only,
                             0 if direct_only else len(stated_all) - len(stated_direct))
    w1 = rules.competition(count_every, v.radius_km)

    ad = None
    if v.ad_text:
        if ask is None:
            ad = {"backend": "keywords", "label": "Stichwortsuche (Regeln, kein Modell)",
                  "checks": adcheck.as_dicts(adcheck.keyword_check(v.ad_text))}
        else:
            checks, meta = adcheck.model_check(v.ad_text, v.beruf, ask)
            ad = {"backend": "subscription", "label": "Modellurteil (Claude), keine Daten",
                  "meta": meta, "checks": adcheck.as_dicts(checks)}

    return {
        "tool": "vakanzcheck 0.1.0",
        "date": (today or dt.date.today()).isoformat(),
        "vacancy": {"title": v.title or v.beruf, "beruf": v.beruf, "wo": v.wo,
                    "place": every.place, "radius_km": v.radius_km,
                    "offer": {**asdict(v.offer), "monthly_base": round(v.offer.base),
                              "monthly_total": round(v.offer.total)},
                    "ad_text": v.ad_text},
        "occupation": {"kldb": occ.kldb, "kldb_label": sources.kldb_label(occ.kldb),
                       "level": level, "source": occ.source},
        "salary": {"region_id": region_id,
                   "region": sources.EGA_REGIONS.get(region_id, str(region_id)),
                   "region_inferred": v.region_id is None,
                   "stichtag": sources.EGA_STICHTAG, "ceiling": ov.get("ceiling"),
                   "stichtag_confirmed": ov.get("ceiling") == sources.EGA_CEILING,
                   "regional": sal_region, "germany": sal_de},
        "competition": {
            "same_beruf": count_every, "same_beruf_direct": count_direct,
            "same_beruf_zeitarbeit": max(count_every - (count_direct or 0), 0),
            "count_source": count_source, "sample": sample,
            "query_total": every.total,
            "related": related, "top_employers": top,
            "stated_pay": {"ads": len(same), "stating": len(stated_all),
                           "stating_direct": len(stated_direct),
                           "by_unit": pay_table(pays)},
            "shift_flagged": shift,
        },
        "verdict": rules.verdict(s1, s2, w1),
        "ad_check": ad,
        "requests": http.log,
    }
