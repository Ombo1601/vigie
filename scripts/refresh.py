"""
Vigie - one-shot refresh: collect, verify, stage, deploy to production.

Designed to run unattended. The primary runner is the GitHub Actions workflow
(.github/workflows/vigie-refresh.yml): it restores the cross-edition state from
the private `Ombo1601/vigie-state` store, runs this chain, and persists the state
back. The Windows scheduled task is an optional local fallback. Every failing
step stops the chain: a broken edition is never deployed and the previously
deployed site stays up.

Steps:
  1. pipeline.py --stage   full online collection + render + stage
  2. verify.py             tests + syntax + stage + smoke. Never --rebuild:
                           an offline rebuild collapses the honest diffs
                           between editions (roadworks, change ledger).
  3. vercel link           re-link deploy/public (staging wipes .vercel, and
                           verify re-stages, so this must come after)
  4. vercel deploy --prod  publish. Later deploys of a linked project default
                           to preview, so --prod is explicit.

Appends to data/ops/refresh.log. A lock file prevents overlapping runs; a
lock older than two hours is treated as stale and taken over. Exit code 0
only when the chain completed (or was skipped because another run is active).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import store_io

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data" / "ops" / "refresh.log"
LOCK_PATH = ROOT / "data" / "ops" / "refresh.lock"
ROADS_SIGNAL_PATH = ROOT / "data" / "ops" / "roads_signal.json"
ROADWORKS_STORE = ROOT / "data" / "roadworks" / "latest_roadworks.json"
ROADS_SIGNAL_METHOD = "roads-signal-v1"
SITE_URL = "https://vigieqc.com"
INDEXNOW_KEY = "c977ad1a490feff9553222dafa4921b4"
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
INDEXNOW_URLS = (
    f"{SITE_URL}/", f"{SITE_URL}/explorer.html", f"{SITE_URL}/morning.html",
    f"{SITE_URL}/registre.html", f"{SITE_URL}/memoire.html",
    f"{SITE_URL}/dossiers.html", f"{SITE_URL}/partir.html", f"{SITE_URL}/affiche.html",
)
LOCK_STALE_SECONDS = 2 * 3600
LOG_ROTATE_BYTES = 5 * 1024 * 1024  # one previous log kept as refresh.log.1
TEAM = "deemto"
PROJECT = "vigie"
DEPLOY_DIR = ROOT / "deploy" / "public"


def log(line: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp} {line}", flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(str(LOG_PATH), str(LOG_PATH.with_suffix(".log.1")))
    except OSError:
        pass
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {line}\n")


def run_step(name: str, command: list[str], timeout: int, cwd: Path = ROOT) -> None:
    log(f"START {name}")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{name} timed out after {timeout}s") from None
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        err = "\n".join((proc.stderr or "").strip().splitlines()[-10:])
        out = "\n".join((proc.stdout or "").strip().splitlines()[-10:])
        raise RuntimeError(f"{name} failed (code {proc.returncode})\n{err}\n{out}")
    log(f"DONE {name} in {elapsed:.0f}s")
    tail = (proc.stdout or "").strip().splitlines()[-4:]
    for line in tail:
        log(f"  | {line}")


def _create_lock(token: str) -> bool:
    """Exclusive create; writes the owner token. False when already held."""
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    return True


def acquire_lock() -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    token = str(os.getpid())
    if _create_lock(token):
        return True
    try:
        age = time.time() - LOCK_PATH.stat().st_mtime
    except OSError:
        # Held then released between our create and the stat: just try again.
        return _create_lock(token)
    if age < LOCK_STALE_SECONDS:
        return False
    log(f"WARN stale lock ({age:.0f}s old) - taking over")
    # Serialize takeover through a sidecar claim created with O_EXCL: only one
    # process can ever hold it, so two runs that both saw the stale lock cannot
    # both replace it (the previous rename approach could move a winner's
    # fresh lock aside).
    claim = LOCK_PATH.with_name(LOCK_PATH.name + ".takeover")
    try:
        cfd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - claim.stat().st_mtime < LOCK_STALE_SECONDS:
                return False  # another taker is working
            os.unlink(str(claim))  # a crashed taker left it behind
            cfd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
    except OSError:
        return False
    try:
        with os.fdopen(cfd, "w", encoding="utf-8") as handle:
            handle.write(token)
        # Re-verify the lock is still the same stale instance before removing
        # it: if a winner refreshed it while we claimed, stand down.
        try:
            if time.time() - LOCK_PATH.stat().st_mtime < LOCK_STALE_SECONDS:
                return False
        except OSError:
            pass
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
        return _create_lock(token)
    finally:
        try:
            claim.unlink()
        except OSError:
            pass


_ROADS_FIELDS = (
    "event_type", "event_status", "vehicle_impact", "direction",
    "start_date", "end_date", "description", "road_names", "restrictions", "update_date",
)


def roads_signal(store_path: Path = ROADWORKS_STORE) -> str:
    """Content signal of the declared obstructions, ignoring collection clocks.

    `fetched_at` and the per-event presence counters move on every collection, so
    they are not a change; the active event set and its relayed fields are. An
    empty or unreadable store yields "" and the caller then refreshes.
    """
    try:
        doc = json.loads(Path(store_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    events = doc.get("events") if isinstance(doc, dict) else None
    if not isinstance(events, list):
        return ""
    rows = [
        [str(event.get("event_id") or "")] + [event.get(field) for field in _ROADS_FIELDS]
        for event in sorted(
            (e for e in events if isinstance(e, dict)),
            key=lambda e: str(e.get("event_id") or ""),
        )
    ]
    payload = json.dumps(rows, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_roads_signal() -> str:
    try:
        doc = json.loads(ROADS_SIGNAL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if isinstance(doc, dict) and doc.get("method") == ROADS_SIGNAL_METHOD:
        return str(doc.get("sha256") or "")
    return ""


def write_roads_signal(sha: str) -> None:
    if not sha:
        return
    try:
        store_io.write_json_atomic(ROADS_SIGNAL_PATH, {"method": ROADS_SIGNAL_METHOD, "sha256": sha})
    except OSError:
        pass


def ping_indexnow() -> None:
    """Best-effort: tell IndexNow (Bing — which DuckDuckGo reads — Yandex,
    Seznam, Naver) that the brief changed. A failure is logged, never fatal."""
    payload = json.dumps({
        "host": "vigieqc.com",
        "key": INDEXNOW_KEY,
        "keyLocation": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
        "urlList": list(INDEXNOW_URLS),
    }).encode("utf-8")
    try:
        request = urllib.request.Request(
            INDEXNOW_ENDPOINT, data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            log(f"indexnow: HTTP {response.status}")
    except Exception as exc:  # discovery is best-effort
        log(f"WARN indexnow ping failed ({type(exc).__name__})")


def _roads_only(python: str, *, deploy: bool) -> int:
    """The real-time lane: refresh the official obstructions without an edition.

    Never runs normalize/enrich/cluster, so the edition, its diff and the durable
    history are untouched. The whole re-render and deploy is skipped when the
    declarations did not change — collection clocks are not a change.
    """
    before = read_roads_signal()
    run_step("wzdx", [python, "-X", "utf8", str(ROOT / "scripts" / "ingest_wzdx.py")], timeout=600)
    after = roads_signal()
    if not after or after == before:
        log("NOCHANGE declared obstructions unchanged; render and deploy skipped")
        return 0
    run_step("anomalies", [python, "-X", "utf8", str(ROOT / "scripts" / "compile_anomalies.py")], timeout=300)
    run_step("edges", [python, "-X", "utf8", str(ROOT / "scripts" / "edge_atlas.py")], timeout=300)
    run_step("render", [python, "-X", "utf8", str(ROOT / "scripts" / "pipeline.py"), "--render-only"], timeout=600)
    run_step("verify", [python, "-X", "utf8", str(ROOT / "scripts" / "verify.py")], timeout=1800)
    if not deploy:
        write_roads_signal(after)
        log("OK roads refresh complete (deploy skipped by --no-deploy)")
        return 0
    vercel = shutil.which("vercel")
    if not vercel:
        raise RuntimeError("vercel CLI not found on PATH")
    run_step(
        "link",
        [vercel, "link", "--yes", "--scope", TEAM, "--project", PROJECT, "--cwd", str(DEPLOY_DIR)],
        timeout=300,
    )
    run_step("deploy", [vercel, "deploy", str(DEPLOY_DIR), "-y", "--prod"], timeout=900)
    write_roads_signal(after)
    log("OK roads production updated")
    ping_indexnow()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect, verify and deploy one edition.")
    parser.add_argument(
        "--no-deploy",
        action="store_true",
        help="Collect, verify and stage only; do not touch Vercel",
    )
    parser.add_argument(
        "--roads-only",
        action="store_true",
        help="Refresh only the official roadworks lane (no new edition): ingest WZDX, "
             "recompile anomalies and edges, re-render, and deploy only when the "
             "declared obstructions changed",
    )
    args = parser.parse_args(argv)

    if not acquire_lock():
        log("SKIP another refresh is already running (lock held)")
        return 0
    try:
        python = sys.executable
        if args.roads_only:
            return _roads_only(python, deploy=not args.no_deploy)
        run_step(
            "pipeline",
            [python, "-X", "utf8", str(ROOT / "scripts" / "pipeline.py"), "--stage"],
            timeout=1800,
        )
        run_step(
            "verify",
            [python, "-X", "utf8", str(ROOT / "scripts" / "verify.py")],
            timeout=1800,
        )
        if args.no_deploy:
            log("OK refresh complete (deploy skipped by --no-deploy)")
            return 0
        vercel = shutil.which("vercel")
        if not vercel:
            raise RuntimeError("vercel CLI not found on PATH")
        run_step(
            "link",
            [
                vercel, "link", "--yes",
                "--scope", TEAM,
                "--project", PROJECT,
                "--cwd", str(DEPLOY_DIR),
            ],
            timeout=300,
        )
        run_step(
            "deploy",
            # No --no-wait: the CLI exiting 0 means the deployment was created,
            # not that production serves the new edition. Waiting (bounded by
            # the step timeout) makes the OK line below a fact.
            [vercel, "deploy", str(DEPLOY_DIR), "-y", "--prod"],
            timeout=900,
        )
        write_roads_signal(roads_signal())
        log("OK production updated")
        ping_indexnow()
        return 0
    except (RuntimeError, OSError) as exc:
        log(f"FAIL {exc}")
        return 1
    finally:
        # Only remove the lock this run created: a takeover or a skipped run
        # must never delete another process's lock.
        try:
            if LOCK_PATH.read_text(encoding="utf-8", errors="replace").strip() == str(os.getpid()):
                LOCK_PATH.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
