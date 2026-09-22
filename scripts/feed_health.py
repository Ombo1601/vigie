"""Vigie feed health ledger - per-source operational facts from data/raw.

Doctrine: every silence must be a diagnosed fact. A dying source used to be
visible only as missing coverage; now ingest_rss/ingest_wzdx meta files are
compiled into data/ops/feed_health.json: consecutive-failure streaks, parse
errors, item-yield trends, not-modified rates and disk growth per source,
each with an honest status (healthy / degraded / failing / dead) and one
attention line a human can act on.

House law:
  * read-only over data/raw - the compiler never touches the network and
    never rewrites collection history (fetch is the scar).
  * no wall clock: "now" is the most recent run stamp observed in data/raw,
    so a recompile over unchanged facts is byte-identical.
  * statuses are fixed thresholds published below, not a model's opinion.
  * fail-soft: corrupt metas are skipped, an unwritable ledger is ignored,
    main always exits 0 - the edition never depends on the ledger.

No LLM. Runs right after the ingest steps so every pipeline mode (online or
offline) leaves a fresh machine-room record.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import store_io

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUT = ROOT / "data" / "ops" / "feed_health.json"

METHOD = "feed-health-v1"
RUNS_EXAMINED = 40        # timeline depth per source
YIELD_WINDOW = 10         # ok-runs considered for the yield trend
DEAD_AFTER_HOURS = 72     # no successful collection for three days
FAILING_STREAK = 3        # consecutive failures before "failing"
NOT_MODIFIED_WINDOW = 10  # recent runs counted for the 304 rate
FUTURE_TOLERANCE_HOURS = 24  # a collection stamp beyond this is a clock anomaly

STATUSES = ("healthy", "degraded", "failing", "dead")


def _parse_stamp(name: str) -> datetime | None:
    """Run timestamp from a snapshot/meta filename prefix (UTC, second precision)."""
    try:
        return datetime.strptime(name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _safe_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def load_source_timeline(src_dir: Path, limit: int = RUNS_EXAMINED) -> list[dict]:
    """Chronological run facts for one source, from its meta/error JSON files.

    Reads both RSS metas (item_count) and WZDX metas (feature_count); a
    corrupt or foreign file is skipped, never fatal.
    """
    try:
        metas = sorted(Path(src_dir).glob("*.json"))[-limit:]
    except OSError:
        return []
    timeline: list[dict] = []
    for path in metas:
        at = _parse_stamp(path.name)
        if at is None:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            timeline.append({"at": at, "ok": False, "items": None, "parse_error": None,
                             "error": "unreadable meta", "bytes": None, "not_modified": False})
            continue
        if not isinstance(doc, dict):
            timeline.append({"at": at, "ok": False, "items": None, "parse_error": None,
                             "error": "foreign meta", "bytes": None, "not_modified": False})
            continue
        items = _safe_int(doc.get("item_count"))
        if items is None:
            items = _safe_int(doc.get("feature_count"))
        timeline.append({
            "at": at,
            "ok": bool(doc.get("ok")),
            "items": items,
            "parse_error": doc.get("parse_error") if isinstance(doc.get("parse_error"), str) else None,
            "error": doc.get("error") if isinstance(doc.get("error"), str) else None,
            "bytes": _safe_int(doc.get("bytes")),
            "not_modified": bool(doc.get("not_modified")),
        })
    timeline.sort(key=lambda t: (t["at"],))
    return timeline


def _disk_bytes(src_dir: Path) -> int:
    total = 0
    try:
        for path in Path(src_dir).rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _streak(timeline: list[dict], predicate) -> int:
    count = 0
    if not isinstance(timeline, (list, tuple)):
        return 0
    for run in reversed(timeline):
        if predicate(run):
            count += 1
        else:
            break
    return count


def _yield_trend(yields: list[int]) -> str | None:
    """"rising"/"falling"/"flat" from the two halves of the yield window."""
    if not isinstance(yields, (list, tuple)) or len(yields) < 4:
        return None
    half = len(yields) // 2
    older = sum(yields[:half]) / half
    newer = sum(yields[half:]) / (len(yields) - half)
    if older <= 0:
        return "rising" if newer > 0 else "flat"
    ratio = newer / older
    if ratio <= 0.5:
        return "falling"
    if ratio >= 1.5:
        return "rising"
    return "flat"


def compile_health(raw_dir: Path | None = None, out_path: Path | None = None) -> dict:
    """Compile the ledger; durable-writes it unless out_path is unwritable.

    Paths default to the module attributes resolved at call time so tests
    can patch them.
    """
    raw_dir = Path(raw_dir) if raw_dir is not None else RAW_DIR
    out_path = Path(out_path) if out_path is not None else OUT
    timelines: dict[str, list[dict]] = {}
    if raw_dir.is_dir():
        for src_dir in sorted(raw_dir.iterdir()):
            if not src_dir.is_dir() or src_dir.name.startswith("_"):
                continue
            timeline = load_source_timeline(src_dir)
            if timeline:
                timelines[src_dir.name] = timeline

    # The reference is a data stamp, never a clock read; the only use of the
    # wall clock is a sanity ceiling, so one phantom future stamp (laptop
    # sleep before NTP) cannot mark every healthy source dead.
    ceiling = datetime.now(timezone.utc) + timedelta(hours=FUTURE_TOLERANCE_HOURS)
    reference: datetime | None = None
    for timeline in timelines.values():
        at = timeline[-1]["at"]
        if at > ceiling:
            continue
        if reference is None or at > reference:
            reference = at

    sources: dict[str, dict] = {}
    attention: list[str] = []
    if timelines and reference is None:
        # Every observed stamp is beyond the ceiling: the collection clock is
        # suspect, so no age can be computed and health is unknown.
        attention.append("all collection stamps are in the future: clock anomaly, "
                         "source ages are unknown")
    for name, timeline in sorted(timelines.items()):
        oks = [t for t in timeline if t["ok"]]
        last_ok_at = oks[-1]["at"] if oks else None
        hours_since_ok = (
            # A source whose only success is future-stamped is not "younger than
            # the reference"; clamp so the ledger never publishes negative ages.
            round(max(0.0, (reference - last_ok_at).total_seconds() / 3600), 1)
            if reference and last_ok_at else None
        )
        failures = _streak(timeline, lambda t: not t["ok"])
        parse_errors = _streak(timeline, lambda t: not t["ok"] and bool(t["parse_error"]))
        yields = [t["items"] for t in oks if t["items"] is not None][-YIELD_WINDOW:]
        trend = _yield_trend(yields)
        recent = timeline[-NOT_MODIFIED_WINDOW:]
        not_modified = sum(1 for t in recent if t["not_modified"])
        last_error = next((t["error"] or t["parse_error"] for t in reversed(timeline)
                           if not t["ok"] and (t["error"] or t["parse_error"])), None)

        if last_ok_at is None:
            # Never succeeded: "dead" means no success for three days, so a
            # newly added feed that failed once is not dead yet.
            first_at = timeline[0]["at"]
            dry_hours = ((reference - first_at).total_seconds() / 3600) if reference else 0.0
            if dry_hours >= DEAD_AFTER_HOURS:
                status = "dead"
            elif failures >= FAILING_STREAK:
                status = "failing"
            else:
                status = "degraded"
        elif hours_since_ok is not None and hours_since_ok >= DEAD_AFTER_HOURS:
            status = "dead"
        elif failures >= FAILING_STREAK:
            status = "failing"
        elif failures >= 1 or trend == "falling":
            # A parse error is a failure with parse_error set, so it is already
            # covered by `failures >= 1`; no separate branch pretends otherwise.
            status = "degraded"
        else:
            status = "healthy"

        sources[name] = {
            "status": status,
            "runs_examined": len(timeline),
            "consecutive_failures": failures,
            "consecutive_parse_errors": parse_errors,
            "last_ok_at": last_ok_at.isoformat() if last_ok_at else None,
            "hours_since_ok": hours_since_ok,
            "last_error": last_error,
            "item_yields": yields,
            "yield_avg": round(sum(yields) / len(yields), 1) if yields else None,
            "yield_trend": trend,
            "not_modified_recent": not_modified,
            "disk_bytes": _disk_bytes(raw_dir / name),
        }
        if status != "healthy":
            detail = last_error or (f"yield falling (avg {sources[name]['yield_avg']})"
                                    if trend == "falling" else "no successful run")
            attention.append(f"{name}: {status} - {str(detail)[:120]}")

    doc = {
        "method": METHOD,
        "compiled_at": reference.isoformat() if reference else None,
        "source_count": len(sources),
        "status_counts": {s: sum(1 for v in sources.values() if v["status"] == s) for s in STATUSES},
        "attention": attention,
        "sources": sources,
    }
    try:
        store_io.write_json_atomic(out_path, doc)
    except OSError:
        pass
    return doc


def main(argv: list[str] | None = None) -> int:
    try:
        doc = compile_health()
    except (OSError, ValueError) as exc:
        print(f"feed health: FAIL {exc} - keeping previous ledger")
        return 0
    counts = doc["status_counts"]
    where = OUT.relative_to(ROOT) if OUT.is_relative_to(ROOT) else OUT
    print(f"feed health -> {where}")
    print(f"  sources={doc['source_count']} healthy={counts['healthy']} degraded={counts['degraded']} "
          f"failing={counts['failing']} dead={counts['dead']}")
    for line in doc["attention"][:6]:
        print(f"  ! {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
