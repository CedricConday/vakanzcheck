"""Regenerate vakanzcheck/data/kldb2010.csv from the BA DKZ service.

The table maps every five-digit KldB 2010 code to its official label, so a
report can name the occupation group behind a salary figure without a network
call. Run it from the repository root:

    python scripts/fetch_kldb.py
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import time
import urllib.request
from pathlib import Path

URL = "https://rest.arbeitsagentur.de/infosysbub/dkz-rest/pc/v1/kldb2010?page={page}&size=50"
KEY = "infosysbub-ega"
OUT = Path(__file__).resolve().parent.parent / "vakanzcheck" / "data" / "kldb2010.csv"


def fetch(page: int) -> dict:
    req = urllib.request.Request(URL.format(page=page), headers={"X-API-Key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> None:
    rows: dict[str, str] = {}
    first = fetch(1)
    pages = first["page"]["totalPages"]
    for page in range(1, pages + 1):
        data = first if page == 1 else fetch(page)
        for e in data["_embedded"]["berufssystematiken"]:
            code = e["codenr"].removeprefix("B ").strip()
            if len(code) == 5 and code.isdigit():
                rows[code] = e["bezeichnungNeutral"]
        time.sleep(0.2)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write(f"# KldB 2010, five-digit codes. Source: {URL.split('?')[0]}, "
                f"fetched {dt.date.today().isoformat()}\n")
        w = csv.writer(f)
        w.writerow(["kldb", "label"])
        for code in sorted(rows):
            w.writerow([code, rows[code]])
    print(f"{len(rows)} codes -> {OUT}")


if __name__ == "__main__":
    main()
