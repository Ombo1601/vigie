"""Vigie watchdog - the one file a founder reads once a week.

Compiles the machine-room ledgers (feed health, media health, edition
metrics) plus the refresh log and disk usage into data/ops/watchdog.md,
regenerated every run, with a weekly snapshot history in watchdog.json
(capped at 26 weeks). Its whole job is answering two questions: "does
anything need a human this week?" and "what changed about the machine
itself?".

House law:
  * derives from ledgers only - never re-measures, never touches the
    network, never edits the facts it reports.
  * no wall clock: the reference stamp is the most recent timestamp found
    in the inputs; the ISO week bucket comes from that stamp, so a
    recompile over unchanged ledgers is idempotent (same week replaces).
  * attention lines are fixed thresholds over ledger facts, published in
    ATTENTION RULES below. An empty attention list means "nothing needs a
    human", and the watchdog says exactly that.
  * fail-soft: missing or corrupt inputs report as absent facts, never as
    false health; main always exits 0.

ATTENTION RULES (fixed thresholds):
  * any feed source failing or dead (feed-health-v1 statuses)
  * more than 5 scoped articles missing an image, or the missing count up
    by more than 3 across the media ledger window
  * average top-of-brief churn above 15 of 30 ids per edition
  * any FAIL line in the refresh log SINCE the last successful production
    deploy (failures already recovered inside the 7-day log window are a
    watch line, not attention - the machine is not currently broken)
  * data/ above 2 GiB (watch line, not attention: growth is measured first)

No LLM. Runs last in the pipeline, after compile_metrics.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import store_io

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "data" / "ops"
DATA = ROOT / "data"
FEED_HEALTH = OPS / "feed_health.json"
MEDIA_HEALTH = OPS / "media_health.json"
EDITION_METRICS = OPS / "edition_metrics.json"
REFRESH_LOG = OPS / "refresh.log"
OUT_MD = OPS / "watchdog.md"
OUT_JSON = OPS / "watchdog.json"

METHOD = "watchdog-v1"
WEEK_HISTORY_CAP = 26
# The 7-day window is the law; the tail only bounds memory. 5000 lines covers
# well over a week at the observed ~25 lines per 6-hourly run.
LOG_TAIL_LINES = 5000
MISSING_IMAGES_ATTENTION = 5
MISSING_IMAGES_RISE = 3
CHURN_ATTENTION = 15
DISK_WATCH_BYTES = 2 * 1024 ** 3


def _load_json(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    # A hand-edited or legacy naive stamp is read as UTC rather than mixed with
    # aware stamps, where max() would raise and kill the whole refresh chain.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _disk_bytes(root: Path) -> int:
    total = 0
    try:
        for path in Path(root).rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def read_refresh_log(path: Path = REFRESH_LOG, tail: int = LOG_TAIL_LINES,
                     window_days: int = 7) -> dict:
    """Facts from the recent refresh log.

    Only top-level timestamped lines are facts. refresh.py echoes pipeline
    stdout and multi-line error tails into the same file; those continuation
    lines have no timestamp and must never be counted as separate failures
    (nor be allowed to hide the real ones). `fails` counts FAIL lines inside
    the window ending at the log's last activity; `fails_since_ok` counts only
    those after the most recent successful production deploy.
    """
    try:
        # Rotation-aware: refresh.py moves the full log to refresh.log.1 and
        # starts a new one, so reading only the current file would hide every
        # in-window FAIL right after a rotation.
        source = Path(path)
        rotated = source.with_suffix(".log.1")
        chunks = []
        for candidate in (rotated, source):
            try:
                chunks.append(candidate.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        if not chunks:
            raise OSError("no refresh log")
        lines = "".join(chunks).splitlines()[-tail:]
    except OSError:
        return {"available": False, "has_entries": False, "fails": 0, "fails_since_ok": 0,
                "last_deploy": None, "last_activity": None, "window_lines": 0}

    def parse_line(line: str) -> tuple[str, str] | None:
        stamp, _, msg = line.strip().partition(" ")
        if line.strip() and not line.lstrip().startswith("|") and _parse_ts(stamp):
            return stamp, msg
        return None

    entries = [p for p in (parse_line(ln) for ln in lines) if p is not None]
    last_deploy = next((stamp for stamp, msg in reversed(entries)
                        if "OK production updated" in msg), None)
    last_activity = entries[-1][0] if entries else None
    anchor = _parse_ts(last_activity)
    last_ok_index = max((i for i, (_, msg) in enumerate(entries)
                         if "OK production updated" in msg), default=-1)
    fails = fails_since_ok = 0
    for i, (stamp, msg) in enumerate(entries):
        if not msg.startswith("FAIL"):
            continue
        ts = _parse_ts(stamp)
        # total_seconds, not .days: `.days` floors toward zero, so a FAIL
        # 7.9 days old passed a 7-day window as if it were 7.0.
        if (ts is None or anchor is None
                or (anchor - ts).total_seconds() <= window_days * 86400):
            fails += 1
            if i > last_ok_index:
                fails_since_ok += 1
    return {"available": True, "has_entries": bool(entries), "fails": fails,
            "fails_since_ok": fails_since_ok, "last_deploy": last_deploy,
            "last_activity": last_activity, "window_lines": len(entries)}


def compile_watchdog(ops_dir: Path | None = None, data_dir: Path | None = None,
                     out_md: Path | None = None, out_json: Path | None = None) -> dict:
    """Paths default to the module attributes resolved at call time (tests patch them)."""
    ops_dir = Path(ops_dir) if ops_dir is not None else OPS
    data_dir = Path(data_dir) if data_dir is not None else DATA
    out_md = Path(out_md) if out_md is not None else OUT_MD
    out_json = Path(out_json) if out_json is not None else OUT_JSON
    feed_doc = _load_json(ops_dir / "feed_health.json")
    media_doc = _load_json(ops_dir / "media_health.json")
    metrics_doc = _load_json(ops_dir / "edition_metrics.json")
    feed = feed_doc if feed_doc.get("method") == "feed-health-v1" else {}
    media = media_doc if media_doc.get("method") == "media-health-v1" else {}
    metrics = metrics_doc if metrics_doc.get("method") == "edition-metrics-v1" else {}
    log = read_refresh_log(ops_dir / "refresh.log")
    disk = _disk_bytes(Path(data_dir))

    stamps = [t for t in (
        _parse_ts(feed.get("compiled_at")),
        _parse_ts((media.get("latest") or {}).get("fetched_at") if isinstance(media.get("latest"), dict) else None),
        _parse_ts(metrics.get("compiled_at")),
        _parse_ts(log.get("last_activity")),
    ) if t is not None]
    reference = max(stamps) if stamps else None
    week = f"{reference.isocalendar()[0]}-W{reference.isocalendar()[1]:02d}" if reference else "unknown"

    # Health is a claim about facts. A channel with no readable facts is
    # reported as blind; the verdict is never "healthy" while one is missing.
    has_feed_facts = bool(feed.get("sources") if isinstance(feed.get("sources"), dict) else False)
    has_media_facts = bool(media.get("latest")) if isinstance(media.get("latest"), dict) else False
    has_metrics_facts = isinstance(metrics.get("latest"), dict) and bool(metrics.get("latest"))
    has_log_facts = bool(log.get("available") and log.get("has_entries"))
    blind_channels = [name for name, present in (
        ("feed ledger", has_feed_facts), ("media ledger", has_media_facts),
        ("edition metrics", has_metrics_facts), ("refresh log", has_log_facts)) if not present]
    facts_present = bool(has_feed_facts or has_media_facts or has_metrics_facts or has_log_facts)

    # --- attention rules (fixed thresholds over ledger facts) ---
    attention: list[str] = []
    watch: list[str] = []
    if not facts_present:
        attention.append("no ledger facts available: the machine room is blind, not healthy")
    elif blind_channels:
        # Some facts exist, but at least one channel cannot be seen. That is not
        # health: the deploy-failure channel in particular must never be silent.
        watch.append("blind channels (no facts): " + ", ".join(blind_channels))
    feed_sources = feed.get("sources") if isinstance(feed.get("sources"), dict) else {}
    for name, src in sorted(feed_sources.items()):
        if isinstance(src, dict) and src.get("status") in ("failing", "dead"):
            attention.append(f"source {name} is {src['status']}: {str(src.get('last_error') or 'no successful run')[:100]}")
    degraded = [n for n, s in sorted(feed_sources.items()) if isinstance(s, dict) and s.get("status") == "degraded"]
    if degraded:
        watch.append(f"sources degraded: {', '.join(degraded)}")

    media_latest = media.get("latest") if isinstance(media.get("latest"), dict) else {}
    missing = media_latest.get("missing")
    if isinstance(missing, int) and missing > MISSING_IMAGES_ATTENTION:
        attention.append(f"{missing} scoped articles without an image ({', '.join(list((media_latest.get('reasons') or {}).keys())[:3])})")
    history = media.get("history") if isinstance(media.get("history"), list) else []
    miss_vals = [h.get("missing") for h in history if isinstance(h, dict) and isinstance(h.get("missing"), int)]
    if len(miss_vals) >= 4 and miss_vals[-1] - miss_vals[0] > MISSING_IMAGES_RISE:
        attention.append(f"missing images rising across the ledger window: {miss_vals[0]} -> {miss_vals[-1]}")

    churn_avg = metrics.get("churn_in_avg")
    if isinstance(churn_avg, (int, float)) and churn_avg > CHURN_ATTENTION:
        attention.append(f"top-of-brief churn averages {churn_avg} of 30 ids per edition")

    if log.get("fails_since_ok"):
        attention.append(f"{log['fails_since_ok']} FAIL line(s) since the last successful production deploy")
    elif log.get("fails"):
        watch.append(f"{log['fails']} FAIL line(s) in the 7-day log window, recovered by the last deploy")
    if disk > DISK_WATCH_BYTES:
        watch.append(f"data/ has grown to {disk / 1024 ** 3:.2f} GiB - consider a retention decision")

    latest_metrics = metrics.get("latest") if isinstance(metrics.get("latest"), dict) else {}
    snapshot = {
        "week": week,
        "reference": reference.isoformat() if reference else None,
        "facts": facts_present,
        "blind_channels": blind_channels,
        "attention": attention,
        "watch": watch,
        "feed": {
            "source_count": feed.get("source_count"),
            "status_counts": feed.get("status_counts"),
            "not_modified_recent": sum(
                s.get("not_modified_recent", 0) for s in feed_sources.values() if isinstance(s, dict)),
        },
        "media": {"missing": missing, "with_image": media_latest.get("with_image"),
                  "feed_resolved": media_latest.get("feed_resolved")},
        "edition": {"items": latest_metrics.get("items"), "churn_in_avg": churn_avg,
                    "edition_count": metrics.get("edition_count"),
                    "dossiers": (latest_metrics.get("dossiers") or {}).get("issue_count"),
                    "roadworks_active": (latest_metrics.get("roadworks") or {}).get("active")},
        "refresh": {"fails": log.get("fails"), "fails_since_ok": log.get("fails_since_ok"),
                    "last_deploy": log.get("last_deploy")},
        "disk_bytes": disk,
    }

    # --- weekly json history (same week replaces; capped) ---
    previous = _load_json(out_json)
    weeks: list[dict] = []
    if previous.get("method") == METHOD and isinstance(previous.get("weeks"), list):
        weeks = [w for w in previous["weeks"] if isinstance(w, dict)]
    compact = {k: snapshot[k] for k in ("week", "reference", "disk_bytes")}
    compact["attention_count"] = len(attention)
    compact["missing_images"] = missing
    compact["churn_in_avg"] = churn_avg
    compact["fails"] = log.get("fails")
    if weeks and weeks[-1].get("week") == week:
        weeks[-1] = compact
    else:
        weeks.append(compact)
    weeks = weeks[-WEEK_HISTORY_CAP:]
    out_doc = {"method": METHOD, "week": week, "latest": snapshot, "weeks": weeks}

    # --- the markdown a human reads ---
    md = _render_markdown(snapshot, feed_sources, media_latest, weeks)
    for path, payload in ((out_json, json.dumps(out_doc, ensure_ascii=False, indent=2)), (out_md, md)):
        try:
            store_io.write_text_atomic(Path(path), payload)
        except OSError:
            pass
    return out_doc


def _render_markdown(snap: dict, feed_sources: dict, media_latest: dict, weeks: list[dict]) -> str:
    lines = [
        f"# Vigie watchdog - {snap['week']}",
        "",
        f"Compiled from the ledgers at {snap['reference'] or 'unknown time'}.",
        "Internal machine room; not published. Regenerated every run by `scripts/compile_watchdog.py`.",
        "",
        "## Needs attention",
        "",
    ]
    if snap["attention"]:
        lines += [f"- {line}" for line in snap["attention"]]
    elif snap.get("blind_channels"):
        lines.append("Nothing needs a human, but some channels are blind: "
                     + ", ".join(snap["blind_channels"]) + ". Health is not claimed.")
    elif snap.get("facts"):
        lines.append("Nothing. The machine is healthy.")
    else:
        lines.append("No facts. The machine room is blind; health is unknown.")
    if snap["watch"]:
        lines += ["", "## Watch", ""] + [f"- {line}" for line in snap["watch"]]
    lines += ["", "## Sources (feed-health-v1)", "",
              "| source | status | fails | last ok (h ago) | yield avg | trend | 304s |",
              "|--------|--------|-------|-----------------|-----------|-------|------|"]
    for name, src in sorted(feed_sources.items()):
        if not isinstance(src, dict):
            continue
        lines.append(
            f"| {name} | {src.get('status')} | {src.get('consecutive_failures')} "
            f"| {src.get('hours_since_ok')} | {src.get('yield_avg')} "
            f"| {src.get('yield_trend') or '-'} | {src.get('not_modified_recent')} |")
    if not feed_sources:
        lines.append("| (no feed facts yet) | - | - | - | - | - | - |")
    reasons = media_latest.get("reasons") or {}
    lines += ["", "## Images (media-health-v1)", "",
              f"- with image: {snap['media']['with_image']} (scope missing: {snap['media']['missing']})",
              f"- resolved via publisher feed media: {snap['media']['feed_resolved']}",
              f"- reasons: {', '.join(f'{k}={v}' for k, v in reasons.items()) or 'none'}",
              "", "## Edition (edition-metrics-v1)", "",
              f"- items ranked: {snap['edition']['items']} | dossiers: {snap['edition']['dossiers']} "
              f"| roadworks active: {snap['edition']['roadworks_active']}",
              f"- top-of-brief churn: avg {snap['edition']['churn_in_avg']} in/edition "
              f"over {snap['edition']['edition_count']} recorded editions",
              "", "## Machine", "",
              f"- refresh log: {snap['refresh']['fails_since_ok']} FAIL since last deploy "
              f"({snap['refresh']['fails']} in the 7-day window); "
              f"last production deploy: {snap['refresh']['last_deploy'] or 'unknown'}",
              f"- data/ on disk: {snap['disk_bytes'] / 1024 ** 2:.1f} MiB",
              "", "## Weekly history", "",
              "| week | attention | missing img | churn avg | fails | disk MiB |",
              "|------|-----------|-------------|-----------|-------|----------|"]
    for w in weeks:
        lines.append(
            f"| {w.get('week')} | {w.get('attention_count')} | {w.get('missing_images')} "
            f"| {w.get('churn_in_avg')} | {w.get('fails')} "
            f"| {round((w.get('disk_bytes') or 0) / 1024 ** 2, 1)} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    try:
        doc = compile_watchdog()
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        print(f"watchdog: FAIL {exc} - keeping previous digest")
        return 0
    snap = doc["latest"]
    where = OUT_MD.relative_to(ROOT) if OUT_MD.is_relative_to(ROOT) else OUT_MD
    print(f"watchdog -> {where} (week {doc['week']})")
    if snap["attention"]:
        for line in snap["attention"]:
            print(f"  ! {line}")
    elif snap.get("blind_channels"):
        print("  attention: nothing, but blind channels: " + ", ".join(snap["blind_channels"]))
    elif snap.get("facts"):
        print("  attention: nothing - the machine is healthy")
    else:
        print("  attention: no facts available - health unknown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
