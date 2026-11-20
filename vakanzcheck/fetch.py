"""GET JSON from the BA services, live or from recorded fixtures.

`Http` fetches live. `Recorder` fetches live and writes each response to a
directory after shrinking it to the fields vakanzcheck reads and replacing
employer names with stable pseudonyms. `Replay` serves those files and fails on
any request that was not recorded, so tests never touch the network by accident.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "vakanzcheck/0.1"


class SourceError(RuntimeError):
    """A data source answered with an error or with something unreadable."""


def request_key(url: str, params: dict) -> str:
    query = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()))
    digest = hashlib.sha1(f"{url}?{query}".encode()).hexdigest()[:12]
    tail = [p for p in urllib.parse.urlparse(url).path.split("/") if p][-1]
    return f"{tail}-{digest}"


class Http:
    def __init__(self, timeout: float = 30.0, pause: float = 0.2):
        self.timeout = timeout
        self.pause = pause
        self.log: list[dict] = []

    def get(self, url: str, params: dict, api_key: str):
        full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        req = urllib.request.Request(full, headers={"X-API-Key": api_key,
                                                    "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raise SourceError(f"HTTP {e.code} from {full}") from e
        except urllib.error.URLError as e:
            raise SourceError(f"{full} unreachable: {e.reason}") from e
        except (TimeoutError, OSError, http.client.HTTPException) as e:
            # A timeout or a dropped connection while reading the body arrives here,
            # not as URLError.
            raise SourceError(f"{full} failed: {type(e).__name__}: {e}") from e
        finally:
            time.sleep(self.pause)
        self.log.append({"url": full, "status": status})
        if not body.strip():
            raise SourceError(f"empty body (HTTP {status}) from {full}")
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise SourceError(f"not JSON from {full}: {body[:200]!r}") from e


# Posting fields vakanzcheck reads. Everything else, including the ad title and
# any link or cipher number, is dropped before a response is written to disk.
POSTING_FIELDS = (
    "referenznummer", "hauptberuf", "alleBerufe", "firma", "entfernung",
    "verguetungsangabe", "artDerVerguetung", "gehaltsspanneVon", "gehaltsspanneBis",
    "festgehalt", "arbeitszeitVollzeit", "arbeitszeitSchichtNachtWochenende",
)


# Facets vakanzcheck reads: occupation counts (top 20 and up to 100) and the
# staffing-agency split.
FACETS = ("topberuf", "beruf", "zeitarbeit")


def shrink(url: str, body, pseudonym):
    """Reduce a response to what the code reads, for a small, shareable fixture."""
    if "/jobsuche-service/" in url and isinstance(body, dict):
        items = []
        for x in body.get("ergebnisliste") or []:
            y = {k: x[k] for k in POSTING_FIELDS if k in x}
            if "firma" in y:
                y["firma"] = pseudonym(y["firma"])
            locs = x.get("stellenlokationen") or []
            if locs:
                y["stellenlokationen"] = [{"adresse": {"region": locs[0].get("adresse", {})
                                                       .get("region")}}]
            items.append(y)
        facets = body.get("facetten") or {}
        return {"maxErgebnisse": body.get("maxErgebnisse"), "page": body.get("page"),
                "size": body.get("size"), "woOutput": body.get("woOutput"),
                "facetten": {k: {"counts": (facets.get(k) or {}).get("counts", {})}
                             for k in FACETS if k in facets},
                "ergebnisliste": items}
    if "/bnet/" in url:
        if isinstance(body, list):
            return [{k: e.get(k) for k in ("id", "kurzBezeichnungNeutral", "kldb2010", "codenr")}
                    for e in body]
        if isinstance(body, dict):
            hits = (body.get("_embedded") or {}).get("berufSucheList") or []
            return {"_embedded": {"berufSucheList": [
                {"id": h.get("id"), "kurzBezeichnungNeutral": h.get("kurzBezeichnungNeutral")}
                for h in hits]}, "page": body.get("page")}
    return body


class Recorder(Http):
    def __init__(self, directory: Path, **kw):
        super().__init__(**kw)
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._names: dict[str, str] = {}

    def _pseudonym(self, name: str) -> str:
        if name not in self._names:
            self._names[name] = f"Arbeitgeber {len(self._names) + 1:03d} GmbH"
        return self._names[name]

    def get(self, url: str, params: dict, api_key: str):
        body = super().get(url, params, api_key)
        small = shrink(url, body, self._pseudonym)
        path = self.dir / f"{request_key(url, params)}.json"
        path.write_text(json.dumps(small, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return body  # the run itself sees the real response; only the file is shrunk


class Replay(Http):
    def __init__(self, directory: Path):
        super().__init__(pause=0)
        self.dir = Path(directory)

    def get(self, url: str, params: dict, api_key: str):
        path = self.dir / f"{request_key(url, params)}.json"
        if not path.exists():
            raise SourceError(f"no fixture {path.name} for {url} {params}")
        self.log.append({"url": f"{url}?{urllib.parse.urlencode(params)}", "status": "replay"})
        return json.loads(path.read_text(encoding="utf-8"))
