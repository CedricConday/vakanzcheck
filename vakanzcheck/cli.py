"""vakanzcheck command line."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import check, report, sources
from .fetch import Http, Recorder, Replay, SourceError
from .salary import Offer


def _region(value: str) -> int:
    if value.isdigit() and int(value) in sources.EGA_REGIONS:
        return int(value)
    for rid, name in sources.EGA_REGIONS.items():
        if name.casefold() == value.casefold():
            return rid
    raise argparse.ArgumentTypeError(f"unknown Entgeltatlas region {value!r}")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("ü", "ue").replace("ö", "oe")
                  .replace("ä", "ae").replace("ß", "ss")).strip("-")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vakanzcheck", description=__doc__)
    p.add_argument("--beruf", required=True,
                   help='BA occupation name as Jobsuche lists it, e.g. "Mechatroniker/in"')
    p.add_argument("--kldb", help="five-digit KldB 2010 code; looked up in BERUFENET if omitted")
    p.add_argument("--title", help="the vacancy's own job title, for the report header")
    p.add_argument("--wo", required=True, help="place or postcode")
    p.add_argument("--radius", type=int, default=25, help="km (default 25)")
    p.add_argument("--salary", type=float, required=True, help="offered gross pay")
    p.add_argument("--per", choices=["month", "hour", "year"], default="month")
    p.add_argument("--hours", type=float, default=40.0, help="hours per week (default 40)")
    p.add_argument("--payments", type=float, default=12.0,
                   help="monthly salaries per year, e.g. 13 (default 12; not with --per year)")
    p.add_argument("--extra", type=float, default=0.0,
                   help="further one-off payments per year in € (holiday/Christmas money)")
    p.add_argument("--ega-region", type=_region,
                   help="Entgeltatlas region id or name; default: the Land of nearby postings")
    p.add_argument("--ad", type=Path, help="ad text file to check")
    p.add_argument("--backend", choices=["keywords", "subscription"], default="keywords",
                   help="how to check the ad text (default keywords; subscription uses claude -p)")
    p.add_argument("--out", type=Path, help="output directory (default results/<slug>)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--record", type=Path, help="also write shrunk responses here as fixtures")
    src.add_argument("--replay", type=Path, help="read responses from fixtures, no network")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    http = Replay(a.replay) if a.replay else Recorder(a.record) if a.record else Http()
    ask = None
    if a.backend == "subscription" and a.ad:
        from .subscription import Subscription
        ask = Subscription()
    try:
        v = check.Vacancy(
            beruf=a.beruf, wo=a.wo, radius_km=a.radius,
            kldb=sources.normalise_kldb(a.kldb) if a.kldb else None,
            region_id=a.ega_region, title=a.title,
            offer=Offer(a.salary, a.per, hours=a.hours, payments=a.payments, extra=a.extra),
            ad_text=a.ad.read_text(encoding="utf-8") if a.ad else None)
    except (ValueError, OSError) as e:  # bad --kldb or offer figures, unreadable --ad
        print(f"vakanzcheck: {e}", file=sys.stderr)
        return 2
    try:
        result = check.run(v, http, ask)
    except (SourceError, LookupError) as e:
        print(f"vakanzcheck: {e}", file=sys.stderr)
        return 2
    out = a.out or Path("results") / slug(f"{a.title or a.beruf} {a.wo}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report.render(result), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                     encoding="utf-8")
    print(f"{result['verdict']['label']}: {out / 'report.md'}")
    return 0
