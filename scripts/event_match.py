"""Event matcher: graded same-event score, confidence tiers, sticky clusterer.

Production form of the Phase 0 graded matcher (eval/graded_matcher.py now
imports this module, so the evaluation measures what ships). Python standard
library only; pure functions; no I/O and no wall clock; deterministic under
any PYTHONHASHSEED (every sum over a set is exactly rounded with math.fsum,
every emitted list is sorted by an explicit key ending in an id).

  score(a, b, ctx)  -> (probability 0..1, reasons)
  tier(score, ...)  -> "certain" | "probable" | "possible" | None
  match(a, b, ctx)  -> Match(score, tier, reasons, guard)
  attach(events, new_items, ctx, edition=...) -> (events, decisions)

What a score is made of (every component is a measured fact about the two
items, never a rewrite of either):

  time      publication proximity (exp decay, 12 h scale; unknown = neutral)
  far       published more than 7 days apart (negative evidence)
  numbers   shared salient numbers (IDF-weighted: "2026" is cheap, "138" is not)
  dates     a shared calendar date or weekday named in the text
  names     shared capitalised names (IDF-weighted overlap)
  place     a shared named place or road from the bilingual lexicon
  conflict  both headlines name a specific place or road, none shared (negative)
  terms     shared institutions, projects, parties, event types (lexicon ids)
  event     a shared event type (fire, strike, trial...)
  event_conflict  both headlines name event types and share none (negative)
  tokens    stemmed token overlap after French/English canonicalisation
  title     the same, headlines only (overlap coefficient)
  chars     character 4-gram TF-IDF cosine (catches names and spelling variants)
  cross     the pair crosses languages (fr-en), plus cross x tokens / chars

A logistic model adds them up: WEIGHTS were fitted once on the DEV split of
the private, model-labelled gold set (L2 pull toward PRIOR_WEIGHTS) and are
frozen here; THRESHOLDS were chosen on DEV only (eval/run_eval.py reproduces
both and says when they drift). The IDF tables come from the context corpus
(the items of the window being matched), never from labels.

Tiers. "certain" and "probable" pairs may group articles; "possible" pairs
are only ever shown as neighbours, never merged. A French/English pair must
share a name, a specific place or a salient number (the FR/EN guard), or its
tier is capped at "possible". Same-owner pairs (CBC / Radio-Canada) are
matched like any other: independence is counted elsewhere, never here.

Reasons are render-time explanations: they quote single words or numbers as
each publisher wrote them (so "privatisation ≙ privatization", never a stem)
and Vigie's own lexicon labels. They must never be stored in the event store
or sealed: only ids, tiers and scores are.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterable, NamedTuple

import event_lexicon as lex

METHOD = ("event-match-v1 logistic(time,numbers,dates,names,lexicon,tokens,chargrams) "
          "tiers(certain,probable,possible) fr-en-guard sticky-average-link")

FEATURES = (
    "time", "far", "numbers", "dates", "names", "place", "conflict", "terms",
    "event", "event_conflict", "tokens", "title", "chars", "cross", "cross_tokens", "cross_chars",
)

# Hand-set priors (log-odds per unit of evidence), Phase 0: what a careful
# reader would weigh, adjusted once after reading DEV errors only.
PRIOR_WEIGHTS = {
    "bias": -4.0, "time": 1.5, "far": -2.0, "numbers": 1.0, "dates": 0.5,
    "names": 2.0, "place": 1.0, "conflict": -1.0, "terms": 1.0, "event": 0.5,
    "event_conflict": -0.5, "tokens": 4.0, "title": 2.0, "chars": 3.0,
    "cross": 0.0, "cross_tokens": 2.0, "cross_chars": -1.0,
}

# Frozen: logistic refit on the gold_v0 DEV split (309 pairs; labels are
# model-generated, see eval/README.md), L2 0.002 toward PRIOR_WEIGHTS, 4000
# batch epochs at rate 0.5, IDF from the 2,139-item private history.
# Reproduced by eval/run_eval.py (scorer "graded-guard", which reports any
# drift); the test split played no part.
WEIGHTS = {
    "bias": -5.379, "time": 3.0737, "far": -2.0156, "numbers": 1.5653, "dates": 0.9298,
    "names": 0.5583, "place": 0.3264, "conflict": -1.0, "terms": 0.7305, "event": 0.4676,
    "event_conflict": -0.73, "tokens": 4.9618, "title": 1.872, "chars": 4.7436,
    "cross": 1.2045, "cross_tokens": 2.5188, "cross_chars": -0.1393,
}

TIERS = ("certain", "probable", "possible")
TIER_RANK = {"certain": 3, "probable": 2, "possible": 1, None: 0}
# Chosen on DEV only by eval/run_eval.py (choose_tiers), FR/EN guard applied,
# floored to 4 decimals so the boundary pair stays in its tier:
#   certain  = lowest dev threshold whose dev precision is >= 0.95 there and
#              at every higher threshold (0.871366...)
#   probable = median of the dev F1 plateau, within 0.01 of the best F1
#              (0.398545...; the single arg-max would be 0.4630)
#   possible = lowest dev threshold at which >= 90% of the dev pairs at or
#              above it are same_event or related (0.098424...)
THRESHOLDS = {"certain": 0.8713, "probable": 0.3985, "possible": 0.0984}
MERGE_TIER = "probable"     # a pair at this tier or above may group articles
MERGE_MIN_CROSS_PAIRS = 2   # two events merge on at least this many cross pairs
MERGE_PAIR_TIER = "probable"  # at this tier or above
# ... and an average cross score >= the probable threshold. Chosen on DEV:
# the same dev induced F1 as the bare two-pair rule (0.812) with higher dev
# precision (0.842 vs 0.800), and on an unlabelled replay of 49 stamped
# editions the bare rule snowballed into a 100-member "event" of unrelated
# stories (96 merges) while this one keeps the largest at 11 (11 merges).
MERGE_AVERAGE = True

TIME_SCALE_HOURS = 12.0
FAR_HOURS = 7 * 24.0        # pairs further apart are never compared by the clusterer
WINDOW_HOURS = 72.0         # an event accepts members while within 72 h of one
EDITION_WINDOW_DAYS = 7     # and while its newest member is within 7 days of the edition
CHAR_N = 4
SUMMARY_CHARS = 400
BLOCK_MIN_CAP = 30          # a blocking key shared by more items than the cap
BLOCK_SHARE = 0.04          # (max of 30 and 4% of the window) proves nothing
GUARD_NAME_SHARE = 0.01     # FR/EN guard: a name counts when at most 1% of the
GUARD_NAME_MIN_DF = 3       # context's items carry it (never fewer than 3)
GUARD_NUMBER_MIN = 10       # and a number when it is >= 10 and not a year

STOPWORDS = frozenset(lex.fold(w) for w in """
le la les un une des du de d l et en au aux a à ce cet cette ces son sa ses leur leurs
il elle ils elles on nous vous je tu que qui quoi dont ou où dans par pour sur sous avec
sans entre vers chez est sont être été avoir ont a plus moins très pas ne se si mais donc
car comme aussi après avant depuis lors selon alors encore déjà tout tous toute toutes
fait faire va vont peut peuvent doit doivent sera seront était étaient cela ceci celui
celle ainsi bien non oui quand lui eux leurs même mêmes autre autres fois hier demain
aujourd hui c est n qu y s t m j via contre lors dès
the an and of to in on for at by with from as is are was were be been has have had it
its this that these those their his her they he she we you i not but or if will would
can could may might after before over into than about new says say said who what when
where why how more most also just out up down off amid among what's it's
""".split())

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "april": 4, "apr": 4,
    "june": 6, "july": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
WEEKDAYS = {
    "lundi": 1, "mardi": 2, "mercredi": 3, "jeudi": 4, "vendredi": 5, "samedi": 6, "dimanche": 7,
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 7,
}
_MONTH_FR = ("", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre")
_MONTH_EN = ("", "January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December")
_WEEKDAY_FR = ("", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_WEEKDAY_EN = ("", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NUMBER_WORDS = {
    "trois": "3", "quatre": "4", "cinq": "5", "six": "6", "sept": "7", "huit": "8",
    "neuf": "9", "dix": "10", "douze": "12", "vingt": "20", "cent": "100",
    "three": "3", "four": "4", "five": "5", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "twelve": "12", "twenty": "20", "hundred": "100",
}
# Capitalised words that are grammar or calendar, not names.
NAME_STOP = STOPWORDS | frozenset(MONTHS) | frozenset(WEEKDAYS) | frozenset(
    lex.fold(w) for w in "mme m mr mrs dr st ste saint sainte nord sud est ouest".split()
)
# Names that are a broad place or a role in the lexicon ("Québec", "Canada",
# "Ottawa", "Premier") prove nothing about one happening: the FR/EN guard
# never accepts them alone.
_WEAK_NAME_KINDS = frozenset({"broad", "role"})

# Applied after the plural is dropped, so singular and plural share a stem.
_SUFFIXES = tuple(sorted((
    "issement", "ement", "ation", "ateur", "atrice", "ance", "ence", "ite", "eur",
    "euse", "ive", "if", "ment", "ing", "er", "ed", "e",
), key=lambda s: (-len(s), s)))


# ---------------------------------------------------------------- text
def stem(word: str) -> str:
    """Light French/English suffix folding (no dictionary). Stems keep >= 4 letters.

    Internal key only: a stem is never shown to a reader (reasons quote the
    words as published)."""
    if len(word) <= 4:
        return word
    if word[-1] in "sx" and not word.endswith("ss"):
        word = word[:-1]
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)]
    return word


def canon_token(word: str) -> str:
    return stem(lex.WORD_CANON.get(word, word))


def fold_with_map(text: str) -> tuple[str, list[int]]:
    """lex.fold(text) plus, for each folded character, its index in text.

    Lets a reason quote the publisher's own spelling of a folded match
    ("Pierre-Laporte", "Québec") instead of the folded key.
    """
    out: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(str(text or "")):
        for d in unicodedata.normalize("NFKD", ch.lower()):
            if unicodedata.combining(d):
                continue
            if "a" <= d <= "z" or "0" <= d <= "9":
                out.append(d)
                where.append(i)
            elif out and out[-1] != " ":
                out.append(" ")
                where.append(i)
    while out and out[-1] == " ":
        out.pop()
        where.pop()
    return "".join(out), where


def _surface(text: str, where: list[int], start: int, end: int) -> str:
    return text[where[start]: where[end - 1] + 1] if end > start else ""


def published_when(item: dict) -> datetime | None:
    raw = str(item.get("published_at") or "").strip()
    if not raw:
        return None
    for parser in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            dt = parser(raw)
        except (ValueError, TypeError, OverflowError):
            continue
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
    return None


def _clock(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else None
    return published_when({"published_at": raw})


def language_of(item: dict) -> str:
    lang = str(item.get("language") or "").strip().lower()
    return "en" if lang.startswith("en") else "fr" if lang.startswith("fr") else ""


_WORD = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+(?:[-'][0-9A-Za-zÀ-ÖØ-öø-ÿ]+)*")
_THOUSANDS = "[ ,  ]"  # "60 000" (fr) and "60,000" (en) are one number
_NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:" + _THOUSANDS + r"\d{3})+(?![\d])|\d+(?:[.,]\d+)?)(?![\w])")
_GROUPED = re.compile(r"\d{1,3}(?:" + _THOUSANDS + r"\d{3})+")


def _strip_elision(word: str) -> str:
    if "'" in word:
        head, _, tail = word.partition("'")
        if len(head) <= 2 and tail:
            return tail
    return word


def names_of(title: str, summary: str) -> tuple[set[str], dict[str, str]]:
    """Capitalised names (folded) and a folded->surface map for explanations.

    The first word of each sentence is skipped unless it continues into another
    capitalised word ("Bruno Marchand dit..." keeps "bruno marchand"). Not NER.
    """
    names: set[str] = set()
    surface: dict[str, str] = {}
    for block in (title, summary):
        text = str(block or "").replace("’", "'")
        for sentence in re.split(r"(?<=[.!?:;«»\"])\s+|\s+[-–—]\s+", text):
            words = [_strip_elision(w) for w in _WORD.findall(sentence)]
            run: list[str] = []

            def flush(run=run):
                if not run:
                    return
                parts = [w for w in run if lex.fold(w) not in NAME_STOP]
                for w in parts:
                    f = lex.fold(w)
                    if len(f) >= 3 and not f.isdigit():
                        names.add(f)
                        surface.setdefault(f, w)
                if len(parts) >= 2:
                    f = lex.fold(" ".join(parts))
                    names.add(f)
                    surface.setdefault(f, " ".join(parts))
                run.clear()

            for i, w in enumerate(words):
                cap = w[:1].isupper() and not w.isupper() or (w.isupper() and len(w) >= 2 and w.isalpha())
                if cap and (i > 0 or (len(words) > 1 and words[1][:1].isupper())):
                    run.append(w)
                elif cap and i == 0:
                    continue
                else:
                    flush()
            flush()
    return names, surface


def numbers_with_surface(text: str) -> dict[str, str]:
    """Canonical number -> the number as written ("60 000" and "60,000" -> 60000)."""
    out: dict[str, str] = {}
    for m in _NUM.finditer(text):
        raw = m.group(1)
        value_text = re.sub(_THOUSANDS, "", raw) if _GROUPED.fullmatch(raw) else raw
        try:
            value = float(value_text.replace(",", "."))
        except ValueError:
            continue
        key = str(int(value)) if value.is_integer() else ("%.3f" % value).rstrip("0")
        out.setdefault(key, raw)
    folded, where = fold_with_map(text)
    for m in re.finditer(r"[a-z0-9]+", folded):
        key = NUMBER_WORDS.get(m.group(0))
        if key:
            out.setdefault(key, _surface(text, where, m.start(), m.end()))
    return out


def numbers_of(text: str) -> set[str]:
    return set(numbers_with_surface(text))


def dates_of(folded: str) -> set[str]:
    out = set()
    words = folded.split()
    for i, w in enumerate(words):
        if w in WEEKDAYS:
            out.add("wd:%d" % WEEKDAYS[w])
        if w in MONTHS:
            month = MONTHS[w]
            # "15 septembre" / "1er octobre" / "September 15"
            prev = words[i - 1] if i > 0 else ""
            nxt = words[i + 1] if i + 1 < len(words) else ""
            for cand in (prev.replace("er", ""), nxt):
                if cand.isdigit() and 1 <= int(cand) <= 31:
                    out.add("d:%02d-%02d" % (month, int(cand)))
    return out


def date_label(key: str, lang: str = "fr") -> str:
    """Vigie's own rendering of a date key ("d:10-01" -> "1er octobre")."""
    if key.startswith("wd:"):
        day = int(key[3:])
        return (_WEEKDAY_EN if lang == "en" else _WEEKDAY_FR)[day]
    month, day = (int(x) for x in key[2:].split("-"))
    if lang == "en":
        return "%s %d" % (_MONTH_EN[month], day)
    return "%s %s" % ("1er" if day == 1 else str(day), _MONTH_FR[month])


def char_grams(folded: str) -> Counter:
    text = " " + re.sub(r"\s+", " ", folded) + " "
    return Counter(text[i:i + CHAR_N] for i in range(max(0, len(text) - CHAR_N + 1)))


def is_year(number: str) -> bool:
    return number.isdigit() and len(number) == 4 and 1900 <= int(number) <= 2100


# ---------------------------------------------------------------- features
class ItemFeatures:
    """Everything the matcher reads from one item, computed once.

    The *_surface maps hold single words, numbers or lexicon matches exactly
    as the publisher wrote them; they exist only to explain a score."""

    __slots__ = ("id", "lang", "when", "tokens", "title_tokens", "names", "name_surface",
                 "terms", "title_terms", "term_surface", "spec_places", "head_places", "title_events",
                 "numbers", "number_surface", "dates", "grams", "vector", "tok_surface",
                 "title_tok_surface", "w", "tot")

    def __init__(self, item: dict):
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or "")[:SUMMARY_CHARS]
        self.id = str(item.get("id") or "")
        self.lang = language_of(item)
        self.when = published_when(item)
        f_title, map_title = fold_with_map(title)
        f_summary, map_summary = fold_with_map(summary)
        self.title_tok_surface: dict[str, str] = {}
        self.tok_surface: dict[str, str] = {}
        title_words: list[str] = []
        for m in re.finditer(r"[a-z0-9]+", f_title):
            w = m.group(0)
            if len(w) >= 3 and w not in STOPWORDS and not w.isdigit():
                key = canon_token(w)
                title_words.append(key)
                self.title_tok_surface.setdefault(key, _surface(title, map_title, m.start(), m.end()))
        self.title_tokens = set(title_words)
        self.tok_surface.update(self.title_tok_surface)
        summary_tokens: set[str] = set()
        for m in re.finditer(r"[a-z0-9]+", f_summary):
            w = m.group(0)
            if len(w) >= 3 and w not in STOPWORDS and not w.isdigit():
                key = canon_token(w)
                summary_tokens.add(key)
                self.tok_surface.setdefault(key, _surface(summary, map_summary, m.start(), m.end()))
        self.tokens = self.title_tokens | summary_tokens
        self.names, self.name_surface = names_of(title, summary)
        self.term_surface: dict[str, str] = {}
        self.title_terms = set()
        for folded, where, text, into_title in ((f_title, map_title, title, True),
                                                 (f_summary, map_summary, summary, False)):
            for m in lex._PATTERN.finditer(folded):
                eid = lex.SURFACE_TO_ID[m.group(0)]
                if into_title:
                    self.title_terms.add(eid)
                self.term_surface.setdefault(eid, _surface(text, where, m.start(), m.end()))
        self.terms = set(self.term_surface)
        self.spec_places = {t for t in self.terms if lex.kind_of(t) in lex.SPECIFIC_PLACE_KINDS}
        self.head_places = {t for t in self.title_terms if lex.kind_of(t) in lex.SPECIFIC_PLACE_KINDS}
        self.title_events = {t for t in self.title_terms if lex.kind_of(t) == "event"}
        self.number_surface = numbers_with_surface(title + " " + summary)
        self.numbers = set(self.number_surface)
        self.dates = dates_of(f_title + " " + f_summary)
        # Headline counted twice: it is the item's own statement of the event.
        self.grams = char_grams(f_title + " " + f_title + " " + f_summary)
        self.vector: dict[str, float] = {}
        # Per-context IDF weights of each feature set and their totals, filled
        # by MatchContext (they depend on the corpus, not on the item alone).
        self.w: dict[str, dict[str, float]] = {}
        self.tot: dict[str, float] = {}


def term_weight(t: str, idf: float) -> float:
    return lex.KIND_WEIGHT.get(lex.kind_of(t), 0.3) * idf


class Idf:
    TABLES = ("tokens", "names", "terms", "numbers", "grams")

    def __init__(self):
        self.n = 0
        self.df: dict[str, Counter] = {k: Counter() for k in self.TABLES}

    def add(self, f: ItemFeatures) -> None:
        self.n += 1
        self.df["tokens"].update(f.tokens)
        self.df["names"].update(f.names)
        self.df["terms"].update(f.terms)
        self.df["numbers"].update(f.numbers)
        self.df["grams"].update(f.grams.keys())

    def idf(self, table: str, key: str) -> float:
        return math.log((self.n + 1) / (self.df[table].get(key, 0) + 1)) + 1.0


def _item_key(item: dict) -> str:
    return str(item.get("id") or "") or repr(sorted((str(k), str(v)) for k, v in item.items()))


class MatchContext:
    """IDF tables of a corpus plus a per-item feature cache.

    The corpus is the set of items being matched (in production: the items of
    the window; in the evaluation: the private history). Built in id order,
    one copy per id, so the same corpus always yields the same tables.
    """

    def __init__(self, corpus: Iterable[dict] = (), *, weights: dict | None = None,
                 thresholds: dict | None = None, guard: bool = True):
        self.weights = dict(WEIGHTS if weights is None else weights)
        self.thresholds = dict(THRESHOLDS if thresholds is None else thresholds)
        # The FR/EN guard is always on in production; the evaluation turns it
        # off only to measure what it changes.
        self.use_guard = guard
        self.idf = Idf()
        self._cache: dict[str, ItemFeatures] = {}
        seen: set[str] = set()
        for item in sorted((c for c in corpus if isinstance(c, dict)), key=_item_key):
            key = _item_key(item)
            if key in seen:
                continue
            seen.add(key)
            self.idf.add(self.features(item, vector=False))
        for f in self._cache.values():
            self._vectorise(f)

    def features(self, item: dict, vector: bool = True) -> ItemFeatures:
        key = _item_key(item)
        f = self._cache.get(key)
        if f is None:
            f = ItemFeatures(item)
            self._cache[key] = f
        if vector and not f.vector:
            self._vectorise(f)
        return f

    def _vectorise(self, f: ItemFeatures) -> None:
        idf = self.idf.idf
        vec = {g: (1.0 + math.log(c)) * idf("grams", g) for g, c in f.grams.items()}
        norm = math.sqrt(math.fsum(v * v for v in vec.values())) or 1.0
        f.vector = {g: v / norm for g, v in vec.items()}
        f.w = {
            "tokens": {k: idf("tokens", k) for k in f.tokens},
            "names": {k: idf("names", k) for k in f.names},
            "numbers": {k: idf("numbers", k) for k in f.numbers},
            "terms": {k: term_weight(k, idf("terms", k)) for k in f.terms},
        }
        f.tot = {table: math.fsum(w.values()) for table, w in f.w.items()}
        f.tot["title"] = math.fsum(f.w["tokens"][k] for k in f.title_tokens)


# ---------------------------------------------------------------- components
# Weighted set similarities. Shared weight is an exactly rounded sum
# (math.fsum), so it never depends on set iteration order (PYTHONHASHSEED);
# per-item totals are precomputed by MatchContext. Both sides carry the same
# IDF weight for a shared key (one context, one table).
def _shared(fa: ItemFeatures, keys, table: str) -> float:
    w = fa.w[table]
    return math.fsum(w[k] for k in keys)


def _dice(shared: float, tot_a: float, tot_b: float) -> float:
    """Dice with weights: one shared name between two long lists scores low."""
    return 2.0 * shared / max(1e-9, tot_a + tot_b)


def _jaccard(shared: float, tot_a: float, tot_b: float) -> float:
    return shared / max(1e-9, tot_a + tot_b - shared)


def _overlap(shared: float, tot_a: float, tot_b: float) -> float:
    """Overlap coefficient with weights: shared weight / smaller side's weight."""
    return shared / max(1e-9, min(tot_a, tot_b))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _values(fa: ItemFeatures, fb: ItemFeatures) -> tuple[dict, dict]:
    """Component values (each 0..1) and the shared key sets behind them."""
    x: dict[str, float] = {}
    if fa.when is None or fb.when is None:
        hours = None
        x["time"], x["far"] = 0.5, 0.0
    else:
        hours = abs((fa.when - fb.when).total_seconds()) / 3600.0
        x["time"] = math.exp(-hours / TIME_SCALE_HOURS)
        x["far"] = 1.0 if hours > FAR_HOURS else 0.0
    sh_numbers = fa.numbers & fb.numbers
    x["numbers"] = _dice(_shared(fa, sh_numbers, "numbers"), fa.tot["numbers"], fb.tot["numbers"]) if sh_numbers else 0.0
    sh_dates = fa.dates & fb.dates
    x["dates"] = 1.0 if any(d.startswith("d:") for d in sh_dates) else 0.5 if sh_dates else 0.0
    sh_names = fa.names & fb.names
    x["names"] = _dice(_shared(fa, sh_names, "names"), fa.tot["names"], fb.tot["names"]) if sh_names else 0.0
    sh_places = fa.spec_places & fb.spec_places
    x["place"] = 1.0 if sh_places else 0.0
    # Conflicts read headlines only: summaries name side streets and
    # neighbouring places that the headline event does not depend on.
    x["conflict"] = 1.0 if fa.head_places and fb.head_places and not sh_places else 0.0
    sh_terms = fa.terms & fb.terms
    evt_a, evt_b = fa.title_events, fb.title_events
    x["event_conflict"] = 1.0 if evt_a and evt_b and not (sh_terms & (evt_a | evt_b)) else 0.0
    x["terms"] = _dice(_shared(fa, sh_terms, "terms"), fa.tot["terms"], fb.tot["terms"]) if sh_terms else 0.0
    sh_events = {t for t in sh_terms if lex.kind_of(t) == "event"}
    x["event"] = 1.0 if sh_events else 0.0
    sh_tokens = fa.tokens & fb.tokens
    shared_tok = _shared(fa, sh_tokens, "tokens") if sh_tokens else 0.0
    x["tokens"] = _jaccard(shared_tok, fa.tot["tokens"], fb.tot["tokens"]) if sh_tokens else 0.0
    sh_title = fa.title_tokens & fb.title_tokens
    x["title"] = _overlap(_shared(fa, sh_title, "tokens"), fa.tot["title"], fb.tot["title"]) if sh_title else 0.0
    va, vb = fa.vector, fb.vector
    x["chars"] = math.fsum(va[g] * vb[g] for g in va.keys() & vb.keys())
    cross = 1.0 if fa.lang and fb.lang and fa.lang != fb.lang else 0.0
    x["cross"] = cross
    x["cross_tokens"] = cross * x["tokens"]
    x["cross_chars"] = cross * x["chars"]
    shared = {"hours": hours, "numbers": sh_numbers, "dates": sh_dates, "names": sh_names, "place": sh_places,
              "terms": sh_terms, "event": sh_events, "tokens": sh_tokens, "title_tokens": sh_title}
    return x, shared


def components(a: dict, b: dict, ctx: MatchContext) -> tuple[dict, dict]:
    """Component values x (each 0..1) and the evidence behind them (for reasons)."""
    fa, fb = ctx.features(a), ctx.features(b)
    x, sh = _values(fa, fb)

    def by_weight(keys, table):
        w = fa.w[table]
        return sorted(keys, key=lambda k: (-w[k], k))

    ev: dict[str, object] = {
        "a": fa, "b": fb, "hours": sh["hours"],
        "numbers": by_weight(sh["numbers"], "numbers"),
        "dates": sorted(sh["dates"]),
        "names": by_weight(sh["names"], "names"),
        "place": sorted(sh["place"]),
        "conflict": (sorted(fa.head_places), sorted(fb.head_places)) if x["conflict"] else None,
        "event_conflict": (sorted(fa.title_events), sorted(fb.title_events)) if x["event_conflict"] else None,
        "terms": by_weight(sh["terms"], "terms"),
        "event": sorted(sh["event"]),
        "tokens": by_weight(sh["tokens"], "tokens"),
        "title_tokens": by_weight(sh["title_tokens"], "tokens"),
    }
    return x, ev


def logit(x: dict, weights: dict) -> float:
    # FEATURES is a tuple: a fixed summation order, so a deterministic result.
    return weights.get("bias", 0.0) + sum(weights.get(k, 0.0) * x[k] for k in FEATURES)


def probability(a: dict, b: dict, ctx: MatchContext) -> float:
    """The score alone (no reasons): what the clusterer and the evaluation use."""
    x, _ = _values(ctx.features(a), ctx.features(b))
    return _sigmoid(logit(x, ctx.weights))


# ---------------------------------------------------------------- FR/EN guard
class Guard(NamedTuple):
    applies: bool          # the pair crosses French and English
    ok: bool               # it shares a name, a specific place or a salient number
    anchors: tuple         # what satisfied it: (("name", "Marchand"), ...)


def _rare_names(fa: ItemFeatures, fb: ItemFeatures, ctx: MatchContext) -> list[str]:
    cap = max(GUARD_NAME_MIN_DF, GUARD_NAME_SHARE * ctx.idf.n)
    out = []
    for n in sorted(fa.names & fb.names):
        eid = lex.SURFACE_TO_ID.get(n)
        if eid and lex.kind_of(eid) in _WEAK_NAME_KINDS:
            continue
        if ctx.idf.df["names"].get(n, 0) > cap:
            continue
        out.append(n)
    return out


def _salient_numbers(fa: ItemFeatures, fb: ItemFeatures) -> list[str]:
    out = []
    for n in sorted(fa.numbers & fb.numbers):
        try:
            value = float(n)
        except ValueError:
            continue
        if value >= GUARD_NUMBER_MIN and not is_year(n):
            out.append(n)
    return out


def fr_en_guard(a: dict, b: dict, ctx: MatchContext) -> Guard:
    """A French/English pair must share a specific place, a rare name or a
    salient number, or it can never be grouped (its tier is capped at
    "possible").

    Shared time and shared common words are not enough across languages:
    Phase 0 found fr-en false merges that shared little more than an hour and
    a country. Never enough on their own: broad places and roles ("Québec",
    "Canada", "premier"), a name carried by more than 1% of the window's items
    (a head of government appears in many unrelated stories), a year, or a
    number below 10. Chosen on DEV only (eval/REPORT.md, "FR/EN guard").
    """
    fa, fb = ctx.features(a), ctx.features(b)
    if not (fa.lang and fb.lang and fa.lang != fb.lang):
        return Guard(False, True, ())
    anchors = [("place", t) for t in sorted(fa.spec_places & fb.spec_places)]
    anchors += [("name", n) for n in _rare_names(fa, fb, ctx)]
    anchors += [("number", n) for n in _salient_numbers(fa, fb)]
    return Guard(True, bool(anchors), tuple(anchors))


# ---------------------------------------------------------------- tiers
def tier(score: float, guard_ok: bool = True, thresholds: dict | None = None) -> str | None:
    """Confidence tier of a pair score. Monotone: a higher score never gets a
    lower tier; a failed FR/EN guard caps the tier at "possible" (shown as a
    neighbour, never merged) and never raises it."""
    t = THRESHOLDS if thresholds is None else thresholds
    if guard_ok and score >= t["certain"]:
        return "certain"
    if guard_ok and score >= t["probable"]:
        return "probable"
    if score >= t["possible"]:
        return "possible"
    return None


def at_least(t: str | None, floor: str) -> bool:
    return TIER_RANK[t] >= TIER_RANK[floor]


# ---------------------------------------------------------------- reasons
def _num(value: float, lang: str, digits: int = 2) -> str:
    text = "%.*f" % (digits, value)
    return text.replace(".", ",") if lang == "fr" else text


def _pairs(keys: list[str], left: dict, right: dict, limit: int = 4) -> list[list[str]]:
    out = []
    for k in keys[:limit]:
        out.append([left.get(k) or right.get(k) or k, right.get(k) or left.get(k) or k])
    return out


def _alias(pairs: list[list[str]]) -> str:
    """Two spellings of one anchor: "Québec ≙ Quebec"; one when identical."""
    return ", ".join(p[0] if p[0] == p[1] else "%s ≙ %s" % (p[0], p[1]) for p in pairs)


def _reason(code: str, contribution: float, x: dict, ev: dict) -> dict | None:
    fa: ItemFeatures = ev["a"]
    fb: ItemFeatures = ev["b"]
    hours = ev.get("hours")
    anchors: list[list[str]] = []
    if code == "time":
        if hours is None:
            fr, en = "heure de publication inconnue", "publication time unknown"
        else:
            fr = "publiés à %s h d'intervalle" % _num(hours, "fr", 0)
            en = "published %s h apart" % _num(hours, "en", 0)
    elif code == "far":
        fr = "publiés à %s jours d'intervalle" % _num(hours / 24.0, "fr", 0)
        en = "published %s days apart" % _num(hours / 24.0, "en", 0)
    elif code == "numbers" and ev["numbers"]:
        anchors = _pairs(ev["numbers"], fa.number_surface, fb.number_surface, 3)
        fr, en = "nombres communs : " + _alias(anchors), "shared numbers: " + _alias(anchors)
    elif code == "dates" and ev["dates"]:
        keys = ev["dates"][:2]
        anchors = [[date_label(k, "fr"), date_label(k, "en")] for k in keys]
        fr = "même date nommée : " + ", ".join(date_label(k, "fr") for k in keys)
        en = "same date named: " + ", ".join(date_label(k, "en") for k in keys)
    elif code == "names" and ev["names"]:
        anchors = _pairs(ev["names"], fa.name_surface, fb.name_surface, 3)
        fr, en = "noms communs : " + _alias(anchors), "shared names: " + _alias(anchors)
    elif code == "place" and ev["place"]:
        anchors = _pairs(ev["place"], fa.term_surface, fb.term_surface, 3)
        fr = "même lieu : " + ", ".join(lex.label_of(t, "fr") for t in ev["place"][:3])
        en = "same place: " + ", ".join(lex.label_of(t, "en") for t in ev["place"][:3])
    elif code == "conflict" and ev["conflict"]:
        pa, pb = ev["conflict"]
        fr = "lieux différents : %s / %s" % (", ".join(lex.label_of(t, "fr") for t in pa[:2]),
                                              ", ".join(lex.label_of(t, "fr") for t in pb[:2]))
        en = "different places: %s / %s" % (", ".join(lex.label_of(t, "en") for t in pa[:2]),
                                             ", ".join(lex.label_of(t, "en") for t in pb[:2]))
    elif code == "terms" and ev["terms"]:
        anchors = _pairs(ev["terms"], fa.term_surface, fb.term_surface, 3)
        fr = "institutions ou sujets communs : " + ", ".join(lex.label_of(t, "fr") for t in ev["terms"][:3])
        en = "shared institutions or topics: " + ", ".join(lex.label_of(t, "en") for t in ev["terms"][:3])
    elif code == "event" and ev["event"]:
        fr = "même type d'événement : " + ", ".join(lex.label_of(t, "fr") for t in ev["event"][:2])
        en = "same event type: " + ", ".join(lex.label_of(t, "en") for t in ev["event"][:2])
    elif code == "event_conflict" and ev["event_conflict"]:
        ea, eb = ev["event_conflict"]
        fr = "types d'événement différents : %s / %s" % (
            ", ".join(lex.label_of(t, "fr") for t in ea[:2]), ", ".join(lex.label_of(t, "fr") for t in eb[:2]))
        en = "different event types: %s / %s" % (
            ", ".join(lex.label_of(t, "en") for t in ea[:2]), ", ".join(lex.label_of(t, "en") for t in eb[:2]))
    elif code in ("tokens", "cross_tokens") and ev["tokens"]:
        anchors = _pairs(ev["tokens"], fa.tok_surface, fb.tok_surface, 4)
        across = code == "cross_tokens"
        fr = "mots communs%s (%s) : %s" % (" entre les langues" if across else "", _num(x[code], "fr"), _alias(anchors))
        en = "shared words%s (%s): %s" % (" across languages" if across else "", _num(x[code], "en"), _alias(anchors))
    elif code == "title" and ev["title_tokens"]:
        # Headline words only: a body word never explains the headline component.
        anchors = _pairs(ev["title_tokens"], fa.title_tok_surface, fb.title_tok_surface, 4)
        fr = "mots communs dans les titres (%s) : %s" % (_num(x[code], "fr"), _alias(anchors))
        en = "shared headline words (%s): %s" % (_num(x[code], "en"), _alias(anchors))
    elif code in ("chars", "cross_chars"):
        across = code == "cross_chars"
        fr = "similarité des caractères%s : %s" % (" entre les langues" if across else "", _num(x["chars"], "fr"))
        en = "character similarity%s: %s" % (" across languages" if across else "", _num(x["chars"], "en"))
    elif code == "cross":
        fr, en = "couverture en français et en anglais", "French and English coverage"
    else:
        return None
    return {"code": code, "sign": "+" if contribution >= 0 else "-", "weight": round(contribution, 3),
            "anchors": anchors, "fr": fr, "en": en}


def reasons_of(x: dict, ev: dict, weights: dict) -> list[dict]:
    """Every non-zero component as a reason, strongest first, then the model's
    starting point, so the listed weights add up to the score's log-odds."""
    parts = [(k, weights.get(k, 0.0) * x[k]) for k in FEATURES if x[k] and weights.get(k, 0.0)]
    parts.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
    out = []
    for code, contribution in parts:
        r = _reason(code, contribution, x, ev)
        if r is None:  # a component with no quotable evidence still counts
            r = {"code": code, "sign": "+" if contribution >= 0 else "-", "weight": round(contribution, 3),
                 "anchors": [], "fr": code, "en": code}
        out.append(r)
    bias = weights.get("bias", 0.0)
    out.append({"code": "base", "sign": "+" if bias >= 0 else "-", "weight": round(bias, 3), "anchors": [],
                "fr": "point de départ du modèle", "en": "model starting point"})
    return out


def score(a: dict, b: dict, ctx: MatchContext) -> tuple[float, list[dict]]:
    """Probability that a and b report the same real-world happening, and why."""
    x, ev = components(a, b, ctx)
    return _sigmoid(logit(x, ctx.weights)), reasons_of(x, ev, ctx.weights)


class Match(NamedTuple):
    score: float
    tier: str | None
    reasons: list
    guard: Guard


def _guard_ok(a: dict, b: dict, ctx: MatchContext) -> bool:
    return fr_en_guard(a, b, ctx).ok if ctx.use_guard else True


def match(a: dict, b: dict, ctx: MatchContext) -> Match:
    s, reasons = score(a, b, ctx)
    g = fr_en_guard(a, b, ctx)
    t = tier(s, g.ok or not ctx.use_guard, ctx.thresholds)
    if ctx.use_guard and g.applies and not g.ok and s >= ctx.thresholds["probable"]:
        reasons = reasons + [{
            "code": "guard", "sign": "-", "weight": 0.0, "anchors": [],
            "fr": "français et anglais sans nom, lieu ni nombre commun : au plus « possible »",
            "en": "French and English with no shared name, place or number: at most possible"}]
    return Match(s, t, reasons, g)


def pair_tier(a: dict, b: dict, ctx: MatchContext) -> tuple[float, str | None]:
    """Score and tier without reasons (the fast path)."""
    s = probability(a, b, ctx)
    return s, tier(s, _guard_ok(a, b, ctx), ctx.thresholds)


# ---------------------------------------------------------------- blocking
def blocking_keys(f: ItemFeatures) -> set[str]:
    keys = {"t:" + t for t in f.tokens}
    keys |= {"n:" + n for n in f.names}
    keys |= {"#:" + n for n in f.numbers if not is_year(n)}
    keys |= {"e:" + t for t in f.terms if lex.kind_of(t) not in ("broad", "role")}
    return keys


def _time_of(item: dict, fallback: datetime | None = None) -> datetime | None:
    return published_when(item) or _clock(item.get("first_seen")) or fallback


def candidate_pairs(items: list[dict], ctx: MatchContext, max_hours: float = FAR_HOURS) -> list[tuple[str, str]]:
    """Pairs worth scoring: they share at least one key (token, name, number,
    lexicon term) that at most max(BLOCK_MIN_CAP, BLOCK_SHARE x n) items carry,
    and were published at most max_hours apart (unknown times are kept).

    Pairs that share nothing rarer than that cannot reach the "possible" floor
    in practice; eval/run_eval.py measures how many gold pairs blocking loses.
    """
    by_id: dict[str, dict] = {}
    for item in items:
        by_id.setdefault(_item_key(item), item)
    ids = sorted(by_id)
    cap = max(BLOCK_MIN_CAP, int(math.ceil(BLOCK_SHARE * len(ids))))
    when = {i: _time_of(by_id[i]) for i in ids}
    postings: dict[str, list[str]] = {}
    for i in ids:
        for k in blocking_keys(ctx.features(by_id[i])):
            postings.setdefault(k, []).append(i)
    out: set[tuple[str, str]] = set()
    limit = max_hours * 3600.0
    for k in sorted(postings):
        plist = postings[k]
        if len(plist) < 2 or len(plist) > cap:
            continue
        for x in range(len(plist)):
            a = plist[x]
            for y in range(x + 1, len(plist)):
                b = plist[y]
                ta, tb = when[a], when[b]
                if ta is not None and tb is not None and abs((ta - tb).total_seconds()) > limit:
                    continue
                out.add((a, b) if a < b else (b, a))
    return sorted(out)


# ---------------------------------------------------------------- clusterer
def event_id_for(anchor_item_id: str) -> str:
    """docs/EVENTS.md section 4: minted once from the founding item, never recomputed."""
    return "ev-" + hashlib.sha256(("event-v1|" + str(anchor_item_id)).encode("utf-8")).hexdigest()[:16]


def _member_id(m) -> str:
    return str(m.get("item_id") if isinstance(m, dict) else m)


def _member_time(m, items_by_id: dict) -> datetime | None:
    if isinstance(m, dict):
        t = _clock(m.get("published_at")) or _clock(m.get("first_seen"))
        if t is not None:
            return t
    item = items_by_id.get(_member_id(m))
    return _time_of(item) if item else None


LINKS = ("average", "complete", "single")
LINK = "average"


def link_ok(compared: list[tuple[float, str | None]], link: str, threshold: float) -> bool:
    """Does a newcomer link to an event, given its (score, tier) against each
    compared member?

    average   mean score >= the merge tier's threshold AND at least one member
              at tier >= probable (so a failed FR/EN guard on every pair can
              never be averaged away)
    complete  every compared member at tier >= probable
    single    any compared member at tier >= probable
    """
    if not compared:
        return False
    matching = sum(1 for _, t in compared if at_least(t, MERGE_TIER))
    if link == "single":
        return matching >= 1
    if link == "complete":
        return matching == len(compared)
    return matching >= 1 and math.fsum(s for s, _ in compared) / len(compared) >= threshold


def attach(events: list[dict], new_items: list[dict], ctx: MatchContext, *, edition: str,
           items_by_id: dict[str, dict] | None = None, link: str = LINK,
           compatible: Callable[[dict, dict], bool] | None = None,
           merge_tier: str | None = None, merge_average: bool | None = None) -> tuple[list[dict], list[dict]]:
    """Attach the new items of one edition to events; mint and merge.

    events     existing events, each {"event_id", "born_edition", "members":
               [item_id or {"item_id", "published_at", "first_seen"}], "lineage":
               {"merged_into", "absorbed"}}; never mutated.
    new_items  the edition's items that belong to no event yet (an item that is
               already a member anywhere is ignored: sticky membership).
    ctx        MatchContext over the window's items.
    edition    the collection clock (ISO) of this edition; never a wall clock.
    items_by_id  texts of earlier members still available; a member whose text
               is gone keeps its membership but is no longer compared.
    compatible optional extra merge condition (docs/EVENTS.md section 4: same
               type and first place, decided by the event builder).

    Order: new items by (published_at or edition clock, item_id). An item
    joins the event whose link is strongest (average score over its compared
    members, at least one member at tier >= probable, guard included); ties
    by more matching members, then oldest born_edition, then smallest
    event_id. No link: it founds a new event. Then in-window events merge
    when at least two cross pairs reach tier >= probable AND their average
    cross score reaches the probable threshold; the older event keeps its
    id, the younger records lineage.merged_into. Existing members never move.

    Returns (events sorted by (born_edition, event_id), decisions): ids,
    tiers and numbers only, safe to store.
    """
    if link not in LINKS:
        raise ValueError("unknown link %r" % (link,))
    merge_tier = MERGE_PAIR_TIER if merge_tier is None else merge_tier
    merge_average = MERGE_AVERAGE if merge_average is None else merge_average
    texts: dict[str, dict] = dict(items_by_id or {})
    clock = _clock(edition)
    window = timedelta(hours=WINDOW_HOURS)
    out: dict[str, dict] = {}
    owner: dict[str, str] = {}          # item id -> the event it joined (never changes)
    when: dict[str, datetime | None] = {}
    for e in sorted((e for e in events if isinstance(e, dict) and e.get("event_id")),
                    key=lambda e: (str(e.get("born_edition") or ""), str(e["event_id"]))):
        copy = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in e.items()}
        copy["members"] = [dict(m) if isinstance(m, dict) else m for m in e.get("members") or []]
        lineage = dict(e.get("lineage") or {})
        lineage["merged_into"] = str(lineage.get("merged_into") or "")
        lineage["absorbed"] = sorted(str(x) for x in lineage.get("absorbed") or [])
        copy["lineage"] = lineage
        out[copy["event_id"]] = copy
        for m in copy["members"]:
            m_id = _member_id(m)
            owner.setdefault(m_id, copy["event_id"])
            when.setdefault(m_id, _member_time(m, texts))

    def born(eid: str) -> tuple[str, str]:
        return (str(out[eid].get("born_edition") or ""), eid)

    def root(eid: str) -> str:
        """The surviving event an event was merged into (itself if none)."""
        seen = set()
        while out[eid]["lineage"]["merged_into"] in out and eid not in seen:
            seen.add(eid)
            eid = out[eid]["lineage"]["merged_into"]
        return eid

    # Effective membership of each surviving event (its own members plus those
    # of every event merged into it) and its publication span.
    eff: dict[str, list[str]] = {}
    for eid in out:
        if root(eid) == eid:
            eff[eid] = []
    for eid in out:
        eff[root(eid)].extend(_member_id(m) for m in out[eid]["members"])
    span: dict[str, list] = {}

    def widen(eid: str, t: datetime | None) -> None:
        if t is None:
            return
        s = span.setdefault(eid, [t, t])
        s[0], s[1] = min(s[0], t), max(s[1], t)

    for eid, ids in eff.items():
        for m_id in ids:
            widen(eid, when.get(m_id))

    fresh: list[dict] = []
    for item in new_items:
        if not isinstance(item, dict):
            continue
        key = _item_key(item)
        if key in owner or key in when:
            continue  # sticky: already a member somewhere, or a duplicate newcomer
        texts[key] = item
        when[key] = _time_of(item, clock)
        fresh.append(item)
    fresh.sort(key=lambda it: (when[_item_key(it)] or datetime.min.replace(tzinfo=timezone.utc), _item_key(it)))

    # Blocking over every text we can compare: earlier members and newcomers.
    pool = [texts[k] for k in sorted(texts)]
    cand = candidate_pairs(pool, ctx)
    neighbours: dict[str, set[str]] = {}
    for a, b in cand:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    cache: dict[tuple[str, str], tuple[float, str | None]] = {}

    def pair(a_id: str, b_id: str) -> tuple[float, str | None]:
        k = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        hit = cache.get(k)
        if hit is None:
            hit = pair_tier(texts[k[0]], texts[k[1]], ctx)
            cache[k] = hit
        return hit

    def open_for(eid: str, t: datetime | None) -> bool:
        s = span.get(eid)
        if t is None or s is None:
            return True
        if t < s[0] - window or t > s[1] + window:
            return False
        return clock is None or clock - s[1] <= timedelta(days=EDITION_WINDOW_DAYS)

    probable = ctx.thresholds[MERGE_TIER]
    far = FAR_HOURS * 3600.0
    decisions: list[dict] = []
    touched: set[str] = set()
    for item in fresh:
        x_id = _item_key(item)
        tx = when[x_id]
        best = None
        for eid in sorted({root(owner[n]) for n in neighbours.get(x_id, ()) if n in owner}):
            if not open_for(eid, tx):
                continue
            compared = []
            for m_id in eff[eid]:
                if m_id not in texts:
                    continue  # text gone: membership kept, no longer compared
                tm = when.get(m_id)
                if tx is not None and tm is not None and abs((tx - tm).total_seconds()) > far:
                    continue
                compared.append(pair(x_id, m_id))
            if not link_ok(compared, link, probable):
                continue
            matching = sum(1 for _, t in compared if at_least(t, MERGE_TIER))
            mean = math.fsum(s for s, _ in compared) / len(compared)
            rank = (-mean, -matching) + born(eid)
            if best is None or rank < best[0]:
                best = (rank, eid, mean, matching, len(compared))
        if best is None:
            eid = event_id_for(x_id)
            n = 1
            while eid in out:  # a re-minted anchor (detached item): stay unique, stay deterministic
                eid = event_id_for("%s|%d" % (x_id, n))
                n += 1
            out[eid] = {"event_id": eid, "born_edition": edition, "last_edition": edition, "members": [],
                        "lineage": {"merged_into": "", "absorbed": []}}
            eff[eid] = []
            decisions.append({"item_id": x_id, "action": "minted", "event_id": eid})
        else:
            _, eid, mean, matching, n_compared = best
            decisions.append({"item_id": x_id, "action": "attached", "event_id": eid,
                              "link": round(mean, 4), "matching": matching, "compared": n_compared})
        out[eid]["members"].append({"item_id": x_id,
                                    "published_at": item.get("published_at") or None,
                                    "first_seen": item.get("first_seen") or edition})
        out[eid]["last_edition"] = edition
        owner[x_id] = eid
        eff[eid].append(x_id)
        widen(eid, tx)
        touched.add(eid)

    # Merges. Strong pairs: candidate pairs between two members at tier >=
    # probable. Two surviving events merge when at least MERGE_MIN_CROSS_PAIRS
    # strong pairs join them, at least one was touched this edition, both are
    # open to each other's span, and `compatible` agrees. Best first: most
    # strong pairs, then highest summed score, then oldest ids.
    strong = []
    for a, b in cand:
        if a in owner and b in owner and owner[a] != owner[b]:
            s, t = pair(a, b)
            if at_least(t, merge_tier):
                strong.append((a, b, s))

    def cross_mean(r1: str, r2: str) -> float:
        """Average score over every comparable cross pair (unblocked = 0)."""
        scores = []
        for i in eff[r1]:
            if i not in texts:
                continue
            ti = when.get(i)
            for j in eff[r2]:
                if j not in texts:
                    continue
                tj = when.get(j)
                if ti is not None and tj is not None and abs((ti - tj).total_seconds()) > far:
                    continue
                scores.append(pair(i, j)[0] if j in neighbours.get(i, ()) else 0.0)
        return math.fsum(scores) / len(scores) if scores else 0.0

    while strong and touched:
        counts: dict[tuple[str, str], list] = {}
        for a, b, s in strong:
            ra, rb = root(owner[a]), root(owner[b])
            if ra == rb or (ra not in touched and rb not in touched):
                continue
            key = (ra, rb) if ra < rb else (rb, ra)
            counts.setdefault(key, []).append(s)
        best_merge = None
        for (r1, r2), hits in counts.items():
            if len(hits) < MERGE_MIN_CROSS_PAIRS:
                continue
            if merge_average and cross_mean(r1, r2) < probable:
                continue
            older, younger = sorted((r1, r2), key=born)
            s_old, s_young = span.get(older), span.get(younger)
            if s_old and s_young and (s_young[0] > s_old[1] + window or s_old[0] > s_young[1] + window):
                continue
            if clock is not None and any(s and clock - s[1] > timedelta(days=EDITION_WINDOW_DAYS)
                                         for s in (s_old, s_young)):
                continue
            if compatible is not None and not compatible(out[older], out[younger]):
                continue
            rank = (-len(hits), -math.fsum(hits)) + born(older) + born(younger)
            if best_merge is None or rank < best_merge[0]:
                best_merge = (rank, older, younger, len(hits))
        if best_merge is None:
            break
        _, older, younger, n_hits = best_merge
        out[younger]["lineage"]["merged_into"] = older
        out[older]["lineage"]["absorbed"] = sorted(set(out[older]["lineage"]["absorbed"]) | {younger})
        out[older]["last_edition"] = edition
        eff[older].extend(eff.pop(younger))
        for t in span.pop(younger, []):
            widen(older, t)
        touched.add(older)
        touched.discard(younger)
        decisions.append({"action": "merged", "event_id": younger, "into": older, "cross_pairs": n_hits})

    result = sorted(out.values(), key=lambda e: (str(e.get("born_edition") or ""), e["event_id"]))
    return result, decisions


def _survivors(events: list[dict]) -> dict[str, str]:
    """event_id -> the surviving event it was merged into (itself if none)."""
    by_id = {e["event_id"]: e for e in events}
    out = {}
    for eid in by_id:
        cur, seen = eid, set()
        while (by_id[cur].get("lineage") or {}).get("merged_into") in by_id and cur not in seen:
            seen.add(cur)
            cur = by_id[cur]["lineage"]["merged_into"]
        out[eid] = cur
    return out


def groups(events: list[dict]) -> list[list[str]]:
    """Item ids grouped by surviving event (merged events folded into their
    survivor), each group sorted, groups sorted. Singletons included."""
    survivor = _survivors(events)
    out: dict[str, list[str]] = {}
    for e in events:
        for m in e.get("members") or []:
            out.setdefault(survivor[e["event_id"]], []).append(_member_id(m))
    return sorted(sorted(g) for g in out.values())


def cluster(items: list[dict], ctx: MatchContext, *, edition: str = "", link: str = LINK,
            merge_tier: str | None = None, merge_average: bool | None = None) -> list[list[str]]:
    """Cluster a batch from scratch (one edition): what the evaluation scores."""
    events, _ = attach([], items, ctx, edition=edition, link=link, merge_tier=merge_tier,
                       merge_average=merge_average)
    return groups(events)


def neighbours(events: list[dict], items_by_id: dict[str, dict], ctx: MatchContext) -> dict[str, list[list]]:
    """Surviving events linked by at least one pair at tier "possible" or above
    but not grouped: shown as neighbours, never merged.

    Returns {event_id: [[other_event_id, best_score], ...]}, best first, then by id."""
    survivor = _survivors(events)
    owner = {}
    for e in events:
        for m in e.get("members") or []:
            owner[_member_id(m)] = survivor[e["event_id"]]
    pool = [items_by_id[k] for k in sorted(items_by_id) if k in owner]
    best: dict[tuple[str, str], float] = {}
    for a, b in candidate_pairs(pool, ctx):
        ea, eb = owner[a], owner[b]
        if ea == eb:
            continue
        s, t = pair_tier(items_by_id[a], items_by_id[b], ctx)
        if t is None:
            continue
        k = (ea, eb) if ea < eb else (eb, ea)
        best[k] = max(best.get(k, 0.0), s)
    out: dict[str, list[list]] = {}
    for (ea, eb), s in sorted(best.items()):
        out.setdefault(ea, []).append([eb, round(s, 4)])
        out.setdefault(eb, []).append([ea, round(s, 4)])
    for k in out:
        out[k].sort(key=lambda r: (-r[1], r[0]))
    return dict(sorted(out.items()))
