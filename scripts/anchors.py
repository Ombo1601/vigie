"""anchors — official-record anchors for events, and the roadworks view.

docs/EVENTS.md section 10. An anchor is a *pointer* from an event to an
official record, attached by a named, deterministic rule. It is never a
confirmation: `official_source_is_confirmation` stays false everywhere, and an
event with no anchor shows "aucun document officiel rattaché", a measured
absence and never an accusation. Precision over recall: when a rule cannot
establish the link with its stated evidence, there is no anchor.

Pure functions. Python standard library only; no I/O, no network, no wall
clock, no randomness; sorted iteration; the output depends only on the
arguments (and not on PYTHONHASHSEED). Every rule is fail-soft on its own: a
fault in one rule prints a diagnosis and that rule yields nothing, the others
still run.

    find_anchors(members, official_items, roadworks, consultations, outages,
                 vocab_places, *, event_type=None) -> [anchor, ...]
    edition_seal_anchor(seq) -> anchor            (the caller adds it)
    roadworks_view(roadworks_doc, edition_clock) -> RoadworksView

An anchor row is `{"type", "ref", "rule", "status": "linked_by_rule"}`, sorted
by `(type, ref)`, one row per `(type, ref)`.

Inputs (all plain dicts, tolerant of missing keys):

  members          the event's items. Each carries `item_id` (or `id`) and, for
                   matching, `title`, `summary` / `excerpt`, `published_at`,
                   `first_seen`; `source_kind` when known.
  official_items   ids (or item dicts) of the edition's official-source items.
                   A member is an `official_item` anchor when its id is here or
                   its own `source_kind` is "official".
  roadworks        `data/roadworks/latest_roadworks.json` (a dict with
                   `events`) or the events list itself.
  consultations    `data/civic/latest_consultations.json` (dict with `events`)
                   or the events list itself.
  outages          Hydro-Quebec items of the edition (dicts).
  vocab_places     the event's place codes (docs/I18N.md table B). None =
                   derived from the member texts with `vocabulaire`.
  event_type       the event's type code. None = derived from the member titles
                   with `vocabulaire.classify_type`.

Rules (their names are what the page prints):

  membership-official-source   a member comes from a source_kind official source.
  roadwork-name-dates-v1       event type is in the roads family, a member's text
                               names the road of a declared obstruction (same
                               type of way, same name, token-exact, with a type
                               word next to the name) and the declared dates
                               overlap the members' publication span. Roads
                               named only as an alternative ("privilégiez le
                               chemin ...") or as the cross streets of a
                               closure ("entre A et B") are not the subject
                               and do not count; a route number alone does not
                               anchor a declaration that names other roads.
                               At most 3 declarations per road, 12 in all,
                               the road most members name first.
  consultation-place-topic-v1  event type is public-consultation, the civic
                               calendar entry shares a specific place code with
                               the event and at least one distinctive word (when neither names
                               one: at least three distinctive words).
  outage-place-time-v1         event type is power-outage, a Hydro-Quebec item
                               that is about an outage shares a specific place
                               code with the event and was published within
                               72 h of the members.
  edition-presence             the event is present in a sealed edition (the
                               caller adds it with `edition_seal_anchor`).

Where this is narrower than a plain reading of section 10 (the consultation
rule also asks for a shared distinctive word; place matches ignore the scope
codes quebec-city / greater-quebec / province / ottawa, which name nearly every
event) the narrowing is deliberate and measured: it removes false pointers.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import datetime, timedelta
from typing import Callable, Iterable

import i18n

METHOD = "anchors-v1 named-rules conservative"
STATUS = "linked_by_rule"
TYPES = ("official_item", "roadwork", "consultation", "outage", "edition_seal")

RULE_OFFICIAL = "membership-official-source"
RULE_ROADWORK = "roadwork-name-dates-v1"
RULE_CONSULTATION = "consultation-place-topic-v1"
RULE_OUTAGE = "outage-place-time-v1"
RULE_EDITION = "edition-presence"

ROADS_TYPES = ("roadworks", "road-closure")
CONSULTATION_TYPE = "public-consultation"
OUTAGE_TYPE = "power-outage"
MAX_ROADWORK_ANCHORS = 12      # one street can carry dozens of declarations:
MAX_PER_ROAD = 3               # at most this many per road, this many in all
OUTAGE_WINDOW_HOURS = 72
PLAUSIBLE_YEARS = (1990, 2100)

# The same order as resident_brief.RW_SEVERITY / depart.py (a test pins it).
RW_SEVERITY = {
    "all-lanes-closed": 0, "some-lanes-closed": 1, "alternating-one-way": 2,
    "some-lanes-closed-intermittent-or-short-duration": 3, "all-lanes-open": 4, "no-lanes-closed": 5,
}


def _warn(message: str) -> None:
    print(f"anchors: {message}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _dicts(value: object) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, (list, tuple)) else []


def _iid(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("item_id") or item.get("id") or "")
    return str(item or "")


def _fold(text: object) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "")).lower()
    raw = "".join(c for c in raw if not unicodedata.combining(c))
    return re.sub(r"[‐‑‒–—−]", "-", raw.replace("’", "'"))


def _instant(raw: object) -> datetime | None:
    dt = i18n.parse_instant(raw)
    if dt is None:
        return None
    lo, hi = PLAUSIBLE_YEARS
    return dt if lo <= dt.year <= hi else None


def _when(member: dict) -> datetime | None:
    """Publication instant, else first_seen; None when neither is a date."""
    return _instant(member.get("published_at")) or _instant(member.get("first_seen"))


def _span(members: list[dict]) -> tuple[datetime, datetime] | None:
    times = sorted(t for t in (_when(m) for m in members) if t is not None)
    return (times[0], times[-1]) if times else None


def _text_of(item: dict) -> str:
    return " . ".join(str(item.get(k) or "") for k in ("title", "summary", "excerpt", "description") if item.get(k))


def _rows_of(doc: object) -> list[dict]:
    """`events` of a store document, or a bare list of event dicts."""
    if isinstance(doc, dict):
        return _dicts(doc.get("events"))
    return _dicts(doc)


def _safely(rule: str, fn: Callable[[], list[dict]]) -> list[dict]:
    try:
        return fn()
    except (TypeError, ValueError, AttributeError, KeyError, IndexError, OverflowError) as exc:
        _warn(f"rule {rule} skipped ({type(exc).__name__}: {exc})")
        return []


def _row(kind: str, ref: str, rule: str) -> dict:
    return {"type": kind, "ref": str(ref), "rule": rule, "status": STATUS}


def edition_seal_anchor(seq: object) -> dict:
    """The `edition_seal` anchor for a published main-chain seal number."""
    return _row("edition_seal", str(int(seq)), RULE_EDITION)


# --------------------------------------------------------------------------- #
# Road names
# --------------------------------------------------------------------------- #
# Type words, folded. Class names are the French words; English words map to them.
_TYPE_WORDS = {
    "rue": "rue", "street": "rue",
    "avenue": "avenue", "ave": "avenue", "av": "avenue",
    "boulevard": "boulevard", "boul": "boulevard", "bd": "boulevard", "blvd": "boulevard",
    "chemin": "chemin", "ch": "chemin", "road": "chemin", "rd": "chemin",
    "route": "route", "rte": "route",
    "autoroute": "autoroute", "aut": "autoroute", "highway": "autoroute", "hwy": "autoroute",
    "cote": "cote", "montee": "montee", "place": "place", "ruelle": "ruelle",
    "impasse": "impasse", "quai": "quai",
}
# After the name, English publishers put the type word ("Charest Boulevard").
# "st" is deliberately absent: it is Saint far more often than Street.
_EN_SUFFIX_TYPES = frozenset({"street", "avenue", "ave", "boulevard", "blvd", "road", "rd", "highway", "hwy"})
# English-only words are never read as a type word BEFORE the name ("Street Bardy").
_SUFFIX_ONLY = frozenset({"street", "road", "rd", "highway", "hwy", "blvd", "ave"})
# A full stop after these is an abbreviation, not a sentence end.
_ABBREV_TYPES = frozenset({"boul", "bd", "blvd", "av", "ave", "ch", "rte", "aut", "rd", "hwy",
                           "saint", "sainte", "monseigneur"})
_PARTICLES = frozenset({"de", "du", "des", "la", "le", "les", "l", "d", "au", "aux", "of", "the"})
_STOP_WORDS = frozenset({"et", "and", "ou", "or", "entre", "between", "vers", "a", "from", "to", "near", "pres"})
_DIRECTIONS = frozenset({"e", "o", "n", "s", "est", "ouest", "nord", "sud", "east", "west", "north", "south"})
_ABBREVIATIONS = {"st": "saint", "ste": "sainte", "mgr": "monseigneur"}
_ORDINAL = re.compile(r"^(\d+)(?:e|er|re|ere|eme|ieme|st|nd|rd|th)$")
_WORD = re.compile(r"[a-z0-9]+")
_MAX_NAME_TOKENS = 5


def _is_ordinal(token: str) -> bool:
    return len(token) > 1 and token.endswith("e") and token[:-1].isdigit()


def _canon(token: str) -> str:
    """Spelling variants onto one form: 3rd / 3eme / 3e, St / Saint, Mgr."""
    if token in _TYPE_WORDS:
        return token
    m = _ORDINAL.match(token)
    if m:
        return m.group(1) + "e"
    return _ABBREVIATIONS.get(token, token)


def _tokens(text: str) -> list[tuple[str, str]]:
    """Folded word tokens of `text`, each with the gap that precedes it (the
    characters between the previous word and this one). A gap of exactly `-`
    joins two words into one compound (`Saint-Louis-de-Gonzague`)."""
    folded = _fold(text)
    out: list[tuple[str, str]] = []
    prev_end = None
    for m in _WORD.finditer(folded):
        out.append((m.group(0), "" if prev_end is None else folded[prev_end:m.start()]))
        prev_end = m.end()
    return out


def _flows(gap: str, prev: str) -> bool:
    """Do two neighbouring words belong to one road mention? Only spaces,
    apostrophes and hyphens separate them (and a full stop right after an
    abbreviated type word): a comma, a colon or a sentence end breaks it."""
    rest = gap.replace(" ", "").replace("'", "").replace("-", "")
    return rest == "" or (rest == "." and prev in _ABBREV_TYPES)


def parse_road(name: object) -> tuple[str | None, tuple[str, ...]] | None:
    """A declared road name as `(type_class, name_tokens)`, or None.

    `Rue St-Louis` -> ("rue", ("saint", "louis")); `Avenue de l'Amiral` ->
    ("avenue", ("amiral",)); `RTE-175` -> ("route", ("175",)); `3e Avenue` ->
    ("avenue", ("3e",)); `Grande Allée E` -> (None, ("grande", "allee")).
    Particles and a trailing compass letter are dropped; Saint/St/Ste are
    normalised; a name with no type word and fewer than two words is not
    matchable (None).
    """
    toks = [_canon(w) for w, _ in _tokens(str(name or ""))]
    if not toks:
        return None
    cls: str | None = None
    if toks[0] in _TYPE_WORDS:
        cls = _TYPE_WORDS[toks[0]]
        toks = toks[1:]
    elif len(toks) >= 2 and _is_ordinal(toks[0]) and toks[1] in _TYPE_WORDS:
        cls = _TYPE_WORDS[toks[1]]
        toks = [toks[0]] + toks[2:]
    elif len(toks) >= 2 and toks[-1] in _EN_SUFFIX_TYPES:
        cls = _TYPE_WORDS[toks[-1]]
        toks = toks[:-1]
    toks = [t for t in toks if t not in _PARTICLES]
    if len(toks) >= 2 and toks[-1] in _DIRECTIONS:
        toks = toks[:-1]
    if not toks or (cls is None and len(toks) < 2):
        return None
    return (cls, tuple(toks))


def mentions(text: str) -> tuple[set, set, set]:
    """Road mentions in free text: `(prefix, suffix, bare)` sets.

    prefix: `(class, tokens)` for a type word then the name (`rue de la
            Couronne`, `boul. Charest`).
    suffix: `(class, tokens)` for the name then an English type word
            (`Charest Boulevard`), or an ordinal then any type word (`3e
            Avenue`, `3rd Avenue`).
    bare:   token tuples of any run of words, for names declared without a
            type word (`Grande Allée`).
    A run cut inside a hyphenated compound is never recorded: `rue
    Saint-Jean-Baptiste` is not a mention of `rue Saint-Jean`. A comma, a
    colon or a sentence end ends a mention.
    """
    toks = _tokens(text)
    words = [_canon(w) for w, _ in toks]
    gaps = [g for _, g in toks]
    n = len(words)
    prefix: set = set()
    suffix: set = set()
    bare: set = set()

    def run_forward(start: int):
        """Name runs (particle-free token tuples) that begin at `start`."""
        j, got, prev = start, [], start - 1
        while j < n and len(got) < _MAX_NAME_TOKENS:
            if j > start and not _flows(gaps[j], words[prev]):
                return
            tok = words[j]
            prev = j
            j += 1
            if tok in _PARTICLES:
                continue
            if tok in _STOP_WORDS:
                return
            got.append(tok)
            if not (j < n and gaps[j] == "-"):          # the name may end here
                yield tuple(got)

    for i in range(n):
        if words[i] in _PARTICLES or words[i] in _STOP_WORDS or (i > 0 and gaps[i] == "-"):
            continue                                      # a run never starts mid-compound
        for name in run_forward(i):
            bare.add(name)
    for i, w in enumerate(words):
        cls = _TYPE_WORDS.get(w)
        if cls is None:
            continue
        if w not in _SUFFIX_ONLY and i + 1 < n and _flows(gaps[i + 1], w):
            for name in run_forward(i + 1):
                prefix.add((cls, name))
        if i == 0 or not _flows(gaps[i], words[i - 1]):
            continue
        # suffix form: runs that END right before this type word
        english = toks[i][0] in _EN_SUFFIX_TYPES
        pending: list[tuple[str, ...]] = []
        blocked = False
        back: list[str] = []
        k = i - 1
        while k >= 0 and len(back) < _MAX_NAME_TOKENS:
            tok = words[k]
            if tok in _TYPE_WORDS:
                blocked = True        # "rue Saint-Jean Avenue ...": the name already has its type word
                break
            if tok in _STOP_WORDS:
                break
            if tok not in _PARTICLES:
                back.insert(0, tok)
                name = tuple(back)
                if not (k > 0 and gaps[k] == "-") and (english or (len(name) == 1 and _is_ordinal(name[0]))):
                    pending.append(name)
            if k == 0 or not _flows(gaps[k], words[k - 1]):
                break
            k -= 1
        if not blocked or not english:
            for name in pending:
                suffix.add((cls, name))
    return prefix, suffix, bare


# A sentence that tells drivers where to go INSTEAD names roads that are not
# the subject of the event ("privilégiez le chemin Sainte-Foy"): it contributes
# no mention. Folded substrings.
_DETOUR_CUES = ("privilegi", "contourn", "detour", "alternativ", "alternate", "emprunt", "plutot", "eviter",
                "evitez", "avoid", "instead", "reroute", "redirig", "deviat", "devie", "itineraire")
_ABBREV_DOT = re.compile(r"(?i)\b(boul|bd|av|ave|ch|rte|aut|st|ste|mgr|no|dr)\.")
_SENTENCE_END = re.compile(r"[.!?]\s+|\n+")
# "entre l'avenue Turnbull et la rue Bardy": the two cross streets that bound a
# closure are not the closed road. The clause is cut out of the sentence.
_BOUNDARY = re.compile(r"(?i)\b(?:entre|between)\b(?:\s+[^\s.;:,()]+){1,8}?\s+(?:et|and)\b(?:\s+[^\s.;:,()]+){1,6}")


def subject_mentions(members: list[dict]) -> tuple[set, set, set]:
    """Road mentions over the members' fields, sentence by sentence. Left out:
    sentences that point drivers to an alternative (see _DETOUR_CUES) and the
    cross streets that bound a closure ("entre A et B")."""
    prefix: set = set()
    suffix: set = set()
    bare: set = set()
    for m in members:
        for key in ("title", "summary", "excerpt", "description"):
            field = str(m.get(key) or "")
            if not field:
                continue
            for sentence in _SENTENCE_END.split(_ABBREV_DOT.sub(r"\1", field)):
                if not sentence.strip() or any(cue in _fold(sentence) for cue in _DETOUR_CUES):
                    continue
                p, q, b = mentions(_BOUNDARY.sub(" . ", sentence))
                prefix |= p
                suffix |= q
                bare |= b
    return prefix, suffix, bare


def _is_route_number(key: tuple[str | None, tuple[str, ...]]) -> bool:
    return key[0] in ("route", "autoroute") and all(t.isdigit() for t in key[1])


def _road_matches(key: tuple[str | None, tuple[str, ...]], prefix: set, suffix: set, bare: set) -> bool:
    cls, name = key
    if cls is None:
        return name in bare                  # two or more words, token-exact
    if len(name) == 1 and _is_ordinal(name[0]):
        return (cls, name) in suffix
    return (cls, name) in prefix or (cls, name) in suffix


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def _official_rule(members: list[dict], official_items: Iterable) -> list[dict]:
    ids = {_iid(x) for x in (official_items or ()) if _iid(x)}
    out = []
    for m in members:
        mid = _iid(m)
        if mid and (mid in ids or str(m.get("source_kind") or "") == "official"):
            out.append(_row("official_item", mid, RULE_OFFICIAL))
    return out


def _roadwork_ordered(events: list[dict]) -> list[dict]:
    """The departure screen's order: most restrictive, newest update, id."""
    ordered = sorted(events, key=lambda e: str(e.get("event_id") or ""))
    ordered.sort(key=lambda e: str(e.get("update_date") or ""), reverse=True)
    ordered.sort(key=lambda e: RW_SEVERITY.get(str(e.get("vehicle_impact") or ""), 6))
    return ordered


def _roadwork_rule(members: list[dict], roadworks: object, event_type: str) -> list[dict]:
    if event_type not in ROADS_TYPES:
        return []
    span = _span(members)
    if span is None:
        return []
    per_member = [subject_mentions([m]) for m in members]
    if not any(bare for _, _, bare in per_member):
        return []
    lo, hi = span
    hits: list[tuple[int, dict, tuple]] = []
    for e in _rows_of(roadworks):
        if not e.get("event_id"):
            continue
        start, end = _instant(e.get("start_date")), _instant(e.get("end_date"))
        if start is None or end is None or start > hi or end < lo:
            continue
        names = e.get("road_names") if isinstance(e.get("road_names"), (list, tuple)) else []
        keys = [k for k in (parse_road(raw) for raw in names) if k is not None]
        # how many members name one of this declaration's roads: the more, the more it is the subject
        votes, group = 0, ()
        for prefix, suffix, bare in per_member:
            matched = [k for k in keys if _road_matches(k, prefix, suffix, bare)]
            # A route number alone is a long road: when the declaration names other
            # roads too, at least one of them must be named as well.
            if matched and not (len(matched) < len(keys) and all(_is_route_number(k) for k in matched)):
                votes += 1
                group = group or tuple(sorted((str(k[0]), k[1]) for k in matched))
        if votes:
            hits.append((votes, e, group))
    # most-named road first, then the departure screen's order. A street can carry
    # dozens of declarations: at most MAX_PER_ROAD per road, MAX_ROADWORK_ANCHORS in all,
    # so one busy street never hides the others.
    rank = {str(e["event_id"]): i for i, e in enumerate(_roadwork_ordered([e for _, e, _ in hits]))}
    hits.sort(key=lambda h: (-h[0], rank[str(h[1]["event_id"])]))
    taken: dict[tuple, int] = {}
    out = []
    for _, e, group in hits:
        if taken.get(group, 0) >= MAX_PER_ROAD:
            continue
        taken[group] = taken.get(group, 0) + 1
        out.append(_row("roadwork", str(e["event_id"]), RULE_ROADWORK))
        if len(out) >= MAX_ROADWORK_ANCHORS:
            break
    return out


_GENERIC = frozenset(_fold(w) for w in (
    "consultation consultations publique publiques public quebec ville arrondissement quartier projet projets "
    "citoyens citoyennes participation seance rencontre information soiree atelier ateliers "
    "municipal municipale municipaux conseil conseiller conseillere annuelle annuel autoriser permettre "
    "nouveau nouvelle nouveaux nouvelles ensemble secteur secteurs "
    "council councillor annual authorize allow new "
    "septembre octobre novembre decembre janvier fevrier avril juillet "
    "hearing hearings meeting city borough district project residents session workshop"
).split())


def _topic_stems(text: str) -> set[str]:
    """Six-letter stems of the distinctive words of `text` (singular and plural
    meet; no translation: a French and an English text share none)."""
    return {w[:6] for w, _ in _tokens(text) if len(w) >= 6 and not w.isdigit() and w not in _GENERIC}


def _specific(codes: Iterable[str]) -> set[str]:
    """Place codes that name somewhere particular (not a scope code)."""
    import vocabulaire

    return {c for c in codes if c and vocabulaire.place_kind(c) and not vocabulaire.is_scope(c)}


def _derive_places(items: list[dict]) -> list[str]:
    import vocabulaire

    return vocabulaire.classify_places([_text_of(i) for i in items])


def _derive_type(members: list[dict]) -> str:
    import vocabulaire

    return vocabulaire.classify_type([str(m.get("title") or "") for m in members])[0]


# A calendar entry that names a quartier or an arrondissement the lexicon cannot
# resolve is about somewhere particular: it never matches an event on topic alone.
_SECTOR_WORD = re.compile(r"\b(quartier|arrondissement|secteur|district|borough|neighbourhood|neighborhood)\b")


def _consultation_rule(members: list[dict], consultations: object, event_type: str, places: list[str]) -> list[dict]:
    if event_type != CONSULTATION_TYPE:
        return []
    import vocabulaire

    mine = _specific(places)
    member_stems = _topic_stems(" . ".join(_text_of(m) for m in members))
    out = []
    for c in _rows_of(consultations):
        ref = str(c.get("event_id") or "")
        text = " . ".join(str(c.get(k) or "") for k in ("title", "mode_text") if c.get(k))
        if not ref or not text:
            continue
        theirs = _specific(vocabulaire.classify_places([text], ["fr"]))
        shared = len(member_stems & _topic_stems(text))
        if mine and theirs:
            ok = bool(mine & theirs) and shared >= 1
        elif not mine and not theirs and not _SECTOR_WORD.search(_fold(text)):
            ok = shared >= 3
        else:
            ok = False
        if ok:
            out.append(_row("consultation", ref, RULE_CONSULTATION))
    return out


_OUTAGE_TERMS = re.compile(
    r"\b(pannes?|interruptions? (?:du service|d'electricite|de courant|de service)|coupures? (?:de courant|d'electricite)"
    r"|sans electricite|power outages?|power failures?|outages?)\b")


def _outage_rule(members: list[dict], outages: Iterable, event_type: str, places: list[str]) -> list[dict]:
    if event_type != OUTAGE_TYPE:
        return []
    span = _span(members)
    mine = _specific(places)
    if span is None or not mine:
        return []
    member_ids = {_iid(m) for m in members}
    slack = timedelta(hours=OUTAGE_WINDOW_HOURS)
    out = []
    for item in _dicts(list(outages or ())):
        ref = _iid(item)
        if not ref or ref in member_ids:
            continue
        if "hydro" not in _fold(" ".join(str(item.get(k) or "") for k in ("institution", "source_id"))):
            continue
        if not _OUTAGE_TERMS.search(_fold(_text_of(item))):
            continue
        when = _when(item)
        if when is None or when < span[0] - slack or when > span[1] + slack:
            continue
        if mine & _specific(_derive_places([item])):
            out.append(_row("outage", ref, RULE_OUTAGE))
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def find_anchors(members: Iterable, official_items: Iterable = (), roadworks: object = None,
                 consultations: object = None, outages: Iterable = (), vocab_places: Iterable | None = None,
                 *, event_type: str | None = None) -> list[dict]:
    """Official-record anchors of one event, sorted by `(type, ref)`.

    See the module docstring for every argument. An event the rules cannot tie
    to an official record gets `[]`, which the page prints as a measured
    absence. `edition_seal` anchors are not produced here: the caller adds
    `edition_seal_anchor(seq)` for each published seal that carries the event.
    """
    mem = _dicts(list(members or ()))
    if not mem:
        return []
    etype = str(event_type or "") or (_safely("type", lambda: [_derive_type(mem)]) or ["unclassified"])[0]
    places = [str(p) for p in vocab_places] if vocab_places is not None else (
        _safely("places", lambda: _derive_places(mem)) or [])
    rows: list[dict] = []
    rows += _safely(RULE_OFFICIAL, lambda: _official_rule(mem, official_items))
    rows += _safely(RULE_ROADWORK, lambda: _roadwork_rule(mem, roadworks, etype))
    rows += _safely(RULE_CONSULTATION, lambda: _consultation_rule(mem, consultations, etype, places))
    rows += _safely(RULE_OUTAGE, lambda: _outage_rule(mem, outages, etype, places))
    unique: dict[tuple[str, str], dict] = {}
    for r in sorted(rows, key=lambda r: (r["type"], r["ref"], r["rule"])):
        unique.setdefault((r["type"], r["ref"]), r)
    return [unique[k] for k in sorted(unique)]


# --------------------------------------------------------------------------- #
# Roadworks view (the front door's "before you leave" block)
# --------------------------------------------------------------------------- #
STALE_AFTER_HOURS = 6


def roadworks_view(roadworks_doc: object, edition_clock: object, *, limit: int = 6) -> dict:
    """The RoadworksView of `scripts/composants.py` for the official lane.

    The hourly roads lane refreshes between editions, so the collection is
    routinely NEWER than the edition clock. That is the normal case: never
    stale, never an error, `clock_skew` stays False. The age is measured
    against the later of the two clocks, so a newer collection has age 0 and a
    collection older than the edition has exactly the gap between them; it is
    `stale` only when that gap exceeds six hours. The kit prints the collection
    time itself, in Quebec City time; this view adds the same time as local
    fields for any caller that wants them, and the age in whole seconds.

    Added keys (the kit ignores them): `age_seconds` (int | None),
    `relation_to_edition` ("newer" | "same" | "older" | "unknown"), `active`
    (declarations whose status is active), `closed_active`,
    `collected_local` / `edition_local` ("YYYY-MM-DD HH:MM", America/Toronto),
    `edition_clock`. Rows keep the departure screen's published order: most
    restrictive impact first, then most recently updated, then event id; a work
    declared once per direction is one row ("both directions"), the cap counts
    rows, and `total`/`active`/`closed_active` stay counts of declarations
    (see `composants.roadworks_view`).
    Pure: no clock of its own; a corrupt document yields an empty, honest view.
    """
    import composants

    doc = roadworks_doc if isinstance(roadworks_doc, dict) else {}
    fetched = i18n.parse_instant(doc.get("fetched_at"))
    edition = i18n.parse_instant(edition_clock)
    if fetched is not None and edition is not None:
        later = max(fetched, edition)
        exact = (later - fetched).total_seconds()
        age = int(round(exact))
        relation = "newer" if fetched > edition else "older" if fetched < edition else "same"
    else:
        later, exact, age, relation = None, None, None, "unknown"
    view = composants.roadworks_view(
        doc, limit=limit, stale_after_hours=float(STALE_AFTER_HOURS),
        render_clock=later.isoformat() if later is not None else "")
    view["stale"] = bool(exact is not None and exact > STALE_AFTER_HOURS * 3600)
    view["clock_skew"] = False
    events = [e for e in _dicts(doc.get("events")) if e.get("event_id")]
    view["active"] = sum(1 for e in events if e.get("event_status") == "active")
    view["closed_active"] = sum(1 for e in events
                                if e.get("event_status") == "active" and e.get("vehicle_impact") == "all-lanes-closed")
    view["age_seconds"] = age
    view["relation_to_edition"] = relation
    view["edition_clock"] = edition.strftime("%Y-%m-%dT%H:%M:%SZ") if edition is not None else ""
    view["collected_local"] = _local(fetched)
    view["edition_local"] = _local(edition)
    return view


def _local(dt: datetime | None) -> str:
    local = i18n.local_dt(dt) if dt is not None else None
    return local.strftime("%Y-%m-%d %H:%M") if local is not None else ""
