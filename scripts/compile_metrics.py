"""Vigie edition quality metrics - the evidence base for law amendments.

Ranking weights, thresholds and source choices are law: humans amend them,
deliberately, in public method files. This ledger exists so those amendments
are made against measured history instead of gut feeling. After every
edition it records one compact snapshot - item counts, top-of-brief churn
against the previous edition, per-source yield into the ranked store,
dossier population and lifetimes, roadworks diff volume, image coverage -
into data/ops/edition_metrics.json with a capped run history.

House law:
  * read-only over the stores; never feeds back into ranking, clustering or
    rendering. Metrics observe the machine; they never steer the edition.
  * no wall clock: the snapshot stamp is the ranked store's own ranked_at,
    so recompiling the same edition is idempotent (the last history entry
    for the same edition is replaced, never duplicated).
  * churn is measured on the top of the brief (first TOP_N ranked ids):
    what a resident actually sees change between two editions.
  * fail-soft: any missing, corrupt or foreign store contributes an empty
    section; main always exits 0.

No LLM. No network. Runs last in the pipeline, after rank_display.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import store_io

ROOT = Path(__file__).resolve().parents[1]
RANKED = ROOT / "data" / "normalized" / "latest_ranked.json"
ISSUES = ROOT / "data" / "issues" / "latest_issues.json"
DOSSIER_HISTORY = ROOT / "data" / "issues" / "history.json"
ROADWORKS = ROOT / "data" / "roadworks" / "latest_roadworks.json"
MEDIA_MANIFEST = ROOT / "data" / "media" / "brief_manifest.json"
FEED_HEALTH = ROOT / "data" / "ops" / "feed_health.json"
OUT = ROOT / "data" / "ops" / "edition_metrics.json"

METHOD = "edition-metrics-v1"
HISTORY_CAP = 120   # ~30 days at four editions per day
TOP_N = 30          # the visible top of the brief


def _load_json(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _as_int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def ranked_snapshot(ranked_doc: dict) -> tuple[str | None, list[dict]]:
    """(ranked_at, candidates) from either ranked store shape."""
    stamp = ranked_doc.get("ranked_at")
    candidates = ranked_doc.get("candidates") or ranked_doc.get("ranked") or []
    if not isinstance(candidates, list):
        candidates = []
    return (stamp if isinstance(stamp, str) else None,
            [c for c in candidates if isinstance(c, dict)])


def edition_entry(ranked_doc: dict, issues_doc: dict, history_doc: dict,
                  roadworks_doc: dict, media_doc: dict, feed_doc: dict,
                  previous_top: list[str] | None = None) -> dict:
    """One compact, honest snapshot of the edition that was just built."""
    stamp, candidates = ranked_snapshot(ranked_doc)
    top = [str(c.get("id")) for c in candidates[:TOP_N] if c.get("id")]
    per_source: dict[str, int] = {}
    for cand in candidates:
        src = str(cand.get("source_id") or "?")
        per_source[src] = per_source.get(src, 0) + 1

    churn_in = churn_out = None
    if isinstance(previous_top, list) and previous_top:
        prev = {str(x) for x in previous_top}
        cur = set(top)
        churn_in = len(cur - prev)
        churn_out = len(prev - cur)

    ledger = issues_doc.get("change_ledger") if isinstance(issues_doc.get("change_ledger"), dict) else {}
    diff = roadworks_doc.get("diff") if isinstance(roadworks_doc.get("diff"), dict) else {}
    counts = roadworks_doc.get("counts") if isinstance(roadworks_doc.get("counts"), dict) else {}
    dossiers = history_doc.get("dossiers") if isinstance(history_doc.get("dossiers"), dict) else {}
    missed = [_as_int(d.get("editions_missed")) for d in dossiers.values() if isinstance(d, dict)]
    feed_sources = feed_doc.get("sources") if isinstance(feed_doc.get("sources"), dict) else {}

    return {
        "edition": stamp,
        "items": len(candidates),
        "top_ids": top,
        "churn_in": churn_in,
        "churn_out": churn_out,
        "per_source": dict(sorted(per_source.items(), key=lambda kv: (-kv[1], kv[0]))),
        "dossiers": {
            "issue_count": _as_int(issues_doc.get("issue_count")),
            "tracked": len(dossiers),
            "edition_count": _as_int(history_doc.get("edition_count")),
            "max_missed": max(missed) if missed else 0,
            "ledger_new": _as_int(ledger.get("new_count")),
            "ledger_developed": _as_int(ledger.get("developed_count")),
            "ledger_quiet": _as_int(ledger.get("quiet_count")),
            "ledger_has_previous": bool(ledger.get("has_previous")),
        },
        "roadworks": {
            "active": _as_int(counts.get("active")),
            "diff_new": _as_int(diff.get("new_count")),
            "diff_removed": _as_int(diff.get("removed_count")),
            "diff_changed": _as_int(diff.get("changed_count")),
            "diff_has_previous": bool(diff.get("has_previous")),
        },
        "media": {
            "with_image": _as_int(media_doc.get("with_image")),
            "scope": _as_int(media_doc.get("scope_count")),
        },
        "feed_attention": len(feed_doc.get("attention")) if isinstance(feed_doc.get("attention"), list) else 0,
        "feed_statuses": {
            status: sum(1 for s in feed_sources.values()
                        if isinstance(s, dict) and s.get("status") == status)
            for status in ("healthy", "degraded", "failing", "dead")
        },
    }


def compile_metrics(ranked_path: Path | None = None, issues_path: Path | None = None,
                    history_path: Path | None = None, roadworks_path: Path | None = None,
                    media_path: Path | None = None, feed_health_path: Path | None = None,
                    out_path: Path | None = None) -> dict:
    """Advance the metrics store by one edition; returns the new document.

    Paths default to the module attributes resolved at call time so tests
    can patch them.
    """
    ranked_path = Path(ranked_path) if ranked_path is not None else RANKED
    issues_path = Path(issues_path) if issues_path is not None else ISSUES
    history_path = Path(history_path) if history_path is not None else DOSSIER_HISTORY
    roadworks_path = Path(roadworks_path) if roadworks_path is not None else ROADWORKS
    media_path = Path(media_path) if media_path is not None else MEDIA_MANIFEST
    feed_health_path = Path(feed_health_path) if feed_health_path is not None else FEED_HEALTH
    out_path = Path(out_path) if out_path is not None else OUT
    previous_doc = _load_json(out_path)
    history: list[dict] = []
    if previous_doc.get("method") == METHOD and isinstance(previous_doc.get("history"), list):
        history = [h for h in previous_doc["history"] if isinstance(h, dict)]

    ranked_doc = _load_json(ranked_path)
    issues_doc = _load_json(issues_path)
    history_doc = _load_json(history_path)
    roadworks_doc = _load_json(roadworks_path)
    media_doc = _load_json(media_path)
    feed_doc = _load_json(feed_health_path)
    current_edition = ranked_snapshot(ranked_doc)[0]
    same_edition_as_last = bool(
        history and current_edition and history[-1].get("edition") == current_edition)
    # Recompiling the same edition must compare against the edition BEFORE it,
    # never against itself: history[-1] is this edition, so using it as the
    # previous top would zero the churn and silently falsify the ledger.
    previous_top = None
    if same_edition_as_last:
        if len(history) >= 2 and isinstance(history[-2].get("top_ids"), list):
            previous_top = history[-2]["top_ids"]
    elif history and isinstance(history[-1].get("top_ids"), list):
        previous_top = history[-1]["top_ids"]
    entry = edition_entry(
        ranked_doc, issues_doc, history_doc, roadworks_doc, media_doc, feed_doc,
        previous_top=previous_top,
    )

    # Idempotent: recompiling the same edition replaces its snapshot. With no
    # edition clock (missing/corrupt ranked store) never mint a phantom
    # `edition: None`: a capped history must not fill with duplicates that
    # evict real editions.
    if not current_edition:
        pass
    elif same_edition_as_last:
        history[-1] = entry
    else:
        history.append(entry)
    history = history[-HISTORY_CAP:]

    churns_in = [h["churn_in"] for h in history if isinstance(h.get("churn_in"), int)]
    doc = {
        "method": METHOD,
        "compiled_at": entry.get("edition"),
        "edition_count": len(history),
        "latest": entry,
        "churn_in_avg": round(sum(churns_in) / len(churns_in), 1) if churns_in else None,
        "history": history,
    }
    try:
        store_io.write_json_atomic(out_path, doc)
    except OSError:
        pass
    return doc


def main(argv: list[str] | None = None) -> int:
    try:
        doc = compile_metrics()
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        print(f"edition metrics: FAIL {exc} - keeping previous ledger")
        return 0
    latest = doc["latest"]
    where = OUT.relative_to(ROOT) if OUT.is_relative_to(ROOT) else OUT
    print(f"edition metrics -> {where}")
    print(f"  edition={latest.get('edition')} items={latest['items']} "
          f"churn_in={latest['churn_in']} churn_out={latest['churn_out']} "
          f"dossiers={latest['dossiers']['issue_count']} "
          f"rw_active={latest['roadworks']['active']} "
          f"images={latest['media']['with_image']}/{latest['media']['scope']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
