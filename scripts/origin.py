#!/usr/bin/env python3
"""Origin classifier (docs/EVENTS.md section 7, docs/MIGRATION.md step 3).

Shadow only: nothing in the pipeline imports this module yet.

    origin_of(item, source) -> (class, rule_id)

Classes: official, wire, press_release, own_reporting, unknown.

House law carried by this file
------------------------------
* R8: publisher text is only *read*. The return value is a class code and a
  rule id drawn from a closed set (RULE_IDS); no title, excerpt, author or
  matched fragment is ever returned, logged or stored.
* The default is `unknown`. `own_reporting` is never inferred from the absence
  of a marker: it needs a named byline that parses as person name(s) (plus,
  optionally, the outlet's own name as a self-credit) and no wire or relay
  marker anywhere in the item. Everything else that is not positively
  established stays `unknown`.
* Vigie issues no verdicts: the class says what the item's own credit or
  marker field declares, never whether the story is true or independent.
* Deterministic and pure: no clock, no I/O, no randomness, no dict-order
  dependence; same inputs, same output, any PYTHONHASHSEED.
* Fail-soft: a malformed item or source yields `unknown.bad_input`; the
  function never raises on any input.

Fields read (verified on the live enriched candidates)
------------------------------------------------------
`author` (publisher byline, capped at 120 chars by normalize), `title`,
`summary`, `source_kind` and the source's `name` / `institution_name` /
`institution` / `id`. `photo_credit` is deliberately NOT read: a wire photo
credit proves the picture, not the text (R2 handles image credit separately).

Rule ids are `<class>.<reason>`; wire rules carry the agency as last segment
(`wire.author.cp`, `wire.text_tag.afp`, ...), `multi` when several agencies are
named. The id set is closed and tested.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Classes
# --------------------------------------------------------------------------

OFFICIAL = "official"
WIRE = "wire"
PRESS_RELEASE = "press_release"
OWN_REPORTING = "own_reporting"
UNKNOWN = "unknown"
CLASSES = (OFFICIAL, WIRE, PRESS_RELEASE, OWN_REPORTING, UNKNOWN)

# Founder decision (b): Agence QMI is Quebecor's in-house wire. The spec's
# closed list does not name it, so the default is False: an "Agence QMI"
# credit is recognised as an organisation (never a person, so never
# own_reporting) and the item stays `unknown`. Flip to True to classify it as
# `wire.author.qmi`.
AGENCE_QMI_IS_WIRE = False

# --------------------------------------------------------------------------
# Closed lists
# --------------------------------------------------------------------------

# agency code -> folded aliases (see _fold: lower-case, no accents, every
# non-alphanumeric run collapsed to one space). Closed list: spec section 7.
WIRE_AGENCIES: dict[str, tuple[str, ...]] = {
    "cp": (
        "la presse canadienne", "presse canadienne", "the canadian press",
        "canadian press", "pc", "cp",
    ),
    "afp": ("agence france presse", "france presse", "afp"),
    "reuters": ("thomson reuters", "reuters"),
    "ap": ("the associated press", "associated press", "ap"),
}
WIRE_QMI_ALIASES = ("agence qmi", "qmi")

# Press-release distribution agencies (relay datelines / credits).
RELAY_AGENCY_ALIASES = (
    "cnw", "cnw telbec", "cnw group", "canada newswire", "newswire",
    "cision", "pr newswire", "prnewswire", "globe newswire", "globenewswire",
    "business wire", "businesswire", "marketwired",
)

# Organisation-ish words: a byline segment containing one is a credit line,
# never a person (so never own_reporting). Folded tokens.
_ORG_WORDS = frozenset({
    "agence", "agency", "journal", "journaux", "radio", "television", "tele",
    "presse", "press", "redaction", "equipe", "team", "staff", "newsroom",
    "nouvelles", "news", "collectif", "bureau", "service", "services",
    "quebecor", "qmi", "cbc", "tva", "cn2i", "tribune", "soleil", "devoir",
    "inc", "ltee", "ltd", "corp", "groupe", "group", "communications",
    "communique", "ville", "gouvernement", "ministere", "hydro", "cision",
    "cnw", "newswire", "reuters", "afp", "ap", "pc", "cp", "wire",
    "canada", "canadian", "canadienne", "quebec", "montreal", "ottawa",
    "collaboration", "special", "speciale", "invite", "invitee", "guest",
    "auteur", "autrice", "auteurs", "signataires", "signataire", "lecteur",
    "lectrice", "chroniqueur", "chroniqueuse",
})
_ARTICLE_FIRST = frozenset({"le", "la", "les", "l", "the", "un", "une", "des"})
# Lower-case name particles allowed inside a person name.
_PARTICLES = frozenset({"de", "du", "des", "la", "le", "van", "von", "der", "den",
                        "di", "da", "dos", "del", "el", "al", "ben", "bin", "st", "ste"})

# --------------------------------------------------------------------------
# Rule ids (closed set)
# --------------------------------------------------------------------------

_AGENCY_CODES = tuple(sorted(WIRE_AGENCIES)) + ("qmi", "multi")
_WIRE_RULE_FAMILIES = ("wire.author", "wire.text_tag", "wire.text_files_from",
                       "wire.text_attribution")

RULE_IDS = frozenset(
    {
        "official.source_kind",
        "press_release.author_relay",
        "press_release.dateline",
        "press_release.par_voie_de_communique",
        "press_release.in_a_news_release",
        "press_release.dans_un_communique",
        "press_release.selon_un_communique",
        "own_reporting.named_byline",
        "unknown.no_signal",
        "unknown.bad_input",
        "unknown.newsroom_credit",
        "unknown.byline_not_a_name",
        "unknown.byline_mixed_credit",
        "unknown.byline_seen_with_wire",
        "unknown.byline_multi_outlet",
    }
    | {f"{family}.{code}" for family in _WIRE_RULE_FAMILIES for code in _AGENCY_CODES}
)

# --------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------

_MAX_FIELD = 4000          # characters ever scanned per field (hostile input guard)
_MAX_SEGMENTS = 12         # a byline longer than this is not a person list
_MAX_SEGMENT_CHARS = 60

_WS_RE = re.compile(r"\s+")
_NONALNUM_RE = re.compile(r"[^0-9a-z]+")


def _clean(value: object) -> str:
    """Bounded, NFKC-normalised, control/format-free, whitespace-collapsed text.
    Anything that is not a string is treated as absent."""
    if not isinstance(value, str):
        return ""
    value = value[:_MAX_FIELD]
    value = unicodedata.normalize("NFKC", value)
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C" or ch in "\t\n\r")
    return _WS_RE.sub(" ", value).strip()


def _fold(text: str) -> str:
    """Lower-case, accent-free, every non-alphanumeric run -> one space."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NONALNUM_RE.sub(" ", text).strip()


# --------------------------------------------------------------------------
# Wire / relay recognition
# --------------------------------------------------------------------------

def _alias_table(aliases: dict[str, tuple[str, ...]]) -> list[tuple[tuple[str, ...], str]]:
    rows = []
    for code, names in aliases.items():
        for name in names:
            rows.append((tuple(name.split()), code))
    # longest alias first so "agence france presse" beats "france presse"
    rows.sort(key=lambda r: (-len(r[0]), r[1], r[0]))
    return rows


_WIRE_TABLE = _alias_table(WIRE_AGENCIES)
_QMI_TABLE = _alias_table({"qmi": WIRE_QMI_ALIASES})
_RELAY_TABLE = _alias_table({"relay": RELAY_AGENCY_ALIASES})
_LEADERS = ("par", "by", "de", "from", "avec", "with", "source")


def _consume(tokens: list[str], table: list[tuple[tuple[str, ...], str]]) -> set[str] | None:
    """If the whole token list is a sequence of aliases from `table` (for
    example "agence france presse afp"), return the agency codes it names;
    otherwise None."""
    codes: set[str] = set()
    pos = 0
    while pos < len(tokens):
        for alias, code in table:
            n = len(alias)
            if tuple(tokens[pos:pos + n]) == alias:
                codes.add(code)
                pos += n
                break
        else:
            return None
    return codes or None


def _segment_agencies(segment: str, table) -> set[str] | None:
    tokens = _fold(segment).split()
    while tokens and tokens[0] in _LEADERS:
        tokens = tokens[1:]
    return _consume(tokens, table) if tokens else None


# Byline shapes seen in the wild: "A, AFP", "A et B", "A (AFP)", "A - AFP",
# "A | AFP", "A pour La Presse Canadienne", "A, La Presse canadienne (Ottawa)".
# A dash separates only when spaced, so "Agence France-Presse" and
# "Jean-Pierre" stay whole; brackets always separate (the city or agency in
# brackets becomes its own, harmless segment).
_SPLIT_RE = re.compile(
    r"\s*(?:,|;|/|&|\||[()\[\]]|(?<=\s)[-–—](?=\s)|"
    r"\bet\b|\band\b|\bwith\b|\bavec\b|\bpar\b|\bby\b|\bpour\b|\bfor\b)\s*",
    re.IGNORECASE)


def _segments(author: str) -> list[str]:
    return [s for s in (p.strip(" .-–—") for p in _SPLIT_RE.split(author)) if s]


def _is_person_name(segment: str) -> bool:
    """A conservative shape test for a person's name: 2 to 6 tokens, letters
    only (hyphen, apostrophe, initial dots allowed), no organisation word, no
    leading article. It never says a person *is* a journalist; it only refuses
    to call a credit line, a bio sentence or a newsroom a person."""
    if not segment or len(segment) > _MAX_SEGMENT_CHARS:
        return False
    tokens = segment.replace("’", "'").split()
    if not 2 <= len(tokens) <= 6:
        return False
    if _fold(tokens[0]) in _ARTICLE_FIRST:
        return False
    real = 0
    for tok in tokens:
        bare = tok.strip(".")
        if not bare:
            return False
        if bare.lower() in _PARTICLES and tok == tok.lower():
            continue
        # letters plus - ' only; first character must be a letter
        if not re.fullmatch(r"[^\W\d_](?:[^\W\d_]|[-'.])*", tok):
            return False
        if len(bare) >= 2 or tok.endswith("."):
            real += 1
        for part in _fold(tok).split():
            if part in _ORG_WORDS:
                return False
    return real >= 2


def _self_names(item: dict, source: dict) -> set[str]:
    names = set()
    for obj in (source, item):
        for key in ("name", "institution_name", "institution", "source_name", "source_id", "id"):
            if obj is item and key == "id":
                continue  # an item id is a hash, not a name
            val = _clean(obj.get(key))
            if val:
                names.add(_fold(val))
    # "Radio-Canada — Québec" style: also the part before a dash
    for raw_key in ("name", "source_name"):
        for obj in (source, item):
            val = _clean(obj.get(raw_key))
            for sep in ("—", "–", " - "):
                if sep in val:
                    names.add(_fold(val.split(sep)[0]))
    names.discard("")
    return names


def _wire_codes_in_author(author: str) -> set[str]:
    """Agency codes named by author segments (closed list)."""
    codes: set[str] = set()
    table = _WIRE_TABLE + (_QMI_TABLE if AGENCE_QMI_IS_WIRE else [])
    # whole-field first ("Agence France-Presse (AFP)" would split oddly)
    whole = _segment_agencies(author, table)
    if whole:
        return whole
    for seg in _segments(author):
        got = _segment_agencies(seg, table)
        if got:
            codes |= got
    return codes


def _agency_suffix(codes: set[str]) -> str:
    return next(iter(codes)) if len(codes) == 1 else "multi"


# --- text markers ----------------------------------------------------------
# Raw (case-preserving) patterns: short acronyms must be upper-case so that
# "ap" in "ap…" or "cp" never fires. Every pattern is bounded and linear.

_AGENCY_RAW = (r"(?:(?-i:AFP)|(?-i:AP)|(?-i:CP)|Reuters|La Presse canadienne|"
               r"Presse canadienne|The Canadian Press|Canadian Press|Agence France-Presse|"
               r"The Associated Press|Associated Press)")
_TAG_RE = re.compile(r"[(\[]\s*" + _AGENCY_RAW + r"\s*[)\]]", re.IGNORECASE)
_END_ATTR_RE = re.compile(r"[—–-]\s*" + _AGENCY_RAW + r"\s*\.?\s*$", re.IGNORECASE)

_AGENCY_FOLD = (r"(?:agence france presse|afp|la presse canadienne|presse canadienne|"
                r"the canadian press|canadian press|reuters|the associated press|associated press|ap)")
_FILES_FROM_RE = re.compile(
    r"\b(?:avec (?:des )?(?:informations|depeches|fichiers|reportages)|"
    r"with (?:files|reporting|information|reports?)) (?:de|d|du|from|by) (?:l |la |le |the )?"
    + _AGENCY_FOLD + r"\b")

_DATELINE_RE = re.compile(
    r"(?:^|\s)/\s*(?:CNW(?:\s+Telbec|\s+Group)?|PR\s?Newswire|GLOBE\s?NEWSWIRE|"
    r"Business\s?Wire|Canada\s+Newswire|Newswire|Marketwired|Cision)\s*/",
    re.IGNORECASE)

_PAR_VOIE_RE = re.compile(r"\bpar voie de communiques?\b")
_NEWS_RELEASE_RE = re.compile(
    r"\b(?:in|via|through|according to|says|said in|stated in)\s+(?:an?|the|its|their|his|her)?\s*"
    r"(?:\w+ )?(?:news|press|media) releases?\b")
_DANS_UN_COMM_RE = re.compile(
    r"\b(?:dans|par) (?:un|le|son|leur|ce) (?:(?:recent|dernier|bref|court|nouveau) )?communique\b")
_SELON_COMM_RE = re.compile(
    r"\bselon (?:un|le|son|leur|ce) (?:(?:recent|dernier|bref|court|nouveau) )?communique\b")


def _wire_text_marker(raw: str, folded: str) -> tuple[str, str] | None:
    """-> (rule family, agency suffix) if a closed-list wire credit marker is
    present. Bare mentions of an agency ("selon Reuters", "a annoncé à l'AFP")
    are attributions inside the outlet's own text, not credits: not matched."""
    m = _TAG_RE.search(raw)
    if m:
        return "wire.text_tag", _agency_code_of(m.group(0))
    m = _END_ATTR_RE.search(raw)
    if m:
        return "wire.text_attribution", _agency_code_of(m.group(0))
    m = _FILES_FROM_RE.search(folded)
    if m:
        return "wire.text_files_from", _agency_code_of(m.group(0))
    return None


def _agency_code_of(fragment: str) -> str:
    codes: set[str] = set()
    toks = _fold(fragment).split()
    for alias, code in _WIRE_TABLE:
        n = len(alias)
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == alias:
                codes.add(code)
    # "agence france presse" also contains... only afp; fine. A bare "ap" token
    # inside "associated press" is the same agency, so one code stays one code.
    return _agency_suffix(codes) if codes else "multi"


def _relay_marker(raw: str, folded: str) -> str | None:
    if _DATELINE_RE.search(raw):
        return "press_release.dateline"
    if _PAR_VOIE_RE.search(folded):
        return "press_release.par_voie_de_communique"
    if _NEWS_RELEASE_RE.search(folded):
        return "press_release.in_a_news_release"
    if _DANS_UN_COMM_RE.search(folded):
        return "press_release.dans_un_communique"
    if _SELON_COMM_RE.search(folded):
        return "press_release.selon_un_communique"
    return None


# --------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------

def _source_kind(item: dict, source: dict) -> str:
    """The source registry is authoritative; the item copy is the fallback."""
    for obj in (source, item):
        val = obj.get("source_kind")
        if isinstance(val, str) and val.strip():
            return val.strip().casefold()
    return ""


def origin_of(item: dict, source: dict | None = None) -> tuple[str, str]:
    """Classify one item. Pure, total, deterministic. See module docstring."""
    try:
        return _origin_of(item, source)
    except Exception:  # fail-soft: a classifier fault is unknown, never a crash
        return UNKNOWN, "unknown.bad_input"


def _origin_of(item: object, source: object) -> tuple[str, str]:
    if not isinstance(item, dict):
        return UNKNOWN, "unknown.bad_input"
    if not isinstance(source, dict):
        source = {}

    # 1. official: the item *is* the communique. A CNW dateline inside an
    # official source is the State's own release, never a relay by a medium.
    if _source_kind(item, source) == "official":
        return OFFICIAL, "official.source_kind"

    author = _clean(item.get("author"))
    title = _clean(item.get("title"))
    summary = _clean(item.get("summary"))

    # 2. wire credit in the author field (closed list; byline + credit counts)
    codes = _wire_codes_in_author(author) if author else set()
    if codes:
        return WIRE, f"wire.author.{_agency_suffix(codes)}"

    # 3. relay agency named as the author/credit ("Cision", "CNW")
    if author:
        if _segment_agencies(author, _RELAY_TABLE) or any(
                _segment_agencies(s, _RELAY_TABLE) for s in _segments(author)):
            return PRESS_RELEASE, "press_release.author_relay"

    # 4. markers in the item text (title + summary)
    raw_text = (title + " \n " + summary).strip()
    folded_text = _fold(raw_text)
    wire = _wire_text_marker(raw_text, folded_text)
    if wire:
        family, agency = wire
        return WIRE, f"{family}.{agency}"
    relay = _relay_marker(raw_text, folded_text)
    if relay:
        return PRESS_RELEASE, relay

    # 5. own reporting: a named byline, nothing else. Absence of markers alone
    # never gets here: an empty author is `unknown.no_signal`.
    if not author:
        return UNKNOWN, "unknown.no_signal"
    own = _self_names(item, source)
    people = 0
    segs = _segments(author)
    if not segs or len(segs) > _MAX_SEGMENTS:
        return UNKNOWN, "unknown.byline_not_a_name"
    others = 0
    for seg in segs:
        if _is_person_name(seg):
            people += 1
        elif _fold(seg) in own:
            continue  # the outlet crediting itself next to a named person
        else:
            others += 1
    if people and not others:
        return OWN_REPORTING, "own_reporting.named_byline"
    if people and others:
        return UNKNOWN, "unknown.byline_mixed_credit"
    # no person at all
    folded_author = _fold(author)
    if folded_author in own or all(_fold(s) in own for s in segs):
        return UNKNOWN, "unknown.newsroom_credit"
    if any(set(_fold(s).split()) & _ORG_WORDS for s in segs) and len(folded_author) <= 40:
        return UNKNOWN, "unknown.newsroom_credit"
    return UNKNOWN, "unknown.byline_not_a_name"


# --------------------------------------------------------------------------
# Batch helper
# --------------------------------------------------------------------------

def _person_names(item: dict) -> set[str]:
    return {_fold(s) for s in _segments(_clean(item.get("author"))) if _is_person_name(s)}


def _outlet_key(item: dict, source: dict) -> str:
    """The institution a byline appeared under (sister feeds share one)."""
    for obj in (item, source):
        val = obj.get("institution")
        if isinstance(val, str) and val.strip():
            return val.strip().casefold()
    sid = item.get("source_id")
    return sid if isinstance(sid, str) else ""


def classify_batch(items: list, sources: dict | list | None = None) -> dict[str, tuple[str, str]]:
    """Classify many items. `sources` is a dict keyed by source id or a list of
    source dicts (as loaded from sources.yaml). Returns {item id: (class, rule)}
    with ids in sorted order (deterministic).

    Two cross-item rules, both strictly conservative: they can only DOWNGRADE
    an `own_reporting` verdict to `unknown`, never promote anything.

    * `unknown.byline_seen_with_wire`: the same person byline appears *with an
      explicit wire credit* ("Name, La Presse Canadienne") elsewhere in the
      batch, so a bare "Name" is probably wire copy that lost its credit.
    * `unknown.byline_multi_outlet`: the same person byline appears under two or
      more distinct institutions in the batch (a wire or syndicated reporter
      writes for several independent outlets; a staff reporter does not).

    The missing credit is never reconstructed as `wire`: absence is not
    inference. Items without a string id are classified but cannot be keyed;
    they are skipped from the result.
    """
    if isinstance(sources, list):
        by_id = {s.get("id"): s for s in sources if isinstance(s, dict) and isinstance(s.get("id"), str)}
    elif isinstance(sources, dict):
        by_id = {k: v for k, v in sources.items() if isinstance(v, dict)}
    else:
        by_id = {}
    clean_items = [it for it in (items if isinstance(items, list) else []) if isinstance(it, dict)]

    wire_names: set[str] = set()
    outlets_of: dict[str, set[str]] = {}
    first: list[tuple[str, str]] = []
    for it in clean_items:
        sid = it.get("source_id")
        src = by_id.get(sid) if isinstance(sid, str) else None
        src = src or {}
        result = origin_of(it, src)
        first.append(result)
        if result[0] == WIRE and result[1].startswith("wire.author."):
            wire_names |= _person_names(it)
        if result[0] in (OWN_REPORTING, WIRE):
            outlet = _outlet_key(it, src)
            for name in _person_names(it):
                outlets_of.setdefault(name, set()).add(outlet)

    out: dict[str, tuple[str, str]] = {}
    for it, result in zip(clean_items, first):
        iid = it.get("id")
        if not isinstance(iid, str):
            continue
        if result == (OWN_REPORTING, "own_reporting.named_byline"):
            names = _person_names(it)
            if names & wire_names:
                result = (UNKNOWN, "unknown.byline_seen_with_wire")
            elif any(len(outlets_of.get(n, ())) >= 2 for n in names):
                result = (UNKNOWN, "unknown.byline_multi_outlet")
        out[iid] = result
    return {k: out[k] for k in sorted(out)}


def tally(classified: dict[str, tuple[str, str]], items: list, by: str = "source_id") -> dict[str, dict[str, int]]:
    """Counts per `by` value (default source id) per class, keys sorted. Pure
    counts, safe to publish."""
    table: dict[str, dict[str, int]] = {}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not isinstance(it.get("id"), str):
            continue
        res = classified.get(it["id"])
        if res is None:
            continue
        key = it.get(by) if isinstance(it.get(by), str) else "unknown"
        row = table.setdefault(key, {c: 0 for c in CLASSES})
        row[res[0]] += 1
    return {k: table[k] for k in sorted(table)}
