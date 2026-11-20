# vakanzcheck

A feasibility check for one vacancy, from public Bundesagentur für Arbeit data.
Give it an occupation, a place and radius, and the salary on offer. It returns a
one-page report (Markdown and JSON) that answers three questions:

1. Where does the offer sit in the pay distribution for this occupation in the
   region? (BA Entgeltatlas: quartiles and median)
2. How many open postings compete for the same people within the radius, how
   many of them are from staffing agencies, and what pay do they state? (BA
   Jobsuche)
3. Does the ad text tell an applicant what they need to know without calling:
   pay, hours, shifts, on-call duty, travel, how to apply? (offline keyword
   check, or Claude with every quote checked against the text)

A verdict follows from three written-down rules, not from a model.

## Demo: three vacancies in Ostwestfalen-Lippe

Vacancies, offers and ad texts are invented ([demo/](demo/)). The committed
reports are a snapshot: every number in them comes from the live BA services on
2026-11-19. [demo/run.sh](demo/run.sh) reruns the three checks against the live
services, so its counts drift with the day's postings (Bielefeld on 2026-11-20:
228 postings, 78 ads stating pay, against 222 and 73 in the snapshot), and its
ad check needs the `claude` CLI (drop `--backend subscription` for the offline
keyword check). Employer names in the committed
reports are replaced by pseudonyms ("Arbeitgeber 001 GmbH"), as in the test
fixtures, because the Zeitarbeit ja/nein printed next to each one comes from
an imperfect flag; a fresh run prints the real names.

| Vacancy | Offer | vs. NRW pay | vs. ads in radius | Same-occupation postings | Verdict |
|---|---|---|---|---|---|
| [Elektroniker/in für Sicherheitstechnik, Paderborn + 30 km](results/elektroniker-sicherheitstechnik-paderborn/report.md) | 3.300 €/month | below Q25 (3.457 €) | 13 of 14 ads without the Zeitarbeit flag pay more | 119, 39 % flagged Zeitarbeit | schwer besetzbar |
| [Mechatroniker/in, Bielefeld + 25 km](results/mechatroniker-bielefeld/report.md) | 23,50 €/h, 35 h, 2.500 €/year extra | between Q25 and median (4.171 €) | 12 of 23 ads without the Zeitarbeit flag pay more | 222, 41 % flagged Zeitarbeit | schwer besetzbar |
| [CNC-Fräser/in, Gütersloh + 25 km](results/cnc-fraeser-guetersloh/report.md) | 4.100 €/month x 13 | above median (4.303 €) | 5 of 31 ads pay more, but 28 of the 31 are flagged Zeitarbeit: shown as unsicher, not used in the verdict | 72, 74 % flagged Zeitarbeit | besetzbar mit Aufwand |

What the runs show about pay in ads, comparing like with like (each unit on its
own, ads flagged Zeitarbeit vs. ads without the flag):

| Occupation, place | Hourly wage, Zeitarbeit flag | Hourly wage, no flag | Annual salary, no flag |
|---|---|---|---|
| Elektroniker/in EG, Paderborn | 21,00 € (n = 28) | 22,50 € (n = 7) | 44.500 € (n = 7) |
| Mechatroniker/in, Bielefeld | 20,00 € (n = 50) | 21,66 € (n = 6) | 48.018 € (n = 17) |
| CNC-Fräser/in, Gütersloh | 20,28 € (n = 28) | 20,75 € (n = 2) | 37.347 € (n = 1) |

- Ads flagged Zeitarbeit are most of the ads that state pay, and they state
  only hourly wages; none gives an annual salary. Among unflagged ads that
  state pay, annual salaries outnumber hourly wages only in Bielefeld (17 to
  6; Paderborn 7 to 7, Gütersloh 1 to 2).
- Hourly wage against hourly wage, the unflagged ads' medians are 0,47 to
  1,66 €/h higher than the flagged ones, on 2 to 7 unflagged ads per place.
  That is too few to claim a gap.
- Annual salaries / 12 include one-off payments and hourly wages do not, so
  the two cannot share one monthly scale. S2 compares each ad on its own
  footing: hourly wages with the offer's base pay, annual salaries with its
  pay including one-off payments.

## Data sources

All three are public. Each takes a fixed client id in the `X-API-Key` header,
the same one the BA's own web pages send. Verified live on 2026-11-19:

| Source | Call | X-API-Key |
|---|---|---|
| Jobsuche | `GET https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?was=…&wo=…&umkreis=…&angebotsart=1&size=100&page=…` | `jobboerse-jobsuche` |
| Entgeltatlas | `GET https://rest.arbeitsagentur.de/infosysbub/entgeltatlas/pc/v1/entgelte/{kldb}?r={region}` | `infosysbub-ega` |
| BERUFENET | `GET https://rest.arbeitsagentur.de/infosysbub/bnet/pc/v1/berufe?suchwoerter=…`, `…/berufe/{id}` | `infosysbub-berufenet` |

What changed against older documentation, and what to watch for:

- The Jobsuche search moved to `/pc/v6/jobs`. `/pc/v4/jobs` and
  `/pc/v4/app/jobs` now answer 403 with a one-byte body. The
  [bundesAPI spec](https://github.com/bundesAPI/jobsuche-api) moved to v6 in May
  2026.
- The Entgeltatlas returns `[]` whenever the `l` (level) parameter disagrees
  with the fifth digit of the KldB code, which fixes the level already (26252
  with `l=3` is empty, with `l=2` or without `l` it is not). vakanzcheck never
  sends `l`. The bundesAPI spec names a different client id
  (`c4f0d292-…`); it gets 403.
- Entgeltatlas figures: median and quartiles of gross monthly pay of full-time
  employees subject to social insurance on 31.12.2025, including one-off
  payments, cut off at the contribution ceiling (8.050 €), suppressed below
  minimum case counts (the API returns negative numbers; vakanzcheck reads them
  as missing). Sources: the
  [Entgeltatlas help page](https://www.arbeitsagentur.de/hilfe-entgeltatlas) and
  the [BA methodology notes](https://statistik.arbeitsagentur.de/DE/Statischer-Content/Grundlagen/Methodik-Qualitaet/Methodische-Hinweise/BST-MethHinweise/Entgelt-meth-Hinweise.html).
- The finest Entgeltatlas region is the Land, plus eleven large cities. There
  is no Kreis level, so Paderborn, Bielefeld and Gütersloh all compare against
  NRW. The Land is taken from the postings near the centre; `--ega-region`
  overrides it.
- `vakanzcheck/data/kldb2010.csv` holds the official labels of all 1302
  five-digit KldB 2010 codes, from the BA DKZ service
  (`infosysbub/dkz-rest/pc/v1/kldb2010`); `scripts/fetch_kldb.py` regenerates it.

## How it works

**Occupation.** `--beruf` is the occupation name as the Jobsuche lists it
("Mechatroniker/in", "Elektroniker/in - Energie- und Gebäudetechnik"). Its KldB
code comes from BERUFENET, by exact name only: "Elektroniker" alone matches a
dozen occupations in different KldB groups, so a near match is listed and the
tool asks for `--kldb`. Some Jobsuche occupations ("CNC-Fräser/in") are not in
BERUFENET and need `--kldb` (24232). Postings are matched to `--beruf` by exact
name too. When none in the radius lists it, one search across Germany decides:
if postings elsewhere list the name, the run goes on with zero local postings;
if not, or if a near name ("Mechatroniker/in" for "Mechatroniker") is listed
more often, the run stops with suggestions instead of reading a typo as an empty
market.

**Competition.** Every open employment posting (`angebotsart=1`) for the
occupation within the radius, paged in full (up to 1000). A posting counts when
the occupation is among its listed occupations. The same search with
`zeitarbeit=false` splits the ads into those the Jobsuche flags as Zeitarbeit
and those it does not. "Direct" in the code and "ohne Zeitarbeit" in the reports
mean only the second set, and it is not the same as direct employers: the flag
misses some personnel-service firms. Re-checked live on 2026-11-20, the
Bielefeld demo still gives 12 of 23, and at least 4 of those 23 unflagged ads
that state pay come from personnel-service firms (5 if an "HR Solutions" firm
counts). Leaving them out moves S2 below 50 %, from gelb to grün; the verdict
stays the same. Ads that state pay keep their unit (the v6 results carry
STUNDENLOHN or JAHRESGEHALT); a range counts as its midpoint, implausible values
(an "hourly" wage of 3.500) are dropped. Hourly wages are compared at the
vacancy's weekly hours.

With more hits than can be paged, the posting counts come from the Jobsuche's
100-entry occupation facet (which can differ from a count of the postings
themselves by one or two), and the report marks everything read per posting
(agency flag, pay, employers, the shift flag) as a sample of the first 1000.

**Offer.** `--salary` with `--per month|hour|year`, `--hours` per week (default
40), `--payments` salaries per year (13 for a 13th salary; refused with
`--per year`, which already includes it), `--extra` one-off payments per year.
An offer outside the ranges used for ads (10 to 100 €/h, 1.000 to 15.000
€/month, 15.000 to 200.000 €/year), over 60 hours, over 14 salaries or over
20.000 € extra is refused before any request is made.
Hourly pay converts at hours x 365.25 / 7 / 12, the BA's day basis. Base pay is
compared with hourly wages in ads; base plus one-off payments with annual
salaries in ads and with the Entgeltatlas.

**Verdict.** Three rules, cut-offs in [vakanzcheck/rules.py](vakanzcheck/rules.py).
They are choices made for this tool, not findings; change them there.

| Rule | rot | gelb | grün |
|---|---|---|---|
| S1 offer vs. regional pay | below Q25 | Q25 to median | median or above |
| S2 share of ads paying more than the offer (ads without the Zeitarbeit flag if at least 5 of them state pay, else all ads; no grade below 5) | 75 % or more | 50 to 74 % | under 50 % |
| W1 same-occupation postings in radius | 100 or more | 30 to 99 | under 30 |

- **schwer besetzbar**: S1 rot; or S1 gelb and W1 rot; or S2 rot and W1 rot
- **besetzbar mit Aufwand**: any other rot or gelb
- **gut besetzbar**: no rot, no gelb
- **nicht bewertbar**: neither salary rule has data

If S2 falls back to all ads and most of them are from agencies, its grade is
shown as unsicher and the verdict treats S2 as having no data.

**Ad text.** An eleven-item checklist of what an applicant in a skilled trade
needs to decide without calling (pay, collective agreement, weekly hours,
shifts, on-call duty, travel, vehicle, holidays, fixed-term or not, simple way
to apply, must-have vs. nice-to-have). The list is ours, not from a study.

- `--backend keywords` (default, offline): one regular expression per item. It
  can only say found or not found; found means a keyword occurs, not that the
  ad is clear.
- `--backend subscription`: one `claude -p` call (Claude Opus 5.5, no tools, no
  MCP servers, no settings files, fresh session, JSON schema) grades each item
  `genannt`, `fehlt` or `unklar` with a verbatim quote. Every quote is checked
  against the ad; a `genannt` whose quote is not in the text becomes `unklar`.
  The report labels the section as model judgment. The verdict never uses it.

## Use

```bash
pip install -e .

vakanzcheck --beruf "Mechatroniker/in" --wo Bielefeld --radius 25 \
  --salary 23.50 --per hour --hours 35 --extra 2500 \
  --ad anzeige.txt

# Claude for the ad check, through the claude CLI on a subscription (no API key)
vakanzcheck ... --ad anzeige.txt --backend subscription

# occupation not in BERUFENET, region set by hand
vakanzcheck --beruf "CNC-Fräser/in" --kldb 24232 --wo 33330 --ega-region 8 --salary 4100

# offline, from the recorded responses the tests use (no network)
vakanzcheck --beruf "Zerspanungsmechaniker/in" --wo Gütersloh --radius 10 \
  --salary 3600 --replay tests/fixtures/guetersloh
```

Each run writes `report.md` and `report.json` (every figure, the rule results,
and the list of requests made) to `results/<slug>/` or `--out`.

`--record DIR` also writes each response, cut down to the fields the code reads
and with employer names replaced by pseudonyms; `--replay DIR` runs from such
files with no network. The tests run offline on one recorded run in
`tests/fixtures/`.

## Limits

- Regional pay is Land-level. Pay in OWL may differ from the NRW figure, and
  the data cannot say by how much.
- A KldB group is broader than a job: "Elektroniker/in - Energie- und
  Gebäudetechnik" sits in 26212 (Bauelektrik), which also covers electricians
  on building sites.
- The Jobsuche holds the postings employers put there or that reach it through
  partner boards. Ads only on company sites or other job boards are not
  counted.
- Postings are a stock on one day, not a flow; a vacancy open for months counts
  the same as one posted yesterday.
- Hourly ad pay is converted at the vacancy's own weekly hours, because the
  ads' hours are not in the search results. Whether an annual salary includes
  a 13th salary is assumed, not known.
- The Zeitarbeit split is the Jobsuche's flag. Personnel-service firms it
  does not flag count as employers; telling them apart by name is not
  attempted.
- The shift count in the report is a structured Jobsuche field
  (Schicht/Nacht/Wochenende), not read from the ad text.
- Values at or above the contribution ceiling are shown as "über BBG", not as
  numbers. The reference date (31.12.2025) is a constant checked against the
  ceiling in each response; the report warns when they disagree.
- Employer names are shown only when they carry a company legal form; a sole
  trader's business name is usually a person's name.
- Not covered: the number of jobseekers for the occupation, vacancy durations,
  and the BA's shortage analysis (Engpassanalyse). Each would sharpen W1; none
  is wired in.

## Related

Three small tools on one question: does an AI step in recruiting do what it
claims, in numbers anyone can recompute?

- [screengate](https://github.com/CedricConday/screengate): what a screening
  chatbot's summary keeps, invents, and whether its verdict moves with the name.
- [poolgate](https://github.com/CedricConday/poolgate): who a talent-pool
  matcher shortlists, who it misses, and why.
- [vakanzcheck](https://github.com/CedricConday/vakanzcheck): whether a
  vacancy's offer can fill it, on live Bundesagentur data and written-down rules.

## License

MIT
