"""Vigie controlled vocabulary: event types, places, families and rule lexicons.

Source of truth: docs/I18N.md section 5 (tables A and B), stored as data in
`vocabulaire.json`; the keyword rules that assign a type and places to an
event live in `vocabulaire_lexique.json`. Codes are ASCII and stable forever
(they are the only thing ever sealed); labels are Vigie's own words, never
publisher text, and may be reworded without touching a code.

Classification is rule-based, folded and word-bounded. It is a *proposal*
(status is always "proposed"), never a verdict: zero hits means
"unclassified", and an honest "unclassified" beats a wrong type. Every score
can be explained from the lexicon (see `explain_type`), so no grouping or
label is a hidden hand.

Stdlib only, no network, no wall clock, deterministic: iteration follows the
table order of the JSON files, never a set or a hash.
"""
from __future__ import annotations

import functools
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
VOCAB_PATH = HERE / "vocabulaire.json"
LEXIQUE_PATH = HERE / "vocabulaire_lexique.json"

STATUS = "proposed"
UNCLASSIFIED = "unclassified"
# The place of an event no evidence locates (no place named, no geo token):
# "Lieu non établi", the place twin of "unclassified". It has no keyword (no
# text can name an absence) and a label names no place when it is the basis.
# Before 2026-10-06 the fallback was "quebec-city": half the live events
# carried the city with no evidence (docs/EVENTS.md, deviations).
FALLBACK_PLACE = "unplaced"
# A scope with positive evidence that the event is outside Quebec.
ELSEWHERE = "elsewhere"
LABEL_SEP = " · "
LANGS = ("fr", "en")

# Weight of one weak keyword hit; strong keywords use the type's
# `strong_weight` (default 3; 2 for institution-context types, 4 for legal-stage types so that "trial" outranks
# the offence it is about).
WEAK_WEIGHT = 1
DEFAULT_STRONG_WEIGHT = 3
# At most this many distinct weak keywords count within one title.
WEAK_CAP_PER_TITLE = 2
# A type is proposed only when its score reaches this floor: one strong
# keyword, or two weak cues (in one headline or across member titles). A single weak word in a
# single headline is not evidence; an honest "unclassified" beats a wrong type.
MIN_SCORE = 2


def _warn(message: str) -> None:
    print(f"vocabulaire: {message}", file=sys.stderr)


# --------------------------------------------------------------------------
# Folding: lowercase, no accents, every non-alphanumeric run becomes one
# space. Same idea as cluster_issues.folded, plus ligatures and punctuation,
# so "L'Île-d'Orléans" and "ile d orleans" are one form.
# --------------------------------------------------------------------------

def fold(text: object) -> str:
    raw = str(text or "").lower().replace("œ", "oe").replace("æ", "ae")
    raw = "".join(c for c in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", raw).split())


# `~` in a keyword: up to this many words of any kind ("demande ~ a ottawa"
# reads "demande à Ottawa" and "demande des comptes à Ottawa").
GAP_WORDS = 4


def _keyword_regex(keyword: str) -> str | None:
    """One lexicon keyword -> regex source over folded text.

    A trailing `*` means word-prefix (no right boundary); `#` stands for a
    number of one to three digits; `~` between two words stands for up to
    GAP_WORDS words. Everything else is word-bounded on both sides, with any
    run of spaces between words.
    """
    prefix = keyword.rstrip().endswith("*")
    body = keyword.rstrip().rstrip("*")
    pattern = ""
    gap = False
    for token in body.replace("#", " # ").split():
        if token == "~":
            gap = bool(pattern)
            continue
        if token == "#":
            word = r"\d{1,3}"
        else:
            folded = fold(token)
            if not folded:
                continue
            word = r"\s+".join(re.escape(w) for w in folded.split())
        if pattern:
            pattern += r"\s+" + (r"(?:[a-z0-9]+\s+){0,%d}?" % GAP_WORDS if gap else "")
        pattern += word
        gap = False
    if not pattern:
        return None
    tail = "" if prefix else r"(?![a-z0-9])"
    return r"(?<![a-z0-9])" + pattern + tail


def _alternation(keywords: list[str]) -> re.Pattern | None:
    parts = [p for p in (_keyword_regex(k) for k in keywords) if p]
    if not parts:
        return None
    return re.compile("|".join(parts))


def _longest_first(phrases: list[str]) -> re.Pattern | None:
    folded = {fold(p.replace("*", "")): p for p in phrases if fold(p.replace("*", ""))}
    return _alternation([folded[k] for k in sorted(folded, key=lambda k: (-len(k), k))])


class _KeepMask:
    """A mask with phrases it must leave: a span a `keep` phrase matches is
    never masked, so "demande à Ottawa" (the federal government addressed)
    survives a mask that removes the city's "à Ottawa"."""

    def __init__(self, mask: re.Pattern, keep: re.Pattern):
        self.mask, self.keep = mask, keep

    def sub(self, repl: str, text: str) -> str:
        out, pos = [], 0
        for m in self.keep.finditer(text):
            out.append(self.mask.sub(repl, text[pos:m.start()]))
            out.append(m.group(0))
            pos = m.end()
        out.append(self.mask.sub(repl, text[pos:]))
        return "".join(out)


def _mask(phrases: list[str], keep: list[str] | None = None) -> "re.Pattern | _KeepMask | None":
    """A removal pattern: longest phrase first, so "le canadien de montreal"
    is removed whole rather than "le canadien" leaving "de montreal" behind
    (ties by the phrase itself: no list-order dependence). `keep` phrases
    are left in place wherever they match (`_KeepMask`)."""
    mask = _longest_first(phrases)
    kept = _longest_first(keep or [])
    return _KeepMask(mask, kept) if mask is not None and kept is not None else mask


# --------------------------------------------------------------------------
# Loaders (cached, fail-soft: a missing or corrupt file yields empty facts)
# --------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _warn(f"{path.name} unreadable ({exc.__class__.__name__}); vocabulary empty")
        return {}
    return doc if isinstance(doc, dict) else {}


@functools.lru_cache(maxsize=1)
def vocabulary() -> dict:
    """The parsed vocabulary with lookups; empty tables on a corrupt file."""
    doc = _read_json(VOCAB_PATH)
    families = [f for f in doc.get("families", []) if isinstance(f, dict) and f.get("code")]
    types = [t for t in doc.get("types", []) if isinstance(t, dict) and t.get("code")]
    places = [p for p in doc.get("places", []) if isinstance(p, dict) and p.get("code")]
    aliases = doc.get("place_aliases") if isinstance(doc.get("place_aliases"), dict) else {}
    specificity = doc.get("place_specificity")
    if not isinstance(specificity, list) or not specificity:
        specificity = ["quartier", "arrondissement", "site", "corridor", "neighbour", "scope"]
    return {
        "families": families,
        "types": types,
        "places": places,
        "family_by_code": {f["code"]: f for f in families},
        "type_by_code": {t["code"]: t for t in types},
        "place_by_code": {p["code"]: p for p in places},
        "place_order": {p["code"]: i for i, p in enumerate(places)},
        "type_order": {t["code"]: i for i, t in enumerate(types)},
        "aliases": dict(aliases),
        "specificity": {kind: i for i, kind in enumerate(specificity)},
    }


def type_codes() -> list[str]:
    return [t["code"] for t in vocabulary()["types"]]


def place_codes() -> list[str]:
    return [p["code"] for p in vocabulary()["places"]]


def family_codes() -> list[str]:
    return [f["code"] for f in vocabulary()["families"]]


def _lang(lang: object) -> str:
    return "en" if str(lang or "").strip().lower().startswith("en") else "fr"


def type_label(type_code: str, lang: str = "fr") -> str:
    entry = vocabulary()["type_by_code"].get(type_code) or vocabulary()["type_by_code"].get(UNCLASSIFIED)
    return str(entry.get(_lang(lang)) or "") if entry else str(type_code or "")


def place_label(place_code: str, lang: str = "fr") -> str:
    entry = vocabulary()["place_by_code"].get(place_code)
    return str(entry.get(_lang(lang)) or "") if entry else ""


def family_label(family_code: str, lang: str = "fr") -> str:
    entry = vocabulary()["family_by_code"].get(family_code)
    return str(entry.get(_lang(lang)) or "") if entry else ""


def label(type_code: str, place_code: str | None = None, lang: str = "fr") -> str:
    """`TYPE . PLACE` (docs/EVENTS.md section 5), built only from the tables.

    An unknown type reads as the unclassified label; an unknown or missing
    place yields the type label alone (never a made-up place).
    """
    base = type_label(type_code, lang)
    where = place_label(place_code, lang) if place_code else ""
    return f"{base}{LABEL_SEP}{where}" if where else base


def family_of(type_code: str) -> str | None:
    entry = vocabulary()["type_by_code"].get(type_code)
    return str(entry["family"]) if entry and entry.get("family") else None


def place_kind(place_code: str) -> str | None:
    entry = vocabulary()["place_by_code"].get(place_code)
    return str(entry["kind"]) if entry and entry.get("kind") else None


def is_scope(place_code: str) -> bool:
    return place_kind(place_code) == "scope"


def specificity_key(place_code: str) -> tuple[int, int]:
    """Sort key: more specific places first; ties by table order."""
    vocab = vocabulary()
    kind = place_kind(place_code)
    rank = vocab["specificity"].get(kind, len(vocab["specificity"])) if kind else len(vocab["specificity"])
    return (rank, vocab["place_order"].get(place_code, 10**6))


# --------------------------------------------------------------------------
# Mappings from the pipeline's existing place tokens onto codes
# --------------------------------------------------------------------------

# Tokens left by cluster_issues._LOCATION (the word after rue/pont/autoroute/
# boulevard, folded). The prefix is lost there, so only names that denote one
# corridor regardless of prefix are listed.
_ROAD_TOKENS = {
    "pierre-laporte": "pierre-laporte-bridge",
    "laporte": "pierre-laporte-bridge",
    "henri-iv": "henri-iv",
    "laurentienne": "laurentienne",
    "dufferin-montmorency": "dufferin-montmorency",
    "laurier": "boulevard-laurier",
}

# Item geo evidence -> place code (the item-level scope when no place is
# named). enrich.propose_geo says "quebec-city" only on a strict city token
# and "quebec" on a Quebec token or an official province document. Its
# "linked" means NO local evidence ("source geography is not article
# geography"): an absence, which maps to no place at all, never to a guessed
# one. "world" is enrich's world-fog branch (a foreign token and no Quebec
# token): positive evidence of elsewhere. "federal" is federal evidence.
_GEO_TO_PLACE = {
    "quebec-city": "quebec-city",
    "quebec": "province",
    "world": ELSEWHERE,
    "federal": "ottawa",
    "ottawa": "ottawa",
}


def place_for_hint(hint: str) -> str | None:
    """Map a cluster_issues._PLACE_HINTS entry onto a place code.

    Identical folded form first, then the two aliases of docs/I18N.md section
    5 (duberger, haute-saint-charles). None when the hint has no code.
    """
    vocab = vocabulary()
    token = str(hint or "").strip().lower()
    if token in vocab["place_by_code"]:
        return token
    alias = vocab["aliases"].get(token)
    return alias if alias in vocab["place_by_code"] else None


def place_for_road(token: str) -> str | None:
    """Map a cluster_issues road-name token onto a corridor code, if it is one."""
    code = _ROAD_TOKENS.get(str(token or "").strip().lower())
    return code if code in vocabulary()["place_by_code"] else None


def place_from_geo(geo: str) -> str | None:
    """The scope an item's geo evidence supports (quebec-city, province,
    elsewhere, ottawa), or None: "linked" and "unknown" are absences."""
    code = _GEO_TO_PLACE.get(str(geo or "").strip().lower())
    return code if code in vocabulary()["place_by_code"] else None


# --------------------------------------------------------------------------
# Rule lexicon
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _lexicon() -> dict:
    doc = _read_json(LEXIQUE_PATH)
    type_rules = []
    raw_types = doc.get("types") if isinstance(doc.get("types"), dict) else {}
    order = vocabulary()["type_order"]
    for code in sorted(raw_types, key=lambda c: (order.get(c, 10**6), c)):
        rule = raw_types[code]
        if code not in order or not isinstance(rule, dict):
            continue
        weak = _words(rule, "fr") + _words(rule, "en")
        strong = _words(rule, "fr_strong") + _words(rule, "en_strong")
        weight = rule.get("strong_weight")
        type_rules.append({
            "code": code,
            "weak": _alternation(weak),
            "strong": _alternation(strong),
            "unless": _alternation(_words(rule, "unless")),
            "weak_words": weak,
            "strong_words": strong,
            "strong_weight": weight if isinstance(weight, int) and weight > 0 else DEFAULT_STRONG_WEIGHT,
        })
    place_rules = []
    raw_places = doc.get("places") if isinstance(doc.get("places"), dict) else {}
    for code, rule in raw_places.items():
        if code not in vocabulary()["place_by_code"] or not isinstance(rule, dict):
            continue
        place_rules.append({
            "code": code,
            "fr": _alternation(_words(rule, "fr")),
            "en": _alternation(_words(rule, "en")),
            "needs_context": bool(rule.get("needs_context")),
            # Phrases removed before THIS place's keywords are tested ("à
            # Ottawa" is the city of Ottawa, not the federal scope; "Nouvelle-
            # France" is no evidence of France), except where a `keep` phrase
            # matches ("demande à Ottawa" addresses the federal government).
            # Other places still see them.
            "mask": _mask(_words(rule, "mask"), _words(rule, "keep")),
        })
    place_rules.sort(key=lambda r: specificity_key(r["code"]))
    context = _alternation(_words(doc, "place_context"))
    mask = _mask(_words(doc, "place_mask"))
    return {"types": type_rules, "places": place_rules, "context": context, "mask": mask}


def _words(rule: dict, key: str) -> list[str]:
    value = rule.get(key)
    return [str(w) for w in value if isinstance(w, str) and w.strip()] if isinstance(value, list) else []


def _title_scores(folded_title: str) -> list[tuple[str, int, list[str]]]:
    """Per type that fires on one folded title: (code, weight, matched keywords)."""
    out = []
    for rule in _lexicon()["types"]:
        if rule["unless"] is not None and rule["unless"].search(folded_title):
            continue
        strong = rule["strong"].findall(folded_title) if rule["strong"] is not None else []
        if strong:
            out.append((rule["code"], rule["strong_weight"], strong))
            continue
        weak = rule["weak"].findall(folded_title) if rule["weak"] is not None else []
        if weak:
            # Two distinct weak cues in one headline are evidence; more add nothing.
            out.append((rule["code"], WEAK_WEIGHT * min(len(set(weak)), WEAK_CAP_PER_TITLE), weak))
    return out


def explain_type(titles: list[str]) -> list[dict]:
    """Every type that fired, best first: score and the keywords that fired.

    Deterministic: sorted by score desc, then table order. This is what a
    "why this type" disclosure reads; classify_type is its first row.
    """
    totals: dict[str, int] = {}
    seen: dict[str, list[str]] = {}
    for title in titles or []:
        for code, weight, words in _title_scores(fold(title)):
            totals[code] = totals.get(code, 0) + weight
            bucket = seen.setdefault(code, [])
            for word in words:
                if word not in bucket:
                    bucket.append(word)
    order = vocabulary()["type_order"]
    rows = [{"type": c, "score": totals[c], "keywords": sorted(seen[c])} for c in totals]
    rows.sort(key=lambda r: (-r["score"], order.get(r["type"], 10**6)))
    return rows


def classify_type(titles: list[str]) -> tuple[str, int]:
    """(type_code, hits) over member titles; ("unclassified", 0) on no hit.

    Each title scores a type once (a strong keyword = the type's strong weight;
    otherwise 1 point per distinct weak keyword, at most 2); scores are summed over the titles; highest wins; ties go to the
    earlier type in table A. Status of the result is always "proposed".
    """
    rows = explain_type(titles)
    if not rows or rows[0]["score"] < MIN_SCORE:
        return (UNCLASSIFIED, 0)
    return (rows[0]["type"], rows[0]["score"])


def classify_places(texts: list, langs: list[str] | None = None) -> list[str]:
    """Ordered place codes named by the texts, most specific first.

    `texts` is a list of strings, or of `(text, lang)` pairs; `langs` is an
    optional parallel list. A text's language selects which keyword list
    applies ("fr"/"en"); without one both apply. Order is specificity
    (quartier > arrondissement > site > corridor > neighbour > scope) then
    table order, so places[0] is always explainable. Ambiguous names (Vanier,
    Montcalm, Saint-Roch...) count only when the same text also says Quebec.
    An empty list means no place was named: the caller falls back to
    `place_from_geo`.
    """
    lex = _lexicon()
    found: set[str] = set()
    for i, entry in enumerate(texts or []):
        if isinstance(entry, (tuple, list)) and entry:
            text, lang = entry[0], (entry[1] if len(entry) > 1 else None)
        else:
            text, lang = entry, (langs[i] if langs and i < len(langs) else None)
        folded = fold(text)
        if lex["mask"] is not None:
            folded = lex["mask"].sub(" ", folded)
        has_context = lex["context"] is not None and lex["context"].search(folded) is not None
        which = [_lang(lang)] if str(lang or "").strip() else ["fr", "en"]
        for rule in lex["places"]:
            if rule["code"] in found or (rule["needs_context"] and not has_context):
                continue
            text = rule["mask"].sub(" ", folded) if rule["mask"] is not None else folded
            if any(rule[w] is not None and rule[w].search(text) for w in which):
                found.add(rule["code"])
    return sorted(found, key=specificity_key)
