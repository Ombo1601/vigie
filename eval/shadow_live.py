"""Shadow run of the shipped event matcher on real stores: counts and timings only.

python -X utf8 eval/shadow_live.py --data-dir data/normalized            # the current edition
python -X utf8 eval/shadow_live.py --data-dir data/normalized --replay   # every stamped snapshot, in order

Nothing here is published or stored: it reads the private stores (gitignored
data/), runs scripts/event_match.py exactly as shipped and prints counts,
never a title, an excerpt or a URL. With --replay each stamped snapshot
(*_candidates.json, in name order) is one edition: its new items are attached
to the events built so far (sticky membership), the context corpus is the
items seen in the 7 days before the edition clock, and earlier members are
compared only while their text is in that window. Fail-soft: absent or
unreadable stores print why and exit 0.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import event_match as em  # noqa: E402

WINDOW_DAYS = 7


def _load(path: Path) -> tuple[str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cands = payload.get("candidates") if isinstance(payload, dict) else payload
    edition = str(payload.get("normalized_at") or "") if isinstance(payload, dict) else ""
    return edition, [c for c in (cands if isinstance(cands, list) else []) if isinstance(c, dict) and c.get("id")]


def _counts(events: list[dict], decisions: list[dict], texts: dict[str, dict]) -> dict:
    groups = em.groups(events)
    multi = [g for g in groups if len(g) > 1]

    def langs(g):
        return {em.language_of(texts[i]) for i in g if i in texts}

    def insts(g):
        return {str(texts[i].get("institution") or texts[i].get("source_id") or "") for i in g if i in texts}

    return {
        "events": len(groups),
        "multi_member_events": len(multi),
        "largest_event": max((len(g) for g in groups), default=0),
        "items_in_multi_member_events": sum(len(g) for g in multi),
        "bilingual_events": sum(1 for g in multi if {"fr", "en"} <= langs(g)),
        "multi_institution_events": sum(1 for g in multi if len(insts(g)) >= 2),
        "merges": sum(1 for d in decisions if d.get("action") == "merged"),
    }


def current(data_dir: Path) -> int:
    latest = data_dir / "latest_candidates.json"
    if not latest.exists():
        print(f"shadow: skipped: no {latest}. Nothing measured; exit 0.")
        return 0
    edition, items = _load(latest)
    clock = em._clock(edition)
    window = [c for c in items if clock is None or (em.published_when(c) or clock) >= clock - timedelta(days=WINDOW_DAYS)]
    started = time.perf_counter()
    ctx = em.MatchContext(items)
    cands = em.candidate_pairs(items, ctx)
    tiers: dict[str, int] = {}
    capped = 0
    by_id = {c["id"]: c for c in items}
    for a_id, b_id in cands:
        s, t = em.pair_tier(by_id[a_id], by_id[b_id], ctx)
        tiers[t or "none"] = tiers.get(t or "none", 0) + 1
        g = em.fr_en_guard(by_id[a_id], by_id[b_id], ctx)
        if g.applies and not g.ok and s >= ctx.thresholds["probable"]:
            capped += 1
    events, decisions = em.attach([], items, ctx, edition=edition)
    elapsed = time.perf_counter() - started
    out = {"edition": edition, "items": len(items), "items_published_in_7_days": len(window),
           "candidate_pairs": len(cands), "pair_tiers": dict(sorted(tiers.items())),
           "fr_en_pairs_capped_by_guard": capped, "seconds": round(elapsed, 2)}
    out.update(_counts(events, decisions, by_id))
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


def replay(data_dir: Path) -> int:
    files = sorted(data_dir.glob("*_candidates.json"))
    files = [f for f in files if f.name[:1].isdigit()]
    if not files:
        print(f"shadow: skipped: no stamped *_candidates.json under {data_dir}. Nothing measured; exit 0.")
        return 0
    events: list[dict] = []
    seen: dict[str, tuple] = {}  # item id -> (edition clock, item)
    timings, all_decisions = [], []
    editions = 0
    moved = 0
    for path in files:
        try:
            edition, items = _load(path)
        except (OSError, ValueError):
            print(f"shadow: unreadable {path.name}: skipped")
            continue
        clock = em._clock(edition)
        if clock is None or not items:
            continue
        editions += 1
        for c in items:
            seen.setdefault(str(c["id"]), (clock, c))
        horizon = clock - timedelta(days=WINDOW_DAYS)
        texts = {k: c for k, (t, c) in sorted(seen.items()) if t >= horizon}
        before = {m["item_id"] if isinstance(m, dict) else m: e["event_id"] for e in events for m in e["members"]}
        started = time.perf_counter()
        ctx = em.MatchContext(list(texts.values()))
        events, decisions = em.attach(events, items, ctx, edition=edition, items_by_id=texts)
        timings.append((time.perf_counter() - started, len(texts)))
        all_decisions += decisions
        after = {m["item_id"] if isinstance(m, dict) else m: e["event_id"] for e in events for m in e["members"]}
        moved += sum(1 for k, v in before.items() if after.get(k) != v)
    last_texts = {k: c for k, (_, c) in seen.items()}
    out = {"editions": editions, "unique_items": len(seen), "memberships_moved": moved,
           "seconds_per_edition_max": round(max(t for t, _ in timings), 2) if timings else None,
           "seconds_per_edition_mean": round(sum(t for t, _ in timings) / len(timings), 2) if timings else None,
           "window_items_max": max((n for _, n in timings), default=0),
           "attached": sum(1 for d in all_decisions if d.get("action") == "attached"),
           "minted": sum(1 for d in all_decisions if d.get("action") == "minted")}
    out.update(_counts(events, all_decisions, last_texts))
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data" / "normalized")
    ap.add_argument("--replay", action="store_true", help="replay every stamped snapshot as one edition")
    args = ap.parse_args(argv)
    if not args.data_dir.is_dir():
        print(f"shadow: skipped: no store at {args.data_dir}. Nothing measured; exit 0.")
        return 0
    try:
        return replay(args.data_dir) if args.replay else current(args.data_dir)
    except (OSError, ValueError) as exc:
        print(f"shadow: skipped: unreadable store ({exc}). Nothing measured; exit 0.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
