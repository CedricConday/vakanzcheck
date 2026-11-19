"""The three BA services vakanzcheck reads, and what it takes from each.

All three are public and keyless in practice: each needs only a fixed client id
in the `X-API-Key` header, the same id the BA's own web pages send. Verified
live on 2026-11-19:

- Jobsuche:    GET {JOBSUCHE}/pc/v6/jobs            X-API-Key: jobboerse-jobsuche
  (/pc/v4/jobs and /pc/v4/app/jobs answer 403 with a one-byte body)
- Entgeltatlas: GET {ENTGELTATLAS}/pc/v1/entgelte/{kldb}  X-API-Key: infosysbub-ega
  (returns [] when the `l` level parameter disagrees with the code's fifth digit,
  so it is never sent; the code already fixes the level)
- BERUFENET:   GET {BERUFENET}/berufe?suchwoerter=…, /berufe/{id}
               X-API-Key: infosysbub-berufenet
"""

from __future__ import annotations

import csv
import re
import statistics
from dataclasses import dataclass, field
from importlib import resources

from .fetch import Http, SourceError

JOBSUCHE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
ENTGELTATLAS = "https://rest.arbeitsagentur.de/infosysbub/entgeltatlas"
BERUFENET = "https://rest.arbeitsagentur.de/infosysbub/bnet/pc/v1"
JOBSUCHE_KEY = "jobboerse-jobsuche"
ENTGELTATLAS_KEY = "infosysbub-ega"
BERUFENET_KEY = "infosysbub-berufenet"

# Entgeltatlas region ids (GET /pc/v1/regionen). Below the Länder there are only
# twelve large cities; there is no Kreis level.
EGA_REGIONS = {
    1: "Deutschland", 2: "Ostdeutschland", 3: "Westdeutschland", 4: "Schleswig-Holstein",
    5: "Hamburg", 6: "Niedersachsen", 7: "Bremen", 8: "Nordrhein-Westfalen", 9: "Hessen",
    10: "Rheinland-Pfalz", 11: "Baden-Württemberg", 12: "Bayern", 13: "Saarland", 14: "Berlin",
    15: "Brandenburg", 16: "Mecklenburg-Vorpommern", 17: "Sachsen", 18: "Sachsen-Anhalt",
    19: "Thüringen", 20: "Dresden", 21: "Düsseldorf", 22: "Dortmund", 23: "Essen",
    24: "Frankfurt am Main", 25: "Nürnberg", 26: "Hannover", 27: "Köln", 28: "Leipzig",
    29: "München", 30: "Stuttgart",
}

# Jobsuche writes the Land of a posting as an enum; this maps it to the region id.
JOBSUCHE_LAND_TO_EGA = {
    "SCHLESWIG_HOLSTEIN": 4, "HAMBURG": 5, "NIEDERSACHSEN": 6, "BREMEN": 7,
    "NORDRHEIN_WESTFALEN": 8, "HESSEN": 9, "RHEINLAND_PFALZ": 10, "BADEN_WUERTTEMBERG": 11,
    "BAYERN": 12, "SAARLAND": 13, "BERLIN": 14, "BRANDENBURG": 15,
    "MECKLENBURG_VORPOMMERN": 16, "SACHSEN": 17, "SACHSEN_ANHALT": 18, "THUERINGEN": 19,
}

# The API does not return its reference date. The Entgeltatlas help page
# (arbeitsagentur.de/hilfe-entgeltatlas, read 2026-11-19) gives 31.12.2025, and
# the response's contribution ceiling (8.050 €, the 2025 value) agrees. When the
# ceiling in a response differs, the data has moved on and the report says so.
EGA_STICHTAG = "31.12.2025"
EGA_CEILING = 8050

LEVELS = {"1": "Helfer", "2": "Fachkraft", "3": "Spezialist", "4": "Experte"}


def kldb_label(code: str) -> str | None:
    text = resources.files("vakanzcheck").joinpath("data/kldb2010.csv").read_text("utf-8")
    rows = csv.reader(line for line in text.splitlines() if not line.startswith("#"))
    return next((label for c, label in rows if c == code), None)


def normalise_kldb(code: str) -> str:
    digits = re.sub(r"\D", "", code)
    if len(digits) != 5:
        raise ValueError(f"KldB code must have five digits, got {code!r}")
    return digits


# --- BERUFENET: occupation name -> KldB --------------------------------------------------------

@dataclass
class Occupation:
    beruf: str
    kldb: str
    source: str  # "BERUFENET id …" or "given"
    candidates: list[str] = field(default_factory=list)


def resolve_kldb(http: Http, beruf: str) -> Occupation:
    """Find the BERUFENET entry whose name is exactly `beruf` and read its KldB code.

    Only an exact name match is accepted. A near match is reported, not used:
    "Elektroniker" alone matches a dozen occupations in different KldB groups.
    """
    hits = http.get(f"{BERUFENET}/berufe", {"suchwoerter": beruf, "page": 0}, BERUFENET_KEY)
    found = (hits.get("_embedded") or {}).get("berufSucheList") or []
    names = [h["kurzBezeichnungNeutral"] for h in found]
    exact = [h for h in found if h["kurzBezeichnungNeutral"].casefold() == beruf.casefold()]
    if not exact:
        raise LookupError(
            f"no BERUFENET occupation named exactly {beruf!r}; pass --kldb. "
            f"Candidates: {names[:10] or 'none'}")
    detail = http.get(f"{BERUFENET}/berufe/{exact[0]['id']}", {}, BERUFENET_KEY)
    code = normalise_kldb((detail[0] if isinstance(detail, list) else detail)["kldb2010"])
    return Occupation(beruf, code, f"BERUFENET id {exact[0]['id']}", names)


# --- Entgeltatlas: salary distribution ----------------------------------------------------------

@dataclass
class SalaryRow:
    region_id: int
    region: str
    branche: str
    median: int | None  # €/month, gross, full-time, incl. one-off payments
    q25: int | None
    q75: int | None
    employees: int | None
    ceiling: int | None  # Beitragsbemessungsgrenze; values at or above it are censored
    censored: tuple[str, ...] = ()  # fields at or above the ceiling, set to None

    @property
    def usable(self) -> bool:
        return self.median is not None or "median" in self.censored


def _value(v) -> int | None:
    return v if isinstance(v, int) and v >= 0 else None


def salary_rows(http: Http, kldb: str, region_id: int) -> list[SalaryRow]:
    """All 'Gesamt' gender/age rows for one region: the overall row and one per branch.

    Negative numbers in the response mark suppressed cells (too few employees);
    they become None. So does any value at or above the contribution ceiling,
    where the true figure is unknown; the row is then marked censored.
    """
    data = http.get(f"{ENTGELTATLAS}/pc/v1/entgelte/{kldb}", {"r": region_id}, ENTGELTATLAS_KEY)
    rows = []
    for e in data:
        if e["gender"]["id"] != 1 or e["ageCategory"]["id"] != 1:
            continue
        ceiling = e["region"].get("beitragsBemessungsGrenze")
        names = {"entgelt": "median", "entgeltQ25": "q25", "entgeltQ75": "q75"}
        vals = {k: _value(e.get(k)) for k in names}
        censored = []
        for k, x in vals.items():
            if x is not None and ceiling and x >= ceiling:
                vals[k] = None
                censored.append(names[k])
        rows.append(SalaryRow(
            region_id=e["region"]["id"], region=e["region"]["bezeichnung"],
            branche=e["branche"]["bezeichnung"], median=vals["entgelt"],
            q25=vals["entgeltQ25"], q75=vals["entgeltQ75"],
            employees=_value(e.get("besetzung")), ceiling=ceiling, censored=tuple(censored)))
    return rows


# --- Jobsuche: competing postings ---------------------------------------------------------------

@dataclass
class PostingSearch:
    query: dict
    total: int  # all hits for the query within the radius
    facet: dict[str, int]  # postings per occupation, top 20 (a posting can list several)
    facet_full: dict[str, int]  # the same, up to 100 occupations
    agency_facet: int | None  # postings the Jobsuche marks as staffing agency
    place: str | None
    items: list[dict]
    truncated: bool


def search_postings(http: Http, beruf: str, wo: str, radius_km: int, *,
                    zeitarbeit: bool = True, max_pages: int = 10) -> PostingSearch:
    """Every open job posting (angebotsart=1, employment) for `beruf` within the radius."""
    params = {"was": beruf, "wo": wo, "umkreis": radius_km, "angebotsart": 1, "size": 100}
    if not zeitarbeit:
        params["zeitarbeit"] = "false"
    items: list[dict] = []
    first = None
    for page in range(1, max_pages + 1):
        body = http.get(f"{JOBSUCHE}/pc/v6/jobs", {**params, "page": page}, JOBSUCHE_KEY)
        if not isinstance(body, dict) or "maxErgebnisse" not in body:
            raise SourceError(f"unexpected Jobsuche response: {str(body)[:200]}")
        first = first or body
        batch = body.get("ergebnisliste") or []
        items += batch
        if not batch or len(items) >= body["maxErgebnisse"]:
            break
    total = first["maxErgebnisse"]
    facets = first.get("facetten") or {}

    def counts(name):
        return dict((facets.get(name) or {}).get("counts") or {})

    agency = counts("zeitarbeit").get("true")
    place = (first.get("woOutput") or {}).get("bereinigterOrt")
    return PostingSearch(query=params, total=total, facet=counts("topberuf"),
                         facet_full=counts("beruf"), agency_facet=agency, place=place,
                         items=items, truncated=len(items) < total)


def facet_count(search: PostingSearch, beruf: str) -> int | None:
    """Postings listing `beruf`, from the facets (they can differ from an item count by one
    or two). None if the occupation is not among the up to 100 the facet lists."""
    return search.facet_full.get(beruf, search.facet.get(beruf))


def lists_beruf(item: dict, beruf: str) -> bool:
    return beruf in (item.get("alleBerufe") or [item.get("hauptberuf")])


def region_from_postings(items: list[dict]) -> int | None:
    """The Land most postings near the centre sit in (nearest 20 km first, then all)."""
    for limit in (20, None):
        lands = [(x.get("stellenlokationen") or [{}])[0].get("adresse", {}).get("region")
                 for x in items if limit is None or (x.get("entfernung") or 0) <= limit]
        ids = [JOBSUCHE_LAND_TO_EGA[r] for r in lands if r in JOBSUCHE_LAND_TO_EGA]
        if ids:
            return statistics.mode(ids)
    return None
