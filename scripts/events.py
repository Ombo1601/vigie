"""Event builder — docs/EVENTS.md, docs/MIGRATION.md step 5. SHADOW ONLY.

One real-world happening = every item Vigie collected about it, whatever the
outlet or the language, including happenings a single source covered. This
stage attaches the current edition's items to events with the production
matcher (`event_match.attach`: tiered, sticky, average link, merge rule) and
keeps, per event, Vigie's own codes and counts. It writes nothing under
`public/`; nothing reads its output yet.

Inputs (all local, no network):
  data/normalized/latest_enriched.json   the current edition; its clock is
                                         `normalized_at`, never a wall clock
  data/events/store.json                 the previous event store (private)
  sources.yaml, takedowns.yaml           institutions, ownership, R10
  data/issues/latest_issues.json         dossiers (overlap counts only)
  data/roadworks/latest_roadworks.json, data/civic/latest_consultations.json
                                         anchor inputs (only when scripts/anchors.py exists)

Outputs (atomic, through store_io; sorted keys; identical inputs, identical bytes):
  data/events/store.json          private: ids, member rows (times, classes,
                                  URLs while the source is enabled), codes,
                                  typed facts, lineage. Never a title, an
                                  excerpt, a quote or a person's name.
  data/events/latest_events.json  the current-edition view (events with a
                                  member in this edition), same law
  data/ops/events_shadow.json     counts only

House law carried here
  * Identity (EVENTS.md section 4): `ev-` + sha256("event-v1|" + founding item
    id)[:16], minted once by the matcher and read back from the store, never
    recomputed from membership. Outlets joining, leaving, editing a URL or
    opting out never move an id; membership is sticky.
  * Texts are the current edition only (titles and excerpts never enter the
    store), so an earlier member whose item left the feeds keeps its
    membership and is no longer compared. Replaying the stamped snapshots
    from an empty store runs this exact function and reproduces the store.
  * Per member, what is derived from its text is derived once, when it joins,
    and kept as codes: type scores (vocabulaire), named place codes, the
    enrich geo, typed fact atoms (facts.py; integers, ISO dates, codes,
    never a verbatim span), origin class and rule (origin.py).
  * Every derived field of a surviving event describes its effective coverage:
    its own members plus those of the events merged into it
    (`lineage.absorbed`). `members` lists only the rows the event owns: an
    item belongs to one event, once.
  * R10: a member withdrawn by takedowns.yaml loses its URL and its display
    (the view omits it) in the same run; the event id and its counts stand.
    A URL is kept only while its source is enabled. This holds in a run that
    does not rebuild the store too (see below): the withdrawn URLs are
    removed from the store as it stands.
  * Fail-soft: any fault prints a diagnosis and exits 0. A run that does not
    rebuild the store (no readable edition, a damaged store, an edition older
    than the store, sources.yaml unreadable, any fault in the build) clears
    the current-edition view, records its status and reason in the ledger,
    and applies R10 to the store; the store is otherwise left as it was (a
    damaged one for repair). `check_store` checks every field the builder
    reads back, so damage is refused before the build, never half-trusted.
    An absent store is a first run.
  * Anchors (EVENTS.md section 10): scripts/anchors.py when present, given
    each live member's stored row joined in memory with its current-edition
    text and its source kind (texts are never stored) and the event's own
    type; otherwise the membership rule alone, under the module's own rule
    name. One row per (type, ref); a pointer found again replaces the stored
    row, a pointer to a withdrawn item goes in the same run.
  * Retention (EVENTS.md section 13): the newest 400 editions; media fact
    values only while the event is in window, then reduced to counts per
    slot (values stated by an official member are kept).

Shadow extensions beyond the event-v1 schema (EVENTS.md section 14), kept in
the same object under EXT_KEYS: family, geo (best member geo, for ranking),
place_basis, tier, neighbours (ids and integer scores), withdrawn, copies
(near-duplicate id pairs), facts_reduced, facts_state; plus
`lineage.merged_at` and `independence.declarations/reporting_count` (open
objects in the schema). `to_v1()` projects an event onto the schema. The
current-edition view adds member_count, in_edition (member ids whose text is
in this edition) and silence (the followed institutions without a member).

    python -X utf8 scripts/events.py                     # one edition (pipeline step)
    python -X utf8 scripts/events.py --replay DIR        # every DIR/*_candidates.json, from an empty store
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cluster_issues  # noqa: E402
import enrich  # noqa: E402
import event_match as em  # noqa: E402
import facts  # noqa: E402
import ingest_rss  # noqa: E402
import origin  # noqa: E402
import ownership  # noqa: E402
import store_io  # noqa: E402
import takedown  # noqa: E402
import vocabulaire  # noqa: E402

DATA = ROOT / "data"
IN_PATH = DATA / "normalized" / "latest_enriched.json"
SOURCES_PATH = ROOT / "sources.yaml"
TAKEDOWNS_PATH = ROOT / "takedowns.yaml"

SCHEMA = 1
STORE_FORMAT = "events-store-v1"
METHOD = "events-v1 rules r1 (event-match-v1, vocabulaire, origin, ownership, facts-v1)"
RETENTION_EDITIONS = 400
PUBLICATION_WINDOW_DAYS = cluster_issues.MAX_AGE_DAYS   # older items found no event
DATE_SUSPECT_HOURS = 6
ITEM_ID = re.compile(r"^[0-9a-f]{16,64}$")
EVENT_ID = re.compile(r"^ev-[0-9a-f]{16}$")

WINDOW_STATES = ("in_window", "out_of_window")
ACTIVITIES = ("new", "developed", "quiet")
ORIGIN_CLASSES = origin.CLASSES
OWNERSHIP_CLASSES = ownership.CLASSES          # the schema's five plus "unverified"
ANCHOR_TYPES = ("official_item", "roadwork", "consultation", "outage", "edition_seal")
SILENCE_STATES = ("no_linked_item", "collection_gap", "not_established")
GEO_RANK = {"quebec-city": 0, "quebec": 1, "linked": 2}

V1_KEYS = ("event_id", "schema", "method", "type", "places", "label", "born_edition",
           "last_edition", "window_state", "activity", "members", "institutions", "languages",
           "independence", "facts", "anchors", "language_pairs", "lineage", "seals")
EXT_KEYS = ("family", "geo", "place_basis", "tier", "neighbours", "withdrawn", "copies",
            "facts_reduced", "facts_state")
MEMBER_KEYS = ("item_id", "institution", "source_id", "language", "published_at", "first_seen",
               "origin_class", "origin_rule", "ownership_class", "owner_group", "url", "date_suspect")

PAIR_RULE_FEATURES = "bilingual-complete-link"   # cluster_issues.features_match, bilingual branch
PAIR_RULE_MATCHER = "event-match-fr-en-guard"    # matcher tier >= probable, FR/EN guard satisfied
ANCHOR_RULE_MEMBERSHIP = "membership-official-source"   # = anchors.RULE_OFFICIAL: one name for one rule
ITEM_ANCHORS = ("official_item", "outage")              # anchor types whose ref is an item id


class StoreUnreadable(Exception):
    """The previous store exists but cannot be trusted: no events this run."""


class EditionRefused(Exception):
    """The edition has no usable clock, or is older than the store's newest edition."""


def _say(message: str) -> None:
    print(f"events: {message}", flush=True)


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def edition_clock(payload: dict) -> str | None:
    """The edition's collection clock (`normalized_at`), UTC ISO, or None."""
    raw = payload.get("normalized_at") if isinstance(payload, dict) else None
    dt = em._clock(raw) if isinstance(raw, str) else None
    return _iso(dt) if dt is not None else None


def _e4(score: float) -> int:
    """A 0..1 score as an integer in ten-thousandths (no float is stored)."""
    return int(round(float(score) * 10000))


def _tier_of(score_e4: int) -> str | None:
    return em.tier(score_e4 / 10000.0)


def _lower_tier(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if em.TIER_RANK[a] <= em.TIER_RANK[b] else b


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def _dump(doc, compact: bool = False) -> str:
    if compact:
        return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


# --------------------------------------------------------------------------- #
# Sources and takedowns
# --------------------------------------------------------------------------- #
class Registry:
    """sources.yaml (every entry) plus the active takedown rules."""

    def __init__(self, records: list[dict], rules: takedown.Rules | None = None):
        self.rules = rules if rules is not None else takedown.Rules([])
        self.by_id: dict[str, dict] = {}
        for rec in records or []:
            if isinstance(rec, dict) and rec.get("id"):
                self.by_id.setdefault(str(rec["id"]), rec)
        # Followed = enabled RSS feeds minus withdrawn ones (ingest_rss.load_enabled_by_type).
        self.enabled: set[str] = {
            sid for sid, rec in self.by_id.items()
            if rec.get("enabled") is True and rec.get("type") == "rss" and rec.get("url")
            and self.rules.match_source(rec) is None
        }
        # An item id is sha256(canonical URL)[:24] (normalize.stable_id) and a
        # `url` takedown value is already canonical, so a withdrawn article is
        # recognised by its id even after its URL left the store.
        self.withdrawn_item_ids: set[str] = {
            hashlib.sha256(value.encode("utf-8")).hexdigest()[:24] for value in sorted(self.rules.urls)
        }
        self._followed: list[dict] | None = None

    @classmethod
    def load(cls, sources_path: Path = SOURCES_PATH, takedowns_path: Path = TAKEDOWNS_PATH) -> "Registry":
        records = ingest_rss.load_sources(Path(sources_path))
        entries, errors = takedown.load(Path(takedowns_path))
        for err in errors:
            _say(f"takedowns: {err}")
        return cls(records, takedown.Rules(entries))

    def followed_institutions(self) -> list[dict]:
        if self._followed is None:
            chancellery = [self.by_id[s] for s in sorted(self.enabled)]
            self._followed = cluster_issues.collapse_institutions(chancellery)
        return self._followed

    def institution_of(self, sid: str, item: dict | None = None) -> str:
        rec = self.by_id.get(sid)
        if rec is not None:
            return cluster_issues.institution_of(rec)
        return _str((item or {}).get("institution")) or sid

    def withdrawn(self, row: dict, host: str = "") -> bool:
        """R10: the member's source, its feed's domain, its article domain
        (`host`, kept privately per item) or its URL is withdrawn."""
        if not self.rules:
            return False
        sid = _str(row.get("source_id"))
        if sid in self.rules.sources or _str(row.get("item_id")) in self.withdrawn_item_ids:
            return True
        rec = self.by_id.get(sid)
        if rec is not None and self.rules.match_source(rec) is not None:
            return True
        if host and self.rules.match_host("https://" + host + "/") is not None:
            return True
        return bool(row.get("url")) and self.rules.match_url(row.get("url")) is not None


# --------------------------------------------------------------------------- #
# Per-item derivation (once, when the item joins; codes only)
# --------------------------------------------------------------------------- #
def item_view(c: dict, reg: Registry, edition: str) -> dict:
    """The candidate as the matcher and the classifiers read it (text included:
    never stored). Source facts come from sources.yaml, the declared language
    included (docs/I18N.md section 3: never guessed per item)."""
    out = dict(c)
    rec = reg.by_id.get(_str(c.get("source_id"))) or {}
    for key in ("institution_name", "source_kind", "nest_role", "geo", "language"):
        if rec.get(key):
            out[key] = rec[key]
    out["institution"] = reg.institution_of(_str(c.get("source_id")), c)
    out["first_seen"] = edition
    return out


def item_geo(item: dict) -> str:
    """enrich's proposed geo (quebec-city / quebec / linked); computed with
    enrich's own rule when the input was not enriched (a replayed snapshot)."""
    block = item.get("enrich") if isinstance(item.get("enrich"), dict) else None
    geo = block.get("geo") if block else None
    if isinstance(geo, dict) and isinstance(geo.get("geo"), str):
        return geo["geo"]
    try:
        return str(enrich.propose_geo(item, enrich.blob(item)).get("geo") or "unknown")
    except Exception:  # noqa: BLE001 - a geo fault is an unknown geo, never a crash
        return "unknown"


def item_places(item: dict) -> list[str]:
    """Place codes the headline names: lexicon rules, cluster_issues place
    hints and road names mapped onto codes; most specific first."""
    title = _str(item.get("title"))
    lang = em.language_of(item)
    found = set(vocabulaire.classify_places([(title, lang)] if lang else [title]))
    probe = {"title": title}
    for hint in sorted(cluster_issues.place_hints(probe)):
        code = vocabulaire.place_for_hint(hint)
        if code:
            found.add(code)
    for token in sorted(cluster_issues.road_places(probe)):
        code = vocabulaire.place_for_road(token)
        if code:
            found.add(code)
    return sorted(found, key=vocabulaire.specificity_key)


def item_type_scores(item: dict) -> dict[str, int]:
    """This headline's score per type (vocabulaire.explain_type over one title)."""
    return {row["type"]: int(row["score"]) for row in vocabulaire.explain_type([_str(item.get("title"))])}


def article_host(url: object) -> str:
    """The article's domain (no path): what a `host` takedown matches after the
    URL itself has left the store. A domain is not publisher text."""
    try:
        host = (urlsplit(_str(url).strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def derive_item(item: dict, diagnosis: list[str] | None = None) -> dict:
    """The item's codes. A classifier fault on one item costs that item its
    codes (diagnosed), never the edition its events."""
    out: dict = {}
    for key, fn, empty in (("type_scores", item_type_scores, {}), ("places", item_places, []),
                           ("geo", item_geo, "unknown"), ("facts", facts.extract_item, [])):
        try:
            out[key] = fn(item)
        except Exception as exc:  # noqa: BLE001 - per-item fail-soft
            out[key] = empty
            if diagnosis is not None:
                msg = f"{key} fault on one item ({type(exc).__name__}); its {key} left empty"
                if msg not in diagnosis:
                    diagnosis.append(msg)
    out["host"] = article_host(item.get("url"))
    return out


def published_fields(raw: object, first_seen: str) -> tuple[str | None, bool]:
    """(published_at, date_suspect). Null when absent or implausible (never
    guessed); suspect when a date was given but is implausible, or when it is
    more than 6 h after the first collection (kept as given, never corrected)."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return None, False
    when = em.published_when({"published_at": text})
    if when is None:
        return None, True
    seen = em._clock(first_seen)
    suspect = seen is not None and when - seen > timedelta(hours=DATE_SUSPECT_HOURS)
    return _iso(when), bool(suspect)


def member_row(item: dict, joined: dict, origin_result: tuple[str, str], reg: Registry) -> dict:
    sid = _str(item.get("source_id"))
    first_seen = _str(joined.get("first_seen")) or _str(item.get("first_seen"))
    published, suspect = published_fields(item.get("published_at"), first_seen)
    row = {
        "item_id": _str(item.get("id")),
        "institution": _str(item.get("institution")) or sid,
        "source_id": sid,
        "language": em.language_of(item) or "fr",
        "published_at": published,
        "first_seen": first_seen,
        "origin_class": origin_result[0],
        "origin_rule": origin_result[1],
        "date_suspect": suspect,
    }
    url = _str(item.get("url"))
    if url:
        row["url"] = url
    _refresh_ownership(row, reg)
    return row


def _refresh_ownership(row: dict, reg: Registry) -> None:
    """Ownership is the current sources.yaml declaration (a corrected row
    propagates); a source no longer listed keeps what was recorded."""
    sid = _str(row.get("source_id"))
    if sid in reg.by_id:
        row["ownership_class"] = ownership.ownership_class_of(sid, reg.by_id)
        row["owner_group"] = ownership.owner_group_of(sid, reg.by_id) or ""
    else:
        row.setdefault("ownership_class", ownership.UNVERIFIED)
        row.setdefault("owner_group", "")


# --------------------------------------------------------------------------- #
# Event-level derivation
# --------------------------------------------------------------------------- #
def event_type(ids: list[str], items: dict) -> str:
    """Sum of the members' headline scores; the best type by score, then table
    order (vocabulaire.classify_type over the member titles, from stored codes)."""
    totals: dict[str, int] = {}
    for i in sorted(ids):
        for code, weight in sorted(((items.get(i) or {}).get("type_scores") or {}).items()):
            if isinstance(weight, int):
                totals[code] = totals.get(code, 0) + weight
    if not totals:
        return vocabulaire.UNCLASSIFIED
    order = vocabulaire.vocabulary()["type_order"]
    best = min(totals, key=lambda c: (-totals[c], order.get(c, 10 ** 6), c))
    return best if totals[best] >= vocabulaire.MIN_SCORE else vocabulaire.UNCLASSIFIED


CITY_SCOPE = "quebec-city"


def event_places(ids: list[str], items: dict) -> tuple[list[str], str]:
    """(places, basis). Named specific places first (quartier > arrondissement >
    site > corridor > neighbour); then scope codes. A member whose enrich geo
    says Québec City (strict city evidence) puts the city scope before any
    scope its headline names, so an item about the city is never labelled with
    the bare scope "ottawa" because its headline mentions Canada. A scope the
    headlines name comes before the province geo (that one is mostly the
    source's own nest, weaker than the text). Never empty: no place at all
    falls back to a scope code with basis "fallback" (the label then names
    no place)."""
    specific: set[str] = set()
    scope_text: set[str] = set()
    scope_geo: set[str] = set()
    for i in sorted(ids):
        entry = items.get(i) or {}
        for code in entry.get("places") or []:
            if not isinstance(code, str) or vocabulaire.place_kind(code) is None:
                continue
            (scope_text if vocabulaire.is_scope(code) else specific).add(code)
        geo_code = vocabulaire.place_from_geo(_str(entry.get("geo")))
        if geo_code:
            scope_geo.add(geo_code)
    key = vocabulaire.specificity_key
    city = [CITY_SCOPE] if CITY_SCOPE in scope_geo else []
    named_scopes = sorted(scope_text - set(city), key=key)
    other_geo = sorted(scope_geo - set(city) - scope_text, key=key)
    places = sorted(specific, key=key) + city + named_scopes + other_geo
    if specific:
        basis = "named"
    elif city:
        basis = "geo"
    elif named_scopes:
        basis = "named"
    elif other_geo:
        basis = "geo"
    else:
        return [vocabulaire.FALLBACK_PLACE], "fallback"
    return places, basis


def event_label(type_code: str, places: list[str], basis: str) -> dict:
    place = None if basis == "fallback" else places[0]
    return {lang: vocabulaire.label(type_code, place, lang)[:120] for lang in vocabulaire.LANGS}


def event_geo(ids: list[str], items: dict) -> str:
    geos = {_str((items.get(i) or {}).get("geo")) for i in ids}
    ranked = sorted((g for g in geos if g in GEO_RANK), key=lambda g: GEO_RANK[g])
    return ranked[0] if ranked else "unknown"


def independence(ids: list[str], rows: dict[str, dict], copies: list | None = None) -> dict:
    """Union-find over the members (EVENTS.md section 8, docs/AUTONOMY.md), in
    sorted order.

    Each member carries one key: a wire member the agency that wrote it
    (`wire:<agency>`), a press-release relay its issuer (unknown to the rules
    today, so `release:unknown-issuer`), any other member its owner group
    (`owner:<group>`; Radio-Canada and CBC are one group, Quebecor titles one
    group), or its institution when no sourced owner is declared. Members
    sharing a key are one group; so are near-duplicate copies (`copies`,
    pairs found by `near_duplicate`, whoever owns them). Counted, never judged.

    `count` is every group (the schema's figure). Official members are
    declarations, never corroboration of the reporting: they are listed in
    `declarations`, and `reporting_count` counts only the groups without one."""
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    first: dict[str, str] = {}
    for i in sorted(ids):
        row = rows.get(i) or {}
        cls = _str(row.get("origin_class"))
        if cls == origin.WIRE:
            key = "wire:" + (_str(row.get("origin_rule")).rsplit(".", 1)[-1] or "unknown")
        elif cls == origin.PRESS_RELEASE:
            key = "release:unknown-issuer"
        elif _str(row.get("owner_group")):
            key = "owner:" + row["owner_group"]
        else:
            key = "institution:" + (_str(row.get("institution")) or _str(row.get("source_id")) or i)
        if key in first:
            union(i, first[key])
        else:
            first[key] = i
    for pair in sorted(tuple(p) for p in copies or [] if isinstance(p, (list, tuple)) and len(p) == 2):
        if pair[0] in parent and pair[1] in parent:
            union(pair[0], pair[1])
    groups: dict[str, list[str]] = {}
    for i in sorted(ids):
        groups.setdefault(find(i), []).append(i)
    out = sorted(sorted(g) for g in groups.values())
    declarations = sorted(i for i in ids if _str((rows.get(i) or {}).get("origin_class")) == origin.OFFICIAL)
    reporting = sum(1 for g in out if not set(g) & set(declarations))
    return {"groups": out, "count": max(1, len(out)), "declarations": declarations, "reporting_count": reporting}


# Near-duplicate copy (docs/AUTONOMY.md): two members whose texts share most
# of their word shingles are one origin, whoever owns them. Published bound:
# Jaccard of word 3-shingles over the folded headline and the first 400
# characters of the excerpt >= 3/5, each side carrying at least 6 shingles.
# Compared at build time only; only the pair of ids is kept (sticky, like the
# language pairs: found while both texts were current, it stays).
COPY_SHINGLE = 3
COPY_BOUND = (3, 5)
COPY_MIN_SHINGLES = 6


def shingles(item: dict) -> set[tuple[str, ...]]:
    text = vocabulaire.fold(_str(item.get("title")) + " " + _str(item.get("summary"))[:em.SUMMARY_CHARS])
    words = text.split()
    return {tuple(words[k:k + COPY_SHINGLE]) for k in range(max(0, len(words) - COPY_SHINGLE + 1))}


def near_duplicate(a: dict, b: dict) -> bool:
    sa, sb = shingles(a), shingles(b)
    if len(sa) < COPY_MIN_SHINGLES or len(sb) < COPY_MIN_SHINGLES:
        return False
    num, den = COPY_BOUND
    return den * len(sa & sb) >= num * len(sa | sb)


def slots_from_atoms(rows: list[tuple[str, str, list[dict]]]) -> list[dict]:
    """facts.build_slots over stored atoms: (item_id, institution, atoms) rows.

    Same grouping, ordering, attribution and divergence rule as
    facts.build_slots(items) (a test holds the equivalence); it exists because
    the event store keeps typed atoms, never the texts build_slots re-reads."""
    slots: dict[tuple, dict] = {}
    for item_id, inst, atoms in rows:
        for a in atoms or []:
            if not isinstance(a, dict) or a.get("kind") not in facts.KINDS:
                continue
            slot = (a["kind"], _str(a.get("unit")), _str(a.get("subject")))
            entry = slots.setdefault(slot, {}).setdefault(
                a.get("value"), {"stated_by": set(), "institutions": set(), "qualifiers": set()})
            entry["stated_by"].add(item_id)
            if inst:
                entry["institutions"].add(inst)
            entry["qualifiers"].update(str(q) for q in a.get("qualifiers") or [])
    out = []
    for slot in sorted(slots, key=lambda s: (facts.KINDS.index(s[0]), s[1], s[2])):
        kind, unit, subject = slot
        values = []
        for value in sorted(slots[slot], key=lambda v: (isinstance(v, str), v)):
            e = slots[slot][value]
            row = {"value": value, "stated_by": sorted(e["stated_by"]), "institutions": sorted(e["institutions"])}
            if kind in ("count", "amount"):
                row["qualifiers"] = sorted(e["qualifiers"])
            values.append(row)
        subject_out = subject or None
        comparable = facts._is_comparable(kind, subject_out)
        stating = {i for v in values for i in v["stated_by"]}
        out.append({
            "slot": {"kind": kind, "unit": unit, "subject": subject_out},
            "values": values,
            "divergent": bool(comparable and len(values) >= 2 and len(stating) >= 2),
            "comparable": comparable,
            "method": facts.METHOD,
            "status": facts.STATUS,
        })
    return out


def reduce_facts(rows: list[dict], official_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """Out of window (EVENTS.md section 13): values stated by an official
    member are kept; media-only values become counts per slot."""
    kept, reduced = [], []
    for row in rows:
        values = [v for v in row.get("values") or [] if set(v.get("stated_by") or []) & official_ids]
        dropped = [v for v in row.get("values") or [] if not set(v.get("stated_by") or []) & official_ids]
        if values:
            kept.append({**row, "values": values})
        if dropped:
            reduced.append({"slot": row.get("slot"), "values": len(dropped),
                            "statements": sum(len(v.get("stated_by") or []) for v in dropped)})
    return kept, reduced


def _without(rows: list[dict], gone: set[str]) -> list[dict]:
    """Fact rows with withdrawn items removed from every attribution (R10)."""
    if not gone:
        return rows
    out = []
    for row in rows:
        values = []
        for v in row.get("values") or []:
            stated = [i for i in v.get("stated_by") or [] if i not in gone]
            if stated:
                values.append({**v, "stated_by": stated})
        if values:
            out.append({**row, "values": values})
    return out


# --------------------------------------------------------------------------- #
# Anchors (EVENTS.md section 10) — hook for scripts/anchors.py
# --------------------------------------------------------------------------- #
_ANCHORS_MODULE: list = []


def anchors_module():
    """scripts/anchors.py when it exists (contract: find_anchors(members,
    official_items, roadworks, consultations, outages, vocab_places, *,
    event_type=None) -> list of {type, ref, rule, status}); None while it does
    not. Cached, with whether its find_anchors takes `event_type`."""
    if not _ANCHORS_MODULE:
        try:
            mod = importlib.import_module("anchors")
        except ImportError:
            mod = None
        fn = getattr(mod, "find_anchors", None)
        _ANCHORS_MODULE.append(mod if callable(fn) else None)
        _ANCHORS_MODULE.append(callable(fn) and _takes_event_type(fn))
    return _ANCHORS_MODULE[0]


def _takes_event_type(fn) -> bool:
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind is p.VAR_KEYWORD
               or (p.name == "event_type" and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)) for p in params)


def membership_rule() -> str:
    """The rule name of an official member's own anchor: the module's
    RULE_OFFICIAL when it declares one, the same name otherwise, so a row
    stored before the module and one found by it are the same row."""
    name = getattr(anchors_module(), "RULE_OFFICIAL", None)
    return name if isinstance(name, str) and name else ANCHOR_RULE_MEMBERSHIP


def membership_anchors(members: list[dict]) -> list[dict]:
    """The one anchor rule that needs no other store: a member from an
    official source is a pointer to the official record itself."""
    rule = membership_rule()
    return [{"type": "official_item", "ref": m["item_id"], "rule": rule, "status": "linked_by_rule"}
            for m in members if m.get("source_kind") == "official" or m.get("origin_class") == origin.OFFICIAL]


def clean_anchors(raw) -> list[dict]:
    """Valid rows, one per (type, ref) as EVENTS.md section 10 and anchors.py
    keep them (when two rules give the same pointer, the first rule name in
    sort order stands), sorted by (type, ref)."""
    out: dict[tuple[str, str], dict] = {}
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict) or a.get("type") not in ANCHOR_TYPES:
            continue
        ref, rule = _str(a.get("ref")), _str(a.get("rule"))
        if not ref or not rule:
            continue
        key = (a["type"], ref)
        if key not in out or rule < out[key]["rule"]:
            out[key] = {"type": a["type"], "ref": ref, "rule": rule, "status": "linked_by_rule"}
    return [out[k] for k in sorted(out)]


def merge_anchors(stored: list[dict], found: list[dict]) -> list[dict]:
    """A pointer found this edition replaces the stored row for the same
    (type, ref) (a renamed or refined rule); a stored pointer that is not
    found again stays (its record may have left the feed since)."""
    out = {(a["type"], a["ref"]): a for a in clean_anchors(stored)}
    out.update({(a["type"], a["ref"]): a for a in clean_anchors(found)})
    return [out[k] for k in sorted(out)]


def _input_ids(rows) -> set[str]:
    rows = rows.get("events") if isinstance(rows, dict) else rows
    return {str(r["event_id"]) for r in rows or [] if isinstance(r, dict) and r.get("event_id")}


def _known_refs(inputs: dict) -> dict[str, set[str]]:
    return {"roadwork": _input_ids(inputs.get("roadworks")), "consultation": _input_ids(inputs.get("consultations"))}


def _ref_ok(a: dict, known: dict[str, set[str]]) -> bool:
    """A pointer names a record: an item id, a declaration or calendar entry
    the module was given, or a seal number. Anything else (a module bug)
    never reaches the store."""
    if a["type"] in ITEM_ANCHORS:
        return bool(ITEM_ID.fullmatch(a["ref"]))
    if a["type"] == "edition_seal":
        return a["ref"].isdigit()
    return a["ref"] in known.get(a["type"], set())


def find_anchors(members: list[dict], places: list[str], event_type: str, inputs: dict,
                 diagnosis: list[str]) -> tuple[list[dict], str]:
    """(anchors, source). `members` are the event's live member rows joined
    with their current-edition text (in memory, never stored) and their
    source kind. scripts/anchors.py when present (given the event's own type
    when it accepts one), else the membership rule alone. A fault in the
    module is diagnosed and falls back."""
    mod = anchors_module()
    if mod is None:
        return clean_anchors(membership_anchors(members)), "membership-only (scripts/anchors.py absent)"
    try:
        args = (members, inputs.get("official_items") or [], inputs.get("roadworks") or [],
                inputs.get("consultations") or [], inputs.get("outages") or [], list(places))
        found = mod.find_anchors(*args, event_type=event_type) if _ANCHORS_MODULE[1] else mod.find_anchors(*args)
        rows = clean_anchors(found)
        known = _known_refs(inputs)
        kept = [a for a in rows if _ref_ok(a, known)]
        if len(kept) < len(rows):
            msg = "anchors.find_anchors returned pointers to no record it was given; dropped"
            if msg not in diagnosis:
                diagnosis.append(msg)
        return kept, "anchors.find_anchors"
    except Exception as exc:  # noqa: BLE001 - an anchor fault never blocks the events
        msg = f"anchors.find_anchors failed ({type(exc).__name__}); membership rule only"
        if msg not in diagnosis:
            diagnosis.append(msg)
        return clean_anchors(membership_anchors(members)), "membership-only (anchors.py fault)"


# --------------------------------------------------------------------------- #
# Store reading
# --------------------------------------------------------------------------- #
def empty_store() -> dict:
    return {"format": STORE_FORMAT, "schema": SCHEMA, "method": METHOD, "editions": [], "events": [], "items": {}}


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value)


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def member_problem(m) -> str | None:
    """Why a stored member row cannot be trusted, or None. The builder writes
    only complete rows (classes included), so a row missing what it reads
    back is a damaged store, never something to guess around."""
    if not isinstance(m, dict) or not ITEM_ID.match(_str(m.get("item_id"))):
        return "a member without a valid item id"
    for key in ("institution", "source_id"):
        if not _nonempty_str(m.get(key)):
            return f"a member without a {key}"
    if m.get("language") not in ("fr", "en"):
        return "a member whose language is not fr or en"
    if not isinstance(m.get("first_seen"), str) or em._clock(m["first_seen"]) is None:
        return "a member without a readable first_seen"
    if m.get("published_at") is not None and not isinstance(m.get("published_at"), str):
        return "a member whose published_at is not a string"
    if m.get("origin_class") not in ORIGIN_CLASSES:
        return "a member without an origin class"
    for key in ("origin_rule", "ownership_class", "owner_group", "url"):
        if key in m and not isinstance(m[key], str):
            return f"a member whose {key} is not a string"
    if "date_suspect" in m and not isinstance(m["date_suspect"], bool):
        return "a member whose date_suspect is not a boolean"
    return None


def item_codes_problem(codes) -> str | None:
    """Why a stored per-item code record cannot be trusted, or None."""
    if not isinstance(codes, dict):
        return "item codes that are not an object"
    scores = codes.get("type_scores", {})
    if not isinstance(scores, dict) or not all(isinstance(k, str) and _int(v) for k, v in scores.items()):
        return "item type scores that are not code -> integer"
    if not _str_list(codes.get("places", [])):
        return "item places that are not a list of codes"
    for key in ("geo", "host"):
        if key in codes and not isinstance(codes[key], str):
            return f"an item {key} that is not a string"
    atoms = codes.get("facts", [])
    if not isinstance(atoms, list) or not all(isinstance(a, dict) for a in atoms):
        return "item facts that are not a list of atoms"
    join = codes.get("join", {})
    if not isinstance(join, dict) or not all(_int(join[k]) for k in ("link_e4", "matching", "compared") if k in join):
        return "an item join record that is not an object of integers"
    return None


def event_problem(e: dict) -> str | None:
    """Why a stored event's carried-over fields cannot be trusted, or None.
    Only what the builder reads back is checked; every derived field is
    recomputed each run."""
    if not em._clock(e.get("born_edition")):
        return "born_edition unreadable"
    if e.get("last_edition") is not None and not em._clock(e.get("last_edition")):
        return "last_edition unreadable"
    lineage = e.get("lineage", {})
    if not isinstance(lineage, dict):
        return "lineage is not an object"
    if not isinstance(lineage.get("merged_into", ""), str) or not isinstance(lineage.get("merged_at", ""), str):
        return "lineage merged_into/merged_at is not a string"
    if not _str_list(lineage.get("absorbed", [])) or not _str_list(lineage.get("detached", [])):
        return "lineage absorbed/detached is not a list of ids"
    for key in ("copies", "language_pairs", "anchors", "facts", "facts_reduced", "neighbours", "seals"):
        if key in e and not isinstance(e[key], list):
            return f"{key} is not a list"
    for key in ("language_pairs", "anchors", "facts", "facts_reduced", "neighbours"):
        if not all(isinstance(x, dict) for x in e.get(key) or []):
            return f"{key} holds a row that is not an object"
    for row in e.get("facts") or []:
        if not isinstance(row.get("values", []), list) or not all(isinstance(v, dict) for v in row.get("values") or []):
            return "facts values that are not a list of objects"
    for p in e.get("language_pairs") or []:
        if not all(isinstance(p.get(k), str) for k in ("fr", "en", "rule")) or not isinstance(p.get("same_owner"), bool):
            return "a language pair that is not {fr, en, rule, same_owner}"
    if e.get("facts_state", "live") not in ("live", "reduced"):
        return "facts_state is neither live nor reduced"
    return None


def check_store(doc) -> dict:
    """The previous store, structurally checked, or StoreUnreadable. Every
    field the builder reads back is checked here, so a damaged store is
    refused before the build (diagnosed), never half-trusted."""
    if not isinstance(doc, dict) or doc.get("format") != STORE_FORMAT:
        raise StoreUnreadable("not an events-store-v1 document")
    events = doc.get("events")
    items = doc.get("items")
    editions = doc.get("editions")
    if not isinstance(events, list) or not isinstance(items, dict) or not isinstance(editions, list):
        raise StoreUnreadable("events/items/editions missing")
    seen_events: set[str] = set()
    seen_items: set[str] = set()
    for e in events:
        if not isinstance(e, dict) or not EVENT_ID.match(_str(e.get("event_id"))):
            raise StoreUnreadable("an event without a valid id")
        if e["event_id"] in seen_events:
            raise StoreUnreadable("a duplicate event id")
        seen_events.add(e["event_id"])
        if not isinstance(e.get("members"), list):
            raise StoreUnreadable(f"{e['event_id']}: members unreadable")
        problem = event_problem(e)
        if problem:
            raise StoreUnreadable(f"{e['event_id']}: {problem}")
        for m in e["members"]:
            problem = member_problem(m)
            if problem:
                raise StoreUnreadable(f"{e['event_id']}: {problem}")
            if m["item_id"] in seen_items:
                raise StoreUnreadable("an item owned by two events")
            seen_items.add(m["item_id"])
    for key, codes in items.items():
        problem = item_codes_problem(codes) if isinstance(key, str) else "an item key that is not an id"
        if problem:
            raise StoreUnreadable(problem)
    if not all(isinstance(x, str) and em._clock(x) for x in editions):
        raise StoreUnreadable("an edition clock is unreadable")
    return doc


def load_store(path: Path) -> dict:
    """{} -> first run. Raises StoreUnreadable on a present but corrupt store."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_store()
    except (OSError, UnicodeDecodeError) as exc:
        raise StoreUnreadable(f"{type(exc).__name__}") from exc
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise StoreUnreadable("invalid JSON") from exc
    return check_store(doc)


# --------------------------------------------------------------------------- #
# The builder (pure: no I/O, no clock)
# --------------------------------------------------------------------------- #
def _roots(events: list[dict]) -> dict[str, str]:
    by_id = {e["event_id"]: e for e in events}
    out = {}
    for eid in sorted(by_id):
        cur, seen = eid, set()
        while True:
            nxt = _str((by_id[cur].get("lineage") or {}).get("merged_into"))
            if nxt not in by_id or cur in seen:
                break
            seen.add(cur)
            cur = nxt
        out[eid] = cur
    return out


def _member_instant(row: dict) -> datetime | None:
    return em._clock(row.get("published_at")) or em._clock(row.get("first_seen"))


def build(previous: dict | None, payload: dict, reg: Registry, *, issues: dict | None = None,
          collection: dict | None = None, anchor_inputs: dict | None = None) -> tuple[dict, dict, dict]:
    """One edition: (store, view, ops). Pure; deterministic; no wall clock.

    previous      the checked previous store (empty_store() on a first run)
    payload       the edition (latest_enriched.json or a stamped snapshot)
    reg           sources.yaml + takedown rules
    issues        latest_issues.json (dossier overlap counts), optional
    collection    registre.institution_collection facts for the silence roster
    anchor_inputs official_items / roadworks / consultations / outages (anchors.py)
    """
    edition = edition_clock(payload)
    if edition is None:
        raise EditionRefused("the edition has no usable normalized_at clock")
    # A copy, checked: whoever calls build gets the same refusal as a store read from disk.
    store = check_store(json.loads(json.dumps(previous))) if previous else empty_store()
    known = sorted(store.get("editions") or [], key=lambda s: em._clock(s))
    if known and em._clock(edition) < em._clock(known[-1]):
        raise EditionRefused(f"edition {edition} is older than the store's newest edition {known[-1]}")
    clock = em._clock(edition)
    diagnosis: list[str] = []

    # ---- eligible items of this edition ------------------------------------
    excluded: dict[str, int] = {}

    def exclude(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    raw = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    texts: dict[str, dict] = {}
    for c in raw:
        if not isinstance(c, dict):
            exclude("malformed_candidate")
            continue
        iid = _str(c.get("id"))
        if not ITEM_ID.match(iid):
            exclude("malformed_id")
            continue
        if iid in texts:
            exclude("duplicate_article")
            continue
        if _str(c.get("source_id")) not in reg.enabled:
            exclude("source_not_enabled")
            continue
        if reg.rules and reg.rules.match_item(c):
            exclude("withdrawn")
            continue
        when = em.published_when(c)
        if when is not None and clock - when > timedelta(days=PUBLICATION_WINDOW_DAYS):
            exclude("publication_outside_window")
            continue
        texts[iid] = item_view(c, reg, edition)
    eligible = [texts[k] for k in sorted(texts)]

    # ---- per-item codes, once ----------------------------------------------
    items: dict[str, dict] = {k: v for k, v in (store.get("items") or {}).items() if isinstance(v, dict)}
    origins = origin.classify_batch(eligible, reg.by_id)
    for it in eligible:
        if it["id"] not in items:
            items[it["id"]] = derive_item(it, diagnosis)

    # ---- attach (sticky, tiered) -------------------------------------------
    prev_events = [e for e in store.get("events") or [] if isinstance(e, dict)]
    registry: dict[str, dict] = {e["event_id"]: e for e in prev_events}

    def live_ids(e: dict, seen: set | None = None) -> list[str]:
        seen = set() if seen is None else seen
        if e["event_id"] in seen:
            return []
        seen.add(e["event_id"])
        out = [em._member_id(m) for m in e.get("members") or []]
        for a in sorted((e.get("lineage") or {}).get("absorbed") or []):
            if a in registry:
                out += live_ids(registry[a], seen)
        return out

    def class_of(e: dict) -> tuple[str, str]:
        ids = live_ids(e)
        places, _ = event_places(ids, items)
        return event_type(ids, items), places[0]

    def compatible(older: dict, younger: dict) -> bool:
        """EVENTS.md section 4.5: two events merge only when they share the
        type and the first place (decided here, on stored codes)."""
        registry[older["event_id"]] = older
        registry[younger["event_id"]] = younger
        return class_of(older) == class_of(younger)

    ctx = em.MatchContext(eligible)
    events, decisions = em.attach(prev_events, eligible, ctx, edition=edition, items_by_id=texts,
                                  compatible=compatible)
    merged_now = {_str(d.get("event_id")) for d in decisions if d.get("action") == "merged"}
    for d in decisions:
        iid = _str(d.get("item_id"))
        if d.get("action") == "minted" and iid in items:
            items[iid]["join"] = {"action": "minted"}
        elif d.get("action") == "attached" and iid in items:
            items[iid]["join"] = {"action": "attached", "link_e4": _e4(d.get("link") or 0.0),
                                  "matching": int(d.get("matching") or 0), "compared": int(d.get("compared") or 0)}

    # ---- member rows -------------------------------------------------------
    rows: dict[str, dict] = {}
    withdrawn_ids: set[str] = set()
    for e in events:
        fixed = []
        for m in e.get("members") or []:
            iid = em._member_id(m)
            if isinstance(m, dict) and "origin_class" in m:
                row = {k: m[k] for k in MEMBER_KEYS if k in m}
                _refresh_ownership(row, reg)
            else:  # joined this edition (a bare stored row is completed from what it carries)
                joined = m if isinstance(m, dict) else {}
                it = texts.get(iid) or {"id": iid, "first_seen": edition,
                                        **{k: joined[k] for k in ("published_at", "source_id", "institution",
                                                                  "language", "url") if joined.get(k)}}
                row = member_row(it, joined, origins.get(iid, (origin.UNKNOWN, "unknown.no_signal")), reg)
            current = texts.get(iid)
            if current is not None and _str(current.get("url")):
                row["url"] = current["url"]
            if reg.withdrawn(row, _str((items.get(iid) or {}).get("host"))):
                withdrawn_ids.add(iid)
                row.pop("url", None)  # R10: no URL and no display, the same run
            elif row.get("source_id") not in reg.enabled:
                row.pop("url", None)  # a cut source keeps its members, never its links
            rows[iid] = row
            fixed.append(row)
        e["members"] = sorted(fixed, key=lambda r: (_str(r.get("first_seen")), _str(r.get("published_at")), r["item_id"]))

    # ---- effective membership ----------------------------------------------
    root = _roots(events)
    by_id = {e["event_id"]: e for e in events}
    eff: dict[str, list[str]] = {}
    for e in events:
        eff.setdefault(root[e["event_id"]], []).extend(r["item_id"] for r in e["members"])
    for k in eff:
        eff[k] = sorted(eff[k])

    def ids_of(eid: str) -> list[str]:
        return eff[eid] if root[eid] == eid else sorted(r["item_id"] for r in by_id[eid]["members"])

    newest: dict[str, datetime | None] = {}
    for r_id, ids in eff.items():
        instants = [t for t in (_member_instant(rows[i]) for i in ids) if t is not None]
        newest[r_id] = max(instants) if instants else None

    def window_of(eid: str) -> str:
        t = newest.get(root[eid])
        if t is None:
            return "in_window"
        return "in_window" if clock - t <= timedelta(days=em.EDITION_WINDOW_DAYS) else "out_of_window"

    present = {r_id for r_id, ids in eff.items() if any(i in texts for i in ids)}
    nbrs = em.neighbours(events, texts, ctx) if texts else {}
    anchor_inputs = dict(anchor_inputs or {})
    if anchors_module() is not None:
        # The edition's own official and Hydro-Québec items, from the
        # in-memory texts (the same in a replay as in a pipeline run).
        official_now = [texts[i] for i in sorted(texts) if _str(texts[i].get("source_kind")) == "official"]
        anchor_inputs.setdefault("official_items", [t["id"] for t in official_now])
        anchor_inputs.setdefault("outages", [t for t in official_now if "hydro" in vocabulaire.fold(
            _str(t.get("institution")) + " " + _str(t.get("source_id")))])
    anchor_source = ""
    gone_items = withdrawn_ids | reg.withdrawn_item_ids

    def anchor_member(i: str) -> dict:
        """The member as anchors.py reads it: its stored row, its text while
        the item is in this edition (in memory only, never stored) and its
        source kind from sources.yaml."""
        rec = reg.by_id.get(rows[i]["source_id"]) or {}
        return {**(texts.get(i) or {}), **rows[i], "source_kind": _str(rec.get("source_kind"))}

    for e in events:
        eid = e["event_id"]
        ids = ids_of(eid)
        live = [i for i in ids if i not in withdrawn_ids]
        e["schema"] = SCHEMA
        e["method"] = METHOD
        e["type"] = event_type(ids, items)
        places, basis = event_places(ids, items)
        e["places"], e["place_basis"] = places, basis
        e["label"] = event_label(e["type"], places, basis)
        e["family"] = vocabulaire.family_of(e["type"]) or ""
        e["geo"] = event_geo(ids, items)
        e["window_state"] = window_of(eid)
        e["activity"] = ("new" if e.get("born_edition") == edition
                         else "developed" if e.get("last_edition") == edition else "quiet")
        e["institutions"] = sorted({rows[i]["institution"] for i in ids})
        e["languages"] = sorted({rows[i]["language"] for i in ids if rows[i].get("language") in ("fr", "en")})
        # Near-duplicate copies (sticky): one origin whoever owns them.
        copies = {tuple(p) for p in e.get("copies") or []
                  if isinstance(p, list) and len(p) == 2 and p[0] in ids and p[1] in ids}
        current = [i for i in ids if i in texts]
        for x in range(len(current)):
            for y in range(x + 1, len(current)):
                a, b = current[x], current[y]
                if (a, b) not in copies and near_duplicate(texts[a], texts[b]):
                    copies.add((a, b))
        e["copies"] = [list(p) for p in sorted(copies)]
        e["independence"] = independence(ids, rows, e["copies"])
        e["withdrawn"] = sorted(set(ids) & withdrawn_ids)
        lineage = dict(e.get("lineage") or {})
        e["lineage"] = {"merged_into": _str(lineage.get("merged_into")),
                        "absorbed": sorted(str(x) for x in lineage.get("absorbed") or []),
                        "detached": sorted(str(x) for x in lineage.get("detached") or [])}
        # The edition of the merge (a collection clock), so a re-run of that
        # edition reports the same counts and a page can say when it merged.
        merged_at = edition if eid in merged_now else _str(lineage.get("merged_at"))
        if e["lineage"]["merged_into"] and merged_at:
            e["lineage"]["merged_at"] = merged_at
        e["seals"] = sorted(int(s) for s in e.get("seals") or [] if isinstance(s, int) and s >= 1)

        # Tier: the weakest link that grouped this event's members.
        tier = None
        if len(ids) > 1:
            for i in ids:
                join = (items.get(i) or {}).get("join") or {}
                if join.get("action") == "attached":
                    tier = _lower_tier(tier, _tier_of(int(join.get("link_e4") or 0)) or "probable")
            if e["lineage"]["absorbed"] or tier is None:
                tier = _lower_tier(tier, "probable")
        e["tier"] = tier

        # Facts: live while in window; reduced once out of it.
        official = {i for i in ids if rows[i].get("origin_class") == origin.OFFICIAL}
        if e.get("facts_state") == "reduced":
            e["facts"] = _without(e.get("facts") or [], withdrawn_ids)
        else:
            live_facts = slots_from_atoms([(i, rows[i]["institution"], (items.get(i) or {}).get("facts") or [])
                                           for i in live])
            if e["window_state"] == "out_of_window":
                e["facts"], e["facts_reduced"] = reduce_facts(live_facts, official)
                e["facts_state"] = "reduced"
            else:
                e["facts"], e["facts_state"] = live_facts, "live"
        e.setdefault("facts_reduced", [])

        # Language pairs (sticky: a pair found while both texts were current stays).
        pairs = {(p["fr"], p["en"]): p for p in e.get("language_pairs") or []
                 if isinstance(p, dict) and p.get("fr") in ids and p.get("en") in ids}
        fr_ids = [i for i in ids if i in texts and rows[i]["language"] == "fr"]
        en_ids = [i for i in ids if i in texts and rows[i]["language"] == "en"]
        for f in fr_ids:
            for n in en_ids:
                if (f, n) in pairs:
                    continue
                rule = None
                if cluster_issues.same_event(texts[f], texts[n]):
                    rule = PAIR_RULE_FEATURES
                elif em.at_least(em.pair_tier(texts[f], texts[n], ctx)[1], em.MERGE_TIER):
                    rule = PAIR_RULE_MATCHER
                if rule:
                    same = bool(rows[f].get("owner_group")) and rows[f].get("owner_group") == rows[n].get("owner_group")
                    pairs[(f, n)] = {"fr": f, "en": n, "rule": rule, "same_owner": same}
        e["language_pairs"] = [pairs[k] for k in sorted(pairs)]

        # Anchors: computed while the event is in this edition; kept otherwise.
        # A pointer to a withdrawn item goes in the same run (R10).
        stored = [a for a in clean_anchors(e.get("anchors"))
                  if not (a["type"] in ITEM_ANCHORS and a["ref"] in gone_items)]
        if root[eid] in present or anchors_module() is None:
            found, anchor_source = find_anchors([anchor_member(i) for i in live], places, e["type"],
                                                anchor_inputs, diagnosis)
            stored = merge_anchors(stored, [a for a in found if not (a["type"] in ITEM_ANCHORS
                                                                     and a["ref"] in gone_items)])
        e["anchors"] = stored

        # Neighbours (possible tier and above, never merged): ids and scores only.
        if root[eid] != eid:
            e["neighbours"] = []  # a merged event is shown through its survivor
        elif eid in present:
            e["neighbours"] = [{"event_id": o, "score_e4": _e4(s)} for o, s in nbrs.get(eid, [])]
        else:
            e.setdefault("neighbours", [])  # texts gone: the last measured neighbours stand

    # ---- retention -----------------------------------------------------------
    editions = sorted(set(known) | {edition}, key=lambda s: em._clock(s))[-RETENTION_EDITIONS:]
    horizon = em._clock(editions[0])
    # An event goes with its survivor, so a survivor never loses an absorbed
    # event's members (the younger's last edition is never after the older's).
    kept_roots = {eid for eid in by_id if root[eid] == eid
                  and (em._clock(by_id[eid].get("last_edition")) or clock) >= horizon}
    kept_events = [e for e in events if root[e["event_id"]] in kept_roots]
    kept_ids = {r["item_id"] for e in kept_events for r in e["members"]}
    for e in kept_events:
        if e["window_state"] == "out_of_window":
            for r in e["members"]:
                (items.get(r["item_id"]) or {}).pop("facts", None)
    items = {k: items[k] for k in sorted(items) if k in kept_ids}
    kept_events.sort(key=lambda e: (_str(e.get("born_edition")), e["event_id"]))
    store = {"format": STORE_FORMAT, "schema": SCHEMA, "method": METHOD, "editions": editions,
             "events": [_ordered(e) for e in kept_events], "items": items}

    view = _view(store, edition, texts, rows, withdrawn_ids, reg, collection)
    ops = _ops(store, view, edition, raw, eligible, excluded, decisions, issues, anchor_source, diagnosis)
    return store, view, ops


def _ordered(e: dict) -> dict:
    out = {k: e[k] for k in V1_KEYS if k in e}
    for k in EXT_KEYS:
        if k in e:
            out[k] = e[k]
    return out


def to_v1(e: dict) -> dict:
    """The event-v1 object (EVENTS.md section 14): shadow extensions dropped."""
    return {k: e[k] for k in V1_KEYS if k in e}


# --------------------------------------------------------------------------- #
# The current-edition view and the shadow counts
# --------------------------------------------------------------------------- #
def silence_roster(institutions: list[str], reg: Registry, collection: dict | None) -> list[dict]:
    """Followed institutions with no member in this event, each with the
    collection state measured this edition: our fetch failed
    (`collection_gap`), else simply no linked article (`no_linked_item`)."""
    have = set(institutions)
    out = []
    for inst in reg.followed_institutions():
        iid = inst["institution_id"]
        if iid in have:
            continue
        state = "no_linked_item"
        f = (collection or {}).get(iid)
        if isinstance(f, dict):
            total, ok = int(f.get("feeds_total") or 0), int(f.get("feeds_ok") or 0)
            if total and ok < total:
                state = "collection_gap"
        out.append({"institution": iid, "institution_name": str(inst.get("institution_name") or iid),
                    "state": state})
    return sorted(out, key=lambda r: r["institution"])


def _view(store: dict, edition: str, texts: dict, rows: dict, withdrawn_ids: set[str], reg: Registry,
          collection: dict | None) -> dict:
    """Surviving events with at least one member in this edition and at least
    one member still displayable. Effective membership; withdrawn rows omitted."""
    events = store["events"]
    root = _roots(events)
    eff: dict[str, list[dict]] = {}
    for e in events:
        eff.setdefault(root[e["event_id"]], []).extend(e["members"])
    out = []
    for e in events:
        eid = e["event_id"]
        if root[eid] != eid:
            continue
        members = sorted(eff.get(eid) or [], key=lambda r: (_str(r.get("first_seen")), _str(r.get("published_at")), r["item_id"]))
        in_edition = sorted(r["item_id"] for r in members if r["item_id"] in texts)
        shown = [r for r in members if r["item_id"] not in withdrawn_ids]
        if not in_edition or not shown:
            continue
        v = _ordered(e)
        v["members"] = shown
        v["member_count"] = len(members)
        v["in_edition"] = in_edition
        v["silence"] = silence_roster(e["institutions"], reg, collection)
        out.append(v)
    return {"format": "events-latest-v1", "schema": SCHEMA, "method": METHOD, "edition": edition,
            "status": "ok", "event_count": len(out), "events": out}


def _bp(n: int, d: int) -> int:
    return int(round(10000 * n / d)) if d else 0


def dossier_overlap(issues: dict | None, store: dict) -> dict:
    """Dossiers (cluster_issues) against events: counts only."""
    if not isinstance(issues, dict) or not isinstance(issues.get("issues"), list):
        return {"dossiers": None, "note": "no dossier store"}
    root = _roots(store["events"])
    owner = {r["item_id"]: root[e["event_id"]] for e in store["events"] for r in e["members"]}
    dossiers = within = split = items_total = items_in = 0
    touched: set[str] = set()
    for iss in issues["issues"]:
        if not isinstance(iss, dict):
            continue
        ids = sorted({_str(it.get("candidate_id")) for t in iss.get("tensions") or [] if isinstance(t, dict)
                      for it in t.get("items") or [] if isinstance(it, dict) and it.get("candidate_id")})
        if not ids:
            continue
        dossiers += 1
        items_total += len(ids)
        evs = {owner[i] for i in ids if i in owner}
        items_in += sum(1 for i in ids if i in owner)
        touched |= evs
        if len(evs) == 1 and all(i in owner for i in ids):
            within += 1
        elif len(evs) >= 2:
            split += 1
    return {"dossiers": dossiers, "dossier_items": items_total, "dossier_items_in_events": items_in,
            "dossiers_within_one_event": within, "dossiers_split_across_events": split,
            "events_holding_dossier_items": len(touched)}


def _ops(store: dict, view: dict, edition: str, raw: list, eligible: list, excluded: dict, decisions: list,
         issues: dict | None, anchor_source: str, diagnosis: list[str]) -> dict:
    vev = view["events"]
    multi = [e for e in vev if e["member_count"] > 1]
    independence_dist: dict[str, int] = {}
    reporting_dist: dict[str, int] = {}
    for e in multi:
        k = str(e["independence"]["count"])
        independence_dist[k] = independence_dist.get(k, 0) + 1
        r = str(e["independence"].get("reporting_count", 0))
        reporting_dist[r] = reporting_dist.get(r, 0) + 1
    activity = {a: sum(1 for e in vev if e["activity"] == a) for a in ACTIVITIES}
    tiers = {}
    for e in multi:
        tiers[str(e.get("tier"))] = tiers.get(str(e.get("tier")), 0) + 1
    unclassified = sum(1 for e in vev if e["type"] == vocabulaire.UNCLASSIFIED)
    root = _roots(store["events"])
    return {
        "format": "events-shadow-v1",
        "method": METHOD,
        "edition": edition,
        "status": "ok",
        "items": {
            "candidates": len(raw), "eligible": len(eligible),
            "excluded": dict(sorted(excluded.items())),
            # Edition-relative (not run-relative): a re-run of the same
            # edition reports the same numbers.
            "minted": sum(1 for e in store["events"] if e.get("born_edition") == edition),
            "attached": sum(1 for e in store["events"] for m in e["members"]
                            if m.get("first_seen") == edition
                            and (store["items"].get(m["item_id"]) or {}).get("join", {}).get("action") == "attached"),
            "merges": sum(1 for e in store["events"] if e["lineage"].get("merged_at") == edition),
        },
        "store": {
            "editions": len(store["editions"]), "events": len(store["events"]),
            "surviving": sum(1 for e in store["events"] if root[e["event_id"]] == e["event_id"]),
            "merged": sum(1 for e in store["events"] if root[e["event_id"]] != e["event_id"]),
            "members": sum(len(e["members"]) for e in store["events"]),
            "in_window": sum(1 for e in store["events"] if e["window_state"] == "in_window"),
        },
        "edition_view": {
            "events": len(vev),
            "multi_member": len(multi),
            "multi_institution": sum(1 for e in vev if len(e["institutions"]) >= 2),
            "bilingual": sum(1 for e in vev if e["languages"] == ["en", "fr"]),
            "anchored": sum(1 for e in vev if e["anchors"]),
            "unclassified": unclassified,
            "unclassified_share_bp": _bp(unclassified, len(vev)),
            "activity": activity,
            "tier_of_multi_member": dict(sorted(tiers.items())),
            "largest_event": max((e["member_count"] for e in vev), default=0),
            "independence_of_multi_member": dict(sorted(independence_dist.items(), key=lambda kv: int(kv[0]))),
            "reporting_origins_of_multi_member": dict(sorted(reporting_dist.items(), key=lambda kv: int(kv[0]))),
            "copy_pairs": sum(len(e.get("copies") or []) for e in vev),
            "withdrawn_members": sum(len(e.get("withdrawn") or []) for e in vev),
            "language_pairs": sum(len(e["language_pairs"]) for e in vev),
        },
        "dossiers_vs_events": dossier_overlap(issues, store),
        "anchors": anchor_source or "no event this edition",
        "diagnosis": sorted(diagnosis),
    }


# --------------------------------------------------------------------------- #
# I/O shell (fail-soft)
# --------------------------------------------------------------------------- #
def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def anchor_inputs_from(data: Path) -> dict:
    """The stores anchors.py reads besides the edition (loaded only when the
    module exists). The edition's official and Hydro-Québec items come from
    the edition itself, inside `build`."""
    if anchors_module() is None:
        return {}
    road = _read_json(data / "roadworks" / "latest_roadworks.json")
    civic = _read_json(data / "civic" / "latest_consultations.json")
    return {
        "roadworks": road.get("events") if isinstance(road, dict) and isinstance(road.get("events"), list) else [],
        "consultations": civic.get("events") if isinstance(civic, dict) and isinstance(civic.get("events"), list) else [],
    }


def collection_facts(payload: dict, sources_path: Path) -> dict:
    try:
        import registre  # noqa: PLC0415 - lazy: only the silence roster needs it
        return registre.institution_collection(payload.get("source_status"), sources_path)
    except Exception:  # noqa: BLE001 - no facts: the roster says "no linked article"
        return {}


def write_outputs(data: Path, store: dict | None, view: dict, ops: dict) -> None:
    events_dir = data / "events"
    if store is not None:
        store_io.write_text_atomic(events_dir / "store.json", _dump(store, compact=True))
    store_io.write_text_atomic(events_dir / "latest_events.json", _dump(view))
    store_io.write_text_atomic(data / "ops" / "events_shadow.json", _dump(ops))


def _empty_outputs(edition: str | None, status: str, reason: str) -> tuple[dict, dict]:
    view = {"format": "events-latest-v1", "schema": SCHEMA, "method": METHOD, "edition": edition,
            "status": status, "event_count": 0, "events": []}
    ops = {"format": "events-shadow-v1", "method": METHOD, "edition": edition, "status": status,
           "diagnosis": [reason]}
    return view, ops


# --------------------------------------------------------------------------- #
# R10 in a run that does not rebuild the store
# --------------------------------------------------------------------------- #
_FLAT_OBJECT = re.compile(r"\{[^{}]*\}")
_URL_FIELD = re.compile(r'("url"\s*:\s*")((?:[^"\\]|\\.)*)')


def _scrub_row(row: dict, reg: Registry, hosts: dict) -> bool:
    """Drop a withdrawn member's URL in place; True when one was dropped."""
    url = row.get("url")
    if not isinstance(url, str):
        return False
    if reg.withdrawn(row, hosts.get(_str(row.get("item_id"))) or article_host(url)):
        del row["url"]
        return True
    return False


def scrub_withdrawn(path: Path, reg: Registry | None) -> tuple[int, str]:
    """R10 for a run that does not rebuild the store (store unreadable, edition
    refused, build failed): every member row whose source, domain or article
    is withdrawn loses its URL in this run; nothing else changes and the file
    is rewritten only when a URL was removed. A store that does not even parse
    is scrubbed object by object (member rows are flat objects), then any
    remaining `"url"` value that a url or host takedown matches is emptied.
    Returns (URLs removed, note)."""
    if reg is None:
        return 0, "takedowns unreadable: nothing scrubbed"
    if not reg.rules:
        return 0, "no active takedown"
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0, "no store"
    except (OSError, UnicodeDecodeError) as exc:
        return 0, f"store unreadable as text ({type(exc).__name__}): not scrubbed"
    removed = 0
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError):
        doc = None
    if isinstance(doc, (dict, list)):
        items = doc.get("items") if isinstance(doc, dict) else None
        hosts = ({k: v["host"] for k, v in items.items() if isinstance(v, dict) and isinstance(v.get("host"), str)}
                 if isinstance(items, dict) else {})
        stack = [doc]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                removed += _scrub_row(x, reg, hosts)
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
        new_text = _dump(doc, compact=True) if removed else text
    else:
        def one_row(m: re.Match) -> str:
            nonlocal removed
            try:
                obj = json.loads(m.group(0))
            except (ValueError, RecursionError):
                return m.group(0)
            if isinstance(obj, dict) and _scrub_row(obj, reg, {}):
                removed += 1
                return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return m.group(0)

        def one_url(m: re.Match) -> str:
            nonlocal removed
            if reg.rules.match_url(m.group(2)) is None:
                return m.group(0)
            removed += 1
            return m.group(1)

        new_text = _URL_FIELD.sub(one_url, _FLAT_OBJECT.sub(one_row, text))
    if not removed:
        return 0, "no withdrawn URL in the store"
    store_io.write_text_atomic(Path(path), new_text)
    return removed, f"{removed} withdrawn URL(s) removed; the rest of the store left as it was"


def _rules_only(takedowns_path: Path) -> Registry | None:
    """The takedown rules without sources.yaml (which may be what failed)."""
    try:
        entries, errors = takedown.load(Path(takedowns_path))
        for err in errors:
            _say(f"takedowns: {err}")
        return Registry([], takedown.Rules(entries))
    except Exception:  # noqa: BLE001 - then nothing can be scrubbed, and the ledger says so
        return None


def _not_built(data: Path, edition: str | None, status: str, reason: str, reg: Registry | None) -> int:
    """Every run that does not rebuild the store ends here: R10 is applied to
    the store as it stands, then the current-edition view is cleared (an
    earlier edition is never left in place marked "ok") and the ledger says
    why. Each step is guarded on its own; always 0."""
    _say(reason)
    try:
        removed, note = scrub_withdrawn(data / "events" / "store.json", reg)
    except Exception as exc:  # noqa: BLE001 - diagnosed in the ledger
        removed, note = 0, f"scrub failed ({type(exc).__name__}: {exc})"
    if removed:
        _say(f"R10: {note}")
    view, ops = _empty_outputs(edition, status, reason)
    ops["r10_store_scrub"] = {"urls_removed": removed, "note": note}
    try:
        write_outputs(data, None, view, ops)
    except Exception as exc:  # noqa: BLE001 - nothing more can be written
        _say(f"could not clear the current-edition view ({type(exc).__name__}: {exc})")
    return 0


def run(in_path: Path = IN_PATH, data: Path = DATA, sources_path: Path = SOURCES_PATH,
        takedowns_path: Path = TAKEDOWNS_PATH) -> int:
    """One pipeline step. Always returns 0; every refusal is written down. A
    run that does not rebuild the store (no edition, store unreadable, edition
    refused, any fault while reading sources.yaml or building) still applies
    R10 to the store and clears the current-edition view (`_not_built`)."""
    store_path = data / "events" / "store.json"
    edition: str | None = None
    reg: Registry | None = None
    try:
        payload = _read_json(in_path)
        if not isinstance(payload, dict):
            return _not_built(data, None, "no_edition",
                              f"no readable edition at {in_path}: nothing built (the brief renders from dossiers)",
                              _rules_only(takedowns_path))
        edition = edition_clock(payload)
        reg = Registry.load(sources_path, takedowns_path)
        try:
            previous = load_store(store_path)
            issues = _read_json(data / "issues" / "latest_issues.json")
            store, view, ops = build(previous, payload, reg, issues=issues if isinstance(issues, dict) else None,
                                     collection=collection_facts(payload, sources_path),
                                     anchor_inputs=anchor_inputs_from(data))
        except StoreUnreadable as exc:
            return _not_built(data, edition, "store_unreadable",
                              f"store unreadable ({exc}); left for repair, no events this run", reg)
        except EditionRefused as exc:
            return _not_built(data, edition, "edition_refused", f"edition refused ({exc}); store unchanged", reg)
        write_outputs(data, store, view, ops)
        ev = ops["edition_view"]
        _say(f"{ev['events']} event(s) in edition {edition} ({ev['multi_member']} multi-member, "
             f"{ev['multi_institution']} multi-institution, {ev['bilingual']} bilingual, {ev['anchored']} anchored, "
             f"{ev['unclassified']} unclassified); store {ops['store']['events']} event(s) -> {store_path}")
        return 0
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - any fault is diagnosed, never left stale
        return _not_built(data, edition, "build_failed",
                          f"build failed ({type(exc).__name__}: {exc}); store left as it was, no events this run",
                          reg if reg is not None else _rules_only(takedowns_path))


def snapshots(directory: Path) -> list[Path]:
    """Stamped `*_candidates.json` snapshots in name (= collection) order."""
    return [p for p in sorted(Path(directory).glob("*_candidates.json")) if p.name[:1].isdigit()]


def replay(paths: list[Path], reg: Registry, previous: dict | None = None, *, timings: list | None = None) -> tuple[dict, dict, dict, dict]:
    """Every snapshot as one edition, in order, from `previous` (empty by
    default). Each step round-trips the store through JSON exactly as the
    pipeline does between runs; a refused or failed edition leaves the store
    as it was and its outputs say so, as `run` does. Returns (store, view,
    ops, counts)."""
    store = json.loads(_dump(previous or empty_store(), compact=True))
    view, ops = _empty_outputs(None, "no_edition", "nothing replayed")
    counts = {"editions": 0, "refused": 0, "failed": 0, "unreadable": 0}
    for path in paths:
        payload = _read_json(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            counts["unreadable"] += 1
            continue
        started = time.perf_counter()
        try:
            new_store, view, ops = build(store, payload, reg)
        except EditionRefused as exc:
            counts["refused"] += 1
            view, ops = _empty_outputs(edition_clock(payload), "edition_refused", f"edition refused ({exc}); store unchanged")
            continue
        except Exception as exc:  # noqa: BLE001 - as run(): diagnosed, the store stays as it was
            counts["failed"] += 1
            reason = f"build failed ({type(exc).__name__}: {exc}); store left as it was, no events this run"
            _say(f"replay {Path(path).name}: {reason}")
            view, ops = _empty_outputs(edition_clock(payload), "build_failed", reason)
            continue
        if timings is not None:
            timings.append(time.perf_counter() - started)
        store = json.loads(_dump(new_store, compact=True))
        counts["editions"] += 1
    return store, view, ops, counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--in", dest="in_path", type=Path, default=IN_PATH)
    ap.add_argument("--data-dir", type=Path, default=DATA)
    ap.add_argument("--sources", type=Path, default=SOURCES_PATH)
    ap.add_argument("--takedowns", type=Path, default=TAKEDOWNS_PATH)
    ap.add_argument("--replay", type=Path, default=None,
                    help="replay every stamped *_candidates.json of this directory from an empty store")
    try:
        args = ap.parse_args(argv)
        if args.replay is None:
            return run(args.in_path, args.data_dir, args.sources, args.takedowns)
        target = args.data_dir / "events" / "store.json"
        if target.exists():
            # A replay builds a store from whatever snapshots are at hand; it
            # never replaces a store that carries minted ids.
            _say(f"replay refused: {target} exists; give --data-dir an empty scratch directory")
            return 0
        reg = Registry.load(args.sources, args.takedowns)
        timings: list[float] = []
        store, view, ops, counts = replay(snapshots(args.replay), reg, timings=timings)
        write_outputs(args.data_dir, store, view, ops)
        _say(f"replayed {counts['editions']} edition(s) ({counts['refused']} refused as out of order, "
             f"{counts['failed']} failed, {counts['unreadable']} unreadable); store {len(store['events'])} event(s); "
             f"seconds per edition max {max(timings, default=0.0):.2f} mean "
             f"{(sum(timings) / len(timings)) if timings else 0.0:.2f}")
        return 0
    except SystemExit as exc:  # argparse --help / usage errors, unreadable sources.yaml
        if exc.code in (0, None):
            return 0
        _say(f"skipped ({exc}); no events this run, the brief renders from dossiers")
        return 0
    except Exception as exc:  # noqa: BLE001 - shadow stage: diagnose, exit 0
        _say(f"skipped ({type(exc).__name__}: {exc}); no events this run, the brief renders from dossiers")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
