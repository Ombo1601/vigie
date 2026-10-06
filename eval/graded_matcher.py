"""Graded same-event matcher (Phase 0 prototype, stdlib only, deterministic).

Scores a pair of collected items from 0 to 1: how strongly the evidence says
both report the same real-world happening. Every component is a measured,
explainable fact about the two items, never a rewrite of either:

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
  title     the same, headline only (overlap coefficient)
  chars     character 4-gram TF-IDF cosine (catches names and spelling variants)
  cross     the pair crosses languages (fr-en), plus cross x tokens / chars

The components are combined by a logistic model: hand-set prior weights,
optionally refit on the dev split with an L2 pull back toward the prior. The
reasons for a score are the components with the largest contributions,
rendered with the evidence that produced them, because the product must be
able to say why two articles were grouped.

The IDF tables are built from an unlabelled corpus (every collected item),
never from the gold labels.
"""
from __future__ import annotations

import math
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import lexicon_fr_en as lex  # noqa: E402

METHOD = "graded-matcher-v0 logistic(time,numbers,dates,names,lexicon,tokens,chargrams)"

FEATURES = (
    "time", "far", "numbers", "dates", "names", "place", "conflict", "terms",
    "event", "event_conflict", "tokens", "title", "chars", "cross", "cross_tokens", "cross_chars",
)

# Hand-set priors (log-odds per unit of evidence): what a careful reader would
# weigh, adjusted once after reading DEV errors only (cross 0.5 -> 0.0, the
# event_conflict feature added). Test pairs were never inspected.
PRIOR_WEIGHTS = {
    "bias": -4.0,
    "time": 1.5,
    "far": -2.0,
    "numbers": 1.0,
    "dates": 0.5,
    "names": 2.0,
    "place": 1.0,
    "conflict": -1.0,
    "terms": 1.0,
    "event": 0.5,
    "event_conflict": -0.5,
    "tokens": 4.0,
    "title": 2.0,
    "chars": 3.0,
    "cross": 0.0,
    "cross_tokens": 2.0,
    "cross_chars": -1.0,
}

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

# Applied after the plural is dropped, so singular and plural share a stem.
_SUFFIXES = tuple(sorted((
    "issement", "ement", "ation", "ateur", "atrice", "ance", "ence", "ite", "eur",
    "euse", "ive", "if", "ment", "ing", "er", "ed", "e",
), key=lambda s: (-len(s), s)))

TIME_SCALE_HOURS = 12.0
FAR_HOURS = 7 * 24.0
CHAR_N = 4
SUMMARY_CHARS = 400


def stem(word: str) -> str:
    """Light French/English suffix folding (no dictionary). Stems keep >= 4 letters."""
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


def language_of(item: dict) -> str:
    lang = str(item.get("language") or "").strip().lower()
    return "en" if lang.startswith("en") else "fr" if lang.startswith("fr") else ""


_WORD = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+(?:[-'][0-9A-Za-zÀ-ÖØ-öø-ÿ]+)*")
_THOUSANDS = "[ ,\u00a0\u202f]"  # "60 000" (fr) and "60,000" (en) are one number
_NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:" + _THOUSANDS + r"\d{3})+(?![\d])|\d+(?:[.,]\d+)?)(?![\w])")


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


def numbers_of(text: str) -> set[str]:
    out = set()
    for m in _NUM.finditer(text):
        raw = m.group(1)
        if re.fullmatch(r"\d{1,3}(?:" + _THOUSANDS + r"\d{3})+", raw):
            raw = re.sub(_THOUSANDS, "", raw)
        raw = raw.replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        out.add(str(int(value)) if value.is_integer() else ("%.3f" % value).rstrip("0"))
    for w in lex.fold(text).split():
        if w in NUMBER_WORDS:
            out.add(NUMBER_WORDS[w])
    return out


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


def char_grams(folded: str) -> Counter:
    text = " " + re.sub(r"\s+", " ", folded) + " "
    return Counter(text[i:i + CHAR_N] for i in range(max(0, len(text) - CHAR_N + 1)))


class ItemFeatures:
    __slots__ = ("id", "lang", "when", "tokens", "title_tokens", "names", "name_surface",
                 "terms", "title_terms", "numbers", "dates", "grams", "vector")

    def __init__(self, item: dict):
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or "")[:SUMMARY_CHARS]
        self.id = str(item.get("id") or "")
        self.lang = language_of(item)
        self.when = published_when(item)
        f_title, f_summary = lex.fold(title), lex.fold(summary)
        words_t = [w for w in f_title.split() if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()]
        words_s = [w for w in f_summary.split() if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()]
        self.title_tokens = {canon_token(w) for w in words_t}
        self.tokens = self.title_tokens | {canon_token(w) for w in words_s}
        self.names, self.name_surface = names_of(title, summary)
        self.title_terms = lex.term_ids(f_title, folded=True)
        self.terms = self.title_terms | lex.term_ids(f_summary, folded=True)
        self.numbers = numbers_of(title + " " + summary)
        self.dates = dates_of(f_title + " " + f_summary)
        # Headline counted twice: it is the item's own statement of the event.
        self.grams = char_grams(f_title + " " + f_title + " " + f_summary)
        self.vector: dict[str, float] = {}


class Idf:
    def __init__(self):
        self.n = 0
        self.df: dict[str, Counter] = {k: Counter() for k in ("tokens", "names", "terms", "numbers", "grams")}

    def add(self, f: ItemFeatures) -> None:
        self.n += 1
        self.df["tokens"].update(f.tokens)
        self.df["names"].update(f.names)
        self.df["terms"].update(f.terms)
        self.df["numbers"].update(f.numbers)
        self.df["grams"].update(f.grams.keys())

    def idf(self, table: str, key: str) -> float:
        return math.log((self.n + 1) / (self.df[table].get(key, 0) + 1)) + 1.0


def _wsum(keys, w) -> float:
    """Sum weights in sorted key order: set order follows the per-process
    string hash seed, and float addition is not associative."""
    return sum(w(k) for k in sorted(keys))


def _weighted_overlap(a: set[str], b: set[str], w) -> float:
    """Overlap coefficient with weights: shared weight / smaller side's weight."""
    if not a or not b:
        return 0.0
    return _wsum(a & b, w) / max(1e-9, min(_wsum(a, w), _wsum(b, w)))


def _weighted_dice(a: set[str], b: set[str], w) -> float:
    """Dice with weights: one shared name between two long lists scores low."""
    if not a or not b:
        return 0.0
    return 2.0 * _wsum(a & b, w) / max(1e-9, _wsum(a, w) + _wsum(b, w))


def _weighted_jaccard(a: set[str], b: set[str], w) -> float:
    if not a or not b:
        return 0.0
    return _wsum(a & b, w) / max(1e-9, _wsum(a | b, w))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class GradedMatcher:
    """Pluggable pair scorer for eval/run_eval.py (also usable on its own)."""

    binary = False

    def __init__(self, weights: dict | None = None, fit: bool = True, name: str | None = None,
                 l2: float = 0.002, epochs: int = 4000, rate: float = 0.5):
        # l2 chosen by 2-fold cross-validation INSIDE the dev split
        # (0.2 / 0.05 / 0.01 / 0.002); the test split played no part.
        self.weights = dict(PRIOR_WEIGHTS if weights is None else weights)
        self.prior = dict(self.weights)
        self.do_fit = fit
        self.name = name or ("graded" if fit else "graded-prior")
        self.l2, self.epochs, self.rate = l2, epochs, rate
        self.idf = Idf()
        self._cache: dict[str, ItemFeatures] = {}
        self.fitted = False

    # -- corpus ------------------------------------------------------------
    def prepare(self, corpus: list[dict]) -> None:
        """Build IDF tables from unlabelled items (sorted by id: deterministic)."""
        self.idf = Idf()
        self._cache = {}
        seen = set()
        for item in sorted((c for c in corpus if isinstance(c, dict)), key=lambda c: str(c.get("id") or "")):
            key = str(item.get("id") or "")
            if key in seen:
                continue
            seen.add(key)
            self.idf.add(self._features(item, vector=False))
        for f in self._cache.values():
            self._vectorise(f)

    def _features(self, item: dict, vector: bool = True) -> ItemFeatures:
        key = str(item.get("id") or "") or repr(sorted(item.items()))
        f = self._cache.get(key)
        if f is None:
            f = ItemFeatures(item)
            self._cache[key] = f
        if vector and not f.vector:
            self._vectorise(f)
        return f

    def _vectorise(self, f: ItemFeatures) -> None:
        vec = {g: (1.0 + math.log(c)) * self.idf.idf("grams", g) for g, c in f.grams.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        f.vector = {g: v / norm for g, v in vec.items()}

    # -- components ------------------------------------------------------------
    def components(self, a: dict, b: dict) -> tuple[dict, dict]:
        fa, fb = self._features(a), self._features(b)
        idf = self.idf.idf
        x: dict[str, float] = {}
        ev: dict[str, object] = {}
        if fa.when is None or fb.when is None:
            hours = None
            x["time"], x["far"] = 0.5, 0.0
        else:
            hours = abs((fa.when - fb.when).total_seconds()) / 3600.0
            x["time"] = math.exp(-hours / TIME_SCALE_HOURS)
            x["far"] = 1.0 if hours > FAR_HOURS else 0.0
        ev["hours"] = hours
        shared_nums = fa.numbers & fb.numbers
        x["numbers"] = _weighted_dice(fa.numbers, fb.numbers, lambda k: idf("numbers", k))
        ev["numbers"] = sorted(shared_nums, key=lambda k: (-idf("numbers", k), k))
        shared_dates = fa.dates & fb.dates
        x["dates"] = 1.0 if any(d.startswith("d:") for d in shared_dates) else 0.5 if shared_dates else 0.0
        ev["dates"] = sorted(shared_dates)
        x["names"] = _weighted_dice(fa.names, fb.names, lambda k: idf("names", k))
        ev["names"] = sorted(fa.names & fb.names, key=lambda k: (-idf("names", k), k))
        ev["name_surface"] = {k: fa.name_surface.get(k) or fb.name_surface.get(k) or k for k in ev["names"]}
        spec_a = {t for t in fa.terms if lex.kind_of(t) in lex.SPECIFIC_PLACE_KINDS}
        spec_b = {t for t in fb.terms if lex.kind_of(t) in lex.SPECIFIC_PLACE_KINDS}
        x["place"] = 1.0 if spec_a & spec_b else 0.0
        ev["place"] = sorted(spec_a & spec_b)
        # Conflicts read headlines only: summaries name side streets and
        # neighbouring places that the headline event does not depend on.
        head_a = {t for t in fa.title_terms if lex.kind_of(t) in lex.SPECIFIC_PLACE_KINDS}
        head_b = {t for t in fb.title_terms if lex.kind_of(t) in lex.SPECIFIC_PLACE_KINDS}
        x["conflict"] = 1.0 if head_a and head_b and not (spec_a & spec_b) else 0.0
        ev["conflict"] = (sorted(head_a), sorted(head_b)) if x["conflict"] else None
        evt_a = {t for t in fa.title_terms if lex.kind_of(t) == "event"}
        evt_b = {t for t in fb.title_terms if lex.kind_of(t) == "event"}
        x["event_conflict"] = 1.0 if evt_a and evt_b and not (fa.terms & fb.terms & (evt_a | evt_b)) else 0.0
        ev["event_conflict"] = (sorted(evt_a), sorted(evt_b)) if x["event_conflict"] else None
        def term_w(t: str) -> float:
            return lex.KIND_WEIGHT.get(lex.kind_of(t), 0.3) * idf("terms", t)
        x["terms"] = _weighted_dice(fa.terms, fb.terms, term_w)
        ev["terms"] = sorted(fa.terms & fb.terms, key=lambda t: (-term_w(t), t))
        events = {t for t in fa.terms & fb.terms if lex.kind_of(t) == "event"}
        x["event"] = 1.0 if events else 0.0
        ev["event"] = sorted(events)
        tok_w = lambda k: idf("tokens", k)  # noqa: E731
        x["tokens"] = _weighted_jaccard(fa.tokens, fb.tokens, tok_w)
        x["title"] = _weighted_overlap(fa.title_tokens, fb.title_tokens, tok_w)
        ev["tokens"] = sorted(fa.tokens & fb.tokens, key=lambda k: (-tok_w(k), k))
        small, large = (fa.vector, fb.vector) if len(fa.vector) <= len(fb.vector) else (fb.vector, fa.vector)
        x["chars"] = sum(v * large.get(g, 0.0) for g, v in small.items())
        cross = 1.0 if fa.lang and fb.lang and fa.lang != fb.lang else 0.0
        x["cross"] = cross
        x["cross_tokens"] = cross * x["tokens"]
        x["cross_chars"] = cross * x["chars"]
        return x, ev

    def logit(self, x: dict) -> float:
        return self.weights.get("bias", 0.0) + sum(self.weights.get(k, 0.0) * x[k] for k in FEATURES)

    def score(self, a: dict, b: dict) -> float:
        x, _ = self.components(a, b)
        return _sigmoid(self.logit(x))

    # -- fitting -------------------------------------------------------------------
    def fit(self, triples: list[tuple[dict, dict, int]]) -> None:
        """Logistic regression on dev pairs, L2-pulled toward the prior weights.

        Plain batch gradient descent with a fixed schedule: deterministic.
        """
        if not self.do_fit or not triples:
            return
        rows = [(self.components(a, b)[0], int(y)) for a, b, y in triples]
        keys = ("bias",) + FEATURES
        w = {k: self.prior.get(k, 0.0) for k in keys}
        n = float(len(rows))
        for _ in range(self.epochs):
            grad = dict.fromkeys(keys, 0.0)
            for x, y in rows:
                z = w["bias"] + sum(w[k] * x[k] for k in FEATURES)
                err = _sigmoid(z) - y
                grad["bias"] += err
                for k in FEATURES:
                    grad[k] += err * x[k]
            for k in keys:
                penalty = 0.0 if k == "bias" else self.l2 * (w[k] - self.prior.get(k, 0.0))
                w[k] -= self.rate * (grad[k] / n + penalty)
        self.weights = {k: round(v, 4) for k, v in w.items()}
        self.fitted = True

    # -- explanations ----------------------------------------------------------------
    def contributions(self, a: dict, b: dict) -> list[tuple[str, float]]:
        x, _ = self.components(a, b)
        out = [(k, self.weights.get(k, 0.0) * x[k]) for k in FEATURES if x[k]]
        out.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
        return out

    def explain(self, a: dict, b: dict, top: int = 4) -> list[str]:
        """Human-readable reasons, strongest first (positive and negative)."""
        x, ev = self.components(a, b)
        parts = [(k, self.weights.get(k, 0.0) * x[k]) for k in FEATURES if x[k]]
        parts.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
        reasons = []
        for k, c in parts:
            text = _reason(k, x, ev)
            if text:
                reasons.append(("+" if c >= 0 else "-") + " " + text)
            if len(reasons) >= top:
                break
        return reasons


def _labels(ids: list[str], n: int = 3) -> str:
    return ", ".join(lex.label_of(t) for t in ids[:n])


def _reason(k: str, x: dict, ev: dict) -> str:
    hours = ev.get("hours")
    if k == "time":
        return "published %s apart" % ("at unknown times" if hours is None else "%.0f h" % hours)
    if k == "far":
        return "published %.0f days apart" % (hours / 24.0)
    if k == "numbers" and ev["numbers"]:
        return "same numbers: " + ", ".join(ev["numbers"][:3])
    if k == "dates" and ev["dates"]:
        return "same date named: " + ", ".join(ev["dates"][:2])
    if k == "names" and ev["names"]:
        return "same names: " + ", ".join(ev["name_surface"][n] for n in ev["names"][:3])
    if k == "place" and ev["place"]:
        return "same place: " + _labels(ev["place"])
    if k == "conflict" and ev["conflict"]:
        a, b = ev["conflict"]
        return "different places: %s vs %s" % (_labels(a, 2), _labels(b, 2))
    if k == "terms" and ev["terms"]:
        return "same institutions or topics: " + _labels(ev["terms"])
    if k == "event_conflict" and ev["event_conflict"]:
        a, b = ev["event_conflict"]
        return "different event types: %s vs %s" % (_labels(a, 2), _labels(b, 2))
    if k == "event" and ev["event"]:
        return "same event type: " + _labels(ev["event"])
    if k in ("tokens", "title", "cross_tokens") and ev["tokens"]:
        return "words in common%s (%.2f): %s" % (
            " across languages" if k == "cross_tokens" else " in the headline" if k == "title" else "",
            x[k], ", ".join(ev["tokens"][:4]))
    if k in ("chars", "cross_chars"):
        return "character similarity %.2f%s" % (x["chars"], " across languages" if k == "cross_chars" else "")
    if k == "cross":
        return "French and English coverage"
    return ""
