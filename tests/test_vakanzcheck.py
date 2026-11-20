import http.client
import json
import stat
from pathlib import Path

import pytest

from vakanzcheck import adcheck, check, cli, report, rules, sources
from vakanzcheck.fetch import Http, Recorder, Replay, SourceError, shrink
from vakanzcheck.salary import AdPay, Offer, ad_pay
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
    assert c["same_beruf"] <= c["query_total"] and not c["sample"]["truncated"]
    assert c["count_source"] == "Einzelabruf aller Anzeigen"
    sp = c["stated_pay"]
    assert sp["stating_direct"] <= sp["stating"] <= sp["ads"] == c["same_beruf"]
    assert sum(row["n"] for row in sp["by_unit"]) == sp["stating"]
    assert r["salary"]["stichtag_confirmed"]
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
    assert "Zuordnung: per --kldb angegeben" in report.render(r)
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
    with pytest.raises(ValueError, match="annual salary"):
        Offer(48000, "year", payments=13)


def test_ad_salaries_keep_their_unit():
    h = 40
    hourly = ad_pay({"verguetungsangabe": "STUNDENLOHN", "festgehalt": 20.0}, h)
    assert hourly.unit == "hour" and hourly.scale == "base"
    assert hourly.monthly == pytest.approx(20 * 40 * 365.25 / 7 / 12)
    annual = ad_pay({"verguetungsangabe": "JAHRESGEHALT", "gehaltsspanneVon": 48000.0,
                     "gehaltsspanneBis": 60000.0}, h)
    assert (annual.unit, annual.value, annual.monthly, annual.scale) == ("year", 54000, 4500,
                                                                         "total")
    assert ad_pay({"verguetungsangabe": "KEINE_ANGABEN"}, h) is None
    # an "hourly" wage of 3500 is a monthly figure in the wrong field: dropped, not converted
    assert ad_pay({"verguetungsangabe": "STUNDENLOHN", "festgehalt": 3500.0}, h) is None


def test_annual_ads_are_compared_with_pay_including_one_offs():
    # 3.600 x 13 = 46.800 a year. An ad offering 46.000 a year pays less, although
    # 46.000 / 12 = 3.833 is more than the 3.600 base.
    offer = Offer(3600, "month", payments=13)
    assert not AdPay("year", 46000, 46000 / 12, "total").above(offer)
    assert AdPay("hour", 21.0, 21.0 * 40 * 365.25 / 7 / 12, "base").above(offer)


def test_suppressed_and_censored_salary_cells_become_none():
    row = {"gender": {"id": 1}, "ageCategory": {"id": 1}, "branche": {"bezeichnung": "Gesamt"},
           "region": {"id": 8, "bezeichnung": "Nordrhein-Westfalen",
                      "beitragsBemessungsGrenze": 8050}}
    data = [{**row, "entgelt": -1, "entgeltQ25": -1, "entgeltQ75": -1, "besetzung": -42},
            {**row, "branche": {"bezeichnung": "X"}, "entgelt": 6000, "entgeltQ25": 5000,
             "entgeltQ75": 8050, "besetzung": 1200},
            {**row, "gender": {"id": 2}, "entgelt": 4000}]
    rows = sources.salary_rows(FakeHttp(lambda u, p: data), "26112", 8)
    assert len(rows) == 2
    assert rows[0].median is None and rows[0].employees is None and not rows[0].usable
    assert rows[1].q75 is None and rows[1].censored == ("q75",) and rows[1].median == 6000


# --- rules ----------------------------------------------------------------------------------

def test_salary_rule_bands():
    assert rules.salary_vs_region(3400, 4000, 3500, "NRW").grade == rules.RED
    assert rules.salary_vs_region(3500, 4000, 3500, "NRW").grade == rules.AMBER
    assert rules.salary_vs_region(4000, 4000, 3500, "NRW").grade == rules.GREEN
    assert rules.salary_vs_region(4000, None, None, "NRW").grade == rules.NODATA
    offer = Offer(3300, "month")

    def ads(*monthly):
        return [AdPay("month", m, m, "base") for m in monthly]

    few = ads(*[4000] * (rules.MIN_STATED - 1))
    assert rules.salary_vs_ads(offer, few, True, 0).grade == rules.NODATA
    # share of ads paying more than the offer: 80 % rot, 60 % gelb, 40 % gruen
    assert rules.salary_vs_ads(offer, ads(3400, 3400, 3400, 3400, 3300), True, 0).grade \
        == rules.RED
    assert rules.salary_vs_ads(offer, ads(3400, 3400, 3400, 3300, 3000), True, 0).grade \
        == rules.AMBER
    assert rules.salary_vs_ads(offer, ads(3400, 3400, 3300, 3300, 3000), True, 0).grade \
        == rules.GREEN


def test_ad_basis_and_the_agency_caveat():
    direct = [AdPay("hour", 22, 3800, "base")] * rules.MIN_STATED
    every = direct + [AdPay("hour", 20, 3450, "base")] * 20
    assert rules.ad_basis(direct, every) == (direct, True)
    assert rules.ad_basis(direct[:-1], every) == (every, False)
    offer = Offer(3300, "month")
    weak = rules.salary_vs_ads(offer, every, False, 20)
    assert weak.grade == rules.RED and weak.caveat.startswith("unsicher")
    assert not rules.salary_vs_ads(offer, direct, True, 0).caveat
    # a caveated S2 does not move the verdict
    s1 = rules.Finding("S1", rules.GREEN, "")
    w1 = rules.Finding("W1", rules.RED, "")
    v = rules.verdict(s1, weak, w1)
    assert v["label"] == "besetzbar mit Aufwand"
    assert v["findings"][1]["grade"] == rules.RED  # still shown as measured


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

    body = {"maxErgebnisse": 1, "facetten": {"topberuf": {"counts": {"A": 1}, "scores": {}},
                                             "arbeitgeber": {"counts": {"Max Muster": 1}},
                                             "zeitarbeit": {"counts": {"true": 1}}},
            "ergebnisliste": [{"firma": "Max Muster Elektro", "stellenangebotsTitel": "X",
                               "externeURL": "https://example.org", "chiffrenummer": "1",
                               "referenznummer": "r1", "alleBerufe": ["A"],
                               "stellenlokationen": [{"adresse": {"region": "BAYERN",
                                                                  "strasse": "Weg 1"}}]}]}
    small = shrink("https://x/jobsuche-service/pc/v6/jobs", body, pseudonym,
                   lambda r: {"r1": "Stelle-00001"}[r])
    item = small["ergebnisliste"][0]
    assert item["firma"] == "Arbeitgeber 001 GmbH"
    assert item["referenznummer"] == "Stelle-00001"
    assert "stellenangebotsTitel" not in item and "externeURL" not in item
    assert item["stellenlokationen"] == [{"adresse": {"region": "BAYERN"}}]
    assert "scores" not in small["facetten"]["topberuf"]
    assert "arbeitgeber" not in small["facetten"]  # employer names live in this facet
    assert small["facetten"]["zeitarbeit"] == {"counts": {"true": 1}}


def test_recorder_returns_the_real_response_and_writes_the_shrunk_one(tmp_path, monkeypatch):
    body = {"maxErgebnisse": 1, "ergebnisliste": [{"firma": "Echte Firma GmbH",
                                                   "referenznummer": "10000-1234567890-S",
                                                   "stellenangebotsTitel": "X"}]}
    monkeypatch.setattr(Http, "get", lambda self, url, params, key: body)
    rec = Recorder(tmp_path)
    got = rec.get("https://x/jobsuche-service/pc/v6/jobs", {"page": 1}, "k")
    rec.get("https://x/jobsuche-service/pc/v6/jobs", {"page": 1, "zeitarbeit": "false"}, "k")
    assert got["ergebnisliste"][0]["firma"] == "Echte Firma GmbH"
    for path in tmp_path.glob("*.json"):  # same posting, same pseudonym in both files
        assert json.loads(path.read_text())["ergebnisliste"][0] == {
            "firma": "Arbeitgeber 001 GmbH", "referenznummer": "Stelle-00001"}


def test_employer_names_without_legal_form_are_hidden():
    assert check.display_employer("Muster Zerspanung GmbH & Co. KG").endswith("KG")
    assert check.display_employer("Max Muster") == check.MASKED


def test_truncated_search_counts_from_facets_and_flags_the_sample():
    beruf = "Mechatroniker/in"
    salary = [{"gender": {"id": 1}, "ageCategory": {"id": 1}, "branche": {"bezeichnung": "Gesamt"},
               "region": {"id": 8, "bezeichnung": "Nordrhein-Westfalen",
                          "beitragsBemessungsGrenze": 8050},
               "entgelt": 4000, "entgeltQ25": 3500, "entgeltQ75": 4600, "besetzung": 9000}]

    def answer(url, params):
        if "entgelte" in url:
            return salary
        direct = params.get("zeitarbeit") == "false"
        n = 100 * params["page"]
        items = [{"referenznummer": f"{'d' if direct else 'a'}{n + i}", "alleBerufe": [beruf],
                  "firma": "X GmbH", "stellenlokationen": [
                      {"adresse": {"region": "NORDRHEIN_WESTFALEN"}}]} for i in range(100)]
        # beruf is outside the top-20 facet but inside the 100-entry one
        return {"maxErgebnisse": 1500 if direct else 2500, "ergebnisliste": items,
                "woOutput": {"bereinigterOrt": "Bielefeld", "suchmodus": "UMKREISSUCHE"},
                "facetten": {"topberuf": {"counts": {"Andere/r": 2000}},
                             "beruf": {"counts": {beruf: 900 if direct else 1800}}}}

    r = check.run(check.Vacancy(beruf, "Bielefeld", 50, Offer(3700, "month"), kldb="26112"),
                  FakeHttp(answer))
    c = r["competition"]
    assert (c["same_beruf"], c["same_beruf_direct"], c["same_beruf_zeitarbeit"]) == (1800, 900, 900)
    assert c["count_source"] == "Facette der Jobsuche"
    assert c["sample"] == {"truncated": True, "items_read": 1000, "query_total": 2500}
    assert "Stichprobe" in report.render(r)


# --- input that must fail loudly, not read as "no competition" -------------------------------

def _jobs(items, facet, place="Gütersloh"):
    wo = ({"bereinigterOrt": place, "suchmodus": "UMKREISSUCHE"} if place
          else {"suchmodus": "UNGUELTIG"})
    return {"maxErgebnisse": len(items), "ergebnisliste": items, "woOutput": wo,
            "facetten": {"topberuf": {"counts": facet}, "beruf": {"counts": facet}}}


def _occupation_market(local_items, local_facet, national_facet):
    """Jobsuche fake: the radius search and the nationwide one (no `wo`)."""
    salary = [{"gender": {"id": 1}, "ageCategory": {"id": 1}, "branche": {"bezeichnung": "Gesamt"},
               "region": {"id": 8, "bezeichnung": "Nordrhein-Westfalen",
                          "beitragsBemessungsGrenze": 8050},
               "entgelt": 4000, "entgeltQ25": 3500, "entgeltQ75": 4600, "besetzung": 9000}]

    def answer(url, params):
        if "entgelte" in url:
            return salary
        if "wo" not in params:
            return {"maxErgebnisse": sum(national_facet.values()), "ergebnisliste": [],
                    "facetten": {"beruf": {"counts": national_facet}}}
        return _jobs(local_items, local_facet)
    return FakeHttp(answer)


def test_misspelt_occupation_is_an_error_with_suggestions():
    items = [{"referenznummer": f"r{i}", "alleBerufe": ["CNC-Fräser/in"]} for i in range(3)]
    http = _occupation_market(items, {"CNC-Fräser/in": 3, "Dreher/in": 1},
                              {"CNC-Fräser/in": 900})
    v = check.Vacancy("CNC-Fraeser", "Gütersloh", 25, Offer(4500, "month"), kldb="24232")
    with pytest.raises(LookupError, match=r"Did you mean: CNC-Fräser/in\?"):
        check.run(v, http)


@pytest.mark.parametrize("old_form_postings", [0, 4])
def test_short_form_suggests_the_slash_in_name(old_form_postings):
    # Some postings still list the old form "Mechatroniker" next to "Mechatroniker/in".
    items = ([{"referenznummer": f"o{i}", "alleBerufe": ["Mechatroniker"]}
              for i in range(old_form_postings)]
             + [{"referenznummer": f"r{i}", "alleBerufe": ["Mechatroniker/in"]}
                for i in range(30)])
    facet = {"Mechatroniker/in": 30, **({"Mechatroniker": old_form_postings}
                                        if old_form_postings else {})}
    http = _occupation_market(items, facet,
                              {"Mechatroniker/in": 5000, "Industriemechaniker/in": 4000})
    v = check.Vacancy("Mechatroniker", "Bielefeld", 25, Offer(4000, "month"), kldb="26112")
    with pytest.raises(LookupError, match=r"Did you mean: Mechatroniker/in\?"):
        check.run(v, http)


def test_made_up_occupation_is_an_error():
    http = _occupation_market([{"referenznummer": "r1", "alleBerufe": ["Bäcker/in"]}],
                              {"Bäcker/in": 1}, {})
    v = check.Vacancy("Quantenbäcker/in", "Bielefeld", 25, Offer(4000, "month"), kldb="26112",
                      region_id=8)
    with pytest.raises(LookupError, match="cannot be told apart"):
        check.run(v, http)


def test_real_occupation_without_local_postings_still_runs():
    http = _occupation_market([{"referenznummer": "r1", "alleBerufe": ["Bäcker/in"]}],
                              {"Bäcker/in": 1}, {"Glasbläser/in": 40})
    v = check.Vacancy("Glasbläser/in", "Bielefeld", 25, Offer(4000, "month"), kldb="93332",
                      region_id=8)
    r = check.run(v, http)
    assert r["competition"]["same_beruf"] == 0


def test_unknown_place_is_an_error():
    http = FakeHttp(lambda url, params: _jobs([], {}, place=None))
    v = check.Vacancy("Mechatroniker/in", "Xyzzyqq", 25, Offer(3700, "month"), kldb="26112",
                      region_id=8)
    with pytest.raises(LookupError, match="does not know the place 'Xyzzyqq'"):
        check.run(v, http)


@pytest.mark.parametrize("extra, message", [
    (["--kldb", "2423"], "five digits"),
    (["--payments", "11"], "payments at least 12"),
])
def test_bad_arguments_give_a_message_not_a_traceback(extra, message, capsys):
    argv = ["--beruf", "CNC-Fräser/in", "--wo", "Gütersloh", "--salary", "4100", *extra]
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("vakanzcheck: ") and message in err and "Traceback" not in err


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"), ConnectionResetError("reset by peer"),
    http.client.IncompleteRead(b"{"), http.client.RemoteDisconnected("closed"),
])
def test_network_failures_become_source_errors(error, monkeypatch):
    def fail(*args, **kw):
        raise error
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(SourceError, match="https://x/y"):
        Http(pause=0).get("https://x/y", {}, "k")


@pytest.mark.parametrize("kw", [
    {"amount": float("nan"), "per": "month"}, {"amount": float("inf"), "per": "month"},
    {"amount": 4000, "per": "month", "hours": float("nan")},
    {"amount": 450000, "per": "month"}, {"amount": 4000, "per": "hour"},
    {"amount": 25, "per": "month"}, {"amount": 4000, "per": "month", "hours": 80},
    {"amount": 4000, "per": "month", "payments": 15},
    {"amount": 4000, "per": "month", "extra": 250000},
])
def test_implausible_offers_are_refused(kw):
    with pytest.raises(ValueError):
        Offer(**kw)


def test_implausible_offer_stops_before_any_request(monkeypatch, capsys):
    def no_network(*args, **kw):
        raise AssertionError("network used")
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    argv = ["--beruf", "Mechatroniker/in", "--wo", "Bielefeld", "--salary", "nan"]
    assert cli.main(argv) == 2
    assert "finite" in capsys.readouterr().err


@pytest.mark.parametrize("radius", ["0", "-5", "201", "ten"])
def test_radius_out_of_range_is_a_usage_error(radius, capsys):
    argv = ["--beruf", "Mechatroniker/in", "--wo", "Bielefeld", "--salary", "4000",
            "--radius", radius]
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2 and "radius" in capsys.readouterr().err


def test_kldb_code_outside_the_classification_stops_before_any_request():
    def no_network(url, params):
        raise AssertionError("network used")
    v = check.Vacancy("Mechatroniker/in", "Bielefeld", 25, Offer(4000, "month"), kldb="12345")
    with pytest.raises(LookupError, match="not a five-digit KldB 2010 code"):
        check.run(v, FakeHttp(no_network))


def test_kldb_without_entgeltatlas_rows_is_an_error():
    items = [{"referenznummer": "r1", "alleBerufe": ["Mechatroniker/in"]}]

    def answer(url, params):
        return [] if "entgelte" in url else _jobs(items, {"Mechatroniker/in": 1})
    v = check.Vacancy("Mechatroniker/in", "Bielefeld", 25, Offer(4000, "month"), kldb="26112",
                      region_id=8)
    with pytest.raises(LookupError, match="no figures for KldB 26112"):
        check.run(v, FakeHttp(answer))


def test_committed_fixtures_hold_no_real_reference_numbers():
    for path in (FIXTURES / "guetersloh").glob("jobs-*.json"):
        for x in json.loads(path.read_text("utf-8"))["ergebnisliste"]:
            assert x["referenznummer"].startswith("Stelle-"), path.name
