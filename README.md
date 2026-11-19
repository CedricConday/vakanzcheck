# vakanzcheck

A feasibility check for one vacancy, from public Bundesagentur für Arbeit data.
Give it an occupation, a place and radius, and the salary on offer. It returns a
one-page report (Markdown and JSON) that answers three questions:

1. Where does the offer sit in the pay distribution for this occupation in the
   region? (BA Entgeltatlas: quartiles and median)
2. How many employers are looking for the same people within the radius, how
   many of them are staffing agencies, and what pay do their ads state? (BA
   Jobsuche)
3. Does the ad text tell an applicant what they need to know without calling:
   pay, hours, shifts, on-call duty, travel, how to apply? (offline keyword
   check, or Claude with every quote checked against the text)

A verdict follows from three written-down rules, not from a model.

## Demo: three vacancies in Ostwestfalen-Lippe

Vacancies, offers and ad texts are invented ([demo/](demo/)). Every number in
the reports comes from the live BA services on 2026-11-19
([demo/run.sh](demo/run.sh) reproduces them).

| Vacancy | Offer | vs. NRW pay | vs. ads in radius | Same-occupation postings | Verdict |
|---|---|---|---|---|---|
| [Elektroniker/in für Sicherheitstechnik, Paderborn + 30 km](results/elektroniker-sicherheitstechnik-paderborn/report.md) | 3.300 €/month | below Q25 (3.457 €) | below Q25 of direct employers (3.540 €) | 119, 39 % agencies | schwer besetzbar |
| [Mechatroniker/in, Bielefeld + 25 km](results/mechatroniker-bielefeld/report.md) | 23,50 €/h, 35 h, 2.500 €/year extra | between Q25 and median (4.171 €) | below median of direct employers (3.750 €) | 222, 41 % agencies | schwer besetzbar |
| [CNC-Fräser/in, Gütersloh + 25 km](results/cnc-fraeser-guetersloh/report.md) | 4.100 €/month x 13 | above median (4.303 €) | above median of all ads (3.522 €) | 72, 74 % agencies | besetzbar mit Aufwand |

Two things the runs showed that a salary benchmark alone does not:

- Most ads that state pay are from staffing agencies, and their stated pay sits
  well below the direct employers'. In Bielefeld the Mechatroniker median is
  3.082 € across all 73 ads that state pay and 3.750 € across the 23 from direct
  employers. Benchmarking against all ads would make a weak offer look fine.
- Ad pay is regular monthly pay; the Entgeltatlas median includes one-off
  payments. Both comparisons are made, each on its own basis.

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
- The finest Entgeltatlas region is the Land, plus twelve large cities. There
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
BERUFENET and need `--kldb` (24232).

**Competition.** Every open employment posting (`angebotsart=1`) for the
occupation within the radius, paged in full (up to 1000). A posting counts when
the occupation is among its listed occupations. The same search with
`zeitarbeit=false` separates staffing agencies from direct employers; that is
the Jobsuche's own filter, and it is not perfect (it let one agency through in a
spot check). Ads that state pay are converted to monthly pay at the vacancy's
weekly hours; a range counts as its midpoint, implausible values (an "hourly"
wage of 3.500) are dropped.

**Offer.** `--salary` with `--per month|hour|year`, `--hours` per week (default
40), `--payments` salaries per year (13 for a 13th salary), `--extra` one-off
payments per year. Hourly pay converts at hours x 365.25 / 7 / 12, the BA's day
basis. Base pay is compared with ads, base plus one-off payments with the
Entgeltatlas.

**Verdict.** Three rules, cut-offs in [vakanzcheck/rules.py](vakanzcheck/rules.py).
They are choices made for this tool, not findings; change them there.

| Rule | rot | gelb | grün |
|---|---|---|---|
| S1 offer vs. regional pay | below Q25 | Q25 to median | median or above |
| S2 offer vs. ad pay (direct employers if at least 5 state pay, else all ads; no grade below 5) | below their Q25 | below their median | median or above |
| W1 same-occupation postings in radius | 100 or more | 30 to 99 | under 30 |

- **schwer besetzbar**: S1 rot; or S1 gelb and W1 rot; or S2 rot and W1 rot
- **besetzbar mit Aufwand**: any other rot or gelb
- **gut besetzbar**: no rot, no gelb
- **nicht bewertbar**: neither salary rule has data

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
  ads' hours are not in the search results.
- Employer names are shown only when they carry a company legal form; a sole
  trader's business name is usually a person's name.
- Not covered: the number of jobseekers for the occupation, vacancy durations,
  and the BA's shortage analysis (Engpassanalyse). Each would sharpen W1; none
  is wired in.

## License

MIT
