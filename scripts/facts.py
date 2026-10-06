"""Vigie facts v1 — typed fact slots from item title+summary. Shadow only.

docs/EVENTS.md section 9. Facts are *values*, not prose: counts, amounts,
dates, places and entities, each attributed to the item ids that state them.
Nothing here is wired into the pipeline; it writes nothing under public/.

House law this module holds on purpose:

* Integers only. Counts are integers, amounts are integer cents (CAD), shares
  are integer basis points. A decimal that is not an integer count is dropped,
  never rounded into one.
* Attribution. Every value lists the item ids (and institutions) that state it.
* Never reconciled. Two different values in one slot stay side by side with
  ``divergent: true``; no value is chosen, averaged or called "confirmed".
  Divergence is computed only where the slot is *comparable* (a count unit, or
  an amount whose subject cue is specific); a generic "a percentage went up"
  is shown but never flagged, because two such numbers need not be about the
  same thing. That rule is printed in the row (``comparable``), not hidden.
* No private persons, ever. The ``entity`` kind is a CLOSED list of institutions
  and public *offices* (mayor, minister, coroner, police services). No name
  matcher exists in this file: victims, minors, accused and journalists cannot
  be extracted because nothing here reads a person's name.
* R8. Publisher text is never stored, paraphrased or translated. Rows carry
  codes, integers, ISO dates and short place tokens only. ``spans(text)``
  returns verbatim numeric spans for RENDER-TIME side-by-side display; its
  output is publisher text, so it must never be stored, sealed or committed.
* Status is always ``proposed``. Rules only, no model, no wall clock: the same
  items give the same bytes, independent of PYTHONHASHSEED.
* Fail-soft. A foreign-shaped item yields no facts; a fault in the CLI prints
  a diagnosis and exits 0.

Reuse: enrich.propose_impact_units (price / percent / housing patterns),
enrich.RE_NUM and enrich._parse_number (number grammar), cluster_issues
.road_places / .place_hints (places).
"""
from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cluster_issues  # noqa: E402
import enrich  # noqa: E402
import store_io  # noqa: E402

ROOT = SCRIPTS.parent
IN_PATH = ROOT / "data" / "normalized" / "latest_candidates.json"
OUT_PATH = ROOT / "data" / "ops" / "facts_shadow.json"

METHOD = "facts-v1"
STATUS = "proposed"
KINDS = ("count", "amount", "date", "place", "entity")
COUNT_UNITS = (
    "persons_dead", "persons_injured", "persons_arrested", "housing_units",
    "jobs", "vehicles", "buildings", "customers_without_power",
)
# Slots where two items may disagree about *one* quantity. Date, place and
# entity slots are sets (an event has many places and actors): they list their
# values but never claim a divergence.
SET_KINDS = frozenset({"date", "place", "entity"})
GENERIC_SUBJECTS = frozenset({"change"})
MAX_ABS_CENTS = 10 ** 15
MAX_COUNT = 10 ** 9

_WS = r"[\s\u00a0\u202f]+"
_WSO = r"[\s\u00a0\u202f]*"

# --- number grammar -------------------------------------------------------
_WORDS_FR = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
    "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "dix-sept": 17,
    "dix-huit": 18, "dix-neuf": 19, "vingt": 20,
}
_WORDS_EN = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
WORD_NUMBERS = {**_WORDS_EN, **_WORDS_FR}
_WORD_ALT = "|".join(sorted((re.escape(w) for w in WORD_NUMBERS), key=len, reverse=True))
# Digits reuse enrich.RE_NUM (scar: "1 450" must not become "0"); one capturing
# group inside, so callers read the whole span through a named wrapper group.
NUM = rf"(?:{enrich.RE_NUM}|(?<![\w\-])(?:{_WORD_ALT})(?![\w\-]))"
# "une voiture" / "one vehicle" is an article, not a count: the non-person
# units never accept un/une/one as a number word.
_WORD_ALT_NO_ONE = "|".join(
    sorted((re.escape(w) for w in WORD_NUMBERS if w not in ("un", "une", "one")), key=len, reverse=True)
)
NUM_NO_ONE = rf"(?:{enrich.RE_NUM}|(?<![\w\-])(?:{_WORD_ALT_NO_ONE})(?![\w\-]))"


def _folded(text: str) -> str:
    return cluster_issues.folded(text)


def _int_count(raw: str) -> int | None:
    """Integer count from digits or a number word. Decimals are not counts."""
    token = " ".join(str(raw or "").split()).lower()
    if token in WORD_NUMBERS:
        return WORD_NUMBERS[token]
    value = enrich._parse_number(token)
    if value is None or not math.isfinite(value):
        return None
    if value != int(value) or not 0 <= value <= MAX_COUNT:
        return None
    return int(value)


# --- qualifiers (how the publisher hedged the number) ---------------------
_QUALIFIERS = (
    ("at_least", re.compile(r"(?:\bau\s+moins|\bplus\s+de|\bplus\s+d['’]|\bat\s+least|\bmore\s+than|\bover|\bdepuis\s+plus\s+de)\W*$", re.I)),
    ("at_most", re.compile(r"(?:\bmoins\s+de|\bmoins\s+d['’]|\bjusqu['’]?\s*[àa]|\bat\s+most|\bless\s+than|\bup\s+to|\bunder|\bau\s+plus)\W*$", re.I)),
    ("about", re.compile(r"(?:\bpr[èe]s\s+de|\benviron|\b[àa]\s+peu\s+pr[èe]s|\bquelque|\babout|\baround|\bnearly|\balmost|\bapproximately|\broughly|\bapprox\.?)\W*$", re.I)),
)


def _qualifier(text: str, start: int) -> str:
    prefix = text[max(0, start - 24):start]
    for name, rx in _QUALIFIERS:
        if rx.search(prefix):
            return name
    return "exact"


# --- count lexicon ---------------------------------------------------------
_PERSON = (
    r"personnes?|persons?|people|individus?|individuals?|victimes?|hommes?|femmes?|men|women|"
    r"enfants?|children|adultes?|adults?|passagers?|passengers?|travailleurs?|workers?|"
    r"r[ée]sidents?|residents?|pi[ée]tons?|pedestrians?|cyclistes?|motocyclistes?|"
    r"conducteurs?|drivers?|suspects?|policiers?|pompiers?|agents?|officers?|occupants?|"
    r"manifestants?|protesters?|jeunes?|adolescents?|mineurs?|citoyens?|"
    r"employ[ée]s?|passants?|membres?|members?"
)
# Auxiliaries only: no severity adverbs. "2 grièvement blessés" is a subset of a
# larger count; mixing it into the same slot would fake a divergence, so it is
# left unextracted (a miss, not an error).
_AUX = (
    r"sont|ont|a|[ée]t[ée]s?|[ée]tait|taient|were|was|been|have|has|also|aussi|"
    r"toutes?|tous|se\s+sont|est|are|is|had"
)
_STATE = {
    "persons_dead": (
        r"morts?|mortes?|d[ée]c[ée]d[ée]e?s?|tu[ée]e?s?|d[ée]c[èe]s|dead|killed|died|"
        r"fatalit(?:y|ies)|deaths?|perdu\s+la\s+vie|lost\s+(?:their\s+)?lives?|p[ée]ri|pertes?\s+de\s+vie"
    ),
    "persons_injured": r"bless[ée]e?s?|injured|hurt|wounded|blessures",
    "persons_arrested": (
        r"arr[êe]t[ée]e?s?|arrestations?|arrested|interpell[ée]e?s?|appr[ée]hend[ée]e?s?"
    ),
}
_STATE_RX = {
    unit: re.compile(
        # N [person noun [de/of up to three words]] [auxiliaries] STATE
        rf"(?P<n>{NUM})(?:{_WS}(?:{_PERSON})(?:{_WS}(?:de|des|du|of|from)(?:{_WS}[\wÀ-ÿ'’-]+){{1,3}})?)?"
        rf"(?:{_WS}(?:{_AUX})){{0,3}}{_WS}(?:{states})(?![\w])",
        re.I,
    )
    for unit, states in _STATE.items()
}
# "arrested N men", "arrestation de N personnes", "death toll ... N".
_ARRESTED_PRE = re.compile(
    rf"(?:\barrest(?:ed|s)?|\barr[êe]t[ée]|\binterpell[ée]|\barrestations?\s+(?:de|of))(?:{_WS}[a-zà-ÿ']+){{0,2}}?{_WS}(?P<n>{NUM}){_WS}(?:{_PERSON})(?![\w\d])",
    re.I,
)
_DEAD_TOLL = re.compile(rf"\bdeath\s+toll\b[^.\d]{{0,30}}?(?P<n>{NUM})(?![\w\d])", re.I)
_SIMPLE_NOUN = {
    "jobs": rf"(?P<n>(?i:{NUM_NO_ONE})){_WS}(?:nouveaux{_WS}|new{_WS})?(?:emplois?|jobs?)(?![\w])",
    "vehicles": rf"(?P<n>(?i:{NUM_NO_ONE})){_WS}(?:v[ée]hicules?|voitures?|automobiles?|camions?|autobus|vehicles?|cars?|trucks?|buses|bus|motos?|motorcycles?)(?![\w])",
    "buildings": rf"(?P<n>(?i:{NUM_NO_ONE})){_WS}(?:b[âa]timents?|immeubles?|[ée]difices?|buildings?|maisons?|houses?)(?![\w])",
}
# Case-sensitive nouns: a capitalised "Maison"/"Bus" inside an address or a
# proper name is not a count of buildings or vehicles.
_SIMPLE_RX = {unit: re.compile(rx) for unit, rx in _SIMPLE_NOUN.items()}
_JOBS_POSTES = re.compile(
    rf"(?P<n>{NUM_NO_ONE}){_WS}postes?{_WS}(?:abolis?|supprim[ée]s?|cr[ée]{{1,2}}s?|[ée]limin[ée]s?)(?![\w])|"
    rf"(?:abolition|suppression|cr[ée]ation|[ée]limination)\s+de\s+(?P<n2>{NUM_NO_ONE}){_WS}postes?(?![\w])",
    re.I,
)
_POWER = (
    r"sans\s+(?:courant|[ée]lectricit[ée])|priv[ée]s?\s+d['’]?\s*(?:courant|[ée]lectricit[ée])|"
    r"without\s+(?:power|electricity)|touch[ée]s?\s+par\s+(?:la|une|cette)\s+panne|"
    r"affected\s+by\s+(?:the\s+|an?\s+)?(?:power\s+)?(?:outage|blackout)"
)
_CUSTOMERS = re.compile(
    rf"(?P<n>{NUM_NO_ONE}){_WS}(?:clients?|abonn[ée]s?|foyers?|customers?|homes?|households?|r[ée]sidences?)"
    rf"(?:{_WS}[\wà-ÿ'’]+){{0,3}}?{_WS}(?:{_POWER})",
    re.I,
)
# "dont 2 grièvement blessés": a subset of a count already stated.
_SUBSET_PREFIX = re.compile(r"(?:\bdont|\bincluding|\bof\s+whom|\bof\s+them|\bdont\s+au\s+moins)\W*$", re.I)


_YEAR_PREFIX = re.compile(r"(?:\ben|\bin|\bde|\bof|\bd['’]|\bdepuis|\bsince|\bpendant|\bduring)\s*$", re.I)


def _count_atoms(text: str) -> list[dict]:
    out: list[dict] = []

    def add(unit: str, m: re.Match, group: str = "n") -> None:
        if _SUBSET_PREFIX.search(text[max(0, m.start(group) - 24):m.start(group)]):
            return
        value = _int_count(m.group(group))
        if value is None:
            return
        if (unit.startswith("persons_") and re.fullmatch(r"(?:19|20)\d{2}", m.group(group))
                and _YEAR_PREFIX.search(text[max(0, m.start(group) - 12):m.start(group)])):
            return  # "in 2031 death of ..." names a year, not a toll
        out.append({
            "kind": "count", "unit": unit, "subject": None, "value": value,
            "qualifier": _qualifier(text, m.start(group)),
        })

    for unit, rx in _STATE_RX.items():
        for m in rx.finditer(text):
            add(unit, m)
    for m in _ARRESTED_PRE.finditer(text):
        add("persons_arrested", m)
    for m in _DEAD_TOLL.finditer(text):
        add("persons_dead", m)
    for unit, rx in _SIMPLE_RX.items():
        for m in rx.finditer(text):
            add(unit, m)
    for m in _JOBS_POSTES.finditer(text):
        add("jobs", m, "n" if m.group("n") else "n2")
    for m in _CUSTOMERS.finditer(text):
        add("customers_without_power", m)
    return out


# --- amounts (integer cents CAD; percent as integer basis points) ---------
_MULT = {
    "milliard": 10 ** 9, "milliards": 10 ** 9, "billion": 10 ** 9, "billions": 10 ** 9,
    "million": 10 ** 6, "millions": 10 ** 6, "m": 10 ** 6, "g": 10 ** 9, "b": 10 ** 9,
    "trillion": 10 ** 12,
    "mille": 10 ** 3, "thousand": 10 ** 3, "k": 10 ** 3,
}
# French "billion" is 10**12, English "billion" 10**9: the bare word is never
# read in the suffix form unless "dollars" follows (English), and never as "B".
_MULT_WORD = rf"milliards?|millions?|mille|thousand|billion(?={_WS}dollars)|M|G|k"
_AMOUNT_SUFFIX = re.compile(
    rf"(?<![\w\d])(?P<n>{enrich.RE_NUM})(?:(?:{_WSO}|-)(?P<mult>{_MULT_WORD})(?![A-Za-zÀ-ÿ])\.?(?:{_WS}de)?)?{_WSO}(?P<cur>\$|dollars?(?![\w]))",
    re.I,
)
_AMOUNT_PREFIX = re.compile(
    rf"(?<![\w])(?:CA|CAD)?\$\s*(?P<n>{enrich.RE_NUM})(?:(?:{_WSO}|-)(?P<mult>million|billion|trillion|thousand|M|B|k)(?![A-Za-zÀ-ÿ]))?",
    re.I,
)
_PER_MONTH = re.compile(r"^\s*(?:de\s+plus\s+)?(?:/\s*|par\s+|per\s+|a\s+|chaque\s+)(?:mois|month)\b", re.I)
_PER_YEAR = re.compile(
    r"^\s*(?:de\s+plus\s+)?(?:/\s*|par\s+|per\s+|a\s+|chaque\s+|each\s+)(?:an(?:n[ée]e)?\b|ann[ée]e|year)|^\s*annually\b|^\s*annuel",
    re.I,
)
_PER_OTHER = re.compile(
    r"^\s*(?:de\s+plus\s+)?(?:/\s*|par\s+|per\s+|a\s+|chaque\s+|each\s+)"
    r"(?:jour|day|semaine|week|heure|hour|h\b|L\b|litre|kwh|km|personne|person|habitant|"
    r"logement|unit[ée]|tonne|baril|barrel|m2|m²|pi2|pi²|m[èe]tre|d[ée]put[ée]|[ée]l[èe]ve|student|ticket|billet|kilo|lb)",
    re.I,
)
_PER_THE = re.compile(
    r"^\s*(?:de\s+plus\s+)?(?:le|la|l['’]|the)\s*(?:litre|gallon|baril|barrel|kilo(?:gramme)?|tonne|kwh|kilowattheure|m[èe]tre)",
    re.I,
)
_PCT = re.compile(
    rf"(?<![\w\d])(?P<n>{enrich.RE_NUM}){_WSO}(?:%|pour\s+cent|per\s*cent|percent)",
    re.I,
)

# Subject cues (folded text). Closed list; nearest cue within the sentence
# window wins, ties broken alphabetically. No cue -> no subject.
_SUBJECT_CUES = (
    ("fine", r"amende|\bfines?\b|constat d.?infraction|\bticket"),
    ("budget", r"\bbudget"),
    ("cost", r"\bcouts?\b|\bcouter|\bcosts?\b|\bcosting|\bcoutera|\bfacture"),
    ("value", r"\bvaleur|\bvalued|\bworth\b|\bvalant"),
    ("cut", r"compression|coupure|\bcuts?\b|restriction budgetaire|abolition"),
    ("funding", r"subvention|financement|\bfunding|subsid|\bgrants?\b|enveloppe|aide financiere"),
    ("investment", r"investissement|\binvest"),
    ("rent", r"\bloyers?|\brents?\b"),
    ("price", r"\bprix\b|\bprices?\b|\btarif|\brates?\b|\bessence\b|\bdiesel\b|\bcarburant|\bgasoline"),
    ("salary", r"salaire|\bsalary|\bsalaries|remuneration|\bwages?\b"),
    ("savings", r"economies|\bsavings?\b"),
    ("debt", r"\bdettes?\b|\bdebt\b"),
    ("deficit", r"\bdeficit|\bsurplus"),
    ("tax", r"\btaxes?\b|\btax(?:es)?\b|\bimpots?\b"),
    ("inflation", r"\binflation"),
    ("unemployment", r"\bchomage|\bunemployment"),
    ("turnout", r"taux de participation|\bturnout|voter participation"),
    ("profit", r"benefice|\bprofits?\b|net income|resultat net"),
    ("interest", r"taux d.?interet|interest rate"),
    ("damages", r"dommages|\bdamages?\b"),
    ("settlement", r"\bsettlement|indemnit|reglement hors cour"),
    ("revenue", r"\brevenus?\b|\brevenue|chiffre d.?affaires"),
    ("reward", r"recompense|\breward"),
)
_SUBJECT_RX = tuple((code, re.compile(rx)) for code, rx in _SUBJECT_CUES)
_DIRECTION = re.compile(
    r"\bhausse|\baugment|\bbaisse|\bdiminu|\bre?duction|\bcrois|\bhike|\bincreas|\bdecreas|\bdrop|\brise\b|\brose\b|\bfall\b|\bjump|\bgrimp|\bchut|\bplonge|\bsurge|\bsoar",
)
_SENTENCE_BREAK = re.compile(r"[.!?;]\s|[|]")


def _window(text: str, start: int, end: int, radius: int = 64) -> tuple[str, int, int]:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    # Clip at the nearest sentence break on each side so a cue from a
    # neighbouring sentence never labels this number.
    before = text[lo:start]
    cut = None
    for m in _SENTENCE_BREAK.finditer(before):
        cut = m.end()
    if cut is not None:
        lo += cut
    after = text[end:hi]
    m = _SENTENCE_BREAK.search(after)
    if m:
        hi = end + m.start() + 1
    return text[lo:hi], start - lo, end - lo


def _subject(text: str, start: int, end: int) -> str | None:
    win, s, e = _window(text, start, end)
    folded = _folded(win)
    if len(folded) != len(win):
        # NFKD of a ligature can change length; positions are then approximate,
        # which only affects tie-breaking between cues, never the cue set.
        s = min(s, len(folded))
        e = min(e, len(folded))
    # A cue that precedes the number ("résultat net de 7 777 M$") labels it
    # before a cue that follows it; nearest wins inside each side, ties go to
    # the alphabetically first code. No wall clock, no dict-order dependence.
    before: tuple[int, str] | None = None
    after: tuple[int, str] | None = None
    for code, rx in _SUBJECT_RX:
        for m in rx.finditer(folded):
            if m.end() <= s:
                cand = (s - m.end(), code)
                if before is None or cand < before:
                    before = cand
            elif m.start() >= e:
                cand = (m.start() - e, code)
                if after is None or cand < after:
                    after = cand
            else:
                return code
    if before is not None and before[0] <= 40:
        return before[1]
    if after is not None:
        return after[1]
    if before is not None:
        return before[1]
    if _DIRECTION.search(folded):
        return "change"
    return None


def _cents(raw: str, mult: str | None) -> int | None:
    token = raw
    if mult and re.fullmatch(r"\d{1,3},\d+", " ".join(str(raw).split())):
        # "1,151 milliard $": beside a multiplier word the comma is a decimal
        # comma whatever its width (enrich._parse_number would read 1 151).
        token = str(raw).replace(",", ".")
    value = enrich._parse_number(token)
    if value is None or not math.isfinite(value):
        return None
    factor = _MULT.get((mult or "").lower(), 1) if mult else 1
    # Round the dollar figure to the cent first (one decimal source only), then
    # scale, so 1.005 cannot drift through a float multiply.
    scaled = value * factor
    if not math.isfinite(scaled):
        return None  # a 400-digit run times a multiplier overflows to inf
    cents = round(round(scaled, 2) * 100)
    if not 0 <= cents <= MAX_ABS_CENTS:
        return None
    return int(cents)


_ANNUAL_BEFORE = re.compile(r"\b(?:annuel(?:le)?s?|annual(?:ly)?|yearly)\b[^.!?;|]{0,28}$", re.I)


def _amount_unit(text: str, start: int, end: int) -> str | None:
    tail = text[end:end + 28]
    if _PER_MONTH.match(tail):
        return "per_month"
    if _PER_YEAR.match(tail):
        return "per_year"
    if _PER_OTHER.match(tail) or _PER_THE.match(tail) or re.match(r"^\s*/\s*(?:kWh|L|h|km)\b", tail, re.I):
        return None  # a rate per something else is not a "total": left out, not relabelled
    if _ANNUAL_BEFORE.search(text[max(0, start - 44):start]):
        return "per_year"  # "économies annuelles de près de 900 000 $"
    return "total"


def _amount_atoms(text: str) -> list[dict]:
    out: list[dict] = []
    foreign = bool(enrich.FOREIGN_DOLLAR.search(text))
    seen_spans: list[tuple[int, int]] = []
    if not foreign:
        for rx in (_AMOUNT_SUFFIX, _AMOUNT_PREFIX):
            for m in rx.finditer(text):
                if any(m.start() < b and a < m.end() for a, b in seen_spans):
                    continue
                seen_spans.append((m.start(), m.end()))
                mult = m.group("mult")
                # "5 M" is a multiplier only because a dollar sign follows
                # (suffix form) or precedes (prefix form); both patterns require it.
                cents = _cents(m.group("n"), mult)
                unit = _amount_unit(text, m.start(), m.end())
                if cents is None or unit is None:
                    continue
                out.append({
                    "kind": "amount", "unit": unit,
                    "subject": _subject(text, m.start(), m.end()),
                    "value": cents, "qualifier": _qualifier(text, m.start()),
                })
    for m in _PCT.finditer(text):
        value = enrich._parse_number(m.group("n"))
        if value is None or not math.isfinite(value) or not 0 <= value <= 100000:
            continue
        subject = _subject(text, m.start(), m.end())
        if subject is None:
            continue  # a bare percentage without any cue is noise, not a fact
        out.append({
            "kind": "amount", "unit": "percent_bp", "subject": subject,
            "value": int(round(value * 100)), "qualifier": _qualifier(text, m.start()),
        })
    return out


def _locate(text: str, raw: str) -> tuple[int, int] | None:
    parts = [re.escape(p) for p in str(raw or "").split()]
    if not parts:
        return None
    m = re.search(r"[\s\u00a0\u202f]+".join(parts), text)
    return (m.start(), m.end()) if m else None


def _enrich_atoms(title: str, summary: str) -> list[dict]:
    """Housing counts and price/percent amounts through enrich's own rules."""
    out: list[dict] = []
    try:
        units = enrich.propose_impact_units(title, summary)
    except Exception as exc:  # noqa: BLE001 - fail-soft: facts never block anything
        print(f"facts: propose_impact_units fault ({type(exc).__name__})", file=sys.stderr)
        return out
    for u in units:
        text = title if u.get("field") == "title" else summary
        span = _locate(text, u.get("raw", ""))
        qual = _qualifier(text, span[0]) if span else "exact"
        kind = u.get("kind")
        value = u.get("value")
        if kind == "housing_count":
            n = _int_count(str(value))
            if n is not None:
                out.append({"kind": "count", "unit": "housing_units", "subject": None, "value": n, "qualifier": qual})
        elif kind == "price" and isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                finite = math.isfinite(float(value))
            except OverflowError:  # an int too large for a float
                finite = False
            if not finite:
                continue  # inf/huge: not a figure we can hold as an integer
            subject = _subject(text, *span) if span else None
            unit = str(u.get("unit") or "")
            if unit == "percent":
                if not 0 <= value <= 100000:
                    continue  # same bound as the direct percent path
                out.append({"kind": "amount", "unit": "percent_bp", "subject": subject or "price",
                            "value": int(round(float(value) * 100)), "qualifier": qual})
            elif unit in ("CAD", "dollars", "CAD_per_month"):
                cents = _cents(repr(float(value)), None)
                per = "per_month" if unit.endswith("per_month") else (_amount_unit(text, span[0], span[1]) if span else "total")
                if cents is not None and per is not None:
                    out.append({"kind": "amount", "unit": per,
                                "subject": subject or "price", "value": cents, "qualifier": qual})
    return out


# --- dates ----------------------------------------------------------------
_MONTHS = {
    "janvier": 1, "janv": 1, "january": 1, "jan": 1,
    "fevrier": 2, "fevr": 2, "fev": 2, "february": 2, "feb": 2,
    "mars": 3, "march": 3,
    "avril": 4, "avr": 4, "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6,
    "juillet": 7, "juil": 7, "july": 7, "jul": 7,
    "aout": 8, "august": 8, "aug": 8,
    "septembre": 9, "sept": 9, "sep": 9, "september": 9,
    "octobre": 10, "oct": 10, "october": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "decembre": 12, "dec": 12, "december": 12, "decem": 12,
}
_MONTH_ALT = (
    "janvier|janv|january|jan|f[eé]vrier|f[eé]vr|f[eé]v|february|feb|mars|(?-i:March)|"
    "avril|avr|april|apr|mai|(?-i:May)|juin|june|juillet|juil|july|jul|ao[uû]t|august|aug|"
    "septembre|sept|september|sep|octobre|oct|october|novembre|nov|november|"
    "d[eé]cembre|d[eé]c|december|dec"
)
_DAY = r"(?:0?[1-9]|[12]\d|3[01])"
_YEAR = r"(?:19|20)\d{2}"
_RX_RANGE = re.compile(
    rf"(?<![\d.])(?P<d1>{_DAY})(?:er|re)?(?:{_WS})?(?:au|[àa]|to|et|and|-|–)(?:{_WS})?(?P<d2>{_DAY})(?:er|re)?{_WS}(?:of{_WS})?"
    rf"(?P<mo>{_MONTH_ALT})\b\.?(?:,?{_WS}(?P<y>{_YEAR})(?!\d))?",
    re.I,
)
_RX_RANGE_EN = re.compile(
    rf"\b(?P<mo>{_MONTH_ALT})\b\.?{_WSO}(?P<d1>{_DAY})(?:{_WS})?(?:-|–|to)(?:{_WS})?(?P<d2>{_DAY})(?!\d)(?:,?{_WS}(?P<y>{_YEAR})(?!\d))?",
    re.I,
)
_RX_DMY = re.compile(
    rf"(?<![\d.])(?P<d>{_DAY})(?:er|re|st|nd|rd|th)?{_WS}(?:of{_WS})?(?P<mo>{_MONTH_ALT})\b\.?(?:,?{_WS}(?P<y>{_YEAR})(?!\d))?",
    re.I,
)
_RX_MDY = re.compile(
    rf"\b(?P<mo>{_MONTH_ALT})\b\.?{_WS}(?P<d>{_DAY})(?:st|nd|rd|th)?(?!\d)(?![\s\u00a0\u202f]\d{{3}}(?!\d))(?:,?{_WS}(?P<y>{_YEAR})(?!\d))?",
    re.I,
)
_RX_ISO = re.compile(r"(?<![\d-])(?P<y>20\d{2})-(?P<m>0[1-9]|1[0-2])-(?P<d>0[1-9]|[12]\d|3[01])(?![\d-])")
_DATELINE_AFTER = re.compile(r"^\s*(?:/\s*CNW\s*/|[–—]|--)")
_DATELINE_BEFORE = re.compile(r"^[^.!?]{0,90}[,)]?\s*(?:le\s+)?$", re.I)


def _month_number(token: str) -> int | None:
    key = _folded(token).rstrip(".")
    return _MONTHS.get(key)


def _published(item: dict) -> date | None:
    dt = cluster_issues.published_when(item)
    return dt.date() if dt else None


def _resolve_year(day: int, month: int, year: str | None, pub: date | None) -> date | None:
    try:
        if year:
            return date(int(year), month, day)
        if pub is None:
            return None
        cand = date(pub.year, month, day)
        if cand < pub - timedelta(days=183):
            cand = date(pub.year + 1, month, day)
        elif cand > pub + timedelta(days=183):
            cand = date(pub.year - 1, month, day)
        return cand
    except ValueError:
        return None


def _date_atoms(text: str, pub: date | None) -> list[dict]:
    found: list[date] = []
    consumed: list[tuple[int, int]] = []

    def free(m: re.Match) -> bool:
        return not any(m.start() < b and a < m.end() for a, b in consumed)

    def dateline(m: re.Match) -> bool:
        # "Lévis, le 14 novembre 2026 –" is the publication stamp, not the
        # date the source states for the event.
        return bool(_DATELINE_AFTER.match(text[m.end():m.end() + 14])) and bool(_DATELINE_BEFORE.match(text[:m.start()]))

    def emit(day: str, mo: str, year: str | None) -> None:
        month = _month_number(mo)
        if month is None:
            return
        d = _resolve_year(int(day), month, year, pub)
        if d is not None:
            found.append(d)

    for rx in (_RX_RANGE, _RX_RANGE_EN):
        for m in rx.finditer(text):
            if not free(m) or dateline(m):
                continue
            consumed.append((m.start(), m.end()))
            emit(m.group("d1"), m.group("mo"), m.group("y"))
            emit(m.group("d2"), m.group("mo"), m.group("y"))
    for m in _RX_ISO.finditer(text):
        if free(m):
            consumed.append((m.start(), m.end()))
            try:
                found.append(date(int(m.group("y")), int(m.group("m")), int(m.group("d"))))
            except ValueError:
                pass
    for rx in (_RX_DMY, _RX_MDY):
        for m in rx.finditer(text):
            if not free(m) or dateline(m):
                continue
            consumed.append((m.start(), m.end()))
            emit(m.group("d"), m.group("mo"), m.group("y"))
    return [
        {"kind": "date", "unit": "stated", "subject": None, "value": d.isoformat(), "qualifier": "exact"}
        for d in found
    ]


# --- places ---------------------------------------------------------------
_NOT_A_ROAD = re.compile(r"-(?:t-)?(?:il|elle|ils|elles|on)$")


def _place_atoms(title: str, summary: str) -> list[dict]:
    probe = {"title": " ".join(x for x in (title, summary) if x)}
    out = []
    areas = cluster_issues.place_hints(probe)
    for token in sorted(cluster_issues.road_places(probe)):
        # "quartier Saint-Roch" is an area, not a road; "route est-il" is a
        # verb inversion, not a road name (both are vocabulary-reuse scars).
        if token in areas or _NOT_A_ROAD.search(token):
            continue
        out.append({"kind": "place", "unit": "road", "subject": None, "value": token, "qualifier": "exact"})
    for token in sorted(areas):
        out.append({"kind": "place", "unit": "area", "subject": None, "value": token, "qualifier": "exact"})
    return out


# --- entities: CLOSED list. No person-name matcher exists here. -----------
# (code, unit, folded-regex or None, case-sensitive regex or None)
_INSTITUTIONS = (
    ("spvq", r"service de police de la ville de quebec|\bspvq\b", None),
    ("spl", r"service de police de levis", None),
    ("sq", r"surete du quebec", r"\bSQ\b"),
    ("grc", r"gendarmerie royale du canada|royal canadian mounted police", r"\b(?:GRC|RCMP)\b"),
    ("fire_service", r"service de protection contre les incendies|fire department|fire service", None),
    ("hydro-quebec", r"hydro[- ]quebec", None),
    ("ville-quebec", r"ville de quebec|city of quebec|quebec city council|quebec city hall|hotel de ville de quebec", None),
    ("gouv-quebec", r"gouvernement du quebec|quebec government|government of quebec", None),
    ("gouv-canada", r"gouvernement du canada|government of canada|federal government|gouvernement federal", None),
    ("rtc", r"reseau de transport de la capitale", r"\bRTC\b"),
    ("mtmd", r"ministere des transports|ministry of transportation|transport ministry", r"\bMTMD\b"),
    ("saaq", r"societe de l.?assurance automobile", r"\bSAAQ\b"),
    ("bst-tsb", r"bureau de la securite des transports|transportation safety board", r"\b(?:BST|TSB)\b"),
    ("service-correctionnel", r"service correctionnel du canada|correctional service of canada", None),
    ("assemblee-nationale", r"assemblee nationale|national assembly", None),
    ("chambre-communes", r"chambre des communes|house of commons", None),
    ("cour-superieure", r"cour superieure|superior court", None),
    ("cour-supreme", r"cour supreme(?! americaine| des etats)|(?<!u\.s\. )(?<!us )(?<!american )supreme court", None),
    ("aeroport-quebec", r"aeroport jean-lesage|jean lesage airport|aeroport de quebec", None),
)
_INSTITUTION_RX = tuple(
    (code, re.compile(f, re.I) if f else None, re.compile(cs) if cs else None)
    for code, f, cs in _INSTITUTIONS
)
# Public offices (the office, never its holder). Case-sensitive pieces are
# scoped with (?-i:...). "mayor of/maire de <other city>" is skipped, not
# relabelled, because that office belongs to someone else's city.
_RX_MAYOR = re.compile(
    r"\b(?:maire|mairesse|mayor)\b(?:\s+(?:de|of)\s+(?:la\s+ville\s+de\s+|the\s+city\s+of\s+)?(?P<city>[A-ZÉÈÀÎ][\wÀ-ÿ'’-]*(?:\s+City)?))?",
)
_RX_OFFICES = (
    ("premier", re.compile(r"\bpremi[èe]re?\s+ministre\b|\bprime\s+minister\b|(?-i:\bPremier\s+(?:of\b|[A-Z]))", re.I)),
    ("minister", re.compile(r"(?<!premier )(?<!première )(?<!prime )\b(?:ministres?|minister)\b", re.I)),
    ("legislator", re.compile(r"\bd[ée]put[ée]e?s?\b|(?-i:\bMNAs?\b)", re.I)),
    ("police_chief", re.compile(r"\bchef\s+de\s+police\b|\bpolice\s+chief\b|\bchief\s+of\s+police\b", re.I)),
    ("coroner", re.compile(r"\bcoroner\b|\bcoroners\b", re.I)),
    ("city_council", re.compile(r"\bconseil\s+(?:municipal|de\s+ville|d['’]arrondissement)\b|\bcity\s+council\b", re.I)),
)
_QUEBEC_CITY = re.compile(r"^(?:qu[ée]bec|quebec\s+city)$", re.I)


def _entity_atoms(text: str) -> list[dict]:
    out: list[dict] = []
    folded = _folded(text)
    for code, rx_folded, rx_cs in _INSTITUTION_RX:
        if (rx_folded and rx_folded.search(folded)) or (rx_cs and rx_cs.search(text)):
            out.append({"kind": "entity", "unit": "institution", "subject": None, "value": code, "qualifier": "exact"})
    for m in _RX_MAYOR.finditer(text):
        city = m.group("city")
        if city is None:
            code = "mayor"
        elif _QUEBEC_CITY.match(city.strip()):
            code = "mayor_quebec"
        else:
            continue
        out.append({"kind": "entity", "unit": "office", "subject": None, "value": code, "qualifier": "exact"})
    for code, rx in _RX_OFFICES:
        if rx.search(text):
            out.append({"kind": "entity", "unit": "office", "subject": None, "value": code, "qualifier": "exact"})
    return out


# --- item level ------------------------------------------------------------
def _field(item: dict, name: str) -> str:
    v = item.get(name) if isinstance(item, dict) else None
    return v if isinstance(v, str) else ""


def extract_item(item: dict) -> list[dict]:
    """Fact atoms of one item: [{kind, unit, subject, value, qualifier}], sorted, unique.

    Atoms carry no publisher text. Fail-soft: anything that is not a dict with
    string title/summary yields what it can (possibly nothing).
    """
    if not isinstance(item, dict):
        return []
    title, summary = _field(item, "title"), _field(item, "summary")
    pub = _published(item)
    atoms: list[dict] = []
    for text in (title, summary):
        if not text:
            continue
        atoms += _count_atoms(text)
        atoms += _amount_atoms(text)
        atoms += _date_atoms(text, pub)
        atoms += _entity_atoms(text)
    atoms += _place_atoms(title, summary)
    # enrich's own price/percent/housing rules add what the local patterns
    # missed; a value both found keeps the local subject (no duplicate slot).
    have = {(a["kind"], a["unit"], a["value"]) for a in atoms if a["kind"] in ("amount", "count")}
    for a in _enrich_atoms(title, summary):
        if (a["kind"], a["unit"], a["value"]) not in have:
            atoms.append(a)
    # The same amount seen in title and summary can get two subjects; keep the
    # most specific one (specific code > "change" > none) so one value is one
    # atom. Tie: alphabetically first code. No order dependence.
    def rank(subject: str | None) -> tuple:
        if subject is None:
            return (2, "")
        return (1, subject) if subject in GENERIC_SUBJECTS else (0, subject)

    best: dict[tuple, str | None] = {}
    for a in atoms:
        if a["kind"] == "amount":
            k = (a["unit"], a["value"])
            if k not in best or rank(a["subject"]) < rank(best[k]):
                best[k] = a["subject"]
    for a in atoms:
        if a["kind"] == "amount":
            a["subject"] = best[(a["unit"], a["value"])]
    # One atom per (kind, unit, subject, value); qualifiers merge deterministically.
    merged: dict[tuple, dict] = {}
    for a in atoms:
        key = (a["kind"], a["unit"], a["subject"] or "", a["value"])
        cur = merged.get(key)
        if cur is None:
            merged[key] = {**a, "qualifiers": {a["qualifier"]}}
        else:
            cur["qualifiers"].add(a["qualifier"])
    out = []
    for key in sorted(merged, key=lambda k: (KINDS.index(k[0]), k[1], k[2], str(k[3]) if isinstance(k[3], str) else f"{k[3]:020d}")):
        a = merged[key]
        out.append({
            "kind": a["kind"], "unit": a["unit"], "subject": a["subject"], "value": a["value"],
            "qualifiers": sorted(a["qualifiers"]),
        })
    return out


# --- slots table (EVENTS.md section 9) -------------------------------------
def _is_comparable(kind: str, subject: str | None) -> bool:
    if kind in SET_KINDS:
        return False
    if kind == "count":
        return True
    return subject is not None and subject not in GENERIC_SUBJECTS


def build_slots(items: list[dict]) -> list[dict]:
    """Group the facts of ``items`` (one event's members) into slot rows.

    Values are attributed to item ids and institutions. Different values in a
    comparable count/amount slot are kept side by side with ``divergent: true``
    and never reconciled. Same inputs, same bytes.
    """
    slots: dict[tuple, dict[object, dict]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        inst = str(item.get("institution") or item.get("source_id") or "")
        for a in extract_item(item):
            slot = (a["kind"], a["unit"], a["subject"] or "")
            entry = slots.setdefault(slot, {}).setdefault(
                a["value"], {"stated_by": set(), "institutions": set(), "qualifiers": set()}
            )
            entry["stated_by"].add(item_id)
            if inst:
                entry["institutions"].add(inst)
            entry["qualifiers"].update(a["qualifiers"])
    rows = []
    for slot in sorted(slots, key=lambda s: (KINDS.index(s[0]), s[1], s[2])):
        kind, unit, subject = slot
        values = []
        for value in sorted(slots[slot], key=lambda v: (isinstance(v, str), v)):
            e = slots[slot][value]
            row = {"value": value, "stated_by": sorted(e["stated_by"]), "institutions": sorted(e["institutions"])}
            if kind in ("count", "amount"):
                row["qualifiers"] = sorted(e["qualifiers"])
            values.append(row)
        subject_out = subject or None
        comparable = _is_comparable(kind, subject_out)
        items_in_slot = {i for v in values for i in v["stated_by"]}
        rows.append({
            "slot": {"kind": kind, "unit": unit, "subject": subject_out},
            "values": values,
            "divergent": bool(comparable and len(values) >= 2 and len(items_in_slot) >= 2),
            "comparable": comparable,
            "method": METHOD,
            "status": STATUS,
        })
    return rows


# --- render-time spans (publisher text: NEVER store, seal or commit) -------
_SPAN_UNITS = (
    r"-?(?:year|years|yr|day|days|month|months|week|weeks|hour|hours)(?:-old)?|"
    r"ans?|jours?|mois|semaines?|heures?|minutes?|h(?![\w])|min(?![\w])|"
    r"km|kilom[èe]tres?|m[èe]tres?|kg|kilos?|tonnes?|m2|m²|pi2|pi²|"
    r"%|\$|¢|"
    r"(?:milliards?|millions?|billions?|mille|thousand|M|G|k)(?:[\s\u00a0]*\$|[\s\u00a0]+(?:de[\s\u00a0]+)?(?:dollars?|\$))?|"
    r"dollars?|"
    r"(?:" + _PERSON + r"|morts?|bless[ée]e?s?|logements?|emplois?|jobs?|v[ée]hicules?|voitures?|"
    r"clients?|foyers?|customers?|b[âa]timents?|immeubles?|units?)"
)
_RX_SPAN = re.compile(
    rf"(?:{_DAY}(?:er|re|st|nd|rd|th)?[\s\u00a0]+(?:of[\s\u00a0]+)?(?:{_MONTH_ALT})\b\.?(?:,?[\s\u00a0]+{_YEAR}(?!\d))?)|"
    rf"(?:\b(?:{_MONTH_ALT})\b\.?[\s\u00a0]+{_DAY}(?:st|nd|rd|th)?(?!\d)(?:,?[\s\u00a0]+{_YEAR}(?!\d))?)|"
    rf"(?:(?<![\w])\$[\s\u00a0]*{enrich.RE_NUM}(?:[\s\u00a0]*(?:million|billion|thousand|M|B|k)\b)?)|"
    rf"(?:{enrich.RE_NUM}(?:[\s\u00a0]*(?:{_SPAN_UNITS})(?![A-Za-zÀ-ÿ0-9]))?)",
    re.I,
)


def spans(text: str) -> list[str]:
    """Verbatim numeric spans of ``text``, in order ("51-year-old", "20 ans", "2003").

    RENDER-TIME ONLY. The result is publisher text: show it next to its source
    attribution, never store, seal, log or commit it. Pure and deterministic.
    """
    if not isinstance(text, str) or not text:
        return []
    out: list[str] = []
    for m in _RX_SPAN.finditer(text):
        s = " ".join(m.group(0).split())
        if s and any(ch.isdigit() for ch in s):
            out.append(s[:60])
    return out


# --- shadow CLI: counts only ------------------------------------------------
def summarize(items: list[dict]) -> dict:
    """Counts and ids only (safe to commit as a measurement)."""
    n = 0
    per_kind = {k: 0 for k in KINDS}
    per_unit: dict[str, int] = {}
    any_fact = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        n += 1
        atoms = extract_item(item)
        if atoms:
            any_fact += 1
        for k in {a["kind"] for a in atoms}:
            per_kind[k] += 1
        for key in {f"{a['kind']}:{a['unit']}" for a in atoms}:
            per_unit[key] = per_unit.get(key, 0) + 1
    return {
        "method": METHOD, "status": STATUS, "items": n, "items_with_any_fact": any_fact,
        "items_with_kind": per_kind, "items_with_unit": dict(sorted(per_unit.items())),
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        i = argv.index("--in") if "--in" in argv else -1
        src = Path(argv[i + 1]) if 0 <= i < len(argv) - 1 else IN_PATH
        doc = json.loads(src.read_text(encoding="utf-8"))
        items = doc.get("candidates") if isinstance(doc, dict) else doc
        summary = summarize(items if isinstance(items, list) else [])
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        if "--write" in argv:
            store_io.write_json_atomic(OUT_PATH, summary)
    except Exception as exc:  # noqa: BLE001 - shadow stage: diagnose, exit 0
        print(f"facts: shadow summary skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
