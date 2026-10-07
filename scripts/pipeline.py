"""Vigie v0 - one-command Critical Path.

Runs: ingest (RSS + official WZDX roadworks + civic HTML) -> takedowns (R10 purge)
-> feed health -> normalize ->
enrich -> cluster -> events (shadow store) -> edge atlas + anomaly rules -> brief media -> rank/display
-> edition metrics -> watchdog. Stdlib only. Does not start the server (open a
second terminal for that). Offline mode reuses raw snapshots and makes no
network requests.

Usage (from repo root):
  python scripts/pipeline.py
Then:
  python scripts/serve.py
  open http://127.0.0.1:8765/
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [
    "ingest_rss.py",
    "ingest_wzdx.py",
    "ingest_civic.py",
    "takedown.py",
    "feed_health.py",
    "normalize.py",
    "enrich.py",
    "cluster_issues.py",
    # Shadow (docs/MIGRATION.md step 5) while the switch is off: the event
    # store, beside the dossiers; load-bearing in preview and live (`is_shadow`).
    # Full and offline runs only; it writes nothing under public/.
    "events.py",
    "edge_atlas.py",
    "compile_anomalies.py",
    "fetch_brief_media.py",
    "rank_display.py",
    "compile_metrics.py",
    "compile_watchdog.py",
]
# Selection is by name, not position: render-only is exactly the display
# step; offline drops the one step that cannot work from snapshots and
# flags the two network steps that can.
RENDER_ONLY = ("rank_display.py",)
OFFLINE_SKIP = frozenset({"ingest_rss.py"})
OFFLINE_FLAGGED = frozenset({"ingest_wzdx.py", "ingest_civic.py", "fetch_brief_media.py"})
# Shadow stages feed no page while the event surfaces are off
# (scripts/surfaces.py): a crash there (even one that escapes the stage's own
# fail-soft, such as an import error) is diagnosed and the edition goes on,
# rendered from the dossiers as before. With the switch on (preview or live)
# events.py feeds the event pages, so it is load-bearing: a crash fails the
# run loudly (the refresh alert) instead of leaving last edition's view
# behind for an empty or stale event door.
SHADOW = frozenset({"events.py"})


def is_shadow(script: str, mode: str | None = None) -> bool:
    """Whether a failure of `script` is diagnosed and passed over: only a
    shadow stage, and only while the event surfaces are off (`mode`: the
    switch's position; None reads it, scripts/surfaces.py)."""
    if script not in SHADOW:
        return False
    import surfaces  # noqa: PLC0415 - lazy: the switch is read when a stage fails

    return (surfaces.mode() if mode is None else surfaces.validate(mode, "pipeline mode")) == "off"


def run(script: str, *extra: str) -> None:
    path = ROOT / "scripts" / script
    print(f"\n=== {' '.join((script, *extra))} ===", flush=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, str(path), *extra], cwd=str(ROOT), env=env)
    if proc.returncode != 0:
        if is_shadow(script):
            print(f"{script} (shadow) failed with code {proc.returncode}: diagnosed; the edition continues", flush=True)
            return
        if script in SHADOW:
            raise SystemExit(f"{script} failed with code {proc.returncode}: the event surfaces are on "
                             "(scripts/surfaces.py), so the event builder is load-bearing; no edition is "
                             "rendered over a view it did not build")
        raise SystemExit(f"{script} failed with code {proc.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Reuse raw snapshots; make no network requests")
    mode.add_argument("--render-only", action="store_true", help="Render the existing enriched store without changing upstream data")
    parser.add_argument("--stage", action="store_true", help="Validate and stage the complete static release after building")
    args = parser.parse_args()
    if args.render_only:
        selected = list(RENDER_ONLY)
    elif args.offline:
        selected = [name for name in SCRIPTS if name not in OFFLINE_SKIP]
    else:
        selected = list(SCRIPTS)
    print("Vigie pipeline" + (" (offline snapshots)" if args.offline else " (render existing store)" if args.render_only else " (refresh sources)"))
    for name in selected:
        if args.offline and name in OFFLINE_FLAGGED:
            run(name, "--offline")
        else:
            run(name)
    if args.stage:
        run("stage_public.py")
    print("\nDone. Serve with: python scripts/serve.py")
    print("Then open: http://127.0.0.1:8765/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
