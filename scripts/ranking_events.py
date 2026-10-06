"""ranking_events — the order of events, and the grouping chip, from evidence.

docs/AUTONOMY.md (binding): no hand-set weight, no editorial choice that
depends on a person. The order is a *rule*, computed from observable inputs,
explained on the page with the real values, recomputed every edition.

Pure functions. Python standard library only; no I/O, no network, no wall
clock, no randomness, no dependence on PYTHONHASHSEED. The only clock is the
one the caller passes in `ctx` (the later of the edition clock and the
roadworks collection), so the same inputs give the same bytes.

    rank_events(events, ctx)   -> [row, ...]      one row per event, display order
    summarize(rows)            -> dict            how many were not shown, and why
    tier_chip(tier, quality)   -> {"kind", "key", "values"}
    quality_public(quality)    -> dict            counts only (public/qualite.json)

The ordering rule (also published, in words, in ranking.md)
------------------------------------------------------------
Successive criteria, compared one after the other, never added up:

  1. now        the event is anchored to an official obstruction that is in
                force now, or starts within the next 24 hours, in its place.
                Only declarations that actually obstruct count (a closed lane,
                an alternating one-way, ...); an obstruction whose impact the
                record does not state is not counted (absence is not impact).
                A declared outage counts while the event's newest coverage is
                under 24 hours old (the record carries no end for an outage).
  2. geo        geographic scope: Quebec City, then the province of Quebec,
                then linked (wider) coverage.
  3. origins    the number of independent REPORTING origins (declarations by
                an authority are not origins: they are listed apart), in one
                of four bands cut at the quartiles of the CURRENT edition.
  4. fresh      the instant of the newest member, in one of four bands cut at
                the quartiles of the CURRENT edition.
  last          the event id, so that equal events always come out in the
                same order.

A quartile band is read off the mid-rank percentile of the value among the
events of the edition: (events strictly below + half the events equal) / n,
in integer arithmetic. So a quiet week and an election night each rank on
their own distribution; no constant here is an opinion. The four criteria and
their order are the only fixed things, and they are the rule.

An event is shown when its key is at or above the edition's median key
(upper median) and it is one of the first CAP in display order. The rest are
not hidden: each carries the recorded reason (`below-median`, `over-cap`,
`no-displayable-member`) and `summarize` counts them for the page.

R10: a withdrawn voice is never counted. Members named in `event["withdrawn"]`
or in `ctx["withdrawn_item_ids"]` are removed before anything is counted, so a
re-render that does not rebuild the events (the hourly roads lane) still
honours a takedown.

Fail-soft: a malformed event is read as having no evidence for the field that
is malformed; it is never a crash, and an event without a usable id is skipped
with a printed diagnosis.
"""
from __future__ import annotations

import math
import re
import sys
from datetime import date, datetime, timedelta, timezone

from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import anchors  # noqa: E402
import i18n  # noqa: E402

METHOD = "ranking-events-v1 successive-criteria edition-quartiles"
CAP = 12
BANDS = 4
CRITERIA = ("now", "geo", "origins", "fresh")
SOON_HOURS = 24
OUTAGE_FRESH_HOURS = 24
GEO_RANK = {"quebec-city": 0, "quebec": 1, "linked": 2}
GEO_UNKNOWN_RANK = 3
PLAUSIBLE_YEARS = (1990, 2100)
# An obstruction obstructs when the departure screen ranks its impact among the
# restrictive ones (anchors.RW_SEVERITY: 0..3). "all-lanes-open", "no-lanes-closed"
# and an unstated impact do not.
OBSTRUCTING = frozenset(k for k, v in anchors.RW_SEVERITY.items() if v <= 3)
ACTIVE_STATUS = "active"
UPCOMING_STATUSES = frozenset({"planned", "pending"})

HIDDEN_BELOW_MEDIAN = "below-median"
HIDDEN_OVER_CAP = "over-cap"
HIDDEN_NO_MEMBER = "no-displayable-member"

# chip (tier_chip)
CERTAIN_BAR = 0.95            # lower bound of the 95 % Wilson interval
CERTAIN_MIN_PAIRS = 73        # the fewest pairs that can reach the bar with no error
GATE = "gate1_tier_precision"
SPLIT = "test"                # the held-out split: never the one the thresholds were fitted on

_SAFE_KEY = re.compile(r"^(?![a-z0-9._-]*[0-9a-f]{12})[a-z0-9][a-z0-9_.-]{0,47}$")   # a code, never an id
_MODEL_WORDS = ("model", "modèle", "modele", "llm", "language model", "ai-labelled", "machine")


def _say(message: str) -> None:
    print(f"ranking_events: {message}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Tolerant readers
# --------------------------------------------------------------------------- #
def _dicts(value: object) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, (list, tuple)) else []


def _strs(value: object) -> list[str]:
    return [x for x in value if isinstance(x, str) and x] if isinstance(value, (list, tuple, set, frozenset)) else []


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _instant(value: object) -> datetime | None:
    """A plausible UTC instant, else None. Feeds stamp 0001-01-01 or 9999-12-31
    for 'unknown': that is not a date (the same bounds as events.py)."""
    try:
        dt = i18n.parse_instant(value)
    except Exception:  # noqa: BLE001 - a malformed stamp is an absent stamp
        return None
    if dt is None or not (PLAUSIBLE_YEARS[0] <= dt.year <= PLAUSIBLE_YEARS[1]):
        return None
    return dt


def _iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else ""


def _day(value: object) -> date | None:
    raw = _str(value)[:10]
    try:
        return date.fromisoformat(raw) if len(raw) == 10 else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Quartile bands of the current edition
# --------------------------------------------------------------------------- #
def band_of(value: object, population: list) -> tuple[int, int]:
    """(band 0..3, percent) of `value` among `population`, by mid-rank percentile.

    percent = 100 * (below + equal / 2) / n, floored, in integers; the band is
    the quartile that percentile falls in (0 = lowest quarter). `None` is below
    every number. An empty population is band 0, percent 0."""
    n = len(population)
    if n == 0:
        return 0, 0

    def lt(a, b) -> bool:
        return (a is None and b is not None) or (a is not None and b is not None and a < b)

    below = sum(1 for x in population if lt(x, value))
    equal = sum(1 for x in population if x == value)
    num = 2 * below + equal                      # twice the mid-rank count
    percent = (100 * num) // (2 * n)
    band = min(BANDS - 1, (BANDS * num) // (2 * n))
    return band, percent


# --------------------------------------------------------------------------- #
# Reading one event
# --------------------------------------------------------------------------- #
def _roadwork_index(view: object) -> dict[str, dict]:
    """Declarations by id from the RoadworksView (`rows`) or from the raw store
    (`events`): {id: {status, impact, start: date|None, end: date|None}}."""
    index: dict[str, dict] = {}
    if not isinstance(view, dict):
        return index
    for r in _dicts(view.get("rows")):
        rid = _str(r.get("id"))
        if rid:
            index.setdefault(rid, {"status": _str(r.get("status")), "impact": _str(r.get("impact")),
                                   "start": _day(r.get("from")), "end": _day(r.get("to"))})
    for r in _dicts(view.get("events")):
        rid = _str(r.get("event_id"))
        if rid:
            index.setdefault(rid, {"status": _str(r.get("event_status")), "impact": _str(r.get("vehicle_impact")),
                                   "start": _day(r.get("start_date")), "end": _day(r.get("end_date"))})
    return index


def _view_total(view: object) -> int:
    if not isinstance(view, dict):
        return 0
    total = view.get("total")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    return len(_dicts(view.get("events")))


def _members(e: dict, gone: set[str]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for m in _dicts(e.get("members")):
        iid = _str(m.get("item_id"))
        if not iid or iid in gone or iid in seen:
            continue
        seen.add(iid)
        out.append(m)
    return sorted(out, key=lambda m: _str(m.get("item_id")))


def _origin_key(m: dict) -> str:
    """The grouping key of events.independence, used only for a member the stored
    groups do not cover (a view without `independence`)."""
    cls = _str(m.get("origin_class"))
    if cls == "wire":
        return "wire:" + (_str(m.get("origin_rule")).rsplit(".", 1)[-1] or "unknown")
    if cls == "press_release":
        return "release:unknown-issuer"
    if _str(m.get("owner_group")):
        return "owner:" + _str(m.get("owner_group"))
    return "institution:" + (_str(m.get("institution")) or _str(m.get("source_id")) or _str(m.get("item_id")))


def origin_counts(e: dict, members: list[dict]) -> dict:
    """Independent origins among the displayable members.

    The stored groups are the event builder's own union-find (same owner, same
    wire credit, same communique, near-duplicate copies). They are restricted to
    the members that may still be shown (R10), a member they do not cover is its
    own group, and a group that holds an official member (a declaration) is not a
    reporting origin: declarations are anchors, never corroboration."""
    ids = [_str(m.get("item_id")) for m in members]
    present = set(ids)
    official = {_str(m.get("item_id")) for m in members if _str(m.get("origin_class")) == "official"}
    official |= set(_strs((e.get("independence") or {}).get("declarations") if isinstance(e.get("independence"), dict) else [])) & present
    groups: list[set[str]] = []
    covered: set[str] = set()
    stored = e.get("independence") if isinstance(e.get("independence"), dict) else {}
    for g in stored.get("groups") if isinstance(stored.get("groups"), list) else []:
        part = set(_strs(g)) & present - covered
        if part:
            groups.append(part)
            covered |= part
    rest = {}
    for m in members:
        iid = _str(m.get("item_id"))
        if iid not in covered:
            rest.setdefault(_origin_key(m), set()).add(iid)
    groups += [rest[k] for k in sorted(rest)]
    reporting = sum(1 for g in groups if not g & official)
    return {"reporting": reporting, "declarations": len(official), "groups": len(groups), "members": len(ids)}


def now_impact(e: dict, rw: dict[str, dict], rw_total: int, now: datetime | None,
               last: datetime | None) -> dict:
    """Criterion 1 with its evidence: counts of obstructing declarations in force,
    declarations starting within 24 h, declared outages, and anchored
    declarations the roadworks view does not carry (`unseen`)."""
    active = soon = outage = unseen = anchored = 0
    refs = sorted({(_str(a.get("type")), _str(a.get("ref"))) for a in _dicts(e.get("anchors"))})
    today = now.date() if now is not None else None
    horizon = (now + timedelta(hours=SOON_HOURS)).date() if now is not None else None
    for typ, ref in refs:
        if not ref:
            continue
        if typ == "roadwork":
            d = rw.get(ref)
            if d is None:
                unseen += 1
                continue
            anchored += 1
            if today is None or d["impact"] not in OBSTRUCTING:
                continue
            start, end = d["start"], d["end"]
            if d["status"] == ACTIVE_STATUS and (start is None or start <= today) and (end is None or end >= today):
                active += 1
            elif d["status"] in UPCOMING_STATUSES and start is not None and today <= start <= horizon                     and (end is None or end >= today):
                soon += 1
        elif typ == "outage" and now is not None:
            if last is not None and timedelta(0) <= now - last <= timedelta(hours=OUTAGE_FRESH_HOURS):
                outage += 1
    return {"impact": bool(active or soon or outage), "active": active, "soon": soon, "outage": outage,
            "anchored": anchored, "unseen": unseen, "declarations_in_view": rw_total, "clock": _iso(now)}


def last_member_instant(members: list[dict]) -> tuple[datetime | None, str]:
    """The newest member instant: the publisher's stamp, or Vigie's first
    sighting when the stamp is absent or suspect. Never a guess."""
    best, source = None, "none"
    for m in members:
        pub = None if m.get("date_suspect") is True else _instant(m.get("published_at"))
        seen = _instant(m.get("first_seen"))
        if pub is not None:
            dt, src = pub, "published_at"
        elif seen is not None:
            dt, src = seen, "first_seen"
        else:
            continue
        if best is None or dt > best:
            best, source = dt, src
    return best, source


def _facts_of(e: dict, ctx_now: datetime | None, rw: dict[str, dict], rw_total: int, gone: set[str]) -> dict:
    members = _members(e, gone | set(_strs(e.get("withdrawn"))))
    last, source = last_member_instant(members)
    geo = _str(e.get("geo"))
    geo = geo if geo in GEO_RANK else "unknown"
    now = now_impact(e, rw, rw_total, ctx_now, last)
    basis = "members"
    if now["anchored"] and GEO_RANK.get(geo, GEO_UNKNOWN_RANK) > 0:
        # place evidence: a declaration of the City's own roadworks feed names this
        # event's place, whatever scope the member texts alone could establish
        geo, basis = "quebec-city", "anchor"
    return {
        "members": members,
        "origins": origin_counts(e, members),
        "last": last, "last_source": source,
        "now": now,
        "geo": geo, "geo_basis": basis,
        "geo_rank": GEO_RANK.get(geo, GEO_UNKNOWN_RANK),
    }


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def _context(ctx: object) -> tuple[datetime | None, str, dict[str, dict], int, set[str]]:
    c = ctx if isinstance(ctx, dict) else {}
    now = _instant(c.get("now")) or _instant(c.get("edition_clock"))
    view = c.get("roadworks_view")
    return now, _iso(_instant(c.get("edition_clock"))), _roadwork_index(view), _view_total(view), \
        set(_strs(c.get("withdrawn_item_ids")))


def _decided_by(prev: tuple, cur: tuple) -> str:
    """The first criterion on which two neighbours differ. The key holds the four
    banded criteria and then the exact instant: a difference in the instant alone
    is still freshness (inside one band, newest first)."""
    for name, a, b in zip(CRITERIA + ("fresh",), prev, cur):
        if a != b:
            return name
    return "id"


def rank_events(events: list[dict], ctx: dict) -> list[dict]:
    """One row per event, in display order.

    row = {"event_id", "position" (1-based), "shown", "criteria", "explain",
           "hidden_reason" ("" when shown)}
    `criteria` carries the real values of the four criteria and which one
    decided the row against the one before it; `explain` is the list of
    {"key", "values"} entries (i18n keys of scripts/i18n/*.json) that the page
    prints under "Pourquoi ici ?". The `ctx` keys are documented in the module
    docstring; `roadworks_view` should carry every declaration the events may be
    anchored to (anchors.roadworks_view(doc, clock, limit=<all>)): an anchored
    declaration absent from it is reported as `unseen`, never counted."""
    try:
        return _rank(events, ctx)
    except Exception as exc:  # noqa: BLE001 - fail-soft: a fault is a diagnosis, never a crash
        _say(f"fault while ranking ({type(exc).__name__}: {str(exc)[:160]}); no order this run")
        return []


def _rank(events: object, ctx: object) -> list[dict]:
    now, edition_clock, rw, rw_total, gone = _context(ctx)
    pool: dict[str, dict] = {}
    for e in events if isinstance(events, (list, tuple)) else []:
        eid = _str(e.get("event_id")) if isinstance(e, dict) else ""
        if not eid:
            _say("an event without a usable event_id was skipped")
            continue
        pool.setdefault(eid, e)
    facts = {eid: _facts_of(e, now, rw, rw_total, gone) for eid, e in sorted(pool.items())}
    eligible = sorted(eid for eid, f in facts.items() if f["members"])
    stamps = [None if facts[i]["last"] is None else int(facts[i]["last"].timestamp()) for i in eligible]
    reporting = [facts[i]["origins"]["reporting"] for i in eligible]

    scored: dict[str, dict] = {}
    for eid in eligible:
        f = facts[eid]
        stamp = None if f["last"] is None else int(f["last"].timestamp())
        o_band, o_pct = band_of(f["origins"]["reporting"], reporting)
        f_band, f_pct = band_of(stamp, stamps)
        scored[eid] = {"o_band": o_band, "o_pct": o_pct, "f_band": f_band, "f_pct": f_pct,
                       "key": (0 if f["now"]["impact"] else 1, f["geo_rank"], -o_band, -f_band)}
    # inside one key the newest event comes first (the exact instant, not its band),
    # then the id: equal events always come out in the same order
    def instant_of(i: str) -> int:
        last = facts[i]["last"]
        return -int(last.timestamp()) if last is not None else 1
    order = sorted(eligible, key=lambda i: (scored[i]["key"], instant_of(i), i))
    median = scored[order[(len(order) - 1) // 2]]["key"] if order else None
    inside = [i for i in order if scored[i]["key"] <= median]
    shown = set(inside[:CAP])
    rows: list[dict] = []
    prev_key = None
    for pos, eid in enumerate(order, start=1):
        s, f = scored[eid], facts[eid]
        if eid in shown:
            reason = ""
        elif eid in inside:
            reason = HIDDEN_OVER_CAP
        else:
            reason = HIDDEN_BELOW_MEDIAN
        cur_key = s["key"] + (instant_of(eid),)
        decided = "first" if prev_key is None else _decided_by(prev_key, cur_key)
        prev_key = cur_key
        rows.append(_row(eid, pos, f, s, decided, reason, len(order), now))
    for eid in sorted(set(facts) - set(eligible)):
        rows.append(_row_withdrawn(pool[eid], eid, len(rows) + 1, facts[eid], now))
    return rows


def _age_hours(last: datetime | None, now: datetime | None) -> int | None:
    if last is None or now is None:
        return None
    return max(0, int((now - last).total_seconds() // 3600))


def _row(eid: str, pos: int, f: dict, s: dict, decided: str, reason: str, compared: int,
         now: datetime | None) -> dict:
    e_now = f["now"]
    age = _age_hours(f["last"], now)
    criteria = {
        "now": dict(e_now),
        "geo": {"scope": f["geo"], "rank": f["geo_rank"], "basis": f["geo_basis"]},
        "origins": {**f["origins"], "percent_below": s["o_pct"], "band": s["o_band"], "events_compared": compared},
        "fresh": {"last_instant": _iso(f["last"]), "source": f["last_source"], "age_hours": age,
                  "percent_older": s["f_pct"], "band": s["f_band"], "events_compared": compared},
        "decided_by": decided,
        "bands": BANDS,
    }
    explain: list[dict] = []
    if now is None:
        explain.append({"key": "rank.now.unknown", "values": {}})
    else:
        if e_now["active"]:
            explain.append({"key": "rank.now.active", "values": {"n": e_now["active"]}})
        if e_now["soon"]:
            explain.append({"key": "rank.now.soon", "values": {"n": e_now["soon"]}})
        if e_now["outage"]:
            explain.append({"key": "rank.now.outage", "values": {"n": e_now["outage"]}})
        if not e_now["impact"]:
            explain.append({"key": "rank.now.none", "values": {}})
    explain.append({"key": "rank.geo.quebec-city.anchor" if f["geo_basis"] == "anchor" else f"rank.geo.{f['geo']}",
                    "values": {}})
    explain.append({"key": "rank.origins", "values": {
        "n": f["origins"]["reporting"], "d": f["origins"]["declarations"], "pct": s["o_pct"]}})
    if f["last"] is None:
        explain.append({"key": "rank.fresh.unknown", "values": {}})
    else:
        explain.append({"key": "rank.fresh", "values": {"h": age if age is not None else 0, "pct": s["f_pct"]}})
    explain.append({"key": f"rank.decided.{decided}", "values": {}})
    if reason == "":
        explain.append({"key": "rank.shown", "values": {"cap": CAP, "n": compared}})
    elif reason == HIDDEN_OVER_CAP:
        explain.append({"key": "rank.hidden.over_cap", "values": {"cap": CAP}})
    else:
        explain.append({"key": "rank.hidden.below_median", "values": {"n": compared}})
    return {"event_id": eid, "position": pos, "shown": reason == "", "hidden_reason": reason,
            "criteria": criteria, "explain": explain}


def _row_withdrawn(e: dict, eid: str, pos: int, f: dict, now: datetime | None) -> dict:
    criteria = {
        "now": dict(f["now"]), "geo": {"scope": f["geo"], "rank": f["geo_rank"], "basis": f["geo_basis"]},
        "origins": {**f["origins"], "percent_below": 0, "band": 0, "events_compared": 0},
        "fresh": {"last_instant": "", "source": "none", "age_hours": None, "percent_older": 0, "band": 0,
                  "events_compared": 0},
        "decided_by": "none", "bands": BANDS,
    }
    return {"event_id": eid, "position": pos, "shown": False, "hidden_reason": HIDDEN_NO_MEMBER,
            "criteria": criteria, "explain": [{"key": "rank.hidden.no_member", "values": {}}]}


def summarize(rows: list[dict]) -> dict:
    """What the page prints about the cut: how many events were compared, shown,
    and not shown, and why (a recorded fact, never a silent omission)."""
    rows = [r for r in rows if isinstance(r, dict)]
    shown = sum(1 for r in rows if r.get("shown"))
    below = sum(1 for r in rows if r.get("hidden_reason") == HIDDEN_BELOW_MEDIAN)
    over = sum(1 for r in rows if r.get("hidden_reason") == HIDDEN_OVER_CAP)
    nomem = sum(1 for r in rows if r.get("hidden_reason") == HIDDEN_NO_MEMBER)
    decided: dict[str, int] = {}
    for r in rows:
        if r.get("shown") and r.get("position", 0) > 1:
            k = str((r.get("criteria") or {}).get("decided_by") or "")
            decided[k] = decided.get(k, 0) + 1
    return {"method": METHOD, "cap": CAP, "total": len(rows), "shown": shown,
            "not_shown": len(rows) - shown,
            "hidden": {HIDDEN_BELOW_MEDIAN: below, HIDDEN_OVER_CAP: over, HIDDEN_NO_MEMBER: nomem},
            "decided_by": dict(sorted(decided.items())),
            "explain": [{"key": "rank.summary", "values": {
                "shown": shown, "total": len(rows), "below": below, "over": over, "cap": CAP}}]}


# --------------------------------------------------------------------------- #
# The grouping chip follows MEASURED quality (docs/AUTONOMY.md)
# --------------------------------------------------------------------------- #
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95 % Wilson score interval, the same figure scripts/events_eval.py computes."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _gate1(quality: object) -> dict:
    gates = quality.get("gates") if isinstance(quality, dict) else None
    g = gates.get(GATE) if isinstance(gates, dict) else None
    return g if isinstance(g, dict) and g.get("status") == "measured" else {}


def provenance_kind(gate: dict) -> str:
    """`human` only when the gold carries the human-verified flag; otherwise
    `model` when its recorded provenance names a model, else `unrecorded`."""
    if gate.get("human_verified") is True:
        return "human"
    text = " ".join(str(x) for x in [gate.get("provenance")] + list(gate.get("labelers") or [])).lower()
    return "model" if any(w in text for w in _MODEL_WORDS) else "unrecorded"


def _tier_counts(gate: dict, tier: str) -> tuple[int, int]:
    """(same-event pairs, predicted pairs) at exactly this tier on the held-out split."""
    split = (gate.get("splits") or {}).get(SPLIT) if isinstance(gate.get("splits"), dict) else None
    by = split.get("tier_by_label") if isinstance(split, dict) else None
    if not isinstance(by, dict):
        return 0, 0
    total = sum(_int((by.get(lab) or {}).get(tier)) for lab in sorted(by) if isinstance(by.get(lab), dict))
    return _int((by.get("same_event") or {}).get(tier)) if isinstance(by.get("same_event"), dict) else 0, total


def tier_chip(tier: object, quality_doc: object) -> dict:
    """The chip of one grouping: {"kind", "key", "values"}.

    kind  "none"      no grouping (a single article); "possible" never a merge;
          "auto"      measured precision, no human-checked evidence for it;
          "probable"  human-checked evidence exists but does not clear the bar;
          "certain"   only when the figure computed from HUMAN-checked pairs has
                      a Wilson lower bound >= 0.95 on at least 73 pairs.
    The `values` carry the measured figures (percent, n, interval, provenance)
    and are the `str.format` arguments of the catalogue text `key`. Fail-soft:
    an absent or unreadable quality document is "auto, not measured"."""
    t = tier if isinstance(tier, str) else ""
    if t == "possible":
        return {"kind": "possible", "key": "tier.possible", "values": {}}
    if t not in ("certain", "probable"):
        return {"kind": "none", "key": "why.single", "values": {}}
    gate = _gate1(quality_doc)
    tp, n = _tier_counts(gate, t) if gate else (0, 0)
    prov = provenance_kind(gate) if gate else "unrecorded"
    if n <= 0:
        return {"kind": "auto", "key": "chip.auto.unmeasured", "values": {"tier": t, "n": 0}}
    lo, hi = wilson(tp, n)
    values = {"tier": t, "p": int(round(100 * tp / n)), "n": n, "tp": tp,
              "lo": int(math.floor(100 * lo)), "hi": int(math.ceil(100 * hi)), "provenance": prov}
    if prov != "human":
        return {"kind": "auto", "key": f"chip.auto.{'model' if prov == 'model' else 'unrecorded'}", "values": values}
    if t == "certain" and n >= CERTAIN_MIN_PAIRS and lo >= CERTAIN_BAR:
        return {"kind": "certain", "key": "chip.certain", "values": values}
    return {"kind": "probable", "key": "chip.probable", "values": values}


_KEEP_STR = frozenset({"verdict", "status", "method", "matcher", "gold_version"})


def _counts_only(value: object, depth: int = 0):
    """A copy of `value` holding only numbers, booleans, null and a few
    structural words. Keys must look like codes; every other string is dropped
    (reasons can carry paths, labels can carry names)."""
    if depth > 8:
        return None
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if not (isinstance(value, float) and (math.isnan(value) or math.isinf(value))) else None
    if isinstance(value, dict):
        out = {}
        for k in sorted(k for k in value if isinstance(k, str) and _SAFE_KEY.match(k)):
            v = value[k]
            if isinstance(v, str):
                if k in _KEEP_STR and _SAFE_KEY.match(v):
                    out[k] = v
                continue
            c = _counts_only(v, depth + 1)
            if c is not None or v is None:
                out[k] = c
        return out
    if isinstance(value, (list, tuple)):
        return [c for c in (_counts_only(x, depth + 1) for x in value if not isinstance(x, str)) if c is not None]
    return None


def quality_public(quality_doc: object) -> dict:
    """The counts-only document public/qualite.json prints: numbers, booleans and
    the gate verdicts; no text, no pair, no id, no path. Fail-soft: an absent
    document yields an honest 'not measured' document."""
    gates = quality_doc.get("gates") if isinstance(quality_doc, dict) else None
    if not isinstance(gates, dict) or not gates:
        return {"format": "events-quality-public-v1", "measured": False, "gates": {},
                "chip": {"human_checked": False, "provenance": "unrecorded"}}
    out_gates = {}
    for name in sorted(k for k in gates if isinstance(k, str) and _SAFE_KEY.match(k)):
        g = gates[name]
        if isinstance(g, dict):
            out_gates[name] = _counts_only(g)
    g1 = _gate1(quality_doc)
    prov = provenance_kind(g1) if g1 else "unrecorded"
    chip = {"human_checked": prov == "human", "provenance": prov,
            "certain_bar_lower95": CERTAIN_BAR, "certain_min_pairs": CERTAIN_MIN_PAIRS}
    for t in ("certain", "probable"):
        tp, n = _tier_counts(g1, t) if g1 else (0, 0)
        lo, hi = wilson(tp, n)
        chip[t] = {"pairs": n, "same_event": tp,
                   "precision_ci95": [round(lo, 4), round(hi, 4)] if n else None}
    return {"format": "events-quality-public-v1", "measured": bool(g1), "chip": chip, "gates": out_gates}
