"""evenements — the event surfaces: French at the site root, English under /en/.

docs/MIGRATION.md steps 9-11, WIRED BEHIND A SWITCH (scripts/surfaces.py):
`rank_display` calls `emit(mode=...)` in the record layer, after registre,
memoire, depart and affiche (so this edition's seal is readable and every seal
link it prints has its page), in the full edition and in the hourly roads-only
re-render alike, and only when the switch is not "off". It never runs the
event builder (events.py stays a full-run stage): it reads the stored view.
The approved prototype is implemented by `scripts/composants.py` (the kit);
this module only turns the real stores into the kit's views, applies the house
law at render time and writes the files.

Modes: "preview" renders every page `noindex, nofollow` (the sitemap omits
them and the brief keeps /); "live" also writes the front door at
index.html and en/index.html (the same page as evenements.html, whose
canonical and alternates then name / and /en/), linking the former brief at
/le-point.html as the full river and giving the roadworks block the id the
other surfaces link as /#travaux.

Outputs (under `public_dir`, through store_io; identical inputs give identical
bytes; no wall clock, only the edition clock and the roadworks collection
clock):

  index.html, en/index.html (live only)
      the front door: the page below, at / and /en/.
  evenements.html, en/evenements.html
      the front door content: edition head and stamp, the three rules, at most
      12 ranked event cards with the ranking's own explanation ("Pourquoi
      ici ?"), "Chez moi" (server-rendered, matched on the device by
      /assets/evenements.js), "Avant de partir" (the hourly roadworks lane,
      collection time and age always printed), "Déclaré par les autorités",
      the end-of-edition card with the measured roster and the seal.
  evenements/<event_id>.html, en/evenements/<event_id>.html
      one record per event. While the event has an article in the current
      collection: attributed verbatim titles and excerpts (<= 240 characters,
      author and photo credit, their own `lang`). Otherwise: Vigie's labels,
      ids, institution names, times, links, counts, anchors, seals and the
      record's journal, and NO publisher text. Each page says which in
      `<meta name="vigie-view" content="current|permanent">`.
  evenements/latest.json
      language-neutral: codes with {fr, en} labels, ids, times, counts, links;
      ownership as the declared structure of sources.yaml (class, group,
      public reference); no publisher text.
  qualite.json
      counts only: ranking_events.quality_public(data/ops/events_quality.json).

Inputs (read-only; each absent or corrupt input is diagnosed and rendered as
an honest absence, never a crash):

  data/events/latest_events.json   the current-edition view (events.py)
  data/events/store.json           the event store (permanent records)
  data/normalized/latest_enriched.json   publisher text, joined by item id at
                                   render time, current collection only
  data/roadworks/latest_roadworks.json   + anchors.roadworks_view
  data/civic/latest_consultations.json   consultation anchors
  data/registre/registre.json      institution register, seals
  data/ops/events_quality.json     the measured quality (chip wording)
  sources.yaml, takedowns.yaml     names, ownership, enabled feeds, R10

Rules (each is a rule computed from evidence and printed on the page;
docs/AUTONOMY.md):

  Cards     ranking_events.rank_events, imported lazily: its order, its `shown`
            flag (at most CARDS_MAX = 12), its explanation. Without the module
            (or when it faults): newest first, by the newest declared
            publication time (else collection time) of the event's articles,
            then event id; the first 12 are shown, each card says it is the
            fallback order and so does the edition's lede (it never claims the
            published rule it could not apply).
  Chip      ranking_events.tier_chip(tier, quality): the grouping chip follows
            the measured quality; without the module a grouped event reads
            "Regroupé automatiquement" and never "certain".
  Pages     a record exists for every event of the current collection, and for
            a past event covered by at least two institutions or tied to an
            official record (an anchor other than an edition seal); at most
            PAGES_MAX = 500, the cards first, then the current collection in
            ranking order, then the newest (last edition, opened edition,
            event id). A record merged into another keeps a page that links to
            the survivor while the survivor has one. Pages outside the rule
            are removed on the next render.
  Official  the collection's official releases that no card shown contains or
            cites as an official record (item anchors). First those inside
            the edition's freshness window (declared time at most
            OFFICIAL_WINDOW_HOURS = 72 h, the event window, before the
            edition clock; a missing or suspect stamp is not inside), by the
            method's proposed geography (Québec City, then Quebec, then
            everything else as one group), newest first within each group
            (declared time, then id); at most OFFICIAL_MAX = 6. Only when
            fewer than 6 remain inside the window is the list completed with
            older releases, in the same order, each marked as outside the
            window, and the block says so. When capped, the block prints that
            order (never "the most recent") and the counts behind it.
  Roster    the institutions followed this collection (enabled feeds, minus
            withdrawn ones), in the registry's order, each with the state the
            registre's own measurement gives (registre.institution_collection
            over this collection's feed statuses): in the cards shown, else
            items collected (official: "declared", media: "outside"), else no
            items, our collection gap, or not established. Names come from
            the registre's institution register when it has them.
  Seals     a record lists the main-chain seals (registre) of the editions in
            which it gained an article, plus this edition's seal while it is
            in this collection; only seals inside the published window
            (registre.PUBLIC_SEAL_CAP), so every seal link has its page.
  R10       takedowns.yaml is applied HERE, at every render, because the
            hourly roads-only lane re-renders from stored files: a withdrawn
            article, feed or domain is never credited, counted, linked or
            quoted on any page; an event left without articles has no page;
            a withdrawn institution is left out of every roster. The official
            lanes that are not article feeds (wzdx roadworks, civic-html
            consultations) are judged as a whole (Law.lane_state: their
            sources.yaml record, the source id and URLs of the stored file):
            a withdrawn lane's block says so and quotes, counts, credits and
            links nothing, and every anchor pointing into it goes, for the
            ranking too. An unreadable takedowns.yaml withholds them as well.
            events.apply_takedowns is applied first to the stored view (with
            this render's rules, sources.yaml and the store's article
            domains), so every derived field describes the members present
            and a voice whose article was withdrawn reads "retiré à la
            demande de l'éditeur" in the silence panel (a voice withdrawn
            whole is named nowhere here); a fault is diagnosed and this
            module's own filter still applies.

    python -X utf8 scripts/evenements.py [--data-dir DIR] [--public-dir DIR]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import importlib
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import composants as ck  # noqa: E402
import i18n  # noqa: E402
import ownership  # noqa: E402
import store_io  # noqa: E402
import surfaces  # noqa: E402
import takedown  # noqa: E402
import vocabulaire  # noqa: E402

METHOD = "evenements-v1 (pages built from the event stores; text joined at render time)"
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
SOURCES_PATH = ROOT / "sources.yaml"
TAKEDOWNS_PATH = ROOT / "takedowns.yaml"

CARDS_MAX = 12
PAGES_MAX = 500
OFFICIAL_MAX = 6
# The freshness window of "declared by the authorities": the event window
# itself (composants.EVENT_SPAN_HOURS = event_match.WINDOW_HOURS = 72 h; a
# test holds them equal), and the tolerance for a stamp later than the edition
# clock (events.DATE_SUSPECT_HOURS).
OFFICIAL_WINDOW_HOURS = ck.EVENT_SPAN_HOURS
OFFICIAL_FUTURE_HOURS = 6
# Live front door (scripts/surfaces.py): the former brief, linked as the full
# river, and the id the other surfaces' "/#travaux" links land on.
RIVER_PATH = surfaces.RIVER_PATH
ROADS_ANCHOR = "travaux"
MODES = ("preview", "live")
ROBOTS = surfaces.ROBOTS
SUGGESTIONS_MAX = 6
NEIGHBOURS_MAX = 3
SHARED_MAX = 8
WORDS_MAX = 8
TITLE_CAP = 300
NAME_CAP = 120
QUALITY_PATH = "/qualite.json"
MATCH_RULE = "event-match-v1 average-link"
FALLBACK_RANKING = "fallback-recency"
LANGS = ("fr", "en")
EVENT_ID = re.compile(r"^ev-[0-9a-f]{16}$")
PAGE_NAME = re.compile(r"^ev-[0-9a-f]{16}\.html$")
TIER_CHIP_KINDS = ("auto", "certain", "probable", "possible", "none")
PLAUSIBLE_YEARS = (1990, 2100)
_EMAIL = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def _say(message: str) -> None:
    print(f"evenements: {message}", flush=True)


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _dicts(value: object) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, (list, tuple)) else []


def _strs(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, (list, tuple)) else []


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return default


def _instant(raw: object) -> datetime | None:
    dt = i18n.parse_instant(raw)
    if dt is None or not (PLAUSIBLE_YEARS[0] <= dt.year <= PLAUSIBLE_YEARS[1]):
        return None
    return dt


def _epoch(raw: object) -> float | None:
    dt = _instant(raw)
    return dt.timestamp() if dt is not None else None


def _same_instant(a: object, b: object) -> bool:
    x, y = i18n.parse_instant(a), i18n.parse_instant(b)
    return x is not None and x == y


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, UnicodeDecodeError):
        return None


def _dump(doc: object) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def plain(value: object, cap: int | None = None) -> str:
    """Publisher text as plain words: markup stripped, entities decoded,
    whitespace collapsed. Never reworded. A bare e-mail address is never
    published: the text is cut before it (a truncation, never an edit), and
    `cap` truncates with an ellipsis that counts."""
    raw = _TAG.sub(" ", _str(value))
    text = " ".join(html.unescape(raw).split())
    hit = _EMAIL.search(text)
    if hit:
        text = text[:hit.start()].rstrip(" ,;:(-") + ("…" if hit.start() > 0 else "")
        text = "" if text == "…" else text
    if cap is not None and len(text) > cap:
        text = text[:cap - 1].rstrip() + "…"
    return text


def _both(fn, code: str) -> dict:
    return {lang: fn(code, lang) for lang in LANGS}


def _labels_of(code: str, kind: str) -> dict:
    try:
        if kind == "type":
            return _both(vocabulaire.type_label, code)
        return _both(vocabulaire.place_label, code)
    except Exception:  # noqa: BLE001 - an unreadable vocabulary leaves the code itself
        return {"fr": code, "en": code}


# --------------------------------------------------------------------------- #
# Registry and takedowns (R10 at render time)
# --------------------------------------------------------------------------- #
class Law:
    """sources.yaml + takedowns.yaml as the pages need them: names, enabled
    feeds, ownership, and one `withdrawn()` for every article row (source,
    feed domain, article domain, URL, or the id of a withdrawn URL)."""

    def __init__(self, sources_path: Path, takedowns_path: Path, diagnosis: list[str]):
        self.sources_path = Path(sources_path)
        self.records: list[dict] = []
        self.reg = None
        entries, errors = takedown.load(Path(takedowns_path))
        for err in errors:
            diagnosis.append(f"takedowns.yaml: {err}")
        # A register nobody can read is a request nobody can enforce: the pages
        # then quote no publisher text and link no article (the release gate
        # refuses such a tree anyway; this is the render's own fail-closed).
        self.unreadable = bool(errors)
        if self.unreadable:
            diagnosis.append("takedowns.yaml has errors: no publisher text and no article link this render")
        self.rules = takedown.Rules(entries)
        try:
            import ingest_rss  # noqa: PLC0415

            self.records = [r for r in ingest_rss.load_sources(self.sources_path) if isinstance(r, dict)]
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - no registry: no names, no links
            diagnosis.append(f"sources.yaml unreadable ({type(exc).__name__}): no names, no outbound links")
        self.by_id = {str(r["id"]): r for r in self.records if r.get("id")}
        try:
            import events  # noqa: PLC0415 - the builder's own R10 test, one definition

            self.reg = events.Registry([dict(r) for r in self.records], self.rules)
        except Exception as exc:  # noqa: BLE001
            diagnosis.append(f"events.Registry unavailable ({type(exc).__name__}): local R10 test")
        if self.unreadable:
            self.enabled: set[str] = set()   # no text, no link (see above)
        elif self.reg is not None:
            self.enabled = set(self.reg.enabled)
        else:
            self.enabled = {sid for sid, r in self.by_id.items() if r.get("enabled") is True
                            and r.get("type") == "rss" and r.get("url") and self.rules.match_source(r) is None}
        self.withdrawn_item_ids = {hashlib.sha256(v.encode("utf-8")).hexdigest()[:24] for v in sorted(self.rules.urls)}
        try:
            self.withdrawn_institutions = takedown.withdrawn_institutions(self.rules, self.sources_path) if self.rules else {}
        except Exception:  # noqa: BLE001
            self.withdrawn_institutions = {}

    def withdrawn(self, row: dict, host: str = "") -> bool:
        if not self.rules:
            return False
        if self.reg is not None:
            return bool(self.reg.withdrawn(row, host))
        sid = _str(row.get("source_id"))
        if sid in self.rules.sources or _str(row.get("item_id") or row.get("id")) in self.withdrawn_item_ids:
            return True
        rec = self.by_id.get(sid)
        if rec is not None and self.rules.match_source(rec) is not None:
            return True
        if host and self.rules.match_host("https://" + host + "/") is not None:
            return True
        return bool(row.get("url")) and self.rules.match_url(row.get("url")) is not None

    def lane_state(self, typ: str, doc: object) -> str:
        """R10 for an official lane that is not an article feed (`wzdx`
        roadworks, `civic-html` consultations): "" when its stored document
        may be quoted, credited and linked; "withdrawn" when an active
        takedown names its source: the sources.yaml record (id, feed or
        homepage domain, as match_source reads it), or the source id or a URL
        the stored document itself carries (so a takedown still bites when
        the registry is unreadable); "register_unreadable" when takedowns.yaml
        has errors (fail-closed, as for every article). Such lanes have no
        article URL in the stores, so the release gate cannot catch a
        source-level withdrawal: this render is the only enforcement."""
        if self.unreadable:
            return "register_unreadable"
        if not self.rules:
            return ""
        d = doc if isinstance(doc, dict) else {}
        sid = _str(d.get("source_id"))
        sids = {sid} if sid else set()
        sids |= {_str(row.get("source_id")) for row in _dicts(d.get("events")) if _str(row.get("source_id"))}
        if sids & set(self.rules.sources):
            return "withdrawn"
        records = [self.by_id[s] for s in sorted(sids) if s in self.by_id] or \
                  [r for r in self.records if r.get("type") == typ]
        if any(self.rules.match_source(r) is not None for r in records):
            return "withdrawn"
        for key in ("feed_url", "source_url", "dataset_url", "homepage", "url"):
            if self.rules.match_url(d.get(key)) is not None:
                return "withdrawn"
        return ""

    def name_of(self, source_id: str, fallback: str = "") -> str:
        rec = self.by_id.get(source_id) or {}
        return plain(rec.get("institution_name") or rec.get("name") or fallback, NAME_CAP)

    def kind_of(self, source_id: str) -> str:
        return "official" if _str((self.by_id.get(source_id) or {}).get("source_kind")) == "official" else "media"

    def followed(self) -> list[dict]:
        if self.reg is not None:
            try:
                return [dict(r) for r in self.reg.followed_institutions()]
            except Exception:  # noqa: BLE001
                return []
        return []

    def declaration(self, source_id: str) -> dict:
        """The source's ownership declaration, and the words every page prints
        for it (`ownership_words`, scripts/ownership.py: the owner's structure
        as /methode/sources.html states it; the class code is never printed)."""
        rec = self.by_id.get(source_id) or {}
        out: dict = {}
        for key in ("ownership_class", "owner_group", "ownership_ref", "ownership_asof"):
            if isinstance(rec.get(key), str) and rec.get(key):
                out[key] = rec[key]
        out["ownership_words"] = {lang: ownership.source_words(rec, lang)[0] for lang in LANGS}
        return out


def _host_of(url: object) -> str:
    try:
        import events  # noqa: PLC0415

        return events.article_host(url)
    except Exception:  # noqa: BLE001
        return ""


def _helper_takedowns(doc: dict, law: Law, hosts: dict[str, str], diagnosis: list[str]) -> dict:
    """events.apply_takedowns (docs/EVENTS.md 17.4): the stored view as a
    rebuild of its edition under the active takedowns would give it, with the
    rules this render enforces, the registry it was given (feed-domain rules,
    voices withdrawn whole) and the store's article domains (a domain rule on
    a member whose URL is gone). Applied to a copy; its result is used only
    when it keeps the document's shape, and a fault is diagnosed and leaves
    the stored view (never a crash). This module's own R10 filter runs
    afterwards in every case, so the helper can only remove more, never less."""
    try:
        import events  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return doc
    fn = getattr(events, "apply_takedowns", None)
    if not callable(fn):
        return doc
    try:
        out = fn(copy.deepcopy(doc), law.rules, sources_path=law.sources_path, hosts=dict(hosts))
    except Exception as exc:  # noqa: BLE001
        diagnosis.append(f"events.apply_takedowns faulted ({type(exc).__name__}); local R10 filter only")
        return doc
    if isinstance(out, tuple) and out and isinstance(out[0], dict):
        out = out[0]
    if isinstance(out, dict) and isinstance(out.get("events"), list):
        return out
    return doc


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def _store_events(store: object, diagnosis: list[str]) -> list[dict]:
    """The store's events, structurally checked by the builder's own checker
    (a damaged store gives no permanent record, never a half-trusted one)."""
    if store is None:
        return []
    try:
        import events  # noqa: PLC0415

        doc = events.check_store(store)
    except Exception as exc:  # noqa: BLE001 - StoreUnreadable or the module itself
        diagnosis.append(f"event store unusable ({type(exc).__name__}: {str(exc)[:80]}): no permanent record from it")
        return []
    return [e for e in doc.get("events") or [] if isinstance(e, dict)]


def _store_hosts(store: object) -> dict[str, str]:
    items = store.get("items") if isinstance(store, dict) else None
    if not isinstance(items, dict):
        return {}
    return {str(k): v["host"] for k, v in items.items() if isinstance(v, dict) and isinstance(v.get("host"), str)}


def _roots(events_: list[dict]) -> dict[str, str]:
    by_id = {e["event_id"]: e for e in events_ if EVENT_ID.match(_str(e.get("event_id")))}
    out: dict[str, str] = {}
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


# --------------------------------------------------------------------------- #
# Ranking (shared contract R with scripts/ranking_events.py)
# --------------------------------------------------------------------------- #
def ranking_module(override: object = None) -> object | None:
    """ranking_events when importable (imported lazily: a parallel tranche
    builds it), or the module passed in (tests); None otherwise. `False`
    forces the documented fallback."""
    if override is False:
        return None
    if override is not None:
        return override
    try:
        return importlib.import_module("ranking_events")
    except Exception:  # noqa: BLE001 - absent or broken: the documented fallback
        return None


def _newest(members: list[dict]) -> str:
    best, best_e = "", None
    for m in members:
        for key in ("published_at", "first_seen"):
            e = _epoch(m.get(key))
            if e is not None:
                if best_e is None or e > best_e:
                    best, best_e = _str(m.get(key)), e
                break
    return best


def fallback_rank(events_: list[dict]) -> list[dict]:
    """The documented fallback: newest first (newest declared publication
    time, else collection time, of the event's articles), then event id; the
    first CARDS_MAX are shown."""
    keyed = []
    for e in events_:
        newest = _newest(_dicts(e.get("members")))
        keyed.append((-(_epoch(newest) or 0.0), _str(e.get("event_id")), newest))
    keyed.sort()
    rows = []
    for n, (_neg, eid, newest) in enumerate(keyed, start=1):
        rows.append({"event_id": eid, "position": n, "shown": n <= CARDS_MAX,
                     "criteria": {"newest": newest},
                     "explain": [{"key": "rank.fallback", "values": {"t": newest}}]})
    return rows


def rank(events_: list[dict], ctx: dict, module: object | None, diagnosis: list[str]) -> tuple[list[dict], str]:
    """(rows in display order, ranking name). Rows naming no known event are
    ignored; known events the ranking forgot follow, unshown, in fallback
    order; a fault or an empty answer is the fallback (diagnosed)."""
    known = {_str(e.get("event_id")) for e in events_}
    fn = getattr(module, "rank_events", None) if module is not None else None
    if not callable(fn):
        return fallback_rank(events_), FALLBACK_RANKING
    try:
        raw = fn(copy.deepcopy(events_), dict(ctx))
    except Exception as exc:  # noqa: BLE001
        diagnosis.append(f"ranking_events.rank_events faulted ({type(exc).__name__}: {str(exc)[:80]}); fallback order")
        return fallback_rank(events_), FALLBACK_RANKING
    rows, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        eid = _str(r.get("event_id"))
        pos = r.get("position")
        if eid not in known or eid in seen or isinstance(pos, bool) or not isinstance(pos, int):
            continue
        seen.add(eid)
        rows.append({"event_id": eid, "position": pos, "shown": r.get("shown") is True,
                     "criteria": r.get("criteria") if isinstance(r.get("criteria"), dict) else {},
                     "explain": _dicts(r.get("explain"))})
    if not rows and events_:
        diagnosis.append("ranking_events.rank_events returned no usable row; fallback order")
        return fallback_rank(events_), FALLBACK_RANKING
    rows.sort(key=lambda r: (r["position"], r["event_id"]))
    rest = [r for r in fallback_rank([e for e in events_ if _str(e.get("event_id")) not in seen])]
    for r in rest:
        r["shown"] = False
    if rest:
        diagnosis.append(f"ranking_events left {len(rest)} event(s) unranked; listed after, never shown")
    out = rows + rest
    for n, r in enumerate(out, start=1):
        r["position"] = n
    shown = 0
    for r in out:
        if r["shown"]:
            shown += 1
            r["shown"] = shown <= CARDS_MAX
    return out, "ranking_events"


def _fallback_chip(tier: object) -> dict:
    if tier in ("certain", "probable"):
        return {"kind": "auto", "key": "tier.auto", "values": {}}
    return {"kind": "none", "key": "", "values": {}}


def tier_view(tier: object, quality: object, module: object | None, diagnosis: list[str]) -> dict:
    fn = getattr(module, "tier_chip", None) if module is not None else None
    if callable(fn):
        try:
            out = fn(tier, copy.deepcopy(quality))
            if isinstance(out, dict) and out.get("kind") in TIER_CHIP_KINDS:
                return {"kind": out["kind"], "key": _str(out.get("key")),
                        "values": out.get("values") if isinstance(out.get("values"), dict) else {}}
            msg = "ranking_events.tier_chip returned an unknown shape; chip reads 'grouped automatically'"
        except Exception as exc:  # noqa: BLE001
            msg = f"ranking_events.tier_chip faulted ({type(exc).__name__}); chip reads 'grouped automatically'"
        if msg not in diagnosis:
            diagnosis.append(msg)
    return _fallback_chip(tier)


def _counts_only(doc: object, depth: int = 0) -> bool:
    """A quality document fit for publication: numbers, booleans, null, short
    codes, nested at most 6 deep. A long string (a title?) refuses it."""
    if depth > 6:
        return False
    if doc is None or isinstance(doc, (bool, int, float)):
        return True
    if isinstance(doc, str):
        return len(doc) <= 160
    if isinstance(doc, list):
        return all(_counts_only(x, depth + 1) for x in doc)
    if isinstance(doc, dict):
        return all(isinstance(k, str) and len(k) <= 80 and _counts_only(v, depth + 1) for k, v in doc.items())
    return False


def quality_doc(quality: object, module: object | None, diagnosis: list[str]) -> dict:
    fn = getattr(module, "quality_public", None) if module is not None else None
    base = {"format": "vigie-qualite-v1", "method": METHOD}
    if callable(fn):
        try:
            out = fn(copy.deepcopy(quality))
            if isinstance(out, dict) and _counts_only(out):
                return {**base, "source": "ranking_events.quality_public", "status": "published", "quality": out}
            diagnosis.append("ranking_events.quality_public returned something other than counts; not published")
        except Exception as exc:  # noqa: BLE001
            diagnosis.append(f"ranking_events.quality_public faulted ({type(exc).__name__}); not published")
    return {**base, "source": "none", "status": "not_established",
            "note": "no measured figure is published by this render; every grouping chip reads 'grouped automatically'"}


# --------------------------------------------------------------------------- #
# The render context: everything read once
# --------------------------------------------------------------------------- #
class Render:
    def __init__(self, data_dir: Path, sources_path: Path, takedowns_path: Path, ctx: dict, diagnosis: list[str]):
        self.diag = diagnosis
        self.law = Law(sources_path, takedowns_path, diagnosis)
        given = ctx if isinstance(ctx, dict) else {}

        def doc(key: str, path: Path) -> object | None:
            return given[key] if key in given else _read_json(path)

        view = doc("events_view", data_dir / "events" / "latest_events.json")
        store = doc("events_store", data_dir / "events" / "store.json")
        enriched = doc("enriched", data_dir / "normalized" / "latest_enriched.json")
        self.roadworks_doc = doc("roadworks", data_dir / "roadworks" / "latest_roadworks.json")
        civic = doc("civic", data_dir / "civic" / "latest_consultations.json")
        self.quality = doc("quality", data_dir / "ops" / "events_quality.json")
        state = doc("registre_state", data_dir / "registre" / "registre.json")
        self.robots = _str(given.get("robots")) or "index, follow"
        self.mode = _str(given.get("mode"))   # "", "preview" or "live" (scripts/surfaces.py)
        self.ranking = ranking_module(given.get("ranking_module"))

        # ---- the edition ----------------------------------------------------
        self.enriched = enriched if isinstance(enriched, dict) else {}
        self.view = view if isinstance(view, dict) else {}
        self.hosts = _store_hosts(store)
        if isinstance(self.view, dict) and self.view:
            self.view = _helper_takedowns(self.view, self.law, self.hosts, diagnosis)
        enriched_clock = _str(self.enriched.get("normalized_at"))
        view_clock = _str(self.view.get("edition"))
        self.view_ok = (self.view.get("status", "ok") == "ok" and isinstance(self.view.get("events"), list)
                        and bool(view_clock) and (not enriched_clock or _same_instant(view_clock, enriched_clock)))
        if self.view and not self.view_ok:
            diagnosis.append(f"current-edition view not usable (status {str(self.view.get('status'))[:40]!r}, "
                             f"edition {view_clock[:40]!r} vs collection {enriched_clock[:40]!r}): no card")
        if not self.view:
            diagnosis.append("no current-edition view: no card")
        self.clock = view_clock if self.view_ok else enriched_clock
        self.status = "ok" if self.view_ok else "not_built"

        # ---- texts of this collection, R10 applied ---------------------------
        self.texts: dict[str, dict] = {}
        self.gone: set[str] = set(self.law.withdrawn_item_ids)
        cands = self.enriched.get("candidates") if isinstance(self.enriched.get("candidates"), list) else []
        for c in cands:
            if not isinstance(c, dict) or not _str(c.get("id")):
                continue
            row = {"item_id": c["id"], "source_id": _str(c.get("source_id")), "url": _str(c.get("url"))}
            if self.law.withdrawn(row, _host_of(c.get("url"))):
                self.gone.add(c["id"])
                continue
            if row["source_id"] not in self.law.enabled:
                continue  # a feed no longer followed lends no text (its members keep their counts)
            self.texts.setdefault(c["id"], c)

        # ---- the store --------------------------------------------------------
        self.store_events = _store_events(store, diagnosis)
        self.roots = _roots(self.store_events)
        self.store_by_id = {e["event_id"]: e for e in self.store_events}
        for e in self.store_events:
            for m in _dicts(e.get("members")):
                if self.law.withdrawn(m, self.hosts.get(_str(m.get("item_id")), "")):
                    self.gone.add(_str(m.get("item_id")))
        for e in _dicts(self.view.get("events")):
            for m in _dicts(e.get("members")):
                if self.law.withdrawn(m, self.hosts.get(_str(m.get("item_id")), "")):
                    self.gone.add(_str(m.get("item_id")))
        self.rows_by_item: dict[str, dict] = {}
        # effective membership of each survivor: its own rows and those of the
        # events merged into it (an item belongs to one event, once)
        self.effective: dict[str, list[dict]] = {}
        for e in self.store_events:
            root = self.roots.get(e["event_id"], e["event_id"])
            for m in _dicts(e.get("members")):
                iid = _str(m.get("item_id"))
                if iid and iid not in self.rows_by_item:
                    self.rows_by_item[iid] = m
                    self.effective.setdefault(root, []).append(m)

        # ---- civic calendar, roadworks source (R10 per lane), registre ----------
        # A withdrawn lane lends nothing: no row, no name, no link, no anchor.
        self.roadworks_state = self.law.lane_state("wzdx", self.roadworks_doc)
        self.civic_state = self.law.lane_state("civic-html", civic)
        for lane, lane_state in (("roadworks (wzdx)", self.roadworks_state),
                                 ("consultations (civic-html)", self.civic_state)):
            if lane_state:
                diagnosis.append(f"{lane} lane {lane_state.replace('_', ' ')}: none of its records is quoted, "
                                 "credited, counted or linked this render")
        civ = civic if isinstance(civic, dict) and not self.civic_state else {}
        self.civic_name = plain(civ.get("institution_name"), NAME_CAP)
        self.civic = {str(r.get("event_id")): r for r in _dicts(civ.get("events")) if r.get("event_id")}
        civ_rec = {} if self.civic_state else (
            self.law.by_id.get(_str(civ.get("source_id"))) or next(
                (r for r in self.law.records if r.get("type") == "civic-html"), {}))
        self.civic_record_name = plain(civ_rec.get("institution_name") or civ_rec.get("name"), NAME_CAP)
        rw_sid = _str(self.roadworks_doc.get("source_id")) if isinstance(self.roadworks_doc, dict) else ""
        wz = {} if self.roadworks_state else (
            self.law.by_id.get(rw_sid) or next((r for r in self.law.records if r.get("type") == "wzdx"), {}))
        self.roadworks_name = plain(wz.get("institution_name"), NAME_CAP)
        self.roadworks_home = ck.safe_url(wz.get("homepage"))
        self.anchor_scope = self._anchor_scope()
        self.state = self._state(state)
        self.seals = self._published_seals()

    def _anchor_scope(self) -> list[str]:
        """Names of the institutions whose records the anchor rules read this
        render, in registry order: followed official feeds, and the roadworks
        and consultation lanes unless withdrawn. Printed when an event has no
        anchor, so a withdrawn institution is never named there (R10)."""
        lanes = {"wzdx": self.roadworks_state, "civic-html": self.civic_state}
        names: list[str] = []
        for rec in self.law.records:
            if _str(rec.get("source_kind")) != "official":
                continue
            typ = _str(rec.get("type"))
            if typ == "rss":
                ok = _str(rec.get("id")) in self.law.enabled
            elif typ in lanes:
                ok = (rec.get("enabled") is True and not lanes[typ] and not self.law.unreadable
                      and self.law.rules.match_source(rec) is None)
            else:
                ok = False
            name = plain(rec.get("institution_name") or rec.get("name"), NAME_CAP)
            if ok and name and ck.fold(name) not in {ck.fold(n) for n in names}:
                names.append(name)
        return names

    # registre ------------------------------------------------------------------
    def _state(self, raw: object) -> dict:
        """The registre state with registre.load_state's own acceptance rules
        (method, then seals that carry seq, root, edition and record), read
        from the given document instead of a file."""
        empty = {"seals": [], "voice": [], "names": {}}
        try:
            import registre  # noqa: PLC0415

            if not isinstance(raw, dict) or raw.get("method") != registre.METHOD or not isinstance(raw.get("seals"), list):
                if raw is not None:
                    self.diag.append("registre state not readable as registre-v1: no seal, roster names from sources")
                return empty
            seals = [s for s in raw["seals"]
                     if isinstance(s, dict) and isinstance(s.get("record"), dict)
                     and isinstance(s.get("seq"), int) and not isinstance(s.get("seq"), bool)
                     and isinstance(s.get("root"), str) and isinstance(s.get("edition"), str)
                     and isinstance(s["record"].get("edition"), str)]
            voice = [registre.migrate_voice_row(v) for v in raw.get("voice") or []
                     if isinstance(v, dict) and v.get("edition")]
            names = raw.get("names") if isinstance(raw.get("names"), dict) else {}
            return {"method": registre.METHOD, "seals": seals, "voice": voice, "names": names}
        except Exception as exc:  # noqa: BLE001
            self.diag.append(f"registre state unusable ({type(exc).__name__}): no seal, roster names from sources")
        return empty

    def _published_seals(self) -> list[dict]:
        try:
            import registre  # noqa: PLC0415

            cap = registre.PUBLIC_SEAL_CAP
        except Exception:  # noqa: BLE001
            cap = 200
        seals = [s for s in _dicts(self.state.get("seals"))
                 if isinstance(s.get("seq"), int) and not isinstance(s.get("seq"), bool)
                 and isinstance(s.get("root"), str) and _instant(s.get("edition")) is not None]
        return seals[-cap:]

    def seal_of(self, edition: object) -> dict | None:
        when = _instant(edition)
        if when is None:
            return None
        for s in reversed(self.seals):
            if _instant(s.get("edition")) == when:
                return s
        return None


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

def _scrub_ids(obj, gone: set):
    """Return `obj` without any withdrawn item id: dict keys, list entries and
    string values equal to one are dropped (recursively). A defence in depth for
    the ranking input; the public pages never print these fields."""
    if not gone:
        return obj
    if isinstance(obj, dict):
        return {k: _scrub_ids(v, gone) for k, v in obj.items() if k not in gone and not (isinstance(v, str) and v in gone)}
    if isinstance(obj, list):
        return [_scrub_ids(v, gone) for v in obj if not (isinstance(v, str) and v in gone)]
    return obj

def _live_rows(r: Render, rows: list[dict]) -> list[dict]:
    return [m for m in rows if _str(m.get("item_id")) and _str(m.get("item_id")) not in r.gone
            and not r.law.withdrawn(m, r.hosts.get(_str(m.get("item_id")), ""))]


def _effective_rows(r: Render, e: dict) -> list[dict]:
    """A store survivor's members plus those of the events merged into it."""
    return list(r.effective.get(e["event_id"]) or [])


def _independence(ids: list[str], rows: dict[str, dict], copies: object) -> dict:
    try:
        import events  # noqa: PLC0415

        pairs = [p for p in (copies if isinstance(copies, list) else [])
                 if isinstance(p, list) and len(p) == 2 and p[0] in rows and p[1] in rows]
        return events.independence(ids, rows, pairs)
    except Exception:  # noqa: BLE001 - one group per owner, the plain rule
        groups: dict[str, list[str]] = {}
        for i in sorted(ids):
            row = rows.get(i) or {}
            key = _str(row.get("owner_group")) or _str(row.get("institution")) or i
            groups.setdefault(key, []).append(i)
        out = sorted(sorted(g) for g in groups.values())
        return {"groups": out, "count": max(1, len(out))}


def _member(r: Render, row: dict, text: dict | None) -> dict:
    sid = _str(row.get("source_id"))
    m = {k: row.get(k) for k in ("item_id", "institution", "source_id", "language", "published_at", "first_seen",
                                 "date_suspect", "origin_class", "ownership_class", "owner_group")}
    m["institution_name"] = r.law.name_of(sid, _str((text or {}).get("institution_name")) or _str(row.get("institution")))
    url = _str(row.get("url")) or _str((text or {}).get("url"))
    if sid in r.law.enabled and ck.safe_url(url):
        m["url"] = url
    if text is not None:
        m["title"] = plain(text.get("title"), TITLE_CAP)
        m["excerpt"] = plain(text.get("summary"))
        author = plain(text.get("author"), NAME_CAP)
        credit = plain(text.get("photo_credit"), NAME_CAP)
        if author:
            m["author"] = author
        if credit:
            m["photo_credit"] = credit
    return m


def anchor_live(r: Render, a: dict) -> bool:
    """R10 for one anchor, the same test for the ranking's input and for every
    page: a pointer to a withdrawn record goes with it (never credited,
    counted or linked). An item anchor whose item is withdrawn; a roadwork
    anchor while the roadworks lane is withdrawn; a consultation anchor while
    the calendar is withdrawn, or whose own fiche URL is."""
    typ, ref = _str(a.get("type")), _str(a.get("ref"))
    if typ in ("official_item", "outage"):
        return ref not in r.gone
    if typ == "roadwork":
        return not r.roadworks_state
    if typ == "consultation":
        if r.civic_state:
            return False
        entry = r.civic.get(ref) or {}
        if _str(entry.get("source_id")) and _str(entry.get("source_id")) in r.law.rules.sources:
            return False
        return r.law.rules.match_url(entry.get("url")) is None
    return True


def _anchor_views(r: Render, anchors: list[dict], current: bool) -> list[dict]:
    out = []
    for a in anchors:
        typ, ref = _str(a.get("type")), _str(a.get("ref"))
        if typ not in ck.ANCHOR_TYPES or not ref or typ == "edition_seal":
            continue
        if not anchor_live(r, a):
            continue  # R10: a pointer to a withdrawn record goes with it
        row = {"type": typ, "ref": ref, "rule": _str(a.get("rule")), "status": "linked_by_rule"}
        if typ in ("official_item", "outage"):
            text = r.texts.get(ref)
            stored = r.rows_by_item.get(ref) or {}
            sid = _str((text or {}).get("source_id")) or _str(stored.get("source_id"))
            row["institution_name"] = r.law.name_of(sid, _str((text or {}).get("institution_name")) or _str(stored.get("institution")))
            row["published_at"] = _str((text or {}).get("published_at")) or _str(stored.get("published_at"))
            url = _str((text or {}).get("url")) or _str(stored.get("url"))
            if sid in r.law.enabled and ck.safe_url(url):
                row["url"] = url
            if text is not None and current:
                row["title"] = plain(text.get("title"), TITLE_CAP)
                row["language"] = _str(text.get("language")) or "fr"
        elif typ == "consultation":
            entry = r.civic.get(ref) or {}
            row["institution_name"] = r.civic_name or r.civic_record_name
            if ck.safe_url(entry.get("url")):
                row["url"] = entry["url"]   # anchor_live already refused a withdrawn fiche
            if current and entry.get("title"):
                row["title"] = plain(entry.get("title"), TITLE_CAP)
                row["language"] = "fr"
        elif typ == "roadwork":
            row["institution_name"] = r.roadworks_name
            if r.roadworks_home:
                row["url"] = r.roadworks_home
        out.append(row)
    return out


def _why_and_words(r: Render, ms: list[dict], matcher: dict) -> tuple[list[dict], list[dict]]:
    """The matcher's own reasons, computed now over this collection's texts
    (never stored): the anchors that tie the first article to each other one."""
    ctx, views = matcher.get("ctx"), matcher.get("items") or {}
    current = [views[m["item_id"]] for m in ms if m.get("item_id") in views]
    if ctx is None or len(current) < 2:
        return [], []
    try:
        import event_match as em  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return [], []
    shared: list[dict] = []
    words: list[dict] = []
    seen_s: set[str] = set()
    seen_w: set[str] = set()
    first = current[0]
    for other in current[1:]:
        try:
            reasons = em.match(first, other, ctx).reasons
        except Exception:  # noqa: BLE001 - one pair without reasons, never a crash
            continue
        for rs in reasons:
            if rs.get("sign") != "+" or not isinstance(rs.get("anchors"), list):
                continue
            for pair in rs["anchors"]:
                if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) and x for x in pair)):
                    continue
                a, b = plain(pair[0], 60), plain(pair[1], 60)
                if not a or not b:
                    continue
                label = a if ck.fold(a) == ck.fold(b) else f"{a} ≙ {b}"
                key = ck.fold(label)
                if key not in seen_s and len(shared) < SHARED_MAX:
                    seen_s.add(key)
                    shared.append({"label": label, "alias": ck.fold(a) != ck.fold(b)})
                if rs.get("code") in ("title", "tokens", "cross_tokens", "names", "place") and key not in seen_w \
                        and len(words) < WORDS_MAX:
                    seen_w.add(key)
                    forms = [a] if ck.fold(a) == ck.fold(b) else [a, b]
                    words.append({"k": ck.fold(a), "label": label, "forms": forms, "alias": ck.fold(a) != ck.fold(b)})
    return shared, words


def _journal(r: Render, e: dict, rows: list[dict], published: set[str]) -> dict:
    joins: dict[str, int] = {}
    for m in rows:
        fs = _str(m.get("first_seen"))
        if _instant(fs) is not None:
            joins[fs] = joins.get(fs, 0) + 1
    lineage = e.get("lineage") if isinstance(e.get("lineage"), dict) else {}
    out: dict = {"born": _str(e.get("born_edition")), "last": _str(e.get("last_edition")),
                 "joins": [{"edition": k, "n": joins[k]} for k in sorted(joins, key=lambda s: (_epoch(s) or 0.0, s))],
                 "detached": len(_strs(lineage.get("detached")))}
    into = _str(lineage.get("merged_into"))
    if EVENT_ID.match(into):
        out["merged_into"] = {"event_id": into, "at": _str(lineage.get("merged_at")),
                              "href": ck.event_path(into) if into in published else ""}
    absorbed = [x for x in _strs(lineage.get("absorbed")) if EVENT_ID.match(x)]
    if absorbed:
        out["absorbed"] = [{"event_id": x, "href": ck.event_path(x) if x in published else ""} for x in sorted(absorbed)]
    return out


def _seals_for(r: Render, rows: list[dict], in_current: bool) -> list[int]:
    seqs = set()
    for m in rows:
        s = r.seal_of(m.get("first_seen"))
        if s is not None:
            seqs.add(s["seq"])
    if in_current:
        s = r.seal_of(r.clock)
        if s is not None:
            seqs.add(s["seq"])
    return sorted(seqs)


def _places(e: dict) -> tuple[list[str], str]:
    places = [p for p in _strs(e.get("places")) if p]
    return places, _str(e.get("place_basis"))


def build_view(r: Render, e: dict, rows: list[dict], *, current: bool, in_current: bool,
               matcher: dict, published: set[str], quality_chip: dict, current_ids: set[str]) -> dict | None:
    """One EventView for the kit. `rows` are the member rows (already R10
    filtered); `current` joins this collection's texts (else none at all)."""
    if not rows:
        return None
    eid = e["event_id"]
    by_id = {_str(m["item_id"]): m for m in rows}
    ids = sorted(by_id)
    texted = {i for i in ids if i in r.texts} if current else set()
    members = [_member(r, by_id[i], r.texts.get(i) if i in texted else None) for i in ids]
    if current:
        for m in members:
            if m["item_id"] not in texted:
                m["text_gone"] = True
    places, basis = _places(e)
    type_code = _str(e.get("type")) or vocabulaire.UNCLASSIFIED
    label = e.get("label") if isinstance(e.get("label"), dict) else {}
    label = {lang: _str(label.get(lang)) or vocabulaire.label(type_code, None, lang) for lang in LANGS}
    # The place is always worded: a named or geo place by its label ("Hors
    # Québec" included); an event no evidence places (basis "fallback", or no
    # place at all) by the place twin of "unclassified", "Lieu non établi",
    # never by a code it may still carry from before that rule (EVENTS.md 17.1).
    if places and basis != "fallback":
        place_label = _labels_of(places[0], "place")
    else:
        place_label = _labels_of(vocabulaire.FALLBACK_PLACE, "place")
    pairs = [p for p in _dicts(e.get("language_pairs")) if p.get("fr") in by_id and p.get("en") in by_id]
    facts_rows = []
    if current:
        for f in _dicts(e.get("facts")):
            vals = []
            for v in _dicts(f.get("values")):
                stated = [i for i in _strs(v.get("stated_by")) if i in by_id]
                if stated:
                    insts = sorted({_str(by_id[i].get("institution")) for i in stated})
                    value = v.get("value")
                    slot = f.get("slot") if isinstance(f.get("slot"), dict) else {}
                    if slot.get("kind") == "entity" and isinstance(value, str):
                        value = r.law.name_of(value, value) if value in r.law.by_id else _entity_name(r, value)
                    vals.append({"value": value, "stated_by": stated, "institutions": insts})
            if vals:
                stating = {i for v in vals for i in v["stated_by"]}
                facts_rows.append({"slot": f.get("slot"), "values": vals,
                                   "divergent": bool(f.get("divergent")) and len(vals) >= 2 and len(stating) >= 2})
    why: dict = {"window_hours": ck.EVENT_SPAN_HOURS, "min_institutions": None, "rule": MATCH_RULE}
    words: list[dict] = []
    if current and len(ids) > 1:
        shared, words = _why_and_words(r, [m for m in members if m["item_id"] in texted], matcher)
        if shared:
            why["shared"] = shared
        if basis == "named" and place_label:
            why["place_anchor"] = place_label
    silence = []
    if current:
        for s in _dicts(e.get("silence")):
            inst = _str(s.get("institution"))
            # a voice withdrawn whole is named nowhere on these pages (the
            # registre and the sources page say it was withdrawn on request)
            if (inst and inst in r.law.withdrawn_institutions) or _str(s.get("scope")) == "institution":
                continue
            if inst in {_str(m.get("institution")) for m in rows}:
                continue
            # the state as measured: "withdrawn" (its article here was
            # withdrawn on request) is said as such, never as "no linked article"
            silence.append({"institution_name": plain(s.get("institution_name") or inst, NAME_CAP),
                            "state": _str(s.get("state"))})
    neighbours = []
    if current:
        for n in _dicts(e.get("neighbours")):
            other = _str(n.get("event_id"))
            if other not in current_ids or other == eid:
                continue
            lead = matcher.get("leads", {}).get(other)
            if lead is None:
                continue
            neighbours.append({"member": lead, "reason": "shared_word", "why": None})
            if len(neighbours) >= NEIGHBOURS_MAX:
                break
    view = {
        "event_id": eid,
        "permanent": not current,
        "type": type_code,
        "places": places,
        "label": label,
        "type_label": _labels_of(type_code, "type"),
        "place_label": place_label,
        "tier": _str(e.get("tier")) or None,
        "tier_view": quality_chip,
        "quality_href": QUALITY_PATH,
        "window_state": _str(e.get("window_state")),
        "activity": _str(e.get("activity")),
        "born_edition": _str(e.get("born_edition")),
        "last_edition": _str(e.get("last_edition")),
        "seals": _seals_for(r, rows, in_current),
        "members": members,
        "languages": sorted({_str(m.get("language")) for m in rows if m.get("language") in ("fr", "en")}),
        "independence": _independence(ids, by_id, e.get("copies")),
        "language_pairs": pairs,
        "anchors": _anchor_views(r, _dicts(e.get("anchors")), current),
        "anchor_scope": list(r.anchor_scope),
        "facts": facts_rows,
        "why": why,
        "words": words if current else [],
        "silence": silence,
        "neighbours": neighbours,
        "journal": _journal(r, e, rows, published),
    }
    return view


def _entity_name(r: Render, value: str) -> str:
    for rec in r.law.records:
        if _str(rec.get("institution")) == value:
            return plain(rec.get("institution_name") or value, NAME_CAP)
    return value


def _matcher(r: Render, current_events: list[tuple[dict, list[dict]]]) -> dict:
    """The matcher's context over this collection's texts (as events.py built
    it), the item views for the reasons, and each current event's lead
    article for the neighbours panel (its first article with text)."""
    out: dict = {"ctx": None, "items": {}, "leads": {}}
    for e, rows in current_events:
        for m in sorted(rows, key=lambda x: (_str(x.get("published_at")) or _str(x.get("first_seen")), _str(x.get("item_id")))):
            if m.get("item_id") in r.texts:
                out["leads"][e["event_id"]] = _member(r, m, r.texts[m["item_id"]])
                break
    if not any(len([m for m in rows if m.get("item_id") in r.texts]) > 1 for _e, rows in current_events):
        return out
    try:
        import event_match as em  # noqa: PLC0415
        import events  # noqa: PLC0415

        reg = r.law.reg
        if reg is None:
            return out
        items = {}
        for iid in sorted(r.texts):
            c = r.texts[iid]
            if _str(c.get("source_id")) in reg.enabled:
                items[iid] = events.item_view(c, reg, r.clock)
        out["items"] = items
        out["ctx"] = em.MatchContext([items[k] for k in sorted(items)])
    except Exception as exc:  # noqa: BLE001 - no reasons, never a crash
        r.diag.append(f"matcher reasons unavailable ({type(exc).__name__}): groupings keep their counts and rule")
    return out


# --------------------------------------------------------------------------- #
# Front door blocks
# --------------------------------------------------------------------------- #
# enrich's proposed geography, in the three groups the published rule names
# (rule.official / off.cap): Québec City, then Quebec, then everything else
# (linked or not placed) as ONE group, so "within each group, newest first"
# is exactly what the sort does.
GEO_GROUPS = ("city", "province", "other")
GEO_RANK = {"quebec-city": 0, "quebec": 1}


def _geo_of(c: dict) -> str:
    """The method's proposed geography of an item (enrich), never guessed here."""
    block = c.get("enrich") if isinstance(c.get("enrich"), dict) else {}
    geo = block.get("geo") if isinstance(block.get("geo"), dict) else {}
    return _str(geo.get("geo")) or _str(c.get("geo"))


def _geo_rank(c: dict) -> int:
    return GEO_RANK.get(_geo_of(c), len(GEO_GROUPS) - 1)


def card_records(cards: list[dict]) -> set[str]:
    """Every item a card shown contains or cites as an official record (its
    members and its item anchors): none of them may be listed again under
    "declared by the authorities", whose heading says none is linked above."""
    out = {_str(m.get("item_id")) for v in cards for m in v.get("members") or []}
    out |= {_str(a.get("ref")) for v in cards for a in v.get("anchors") or []
            if a.get("type") in ("official_item", "outage")}
    out.discard("")
    return out


def in_official_window(c: dict, clock: object) -> bool:
    """A release is inside the edition's freshness window when its declared
    publication time is at most OFFICIAL_WINDOW_HOURS (the event window)
    before the edition clock, and not more than OFFICIAL_FUTURE_HOURS after it
    (later than that is a suspect stamp, the events layer's DATE_SUSPECT_HOURS).
    No declared time, or no edition clock: freshness is not established, so
    the release counts as outside (never guessed inside)."""
    when, edition = _instant(c.get("published_at")), _instant(clock)
    if when is None or edition is None:
        return False
    age = (edition - when).total_seconds()
    return -OFFICIAL_FUTURE_HOURS * 3600 <= age <= OFFICIAL_WINDOW_HOURS * 3600


def official_items(r: Render, card_items: set[str]) -> dict:
    """The published rule.official (ranking.md, « Déclaré par les autorités »),
    with the real values the block prints:

      1. the official releases of the collection that no card shown contains
         or cites as an official record;
      2. first those inside the edition's freshness window
         (`in_official_window`), ordered by the method's proposed geography
         (Québec City, Quebec, everything else as one group), then newest
         first (declared time), then id; at most OFFICIAL_MAX;
      3. only when fewer than OFFICIAL_MAX remain inside the window, the rest
         is completed with releases outside it (or with no established date),
         in the same order, each marked `older` (the block says so in words).

    Returns {"rows", "total", "fresh", "older", "groups" (of the fresh ones),
    "window_hours"}."""
    rows = []
    for iid in sorted(r.texts):
        c = r.texts[iid]
        sid = _str(c.get("source_id"))
        if r.law.kind_of(sid) != "official" or sid not in r.law.enabled or iid in card_items:
            continue
        title = plain(c.get("title"), TITLE_CAP)
        if not title:
            continue
        rows.append(c)

    def order(c: dict) -> tuple:
        return (_geo_rank(c), -(_epoch(c.get("published_at")) or 0.0), _str(c.get("id")))

    fresh = sorted((c for c in rows if in_official_window(c, r.clock)), key=order)
    older = sorted((c for c in rows if not in_official_window(c, r.clock)), key=order)
    groups = {g: 0 for g in GEO_GROUPS}
    for c in fresh:
        groups[GEO_GROUPS[_geo_rank(c)]] += 1
    shown = [(c, False) for c in fresh[:OFFICIAL_MAX]]
    shown += [(c, True) for c in older[:max(0, OFFICIAL_MAX - len(shown))]]
    out = []
    for c, aged in shown:
        sid = _str(c.get("source_id"))
        out.append({"institution_name": r.law.name_of(sid, _str(c.get("institution_name"))),
                    "ownership_class": _str((r.law.by_id.get(sid) or {}).get("ownership_class")) or "government",
                    "published_at": _str(c.get("published_at")), "title": plain(c.get("title"), TITLE_CAP),
                    "url": _str(c.get("url")) if ck.safe_url(c.get("url")) else "",
                    "language": _str(c.get("language")) or _str((r.law.by_id.get(sid) or {}).get("language")) or "fr",
                    "older": aged})
    return {"rows": out, "total": len(rows), "fresh": len(fresh), "older": len(older), "groups": groups,
            "window_hours": OFFICIAL_WINDOW_HOURS}


def roster(r: Render, cards: list[dict]) -> list[dict]:
    collection: dict = {}
    try:
        import registre  # noqa: PLC0415

        collection = registre.institution_collection(self_status(r), r.law.sources_path)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - a malformed sources.yaml exits in its parser
        r.diag.append(f"collection facts unavailable ({type(exc).__name__}): states not established")
    names: dict[str, str] = {}
    try:
        import registre  # noqa: PLC0415

        for row in registre.institution_register(r.state, r.law.withdrawn_institutions, {}):
            names[_str(row.get("institution_id"))] = plain(row.get("institution_name"), NAME_CAP)
    except Exception:  # noqa: BLE001 - names then come from sources.yaml
        pass
    in_cards: dict[str, int] = {}
    for ev in cards:
        for inst in sorted({_str(m.get("institution")) for m in ev.get("members") or []}):
            in_cards[inst] = in_cards.get(inst, 0) + 1
    out = []
    for inst in r.law.followed():
        iid = _str(inst.get("institution_id"))
        if not iid or iid in r.law.withdrawn_institutions:
            continue
        name = names.get(iid) or plain(inst.get("institution_name") or iid, NAME_CAP)
        facts = collection.get(iid) if isinstance(collection.get(iid), dict) else None
        row = {"name": name, "events": in_cards.get(iid, 0), "articles": 0}
        if in_cards.get(iid):
            row["state"] = "in_events"
        elif facts is None:
            row["state"] = "not_established"
        elif _int(facts.get("feeds_total")) and _int(facts.get("feeds_ok")) < _int(facts.get("feeds_total")):
            row["state"] = "collection_gap"
        elif _int(facts.get("items")) > 0:
            row["state"] = "declared" if _str(inst.get("source_kind")) == "official" else "outside"
            row["articles"] = _int(facts.get("items"))
            row["scope"] = "shown"
        else:
            row["state"] = "no_items"
        out.append(row)
    return out


def self_status(r: Render) -> dict:
    status = r.enriched.get("source_status")
    return status if isinstance(status, dict) else {}


def suggestions(cards: list[dict]) -> list[str]:
    """Examples for "Chez moi": the specific places the cards name, in card
    order (Vigie's own labels; never a roadworks street, so the hourly lane
    changes the roadworks block and nothing else)."""
    out: list[str] = []
    seen: set[str] = set()
    for ev in cards:
        for code in ev.get("places") or []:
            try:
                if vocabulaire.is_scope(code) or vocabulaire.place_kind(code) is None:
                    continue
                text = vocabulaire.place_label(code, "fr")
            except Exception:  # noqa: BLE001
                continue
            if text and ck.fold(text) not in seen:
                seen.add(ck.fold(text))
                out.append(text)
            if len(out) >= SUGGESTIONS_MAX:
                return out
    return out


def roadworks_view(r: Render) -> tuple[dict, str]:
    """(RoadworksView, collection clock). anchors.roadworks_view measures the
    age against the later of the edition clock and the collection.

    R10, applied here because the hourly roads lane re-renders from the stored
    file: when the lane is withdrawn (or the takedown register is unreadable)
    the view is only that marker (the block then quotes, counts, credits and
    links nothing, and has no collection clock), and the City's map link is
    dropped when its domain is withdrawn."""
    if r.roadworks_state:
        return {"withdrawn": r.roadworks_state, "rows": []}, ""
    doc = r.roadworks_doc if isinstance(r.roadworks_doc, dict) else {}
    try:
        import anchors  # noqa: PLC0415

        view = anchors.roadworks_view(doc, r.clock)
    except Exception as exc:  # noqa: BLE001
        r.diag.append(f"anchors.roadworks_view unavailable ({type(exc).__name__}); kit adapter")
        view = ck.roadworks_view(doc, render_clock=r.clock)
    if not view.get("institution_name") and r.roadworks_name:
        view["institution_name"] = r.roadworks_name
    if r.law.rules.match_url(ck.RW_MAP_URL) is not None:
        view["map_url"] = ""
    return view, _str(view.get("collected_at"))


# --------------------------------------------------------------------------- #
# latest.json
# --------------------------------------------------------------------------- #
def _origin_counts(members: list[dict]) -> dict:
    out: dict[str, int] = {}
    for m in members:
        cls = _str(m.get("origin_class")) or "unknown"
        out[cls] = out.get(cls, 0) + 1
    return dict(sorted(out.items()))


def latest_doc(r: Render, entries: list[dict], pages: dict[str, str], ranking_name: str, counts: dict) -> dict:
    """The machine view (no publisher text; no roadworks field, so the hourly
    lane leaves it as it was unless the ranking itself reads roadworks)."""
    institutions: dict[str, dict] = {}
    events_out = []
    for ent in entries:
        v = ent["view"]
        members = []
        for m in v["members"]:
            row = {k: m.get(k) for k in ("item_id", "institution", "source_id", "language", "published_at",
                                         "first_seen", "origin_class", "ownership_class", "owner_group")}
            if m.get("url"):
                row["url"] = m["url"]
            members.append(row)
            sid = _str(m.get("source_id"))
            inst = _str(m.get("institution"))
            if inst and inst not in institutions:
                institutions[inst] = {"name": _str(m.get("institution_name")) or inst,
                                      "source_kind": r.law.kind_of(sid), **r.law.declaration(sid)}
        ind = v.get("independence") or {}
        events_out.append({
            "event_id": v["event_id"],
            "path": {"fr": ck.event_path(v["event_id"]), "en": ck.en_path(ck.event_path(v["event_id"]))},
            "type": v["type"], "type_label": v["type_label"],
            "places": v["places"], "place_label": v["place_label"], "label": v["label"],
            # every place code with its words ("elsewhere", "unplaced" included)
            "place_labels": [{"code": p, **_labels_of(p, "place")} for p in v["places"]],
            "born_edition": v["born_edition"], "last_edition": v["last_edition"],
            "window_state": v["window_state"], "activity": v["activity"],
            "tier": v["tier"], "tier_chip": (v.get("tier_view") or {}).get("kind") or "none",
            "position": ent.get("position"), "shown": bool(ent.get("shown")),
            "rank_criteria": ent.get("criteria") or {},
            "member_count": len(members), "institutions": sorted({_str(m.get("institution")) for m in members}),
            "languages": v["languages"],
            "independence": {"count": _int(ind.get("count")), "groups": ind.get("groups") or [],
                             "reporting_count": _int(ind.get("reporting_count"), _int(ind.get("count")))},
            "origin": _origin_counts(members),
            "members": members,
            "anchors": [{k: a[k] for k in ("type", "ref", "rule", "status") if k in a} for a in v["anchors"]],
            "language_pairs": v["language_pairs"],
            "seals": v["seals"],
            "lineage": {k: x for k, x in (v.get("journal") or {}).items() if k in ("merged_into", "absorbed", "detached")},
        })
    return {
        "format": "vigie-evenements-v1",
        "method": METHOD,
        "edition": r.clock,
        "status": r.status,
        "ranking": ranking_name,
        "rules": {
            "cards_max": CARDS_MAX, "pages_max": PAGES_MAX, "official_max": OFFICIAL_MAX,
            "official_window_hours": OFFICIAL_WINDOW_HOURS,
            "pages": {lang: i18n.t("rule.pages", lang, n=i18n.fmt_int(PAGES_MAX, lang)) for lang in LANGS},
            "ownership": "ownership_class, owner_group and ownership_ref are the declarations of sources.yaml, each with its public reference; ownership_words are the only ownership words the pages print (the owner's structure, as /methode/sources.html states it; a class code is never printed); members sharing an owner_group count as one origin",
        },
        "counts": counts,
        "institutions": dict(sorted(institutions.items())),
        "events": events_out,
        "pages": dict(sorted(pages.items())),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def _home(lang: str, mode: str = "") -> str:
    # Live: / and /en/ are the front door in both languages. Otherwise "/"
    # exists in French only (the brief), and the English home is this surface.
    if mode == "live":
        return ck.path_for(lang, "/")
    return "/" if lang == "fr" else ck.path_for("en", "/evenements.html")


def _merged_page(r: Render, e: dict, survivor_view: dict, lang: str, note: str) -> str:
    eid = e["event_id"]
    label = e.get("label") if isinstance(e.get("label"), dict) else {}
    title = ck.loc({k: _str(label.get(k)) for k in LANGS}, lang) or ck.loc(survivor_view.get("label"), lang)
    lineage = e.get("lineage") if isinstance(e.get("lineage"), dict) else {}
    when = i18n.fmt_datetime(lineage.get("merged_at"), lang)
    text = i18n.t("merged.p", lang, d=when) if when else i18n.t("merged.p.nodate", lang)
    target = ck.path_for(lang, ck.event_path(survivor_view["event_id"]))
    go = i18n.t("merged.go", lang, label=ck.loc(survivor_view.get("label"), lang))
    main = (f'{ck.internal_link(lang, "/evenements.html", i18n.t("crumb.index", lang), cls="crumb")}'
            f'<header class="evp-head"><h1 id="h1" tabindex="-1">{ck.esc(title)}</h1>'
            f'<p class="facts-line"><code>{ck.esc(eid)}</code></p></header>'
            f'<section class="card panel"><p class="m0">{ck.esc(text)}</p>'
            f'<p class="mt-s"><a class="btn primary" href="{ck.esc(target)}">{ck.esc(go)}</a></p></section>')
    desc = i18n.t("meta.desc.event.merged", lang, label=title)
    return ck.document(lang=lang, fr_path=ck.event_path(eid), title=title, description=desc, main=main,
                       robots=r.robots, mine_href=ck.path_for(lang, "/evenements.html") + "#chez-moi",
                       page_type="article", home=_home(lang, r.mode), footer_note=note, view="permanent")


def build_site(r: Render) -> tuple[dict[str, str], dict]:
    """Every file of the surface, path -> text, and the counts. Pure on the
    Render (no I/O)."""
    diag = r.diag
    # ---- current events, R10 filtered -----------------------------------------
    current_raw: list[tuple[dict, list[dict]]] = []
    view_events = _dicts(r.view.get("events")) if r.view_ok else []
    seen_ids: set[str] = set()
    for e in sorted(view_events, key=lambda x: _str(x.get("event_id"))):
        eid = _str(e.get("event_id"))
        if not EVENT_ID.match(eid) or eid in seen_ids:
            continue
        seen_ids.add(eid)
        rows = _live_rows(r, _dicts(e.get("members")))
        if rows:
            current_raw.append((e, rows))
    current_ids = {e["event_id"] for e, _rows in current_raw}

    # ---- ranking input: the view events as the pages count them ---------------
    rank_input = []
    for e, rows in current_raw:
        by_id = {_str(m["item_id"]): m for m in rows}
        ids = sorted(by_id)
        # R10 before ranking: no withdrawn id anywhere in what the ranking reads
        x = {k: copy.deepcopy(v) for k, v in e.items() if k not in ("members", "silence", "withdrawn")}
        x["members"] = [dict(m) for m in rows]
        x["copies"] = [p for p in (x.get("copies") if isinstance(x.get("copies"), list) else [])
                       if isinstance(p, list) and len(p) == 2 and p[0] in by_id and p[1] in by_id]
        x["language_pairs"] = [p for p in _dicts(x.get("language_pairs")) if p.get("fr") in by_id and p.get("en") in by_id]
        facts_live = []
        for f in _dicts(x.get("facts")):
            vals = [{**v, "stated_by": [i for i in _strs(v.get("stated_by")) if i in by_id]} for v in _dicts(f.get("values"))]
            vals = [v for v in vals if v["stated_by"]]
            if vals:
                facts_live.append({**f, "values": vals})
        x["facts"] = facts_live
        x["member_count"] = len(rows)
        x["institutions"] = sorted({_str(m.get("institution")) for m in rows})
        x["languages"] = sorted({_str(m.get("language")) for m in rows if m.get("language") in ("fr", "en")})
        x["independence"] = _independence(ids, by_id, e.get("copies"))
        x["anchors"] = [a for a in _dicts(e.get("anchors")) if anchor_live(r, a)]
        x["in_edition"] = [i for i in _strs(e.get("in_edition")) if i in by_id]
        # R10, whatever new private field the builder adds: no withdrawn item id survives anywhere
        # in what the ranking reads (dict keys, list entries, or values equal to a withdrawn id)
        gone_ids = {_str(m.get("item_id")) for m in _dicts(e.get("members"))} - set(by_id)
        gone_ids |= set(r.law.withdrawn_item_ids)
        x = _scrub_ids(x, gone_ids)
        rank_input.append(x)
    rw_view, rw_clock = roadworks_view(r)
    later = max((c for c in (r.clock, rw_clock) if _instant(c) is not None), key=lambda c: _epoch(c) or 0.0, default=None)
    # a withdrawn roadworks lane is not available to the ranking either (R10)
    rank_ctx = {"edition_clock": r.clock, "roadworks_view": None if r.roadworks_state else copy.deepcopy(rw_view),
                "now": later}
    rows, ranking_name = rank(rank_input, rank_ctx, r.ranking, diag) if rank_input else ([], FALLBACK_RANKING)
    pos = {row["event_id"]: row for row in rows}

    # ---- the page set ------------------------------------------------------------
    candidates: list[tuple[tuple, str, str]] = []   # (order key, event id, kind)
    for e, rows_ in current_raw:
        row = pos.get(e["event_id"]) or {}
        candidates.append(((0 if row.get("shown") else 1, _int(row.get("position"), 10 ** 6), ""), e["event_id"], "current"))
    perm_rows: dict[str, list[dict]] = {}
    for e in r.store_events:
        eid = e["event_id"]
        if eid in current_ids or r.roots.get(eid) != eid:
            continue
        rows_ = _live_rows(r, _effective_rows(r, e))
        if not rows_:
            continue
        multi = len({_str(m.get("institution")) for m in rows_}) >= 2
        anchored = bool(_anchor_views(r, _dicts(e.get("anchors")), False))
        if not (multi or anchored):
            continue
        perm_rows[eid] = rows_
        candidates.append(((2, -(_epoch(e.get("last_edition")) or 0.0), -(_epoch(e.get("born_edition")) or 0.0)), eid, "permanent"))
    for e in r.store_events:
        eid = e["event_id"]
        root = r.roots.get(eid, eid)
        if root == eid:
            continue
        candidates.append(((2, -(_epoch(e.get("last_edition")) or 0.0), -(_epoch(e.get("born_edition")) or 0.0)), eid, "merged"))
    candidates.sort(key=lambda c: (c[0], c[1]))
    chosen = candidates[:PAGES_MAX]
    published = {eid for _k, eid, _kind in chosen}
    # a merged record's page exists only while its survivor has one
    chosen = [c for c in chosen if c[2] != "merged" or r.roots.get(c[1]) in published]
    published = {eid for _k, eid, _kind in chosen}

    # ---- views -----------------------------------------------------------------
    followed = len([i for i in r.law.followed() if _str(i.get("institution_id")) not in r.law.withdrawn_institutions])
    matcher = _matcher(r, current_raw)
    current_map = {e["event_id"]: (e, rows_) for e, rows_ in current_raw}
    views: dict[str, dict] = {}
    entries: list[dict] = []
    for _key, eid, kind in chosen:
        if kind == "current":
            e, rows_ = current_map[eid]
            texted = any(m.get("item_id") in r.texts for m in rows_)
            chip = tier_view(e.get("tier"), r.quality, r.ranking, diag)
            v = build_view(r, e, rows_, current=texted, in_current=True, matcher=matcher,
                           published=published, quality_chip=chip, current_ids=current_ids)
            row = pos.get(eid) or {}
            if v is not None:
                if row.get("shown"):
                    v["rank"] = {"position": row["position"], "of": len(rows), "explain": row.get("explain") or []}
                views[eid] = v
                entries.append({"view": v, "position": row.get("position"), "shown": bool(row.get("shown")),
                                "criteria": row.get("criteria") or {}})
        elif kind == "permanent":
            e = r.store_by_id[eid]
            chip = tier_view(e.get("tier"), r.quality, r.ranking, diag)
            v = build_view(r, e, perm_rows[eid], current=False, in_current=False, matcher={},
                           published=published, quality_chip=chip, current_ids=current_ids)
            if v is not None:
                views[eid] = v
    cards = [views[row["event_id"]] for row in rows if row.get("shown") and row["event_id"] in views][:CARDS_MAX]

    # ---- files -----------------------------------------------------------------
    files: dict[str, str] = {}
    pages: dict[str, str] = {}
    official = official_items(r, card_records(cards))
    seal = r.seal_of(r.clock)
    edition = {
        "clock": r.clock, "period": "", "next_collection": None,
        "institutions_followed": followed, "events": cards,
        "events_total": len(current_raw) if len(current_raw) > len(cards) else None,
        "roster": roster(r, cards),
        "seal": {"seq": seal["seq"], "root": seal["root"]} if seal else None,
        "official": official["rows"], "official_total": official["total"], "official_groups": official["groups"],
        "official_fresh": official["fresh"], "official_older": official["older"],
        "official_window_hours": official["window_hours"],
        "suggestions": suggestions(cards),
        "status": "" if r.view_ok else "not_built",
        # the lede says the published ranking was not applied when it was not
        "ranking": "fallback" if cards and ranking_name == FALLBACK_RANKING else "",
    }
    live = r.mode == "live"
    if live:
        # the live front door: the former brief is its full river, and the
        # other surfaces' /#travaux links land on the roadworks block
        edition["river"] = RIVER_PATH
        edition["roads_id"] = ROADS_ANCHOR
    for lang in LANGS:
        base = "" if lang == "fr" else "en/"
        note = i18n.t("rule.pages", lang, n=i18n.fmt_int(PAGES_MAX, lang))
        front_note = note + " " + i18n.t("rule.official", lang, n=i18n.fmt_int(OFFICIAL_MAX, lang),
                                         h=i18n.fmt_int(OFFICIAL_WINDOW_HOURS, lang))
        # live: / and /en/ are the front door; /evenements.html keeps serving
        # the same page (every crumb and "Chez moi" link points there), with
        # the front door's canonical and alternates, so one URL is indexed
        index_path = "/" if live else "/evenements.html"
        front = ck.edition_page(edition, lang, roadworks=rw_view, fr_path=index_path,
                                robots=r.robots, home=_home(lang, r.mode), footer_note=front_note)
        files[f"{base}evenements.html"] = front
        if live:
            files[f"{base}index.html"] = front
        for _key, eid, kind in chosen:
            name = f"{base}evenements/{eid}.html"
            if kind == "merged":
                survivor = views.get(r.roots.get(eid, ""))
                if survivor is None:
                    continue
                files[name] = _merged_page(r, r.store_by_id[eid], survivor, lang, note)
                pages[eid] = "merged"
                continue
            v = views.get(eid)
            if v is None:
                continue
            files[name] = ck.event_page(v, lang, edition=edition if not v["permanent"] else None, followed=followed,
                                        robots=r.robots, home=_home(lang, r.mode), footer_note=note)
            pages[eid] = "permanent" if v["permanent"] else "current"
    counts = {
        "events_current": len(current_raw),
        "cards": len(cards),
        "pages": len(pages),
        "pages_current": sum(1 for k in pages.values() if k == "current"),
        "pages_permanent": sum(1 for k in pages.values() if k == "permanent"),
        "pages_merged": sum(1 for k in pages.values() if k == "merged"),
        "official_shown": len(official["rows"]), "official_total": official["total"],
        "official_fresh": official["fresh"], "official_older_shown": sum(1 for o in official["rows"] if o["older"]),
        "withdrawn_items_known": len(r.gone),
    }
    files["evenements/latest.json"] = _dump(latest_doc(r, entries, pages, ranking_name, counts))
    files["qualite.json"] = _dump(quality_doc(r.quality, r.ranking, diag))
    return dict(sorted(files.items())), {**counts, "ranking": ranking_name}


def write_site(files: dict[str, str], public_dir: Path) -> int:
    """Write every file, then remove the event pages this render did not
    produce (outside the rule, withdrawn, or merged away), and an English
    front door a live render left behind when this render is not live (the
    French root is the brief's to write). Returns removed."""
    for rel, text in files.items():
        store_io.write_text_atomic(public_dir / rel, text)
    removed = 0
    stale_front = public_dir / "en" / "index.html"
    if "en/index.html" not in files and stale_front.is_file():
        try:
            stale_front.unlink()
            removed += 1
        except OSError:
            pass
    for base in ("evenements", "en/evenements"):
        folder = public_dir / base
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_file() and PAGE_NAME.match(path.name) and f"{base}/{path.name}" not in files:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def emit(ctx: dict | None = None, *, public_dir: Path | str | None = None, data_dir: Path | str | None = None,
         sources_path: Path | str | None = None, takedowns_path: Path | str | None = None,
         mode: str | None = None) -> dict:
    """Render the event surfaces. Fail-soft: any fault prints a diagnosis and
    returns a status; it never raises and never stops the render.

    `ctx` (optional): already-loaded documents the integrator may hand over
    instead of re-reading them (`events_view`, `events_store`, `enriched`,
    `roadworks`, `civic`, `quality`, `registre_state`), `robots`, and
    `ranking_module` (a module object honouring contract R, for tests).

    `mode` (scripts/surfaces.py): "preview" renders every page `noindex,
    nofollow`; "live" also writes the front door (index.html, en/index.html)
    linking the full river (/le-point.html), every page indexable; "off"
    writes nothing; None keeps the robots given in `ctx` (direct use, tests)."""
    diagnosis: list[str] = []
    public = Path(public_dir) if public_dir is not None else PUBLIC
    data = Path(data_dir) if data_dir is not None else DATA
    if mode == "off":
        _say("event surfaces off (scripts/surfaces.py): nothing written")
        return {"method": METHOD, "status": "off", "diagnosis": []}
    ctx = dict(ctx or {})
    if mode is not None:
        if mode not in MODES:
            _say(f"unknown mode {str(mode)[:40]!r}: nothing written")
            return {"method": METHOD, "status": "off", "diagnosis": [f"unknown mode {str(mode)[:40]!r}"]}
        ctx["mode"] = mode
        ctx["robots"] = ROBOTS[mode]
    try:
        r = Render(data, Path(sources_path) if sources_path else SOURCES_PATH,
                   Path(takedowns_path) if takedowns_path else TAKEDOWNS_PATH, ctx, diagnosis)
        files, counts = build_site(r)
        removed = write_site(files, public)
        for d in sorted(set(diagnosis)):
            _say(d)
        _say(f"{counts['cards']} card(s) of {counts['events_current']} current event(s); {counts['pages']} record(s) "
             f"({counts['pages_current']} current, {counts['pages_permanent']} permanent, {counts['pages_merged']} merged) "
             f"x 2 languages; ranking {counts['ranking']}; {removed} stale page(s) removed -> {public}")
        return {"method": METHOD, "status": "ok", "mode": r.mode, **counts, "removed": removed,
                "files": len(files), "diagnosis": sorted(set(diagnosis))}
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - fail-soft: the brief still renders
        _say(f"skipped ({type(exc).__name__}: {str(exc)[:160]}); the event surfaces were not rendered")
        tail = traceback.format_exc(limit=3).strip().splitlines()[-3:]
        for line in tail:
            _say(f"  {line}")
        return {"method": METHOD, "status": "failed", "error": f"{type(exc).__name__}", "diagnosis": sorted(set(diagnosis))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--data-dir", type=Path, default=DATA)
    ap.add_argument("--public-dir", type=Path, default=PUBLIC)
    ap.add_argument("--sources", type=Path, default=SOURCES_PATH)
    ap.add_argument("--takedowns", type=Path, default=TAKEDOWNS_PATH)
    ap.add_argument("--mode", choices=MODES, default=None,
                    help="render as the switch would (scripts/surfaces.py); default: no switch")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 0
    emit(public_dir=args.public_dir, data_dir=args.data_dir, sources_path=args.sources, takedowns_path=args.takedowns,
         mode=args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
