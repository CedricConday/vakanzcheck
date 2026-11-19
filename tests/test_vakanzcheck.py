import json
import stat
from pathlib import Path

import pytest

from vakanzcheck import adcheck, check, report, rules, sources
from vakanzcheck.fetch import Http, Replay, SourceError, shrink
from vakanzcheck.salary import Offer, ad_monthly, quartiles
from vakanzcheck.subscription import CliError, Subscription

FIXTURES = Path(__file__).parent / "fixtures"
AD = (Path(__file__).parent.parent / "demo" / "cnc-fraeser-guetersloh.txt").read_text("utf-8")


def recorded_vacancy(**kw):
    """The case the fixtures in tests/fixtures/guetersloh were recorded from."""
    return check.Vacancy(beruf="Zerspanungsmechaniker/in", wo="Gütersloh", radius_km=10,
                         offer=Offer(3600, "month"), **kw)


class FakeHttp(Http):
    def __init__(self, answer):
        super().__init__(pause=0)
        self.answer = answer

    def get(self, url, params, api_key):
        return self.answer(url, params)


# --- end to end on recorded responses ---------------------------------------------------------

def test_recorded_run_offline():
    r = check.run(recorded_vacancy(ad_text=AD), Replay(FIXTURES / "guetersloh"))
    assert r["occupation"]["kldb"] == "24232"
    assert r["occupation"]["source"].startswith("BERUFENET")
    assert r["salary"]["region"] == "Nordrhein-Westfalen" and r["salary"]["region_inferred"]
    ov = r["salary"]["regional"]["overall"]
    assert ov["q25"] < ov["median"] < ov["q75"]
    c = r["competition"]
    assert c["same_beruf"] == c["same_beruf_direct"] + c["same_beruf_zeitarbeit"]
    assert c["same_beruf"] <= c["query_total"] and not c["truncated"]
    assert c["stated_pay"]["stating"] <= c["stated_pay"]["ads"] == c["same_beruf"]
    # 3600 € is below the NRW lower quartile for KldB 24232 in the recording.
    assert r["verdict"]["findings"][0]["grade"] == rules.RED
    assert r["verdict"]["label"] == "schwer besetzbar"
    assert r["ad_check"]["backend"] == "keywords"
    assert all(q["status"] == "replay" for q in r["requests"])
    md = report.render(r)
    assert "Einschätzung: schwer besetzbar" in md and "Stichwortsuche" in md


def test_given_kldb_skips_lookup_and_explicit_region_is_kept():
    r = check.run(recorded_vacancy(kldb="24232", region_id=8), Replay(FIXTURES / "guetersloh"))
    assert r["occupation"]["source"] == "given"
    assert not r["salary"]["region_inferred"]
    assert r["ad_check"] is None


def test_replay_refuses_unrecorded_requests():
    with pytest.raises(SourceError, match="no fixture"):
        check.run(check.Vacancy("Mechatroniker/in", "Bielefeld", 25, Offer(3700, "month")),
                  Replay(FIXTURES / "guetersloh"))


# --- salary arithmetic -----------------------------------------------------------------------

def test_offer_conversions():
    assert Offer(20, "hour", hours=40).base == pytest.approx(20 * 40 * 365.25 / 7 / 12)
    o = Offer(3600, "month", payments=13, extra=600)
    assert o.base == 3600 and o.total == pytest.approx(3600 * 13 / 12 + 50)
    assert Offer(48000, "year").base == Offer(48000, "year").total == 4000
    with pytest.raises(ValueError):
        Offer(3600, "week")
    with pytest.raises(ValueError):
        Offer(3600, "month", payments=11)


def test_ad_salaries():
    h = 40
    assert ad_monthly({"verguetungsangabe": "STUNDENLOHN", "festgehalt": 20.0}, h) \
        == pytest.approx(20 * 40 * 365.25 / 7 / 12)
    assert ad_monthly({"verguetungsangabe": "JAHRESGEHALT", "gehaltsspanneVon": 48000.0,
                       "gehaltsspanneBis": 60000.0}, h) == 4500
    assert ad_monthly({"verguetungsangabe": "KEINE_ANGABEN"}, h) is None
    # an "hourly" wage of 3500 is a monthly figure in the wrong field: dropped, not converted
    assert ad_monthly({"verguetungsangabe": "STUNDENLOHN", "festgehalt": 3500.0}, h) is None
    assert quartiles([1, 2, 3]) is None
    assert quartiles([1, 2, 3, 4, 5])["median"] == 3


def test_suppressed_and_censored_salary_cells_become_none():
    row = {"gender": {"id": 1}, "ageCategory": {"id": 1}, "branche": {"bezeichnung": "Gesamt"},
           "region": {"id": 8, "bezeichnung": "Nordrhein-Westfalen",
                      "beitragsBemessungsGrenze": 8050}}
    data = [{**row, "entgelt": -1, "entgeltQ25": -1, "entgeltQ75": -1, "besetzung": -42},
            {**row, "branche": {"bezeichnung": "X"}, "entgelt": 8050, "entgeltQ25": 7000,
             "entgeltQ75": -1, "besetzung": 1200},
            {**row, "gender": {"id": 2}, "entgelt": 4000}]
    rows = sources.salary_rows(FakeHttp(lambda u, p: data), "26112", 8)
    assert len(rows) == 2
    assert rows[0].median is None and rows[0].employees is None and not rows[0].usable
    assert rows[1].median is None and rows[1].q25 == 7000


# --- rules ----------------------------------------------------------------------------------

def test_salary_rule_bands():
    assert rules.salary_vs_region(3400, 4000, 3500, "NRW").grade == rules.RED
    assert rules.salary_vs_region(3500, 4000, 3500, "NRW").grade == rules.AMBER
    assert rules.salary_vs_region(4000, 4000, 3500, "NRW").grade == rules.GREEN
    assert rules.salary_vs_region(4000, None, None, "NRW").grade == rules.NODATA
    few = {"n": rules.MIN_STATED - 1, "q25": 1, "median": 2, "q75": 3}
    assert rules.salary_vs_ads(3000, few).grade == rules.NODATA
    enough = {**few, "n": rules.MIN_STATED, "q25": 3000, "median": 3300}
    assert rules.salary_vs_ads(2999, enough).grade == rules.RED
    assert rules.salary_vs_ads(3000, enough).grade == rules.AMBER
    assert rules.salary_vs_ads(3300, enough).grade == rules.GREEN


def test_competition_and_verdict():
    assert rules.competition(rules.MEDIUM_COMPETITION - 1, 25).grade == rules.GREEN
    assert rules.competition(rules.MEDIUM_COMPETITION, 25).grade == rules.AMBER
    assert rules.competition(rules.HIGH_COMPETITION, 25).grade == rules.RED

    def f(rule, grade):
        return rules.Finding(rule, grade, "")

    def label(s1, s2, w1):
        return rules.verdict(f("S1", s1), f("S2", s2), f("W1", w1))["label"]

    G, A, R, N = rules.GREEN, rules.AMBER, rules.RED, rules.NODATA
    assert label(R, G, G) == "schwer besetzbar"
    assert label(A, G, R) == "schwer besetzbar"
    assert label(G, R, R) == "schwer besetzbar"
    assert label(A, G, A) == "besetzbar mit Aufwand"
    assert label(G, G, R) == "besetzbar mit Aufwand"
    assert label(G, N, G) == "gut besetzbar"
    assert label(N, N, R) == "nicht bewertbar"


# --- occupation lookup ----------------------------------------------------------------------

def test_lookup_needs_an_exact_name():
    hits = {"_embedded": {"berufSucheList": [{"id": 1, "kurzBezeichnungNeutral": "Elektroniker/in"
                                                          " - Betriebstechnik"}]}}
    with pytest.raises(LookupError, match="pass --kldb"):
        sources.resolve_kldb(FakeHttp(lambda u, p: hits), "Elektroniker")
    with pytest.raises(ValueError):
        sources.normalise_kldb("2611")
    assert sources.normalise_kldb("B 26112") == "26112"
    assert sources.kldb_label("26112").startswith("Berufe in der Mechatronik")


# --- ad text --------------------------------------------------------------------------------

def test_keyword_check_does_not_read_shift_willingness_as_on_call():
    checks = {c.id: c for c in adcheck.keyword_check(AD)}
    assert checks["schicht"].status == "gefunden"
    assert checks["bereitschaft"].status == "nicht gefunden"
    assert checks["gehalt"].status == "nicht gefunden"  # "Leistungsgerechte Bezahlung"
    assert checks["anforderungen"].status == "nicht prüfbar"
    paid = {c.id: c for c in adcheck.keyword_check("21,50 € pro Stunde, 35-Stunden-Woche")}
    assert paid["gehalt"].status == "gefunden" and paid["arbeitszeit"].status == "gefunden"


def test_quote_matching():
    text = "Wir bieten:\n- Eine  attraktive Vergütung\n- Einen unbefristeten Vertrag"
    assert adcheck.quote_in_text("eine attraktive vergütung", text)
    assert adcheck.quote_in_text("Eine attraktive … unbefristeten Vertrag", text)
    assert not adcheck.quote_in_text("3.800 € brutto", text)
    assert not adcheck.quote_in_text("unbefristeten Vertrag ... attraktive", text)


def test_model_check_downgrades_unverified_quotes():
    def ask(system, prompt, schema):
        assert "Leistungsgerechte Bezahlung" in prompt
        assert schema["properties"]["pruefungen"]["items"]["properties"]["status"]["enum"] \
            == list(adcheck.STATUSES)
        return {"pruefungen": [
            {"id": "gehalt", "status": "genannt", "zitat": "3.800 € brutto", "hinweis": ""},
            {"id": "schicht", "status": "unklar", "zitat": "Bereitschaft zur Schichtarbeit",
             "hinweis": "Schichtmodell fehlt"},
        ]}, {"served_by": ["claude-opus-5-5"], "requested_model": "claude-opus-5-5"}

    checks, _meta = adcheck.model_check(AD, "Zerspanungsmechaniker/in", ask)
    by_id = {c.id: c for c in checks}
    assert by_id["gehalt"].status == "unklar" and by_id["gehalt"].quote_verified is False
    assert by_id["schicht"].status == "unklar" and by_id["schicht"].quote_verified is True
    assert by_id["urlaub"].status == "unklar" and "ausgelassen" in by_id["urlaub"].hinweis
    assert len(checks) == len(adcheck.CRITERIA)


def _fake_claude(tmp_path, payload) -> str:
    script = tmp_path / "claude"
    script.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                      "open(sys.argv[0] + '.args', 'w').write(json.dumps(sys.argv[1:]))\n"
                      "sys.stdin.read()\n"
                      f"print(json.dumps({payload!r}))\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_subscription_runs_claude_bare(tmp_path):
    payload = {"is_error": False, "structured_output": {"pruefungen": []},
               "modelUsage": {"claude-opus-5-5": {}}, "total_cost_usd": 0.01}
    binary = _fake_claude(tmp_path, payload)
    raw, meta = Subscription(binary=binary)("sys", "prompt", {"type": "object"})
    assert raw == {"pruefungen": []} and meta["served_by"] == ["claude-opus-5-5"]
    args = json.loads(Path(binary + ".args").read_text())
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--json-schema"):
        assert flag in args
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--output-format") + 1] == "json"


def test_subscription_errors_are_loud(tmp_path):
    binary = _fake_claude(tmp_path, {"is_error": True, "result": "quota"})
    with pytest.raises(CliError, match="quota"):
        Subscription(binary=binary)("sys", "prompt", {})


# --- fixtures and privacy -------------------------------------------------------------------

def test_shrink_keeps_only_read_fields_and_pseudonymises():
    names = {}

    def pseudonym(n):
        return names.setdefault(n, f"Arbeitgeber {len(names) + 1:03d} GmbH")

    body = {"maxErgebnisse": 1, "facetten": {"topberuf": {"counts": {"A": 1}, "scores": {}}},
            "ergebnisliste": [{"firma": "Max Muster Elektro", "stellenangebotsTitel": "X",
                               "externeURL": "https://example.org", "chiffrenummer": "1",
                               "referenznummer": "r1", "alleBerufe": ["A"],
                               "stellenlokationen": [{"adresse": {"region": "BAYERN",
                                                                  "strasse": "Weg 1"}}]}]}
    small = shrink("https://x/jobsuche-service/pc/v6/jobs", body, pseudonym)
    item = small["ergebnisliste"][0]
    assert item["firma"] == "Arbeitgeber 001 GmbH"
    assert "stellenangebotsTitel" not in item and "externeURL" not in item
    assert item["stellenlokationen"] == [{"adresse": {"region": "BAYERN"}}]
    assert "scores" not in small["facetten"]["topberuf"]


def test_employer_names_without_legal_form_are_hidden():
    assert check.display_employer("Muster Zerspanung GmbH & Co. KG").endswith("KG")
    assert check.display_employer("Max Muster") == check.MASKED
