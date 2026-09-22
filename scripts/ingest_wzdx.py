"""Vigie v0 — ingest official WZDX roadwork feeds into data/raw + data/roadworks.

Structured official change data, not articles: this bypasses normalize/enrich/
cluster/rank entirely. Raw GeoJSON snapshots are append-only (fetch is the scar).
The store keeps parsed events inside the metro bbox plus an honest collection
diff — removal from a collection is never reported as ended or resolved, and
estimated dates stay marked estimated. A feed outage never kills the news
pipeline: on failure the previous store is kept and this exits 0. The store
also accumulates a durable per-event history (`event_history`: first seen,
collections seen/missed — presence facts only, never a timeline of the works
themselves) under the same forward-only, method-guarded discipline: one
collection is one distinct fetched_at snapshot, so offline reuse never
inflates counts, and an absence is never an end. The diff relays two City
revision signals literally: a revised end date carries the direction of the
City's own revision (later/earlier), and a removed event still carried by the
feed under an ended status (completed/cancelled/archived) carries that literal
declaration — a declaration, never a verified resolution.

Event identity is the GeoJSON feature-level `id` (e.g. "ACL-20260917-EC-001"),
verified stable across collections. The WZDX `data_source_id` property is
feed-level on this endpoint ("TIC-Quebec/1" on every feature) and is never used
as an event key. Duplicate feature ids inside one collection are counted and
deduplicated (first occurrence in feed order wins).

Stdlib only. No pip. Does not invent, rank, or interpret anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from ingest_rss import SOURCES_PATH, fetch_bytes, load_enabled_by_type, sha256_hex, utc_now
import store_io

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
STORE_PATH = ROOT / "data" / "roadworks" / "latest_roadworks.json"
METHOD = "wzdx-roadworks-v2"
# Quebec metro bbox (min_lon, min_lat, max_lon, max_lat) — a locality sanity
# guard on the feed, not an editorial filter. The feed is the city's own.
METRO_BBOX = (-71.85, 46.50, -70.75, 47.25)
COMPARE_FIELDS = (
    "event_status", "vehicle_impact", "start_date", "end_date",
    "description", "road_names", "restrictions",
)
ENDED_STATUSES = ("completed", "cancelled", "archived")
SKIP_REASONS = ("malformed", "missing_identifier", "missing_geometry", "outside_bbox", "duplicate_identifier")
HISTORY_METHOD = "wzdx-event-history-v1"
HISTORY_MISSED_PRUNE = 120  # collections missed before a dormant event is dropped (~30 days at 6 h)
HISTORY_EVENT_CAP = 4000    # max events tracked; pruned by oldest last_seen


def _parse_iso(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _is_after(new_ts: str, old_ts: str) -> bool:
    """True when new_ts is strictly after old_ts. _parse_iso normalizes mixed
    UTC offsets (Z, +00:00, -04:00) into comparable datetimes, so ordering is
    chronological, not lexicographic; unparseable values fall back to string
    order."""
    if not old_ts:
        return bool(new_ts)
    new_dt, old_dt = _parse_iso(new_ts), _parse_iso(old_ts)
    if new_dt is not None and old_dt is not None:
        return new_dt > old_dt
    return new_ts > old_ts


def _chrono_key(value: object) -> tuple[int, object]:
    """Sort key matching _is_after: mixed-offset stamps compare chronologically,
    unparseable ones sort below parsed ones and by string."""
    dt = _parse_iso(value)
    return (1, dt) if dt is not None else (0, str(value or ""))


def _points(geometry: object) -> list[tuple[float, float]]:
    """Flatten Point/LineString/MultiLineString coordinates into (lon, lat) pairs."""
    if not isinstance(geometry, dict):
        return []
    coords = geometry.get("coordinates")
    kind = str(geometry.get("type") or "").lower()

    def pair(value: object) -> tuple[float, float] | None:
        if isinstance(value, (list, tuple)) and len(value) >= 2 and all(isinstance(n, (int, float)) for n in value[:2]):
            return (float(value[0]), float(value[1]))
        return None

    points: list[tuple[float, float]] = []
    if kind == "point":
        p = pair(coords)
        if p:
            points.append(p)
    elif kind == "linestring" and isinstance(coords, list):
        points.extend(p for p in map(pair, coords) if p)
    elif kind == "multilinestring" and isinstance(coords, list):
        for line in coords:
            if isinstance(line, list):
                points.extend(p for p in map(pair, line) if p)
    return points


def in_bbox(points: list[tuple[float, float]], bbox: tuple[float, float, float, float] = METRO_BBOX) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return any(min_lon <= lon <= max_lon and min_lat <= lat <= max_lat for lon, lat in points)


def _clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v or "").strip()]


def _clean_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def parse_event(feature: object) -> tuple[dict | None, str | None]:
    """One WZDX feature -> (event, None) or (None, skip reason). Never invents fields.

    Identity is the GeoJSON feature-level `id`. The `data_source_id` property is
    feed-level on this endpoint and must never be used as an event key.
    """
    if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
        return None, "malformed"
    props = feature["properties"]
    # RFC 7946 allows numeric ids; `0 or ""` would drop a valid feature, so
    # only None/"" count as missing.
    raw_id = feature.get("id")
    event_id = "" if raw_id is None else str(raw_id).strip()
    if not event_id:
        return None, "missing_identifier"
    points = _points(feature.get("geometry"))
    if not points:
        return None, "missing_geometry"
    if not in_bbox(points):
        return None, "outside_bbox"
    event = {
        "event_id": event_id,
        # First declared vertex: enough for the brief's static spatial scheme,
        # never a route or a geographic proof. Official coordinates only.
        "point": [float(points[0][0]), float(points[0][1])],
        "event_type": _clean_str(props.get("event_type")),
        "event_status": (_clean_str(props.get("event_status")) or "").lower() or None,
        "vehicle_impact": (_clean_str(props.get("vehicle_impact")) or "").lower() or None,
        "road_names": _clean_list(props.get("road_names")),
        "direction": (_clean_str(props.get("direction")) or "").lower() or None,
        "start_date": props.get("start_date") if isinstance(props.get("start_date"), str) else None,
        "end_date": props.get("end_date") if isinstance(props.get("end_date"), str) else None,
        "start_date_accuracy": (_clean_str(props.get("start_date_accuracy")) or "").lower() or None,
        "end_date_accuracy": (_clean_str(props.get("end_date_accuracy")) or "").lower() or None,
        "description": _clean_str(props.get("description")),
        "update_date": props.get("update_date") if isinstance(props.get("update_date"), str) else None,
        "restrictions": _clean_list(props.get("restrictions")),
    }
    return event, None


def is_active(event: dict, now: datetime) -> bool:
    """Listed while the city still declares it and the official window is open.

    Official status wins for ended events; `planned`/`pending` declarations stay
    listed until their official end date passes, and the brief relays the City's
    own status label rather than reinterpreting it. No status and no end date on
    a real-time feed means the city is still publishing it. Fetch time is never
    an event time.
    """
    status = str(event.get("event_status") or "").lower()
    if status == "active":
        return True
    if status in ENDED_STATUSES:
        return False
    end = _parse_iso(event.get("end_date"))
    if end is None:
        return True
    return end >= now


def diff_events(current: list[dict], previous: list[dict] | None, *, now: datetime,
                ended: dict | None = None) -> dict:
    """Honest collection diff. New ≠ important; changed ≠ worse; removed ≠ ended.

    `ended` maps event ids to the City's own ended-status vocabulary for events
    the feed still carries this collection; a removal carrying such a status is
    a literal City declaration, never a verified resolution.
    """
    empty = {"has_previous": False, "new": [], "removed": [], "changed": [],
             "new_count": 0, "removed_count": 0, "changed_count": 0,
             "note": "Aucune collecte précédente comparable."}
    if previous is None:
        return empty

    def keyed(rows: object) -> dict[str, dict]:
        if not isinstance(rows, (list, tuple)):
            return {}
        return {str(e["event_id"]): e for e in rows if isinstance(e, dict) and e.get("event_id")}

    cur, prev = keyed(current), keyed(previous)
    new = [
        {"event_id": eid, "road_names": cur[eid].get("road_names") or [],
         "event_type": cur[eid].get("event_type"), "change": "new", "status": "proposed"}
        for eid in sorted(set(cur) - set(prev))
    ]
    removed = []
    for eid in sorted(set(prev) - set(cur)):
        end = _parse_iso(prev[eid].get("end_date"))
        entry = {
            "event_id": eid, "road_names": prev[eid].get("road_names") or [],
            "event_type": prev[eid].get("event_type"), "change": "removed", "status": "proposed",
            "official_end_date_passed": bool(end is not None and end < now),
        }
        declared = str((ended or {}).get(eid) or "").strip().lower()
        if declared:
            entry["city_declared_status"] = declared
        removed.append(entry)
    changed = []
    for eid in sorted(set(cur) & set(prev)):
        fields = [f for f in COMPARE_FIELDS if cur[eid].get(f) != prev[eid].get(f)]
        if fields:
            entry = {"event_id": eid, "road_names": cur[eid].get("road_names") or [],
                     "fields": fields, "change": "changed", "status": "proposed"}
            if "end_date" in fields:
                cur_end = _parse_iso(cur[eid].get("end_date"))
                prev_end = _parse_iso(prev[eid].get("end_date"))
                if cur_end is not None and prev_end is not None and cur_end != prev_end:
                    # Direction of the City's own revision. Postponed ≠ extended
                    # works; advanced ≠ finished. Unparseable dates stay directionless.
                    entry["end_date_moved"] = "later" if cur_end > prev_end else "earlier"
            changed.append(entry)
    return {
        "has_previous": True, "new": new, "removed": removed, "changed": changed,
        "new_count": len(new), "removed_count": len(removed), "changed_count": len(changed),
        "note": "« Retirée » signifie absente de cette collecte, pas terminée ni résolue.",
    }


def load_previous(store_path: Path, method: str = METHOD) -> list[dict] | None:
    """Previous store events, or None when absent/corrupt/from another identity
    model. A store built under a different METHOD is never compared against:
    an identity change must not masquerade as mass additions and removals."""
    try:
        doc = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("method") != method:
        return None
    events = doc.get("events")
    return events if isinstance(events, list) else None


def empty_event_history() -> dict:
    return {"method": HISTORY_METHOD, "updated_at": None, "collection_count": 0, "events": {}}


def load_event_history(store_path: Path) -> dict:
    """Previous store's event history. Missing, corrupt or foreign-method
    history starts fresh — never mixed (same scar discipline as the store)."""
    try:
        doc = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_event_history()
    if not isinstance(doc, dict) or doc.get("method") != METHOD:
        return empty_event_history()
    hist = doc.get("event_history")
    if not isinstance(hist, dict) or hist.get("method") != HISTORY_METHOD:
        return empty_event_history()
    events = hist.get("events")
    if not isinstance(events, dict):
        return empty_event_history()
    count = _safe_int(hist.get("collection_count"))
    return {
        "method": HISTORY_METHOD,
        "updated_at": hist.get("updated_at"),
        "collection_count": count,
        "events": {str(k): v for k, v in events.items() if isinstance(v, dict)},
    }


def update_event_history(history: dict, present_events: list[dict], collection_ts: str) -> dict:
    """Pure: history advanced by one collection; the input is not mutated.

    One collection is one distinct fetched_at snapshot, so offline reuse never
    inflates counts and history only moves forward: a timestamp at or before
    the last update returns history unchanged. `present_events` is every event
    the feed still published this collection - including one whose status is
    ended or cancelled. An event absent from that set gains collections_missed:
    an absence from the feed, never a claim that the works ended (removed ≠
    ended), and never a "miss" for a work the publisher still lists as finished.
    """
    history = history if isinstance(history, dict) else empty_event_history()
    collection_ts = str(collection_ts or "").strip()
    if not collection_ts:
        return history
    updated_at = str(history.get("updated_at") or "")
    if not _is_after(collection_ts, updated_at):
        return history

    events: dict[str, dict] = {
        str(k): dict(v)
        for k, v in (history.get("events") or {}).items()
        if isinstance(v, dict)
    }
    present: set[str] = set()
    for event in present_events if isinstance(present_events, (list, tuple)) else []:
        eid = str(event.get("event_id") or "").strip() if isinstance(event, dict) else ""
        if not eid:
            continue
        present.add(eid)
        rec = events.get(eid)
        if rec is None:
            events[eid] = {
                "first_seen": collection_ts,
                "last_seen": collection_ts,
                "collections_seen": 1,
                "collections_missed": 0,
                "absent_streak": 0,
            }
            continue
        rec["last_seen"] = collection_ts
        rec["collections_seen"] = _safe_int(rec.get("collections_seen")) + 1
        rec["absent_streak"] = 0  # present in the feed: the dormancy clock restarts
        events[eid] = rec

    for eid, rec in events.items():
        if eid not in present:
            # collections_missed stays the lifetime display fact; absent_streak
            # is the consecutive-absence clock this prune uses (mirrors
            # dossier_history): a flickering event is never dropped mid-feed.
            rec["collections_missed"] = _safe_int(rec.get("collections_missed")) + 1
            rec["absent_streak"] = _safe_int(rec.get("absent_streak")) + 1

    events = {
        eid: rec for eid, rec in events.items()
        if _safe_int(rec.get("absent_streak", rec.get("collections_missed"))) < HISTORY_MISSED_PRUNE
    }
    if len(events) > HISTORY_EVENT_CAP:
        keep = sorted(
            events,
            key=lambda eid: (_chrono_key(events[eid].get("last_seen")), eid),
            reverse=True,
        )[:HISTORY_EVENT_CAP]
        events = {eid: events[eid] for eid in keep}

    return {
        "method": HISTORY_METHOD,
        "updated_at": collection_ts,
        "collection_count": _safe_int(history.get("collection_count")) + 1,
        "events": events,
    }


def build_store(src: dict, active: list[dict], counts: dict, diff: dict, fetched_at: datetime,
                history: dict | None = None) -> dict:
    return {
        "method": METHOD,
        "status": "proposed",
        "source_id": src.get("id"),
        "source_name": src.get("name"),
        "institution_name": src.get("institution_name") or src.get("name"),
        "feed_url": src.get("url"),
        "dataset_url": src.get("homepage"),
        "license_note": src.get("license_note"),
        "fetched_at": fetched_at.isoformat(),
        "bbox": list(METRO_BBOX),
        "counts": {**counts, "active": len(active)},
        "events": active,
        "diff": diff,
        "event_history": history if isinstance(history, dict) else empty_event_history(),
    }


def latest_snapshot(raw_dir: Path, source_id: str) -> Path | None:
    """Most recent raw GeoJSON snapshot (stamps sort chronologically)."""
    directory = raw_dir / source_id
    if not directory.is_dir():
        return None
    snapshots = sorted(directory.glob("*.geojson"))
    return snapshots[-1] if snapshots else None


def snapshot_fetched_at(snapshot: Path) -> datetime | None:
    meta = snapshot.with_suffix(".json")
    if meta.exists():
        try:
            doc = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                return _parse_iso(doc.get("fetched_at"))
        except (OSError, ValueError):
            pass
    return None


def collect(sources: list[dict], now: datetime, *, offline: bool = False,
            raw_dir: Path = RAW_DIR, store_path: Path = STORE_PATH, fetch=fetch_bytes) -> dict:
    """Rebuild the roadworks store. All-or-nothing per run: any fetch or parse
    failure keeps the previous store untouched and reports ok=False."""
    if not sources:
        print("  no enabled wzdx sources; keeping previous store")
        return {"ok": False, "reason": "no_sources"}
    previous = load_previous(store_path)
    counts = {"features": 0, "parsed": 0, **{reason: 0 for reason in SKIP_REASONS}}
    all_events: list[dict] = []
    seen_ids: set[str] = set()
    fetched_ats: list[datetime] = []
    for src in sources:
        source_id = str(src.get("id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", source_id):
            print(f"  FAIL {source_id!r}: invalid source identifier; keeping previous store")
            return {"ok": False, "reason": "invalid_source_id"}
        dest_dir = raw_dir / source_id
        if offline:
            snapshot = latest_snapshot(raw_dir, source_id)
            if snapshot is None:
                print(f"  {source_id}: no raw snapshot to reuse offline; keeping previous store")
                return {"ok": False, "reason": "no_snapshot"}
            try:
                raw = snapshot.read_bytes()
            except OSError as exc:
                print(f"  FAIL {source_id}: unreadable snapshot ({exc}); keeping previous store")
                return {"ok": False, "reason": "snapshot_unreadable"}
            fetched_at = snapshot_fetched_at(snapshot) or now
            print(f"  {source_id}: reusing {snapshot.name} (fetched {fetched_at.isoformat()})")
        else:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"  FAIL {source_id}: unwritable raw directory ({exc}); keeping previous store")
                return {"ok": False, "reason": "raw_dir_unwritable"}
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            try:
                raw, content_type = fetch(src["url"])
            except Exception as exc:
                error = {"ok": False, "source_id": source_id, "url": src.get("url"),
                         "error": f"{type(exc).__name__}: {exc}", "fetched_at": now.isoformat()}
                try:
                    (dest_dir / f"{stamp}_error.json").write_text(
                        json.dumps(error, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    pass
                print(f"  FAIL {source_id}: {error['error']} — keeping previous store")
                return {"ok": False, "reason": "fetch_failed"}
            fetched_at = now
            digest = sha256_hex(raw)
            snapshot = dest_dir / f"{stamp}_{digest[:12]}.geojson"
            archived = True
            try:
                prev_snaps = sorted(dest_dir.glob("*.geojson"))
                same = bool(prev_snaps) and prev_snaps[-1].name.endswith(f"_{digest[:12]}.geojson")
                store_io.write_bytes_dedup(snapshot, raw, prev_snaps[-1] if same else None)
            except OSError as exc:
                archived = False
                print(f"  WARN {source_id}: snapshot not archived ({exc}); continuing from memory")
        parse_error = None
        features: list = []
        try:
            doc = json.loads(raw)
            features = doc.get("features") if isinstance(doc, dict) else None
            if not isinstance(features, list):
                raise ValueError("no features array in GeoJSON document")
        except (ValueError, RecursionError) as exc:
            # A hostile or corrupt document can be deeply nested; json.loads
            # then raises RecursionError, which is not a ValueError. Either way
            # the feed is diagnosed, never fatal.
            parse_error = f"{type(exc).__name__}: {exc}"
        if not offline and archived:
            meta = {
                "ok": parse_error is None, "source_id": source_id, "source_name": src.get("name"),
                "institution": src.get("institution") or source_id, "type": "wzdx",
                "feed_url": src.get("url"), "fetched_at": fetched_at.isoformat(),
                "content_type": content_type if parse_error is None else None,
                "bytes": len(raw), "sha256": sha256_hex(raw),
                "geojson_file": str(snapshot.relative_to(raw_dir.parent.parent)).replace("\\", "/")
                if snapshot.is_relative_to(raw_dir.parent.parent) else str(snapshot),
                "feature_count": len(features), "parse_error": parse_error,
            }
            try:
                snapshot.with_suffix(".json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                print(f"  WARN {source_id}: meta not archived ({exc}); continuing")
        if parse_error:
            print(f"  FAIL {source_id}: {parse_error} — keeping previous store")
            return {"ok": False, "reason": "parse_failed"}
        for feature in features:
            counts["features"] += 1
            event, reason = parse_event(feature)
            if event is None:
                counts[reason or "malformed"] += 1
                continue
            if event["event_id"] in seen_ids:
                # The feed repeats a few ids within one collection; first
                # occurrence in feed order wins, the drop is counted, never silent.
                counts["duplicate_identifier"] += 1
                continue
            seen_ids.add(event["event_id"])
            event["source_id"] = source_id
            event["active"] = is_active(event, fetched_at)
            all_events.append(event)
        counts["parsed"] += sum(1 for e in all_events if e.get("source_id") == source_id)
        fetched_ats.append(fetched_at)
    store_fetched_at = min(fetched_ats)
    active = sorted((e for e in all_events if e.get("active")), key=lambda e: str(e["event_id"]))
    ended_statuses = {
        str(e["event_id"]): str(e.get("event_status"))
        for e in all_events
        if str(e.get("event_status") or "").lower() in ENDED_STATUSES
    }
    diff = diff_events(active, previous, now=store_fetched_at, ended=ended_statuses)
    history = update_event_history(
        load_event_history(store_path), all_events, store_fetched_at.isoformat()
    )
    store = build_store(sources[0], active, counts, diff, store_fetched_at, history)
    try:
        store_io.write_json_atomic(store_path, store)
    except OSError as exc:
        print(f"  FAIL store write ({type(exc).__name__}: {exc}); keeping previous store")
        return {"ok": False, "reason": "store_write_failed"}
    return {"ok": True, "store": store, "store_path": store_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="Reuse the latest raw snapshot; make no network requests")
    args = parser.parse_args(argv)
    sources = load_enabled_by_type(SOURCES_PATH, "wzdx")
    now = utc_now()
    print(f"Vigie roadworks ingest {now.isoformat()} — {len(sources)} enabled WZDX"
          + (" (offline snapshots)" if args.offline else ""))
    result = collect(sources, now, offline=args.offline)
    if result.get("ok"):
        store = result["store"]
        c, d = store["counts"], store["diff"]
        skipped = sum(c.get(reason, 0) for reason in SKIP_REASONS)
        print(f"  features={c['features']} parsed={c['parsed']} active={c['active']} skipped={skipped}")
        if d["has_previous"]:
            print(f"  diff: +{d['new_count']} new, -{d['removed_count']} removed, ~{d['changed_count']} changed")
        else:
            print("  diff: no previous collection to compare")
        h = store.get("event_history") or {}
        print(f"  event history: {h.get('collection_count')} collection(s), "
              f"{len(h.get('events') or {})} event(s) tracked")
        try:
            print(f"store: {result['store_path'].relative_to(ROOT)}")
        except ValueError:
            print(f"store: {result['store_path']}")
    # Always 0: a roadwork feed outage must never block the news pipeline.
    return 0


if __name__ == "__main__":
    sys.exit(main())
