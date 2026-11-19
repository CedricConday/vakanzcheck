"""Check an ad text for the facts an applicant in a skilled trade needs to decide.

The checklist is ours: the questions a Facharbeiter applicant can only answer by
calling, if the ad leaves them out (pay, hours, shifts, on-call duty, travel,
...). It is not drawn from a survey, and the report says so.

Two backends fill it in:

- `keywords` (default, offline): a regular expression per item. It can only say
  "found" or "not found", and "found" means a keyword occurs, not that the ad is
  clear about it.
- `subscription`: Claude reads the ad and grades each item `genannt`, `fehlt` or
  `unklar` with a verbatim quote. That is model judgment, not data. Every quote is
  checked against the ad text; a `genannt` whose quote is not in the ad is
  downgraded to `unklar` and flagged.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

CRITERIA: list[tuple[str, str, str | None]] = [
    # id, question, keyword pattern (None: not checkable by keyword)
    ("gehalt", "Gehalt oder Stundenlohn als Betrag oder Spanne genannt",
     r"\d[\d.,]*\s*(?:€|euro\b|eur\b)|€\s*\d"),
    ("tarif", "Tarifbindung oder Tarifvertrag genannt",
     r"tarif|ig metall|entgeltgruppe|\bera\b|tv-?l\b"),
    ("arbeitszeit", "Wochenarbeitszeit in Stunden genannt",
     (r"\d{2}(?:[,.]\d)?\s*(?:-\s*)?(?:stunden|std\.?|h)\b[^.\n]{0,20}woche"
     r"|\d{2}(?:[,.]\d)?[- ]stunden-woche|wochenarbeitszeit")),
    ("schicht", "Schicht-, Nacht- oder Wochenendarbeit geklärt (ja oder nein)",
     r"schicht|nachtarbeit|wochenendarbeit|gleitzeit|montag bis freitag|mo\.?\s*[-–]\s*fr"),
    ("bereitschaft", "Rufbereitschaft oder Notdienst geklärt (ja oder nein)",
     r"rufbereitschaft|bereitschaftsdienst|notdienst|notfalldienst"),
    ("einsatzort", "Einsatzort, Montageanteil oder Reisetätigkeit genannt",
     r"montage|reisetätigkeit|reisebereitschaft|einsatzgebiet|übernacht|beim kunden"),
    ("fahrzeug", "Firmen- oder Servicefahrzeug geklärt",
     r"firmenwagen|firmenfahrzeug|servicefahrzeug|dienstwagen|werkstattwagen|firmen-pkw"),
    ("urlaub", "Urlaubstage genannt", r"\d{2}\s*(?:tage\s*)?urlaub|urlaubstage"),
    ("befristung", "Befristet oder unbefristet genannt", r"unbefristet|befristet"),
    ("bewerbung", "Einfacher Bewerbungsweg genannt (ohne Anschreiben, Telefon, WhatsApp)",
     r"ohne anschreiben|whatsapp|telefonisch|kurzbewerbung|lebenslauf genügt|ruf\w* an"),
    ("anforderungen", "Muss- und Kann-Anforderungen unterscheidbar", None),
]

STATUSES = ("genannt", "fehlt", "unklar")


@dataclass
class Check:
    id: str
    frage: str
    status: str  # genannt / fehlt / unklar / gefunden / nicht gefunden / nicht prüfbar
    zitat: str
    hinweis: str = ""
    quote_verified: bool | None = None


def _snippet(text: str, m: re.Match, width: int = 35) -> str:
    start, end = max(0, m.start() - width), min(len(text), m.end() + width)
    return " ".join(text[start:end].split())


def keyword_check(text: str) -> list[Check]:
    low = text.lower()
    out = []
    for cid, question, pattern in CRITERIA:
        if pattern is None:
            out.append(Check(cid, question, "nicht prüfbar", ""))
            continue
        m = re.search(pattern, low)
        out.append(Check(cid, question, "gefunden" if m else "nicht gefunden",
                         _snippet(text, m) if m else ""))
    return out


def _norm(s: str) -> str:
    s = s.replace("„", '"').replace("“", '"').replace("”", '"').replace("’", "'")
    return " ".join(s.split()).casefold()


def quote_in_text(quote: str, text: str) -> bool:
    """True if the quote occurs in the text, ignoring case, whitespace and quote marks.

    An ellipsis splits the quote; each piece must occur, in order.
    """
    pieces = [p.strip(" .") for p in re.split(r"\.\.\.|…", quote) if p.strip(" .")]
    hay, pos = _norm(text), 0
    for p in pieces:
        i = hay.find(_norm(p), pos)
        if i < 0:
            return False
        pos = i + len(_norm(p))
    return bool(pieces)


SYSTEM = (
    "Du prüfst Stellenanzeigen für gewerblich-technische Berufe. Du beurteilst nur, ob die "
    "Anzeige bestimmte Angaben macht, die Bewerber brauchen, um sich ohne Rückfrage zu "
    "entscheiden. Du bewertest nicht, ob die Angaben gut oder attraktiv sind, und du erfindest "
    "nichts. Für jede Prüffrage: status 'genannt', wenn die Anzeige die Frage beantwortet; "
    "'fehlt', wenn nichts dazu steht; 'unklar', wenn es nur vage oder widersprüchlich steht "
    "(z. B. 'attraktive Vergütung' ohne Betrag ist 'fehlt' bei der Gehaltsfrage, "
    "'Schichtarbeit möglich' ist 'unklar'). zitat: die entscheidende Stelle wörtlich aus der "
    "Anzeige, höchstens 200 Zeichen, sonst leer. hinweis: höchstens ein kurzer Satz, sonst leer."
)


def schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "enum": [c[0] for c in CRITERIA]},
            "status": {"type": "string", "enum": list(STATUSES)},
            "zitat": {"type": "string"},
            "hinweis": {"type": "string"},
        },
        "required": ["id", "status", "zitat", "hinweis"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"pruefungen": {"type": "array", "items": item}},
            "required": ["pruefungen"], "additionalProperties": False}


def prompt(text: str, beruf: str) -> str:
    questions = "\n".join(f"- {cid}: {q}" for cid, q, _ in CRITERIA)
    return (f"Beruf: {beruf}\n\nPrüffragen (jede genau einmal beantworten):\n{questions}\n\n"
            f"Stellenanzeige:\n<<<\n{text}\n>>>")


def model_check(text: str, beruf: str, ask) -> tuple[list[Check], dict]:
    """Run the checklist through a model. `ask(system, prompt, schema)` returns (dict, meta)."""
    raw, meta = ask(SYSTEM, prompt(text, beruf), schema())
    by_id = {r["id"]: r for r in raw.get("pruefungen", [])}
    out = []
    for cid, question, _ in CRITERIA:
        r = by_id.get(cid)
        if r is None:
            out.append(Check(cid, question, "unklar", "", "Modell hat die Frage ausgelassen"))
            continue
        status, quote = r["status"], r["zitat"].strip()
        verified = quote_in_text(quote, text) if quote else None
        hint = r["hinweis"].strip()
        if status == "genannt" and not verified:
            status = "unklar"
            hint = "Zitat steht so nicht in der Anzeige; vom Prüfer auf 'unklar' gesetzt."
        out.append(Check(cid, question, status, quote, hint, verified))
    return out, meta


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
