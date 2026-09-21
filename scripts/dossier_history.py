"""
Vigie - durable multi-edition history per dossier (facts only).

Answers "is this rapprochement new, or has Vigie been seeing it for a while?"
without inventing importance. An edition is one collection snapshot
(normalized_at), not one pipeline run: re-running offline over the same
snapshot never inflates the counts.

House-law guards (scar discipline):
  * absence != resolution: editions_missed counts absences from the collected
    snapshot (fetch limits, RSS caps, the 7-day window, unmatched wording).
    Never "resolved", never "over".
  * history only moves forward: an edition timestamp at or before the last
    recorded update is ignored (idempotent re-runs, older snapshots).
    Mixed UTC offsets (Z, +00:00, -04:00) compare chronologically, not as
    strings; naive timestamps are read as UTC.
  * editions_seen is a collection fact, never a rank, never a confirmation
    count. Grouping stays proposed.
  * a foreign-method store is never mixed in - start fresh instead.
  * garbage counters coerce to 0 instead of crashing the pipeline.

No LLM. No network. Pure functions plus one small JSON store kept beside the
issue store, so tests that redirect OUT_ISSUES redirect the history with it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

METHOD = "dossier-history-v1"
TIMELINE_CAP = 40   # most recent presence entries kept per dossier
DOSSIER_CAP = 200   # max dossiers tracked; pruned by oldest last_seen
MISSED_PRUNE = 120  # editions missed before a dormant dossier is dropped


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _parse_ts(value: str) -> datetime | None:
    try:
        text = str(value).strip().replace("Z", "+00:00") if isinstance(value, str) else value
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _is_after(new_ts: str, old_ts: str) -> bool:
    """True when new_ts is strictly after old_ts.

    Parses ISO timestamps so mixed offsets compare chronologically; naive
    values are read as UTC; unparseable values fall back to string order.
    """
    if not old_ts:
        return bool(new_ts)
    new_dt, old_dt = _parse_ts(new_ts), _parse_ts(old_ts)
    if new_dt is not None and old_dt is not None:
        if new_dt.tzinfo is None:
            new_dt = new_dt.replace(tzinfo=timezone.utc)
        if old_dt.tzinfo is None:
            old_dt = old_dt.replace(tzinfo=timezone.utc)
        return new_dt > old_dt
    return new_ts > old_ts


def _chrono_key(value: object) -> tuple[int, object]:
    """Sort key that compares mixed-offset ISO stamps chronologically, exactly
    like _is_after (a string sort would order '+00:00' before 'Z' for the same
    instant and keep the wrong dossiers under the cap)."""
    text = str(value or "")
    dt = _parse_ts(text)
    if dt is None:
        return (0, text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (1, dt)


def empty_history() -> dict:
    return {"method": METHOD, "updated_at": None, "edition_count": 0, "dossiers": {}}


def load_history(path: Path) -> dict:
    """Missing, corrupt or foreign-method stores start fresh - never mixed."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        # RecursionError: a deeply nested hostile document is corrupt input,
        # not a reason to abort the chain.
        return empty_history()
    if not isinstance(raw, dict) or raw.get("method") != METHOD:
        return empty_history()
    dossiers = raw.get("dossiers")
    if not isinstance(dossiers, dict):
        return empty_history()
    return {
        "method": METHOD,
        "updated_at": raw.get("updated_at"),
        "edition_count": _safe_int(raw.get("edition_count")),
        "dossiers": {
            str(k): v for k, v in dossiers.items() if isinstance(v, dict)
        },
    }


def update_history(history: dict, issues: list[dict], edition_ts: str) -> dict:
    """Pure: returns the history advanced by one edition; input is not mutated.

    Counts one edition per distinct collection timestamp and only moves
    forward: an edition_ts at or before updated_at returns history unchanged.
    Dossiers absent from this edition gain editions_missed - an absence from
    the collection, never a resolution.
    """
    edition_ts = str(edition_ts or "").strip()
    if not edition_ts:
        return history
    updated_at = str(history.get("updated_at") or "")
    if not _is_after(edition_ts, updated_at):
        return history

    dossiers: dict[str, dict] = {}
    for key, rec in (history.get("dossiers") or {}).items():
        if isinstance(rec, dict):
            dossiers[str(key)] = dict(rec)

    present: set[str] = set()
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        iid = str(issue.get("issue_id") or "").strip()
        if not iid:
            continue
        present.add(iid)
        rec = dossiers.get(iid)
        prior = list(rec.get("timeline") or []) if isinstance(rec, dict) else []
        entry = {
            "ts": edition_ts,
            "sources": _safe_int(issue.get("source_count")),
            "items": _safe_int(issue.get("item_count")),
            "official": _safe_int(issue.get("official_voice_count")),
        }
        # Field-level revision: the dossier question is stored only when it
        # changes (the first edition stores it as the initial question). A
        # reformulated headline is a wording change, never a change of meaning.
        question = " ".join(str(issue.get("question") or "").split())[:140]
        previous_question = next(
            (str(e.get("question")) for e in reversed(prior)
             if isinstance(e, dict) and e.get("question")),
            "",
        )
        if question and question != previous_question:
            entry["question"] = question
        if rec is None:
            dossiers[iid] = {
                "scar": issue.get("scar"),
                "first_seen": edition_ts,
                "last_seen": edition_ts,
                "editions_seen": 1,
                "editions_missed": 0,
                "absent_streak": 0,
                "timeline": [entry],
            }
            continue
        rec["scar"] = issue.get("scar") or rec.get("scar")
        rec["last_seen"] = edition_ts
        rec["editions_seen"] = _safe_int(rec.get("editions_seen")) + 1
        rec["absent_streak"] = 0  # present: the dormancy clock restarts
        timeline = list(rec.get("timeline") or [])
        timeline.append(entry)
        rec["timeline"] = timeline[-TIMELINE_CAP:]
        dossiers[iid] = rec

    for iid, rec in dossiers.items():
        if iid not in present:
            # editions_missed stays the lifetime display fact; absent_streak is
            # the consecutive-absence clock the prune actually uses, so a
            # long-lived flickering dossier is never dropped for history it
            # already paid for.
            rec["editions_missed"] = _safe_int(rec.get("editions_missed")) + 1
            rec["absent_streak"] = _safe_int(rec.get("absent_streak")) + 1

    dossiers = {
        iid: rec
        for iid, rec in dossiers.items()
        # A dossier present in this edition is never pruned; only a dossier
        # absent for MISSED_PRUNE consecutive editions is dropped.
        if iid in present
        or _safe_int(rec.get("absent_streak", rec.get("editions_missed"))) < MISSED_PRUNE
    }
    if len(dossiers) > DOSSIER_CAP:
        keep = sorted(
            dossiers,
            key=lambda iid: (_chrono_key(dossiers[iid].get("last_seen")), iid),
            reverse=True,
        )[:DOSSIER_CAP]
        dossiers = {iid: dossiers[iid] for iid in keep}

    return {
        "method": METHOD,
        "updated_at": edition_ts,
        "edition_count": _safe_int(history.get("edition_count")) + 1,
        "dossiers": dossiers,
    }


def tracking_of(history: dict, issue_id: str) -> dict | None:
    """The per-dossier summary embedded in latest_issues.json (facts only)."""
    rec = (history.get("dossiers") or {}).get(str(issue_id))
    if not isinstance(rec, dict):
        return None
    timeline = rec.get("timeline") if isinstance(rec.get("timeline"), list) else []
    compact = []
    for entry in timeline:
        if not isinstance(entry, dict) or not entry.get("ts"):
            continue
        row = {"ts": entry.get("ts"), "sources": _safe_int(entry.get("sources"))}
        # Only fields the record actually carried: a pre-v0.2 entry has no
        # items/official, and rendering a fabricated 0 would be a lie.
        for key in ("items", "official"):
            if key in entry:
                row[key] = _safe_int(entry.get(key))
        if entry.get("question"):
            row["question"] = str(entry.get("question"))[:140]
        compact.append(row)
    return {
        "status": "proposed",
        "method": METHOD,
        "first_seen": rec.get("first_seen"),
        "last_seen": rec.get("last_seen"),
        "editions_seen": _safe_int(rec.get("editions_seen")),
        "editions_missed": _safe_int(rec.get("editions_missed")),
        "timeline": compact[-8:],
    }
