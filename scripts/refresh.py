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
  3. chain guard           the registre about to be published must continue the
                           anchored (anchors/checkpoint.txt) and live chain:
                           never shorter, never forked, and a full run must
                           have sealed a new edition.
  4. vercel link           re-link deploy/public (staging wipes .vercel, and
                           verify re-stages, so this must come after)
  5. vercel deploy --prod  publish. Later deploys of a linked project default
                           to preview, so --prod is explicit.
  6. live check            the served checkpoint must match what was deployed.

Production deploys run only on GitHub Actions (GITHUB_ACTIONS=true). Break-glass,
logged loudly: VIGIE_ALLOW_LOCAL_DEPLOY=1 (deploy from elsewhere) and
VIGIE_SKIP_CHAIN_GUARD=1 (publish despite a continuity refusal).

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
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import registre
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
ANCHOR_PATH = ROOT / "anchors" / "checkpoint.txt"
STAGED_CHECKPOINT = DEPLOY_DIR / "registre" / "checkpoint.txt"
LIVE_CHECKPOINT_URL = f"{SITE_URL}/registre/checkpoint.txt"
LIVE_TIMEOUT = 15
LIVE_ATTEMPTS = 4          # post-deploy reads of the served checkpoint
LIVE_SPACING = 20          # seconds between them
ALLOW_LOCAL_ENV = "VIGIE_ALLOW_LOCAL_DEPLOY"
SKIP_GUARD_ENV = "VIGIE_SKIP_CHAIN_GUARD"


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
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        log(f"WARN indexnow ping failed ({type(exc).__name__})")


# --------------------------------------------------------------------------- #
# Deploy authority and chain continuity
# --------------------------------------------------------------------------- #
class GuardRefusal(RuntimeError):
    """A deploy refused on evidence: the run fails, production stays as it is."""


def deploy_refusal(env) -> str:
    """Why this process may not publish production ("" when it may).

    The workflows are the only production writer: they restore the real state
    first. A laptop or the old scheduled task holds a stale (possibly forked)
    data/, so publishing from it would rewrite the public record. This is an
    accident guard, not an access control: the token is the access control.
    """
    if env.get("GITHUB_ACTIONS") == "true":
        return ""
    if env.get(ALLOW_LOCAL_ENV) == "1":
        log(f"WARN {ALLOW_LOCAL_ENV}=1: deploying production from outside GitHub Actions "
            "(break-glass) - this data/ must hold the restored vigie-state chain")
        return ""
    return (f"production deploys run only on GitHub Actions; this run would publish "
            f"local data/. Use --no-deploy, or set {ALLOW_LOCAL_ENV}=1 (break-glass, "
            f"after unpacking the private state)")


@dataclass(frozen=True)
class Reading:
    """One checkpoint as seen from one place.

    status: "ok" (seq/root parsed), "absent" (the file/URL does not exist) or
    "unusable" (unreadable, unparseable or unreachable; detail says why).
    """
    name: str
    status: str
    seq: int = 0
    root: str = ""
    detail: str = ""


NOT_A_CHECKPOINT = "not a registre checkpoint"


def parse_checkpoint(text: object) -> tuple[int, str] | None:
    """(seq, root) from registre.checkpoint_text output; None when it is not one."""
    if not isinstance(text, str):
        return None
    lines = text.lstrip("﻿").splitlines()  # tolerate a leading UTF-8 BOM
    if len(lines) < 3 or lines[0].strip() != registre.ORIGIN:
        return None
    seq_raw, root = lines[1].strip(), lines[2].strip()
    if not seq_raw.isdigit():
        return None
    seq = int(seq_raw)
    if seq == 0:
        return (0, "") if not root else None
    if len(root) != 64 or any(c not in "0123456789abcdef" for c in root):
        return None
    return seq, root


def read_file_checkpoint(name: str, path: Path) -> Reading:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return Reading(name, "absent")
    except (OSError, UnicodeDecodeError) as exc:
        return Reading(name, "unusable", detail=type(exc).__name__)
    parsed = parse_checkpoint(text)
    if parsed is None:
        return Reading(name, "unusable", detail=NOT_A_CHECKPOINT)
    return Reading(name, "ok", *parsed)


def fetch_text(url: str, timeout: float) -> str | None:
    """GET a small text file. None means definitively absent (404/410);
    any other failure raises."""
    request = urllib.request.Request(
        url, headers={"Cache-Control": "no-cache", "User-Agent": "vigie-refresh"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return None
        raise


def read_live_checkpoint(fetcher=None, url: str = LIVE_CHECKPOINT_URL) -> Reading:
    try:
        text = (fetcher or fetch_text)(url, LIVE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - the network is never evidence
        return Reading("live", "unusable", detail=f"{type(exc).__name__}: {exc}"[:160])
    if text is None:
        return Reading("live", "absent")
    parsed = parse_checkpoint(text)
    if parsed is None:
        return Reading("live", "unusable", detail=NOT_A_CHECKPOINT)
    return Reading("live", "ok", *parsed)


def continuity_verdict(seals: list[dict], staged: Reading, anchor: Reading,
                       live: Reading, *, full_run: bool) -> tuple[list[str], list[str]]:
    """(refusals, warnings) for publishing `seals` as the registre.

    Fail-closed on evidence of regression (a shorter chain, another root at a
    published seq, a stalled edition), fail-open on the network: an unreachable
    live site is a warning and the committed anchor decides. Hashing is
    registre.py's; nothing here recomputes a root.
    """
    refusals: list[str] = []
    warnings: list[str] = []
    ok, msg = registre.verify_chain(seals)
    if not ok:
        refusals.append(f"local registre chain does not verify ({msg})")
    if [s.get("seq") for s in seals] != list(range(1, len(seals) + 1)):
        refusals.append("local registre seqs are not contiguous from 1")
    tip_seq = seals[-1]["seq"] if seals else 0
    tip_root = seals[-1]["root"] if seals else ""
    by_seq = {s.get("seq"): s.get("root") for s in seals}

    # The staged checkpoint is what goes live; the state is what proves it.
    if staged.status != "ok":
        refusals.append(f"staged checkpoint {staged.status} ({staged.detail or 'missing'})")
    elif (staged.seq, staged.root) != (tip_seq, tip_root):
        refusals.append(
            f"staged checkpoint (seq {staged.seq} {staged.root[:12]}) differs from the "
            f"registre state (seq {tip_seq} {tip_root[:12]}) - did the registre emit fail?")

    if anchor.status == "unusable":
        refusals.append(f"anchor checkpoint unusable ({anchor.detail}) - restore it from git history")
    elif anchor.status == "absent":
        if live.status == "absent":
            warnings.append("no anchor and no live checkpoint: first run")
        elif live.status == "ok":
            refusals.append(f"anchor checkpoint missing while live is at seq {live.seq} "
                            "- restore anchors/checkpoint.txt from git history")
        else:
            refusals.append("anchor checkpoint missing and live unreachable: "
                            "cannot establish a first run")
    if live.status == "unusable":
        warnings.append(f"live checkpoint unusable ({live.detail}); relying on the anchor")
    elif live.status == "absent" and anchor.status == "ok":
        warnings.append("live checkpoint absent although an anchor exists")

    published = 0
    for ref in (anchor, live):
        if ref.status != "ok":
            continue
        published = max(published, ref.seq)
        if tip_seq < ref.seq:
            kind = "genesis reset" if tip_seq <= 1 else "shrink"
            refusals.append(f"chain regression ({kind}): about to publish seq {tip_seq}, "
                            f"{ref.name} is at seq {ref.seq}")
        elif tip_seq == ref.seq:
            if tip_root != ref.root:
                refusals.append(f"fork: seq {tip_seq} root {tip_root[:12]} differs from "
                                f"{ref.name} root {ref.root[:12]}")
        elif ref.seq > 0 and by_seq.get(ref.seq) != ref.root:
            refusals.append(f"fork: {ref.name} seal {ref.seq} ({ref.root[:12]}) is not in "
                            f"the local chain ({str(by_seq.get(ref.seq) or '-')[:12]})")
    # A full run collects, so it must have sealed an edition beyond everything
    # already published; the roads lane never mints one.
    if full_run and tip_seq <= published:
        refusals.append(f"stalled chain: a full refresh must seal a new edition "
                        f"(local seq {tip_seq}, published seq {published})")
    return refusals, warnings


def chain_guard(*, full_run: bool, fetcher=None, env=None,
                state_path: Path | None = None, anchor_path: Path | None = None,
                staged_path: Path | None = None) -> tuple[int, str]:
    """Refuse a publish that would break the registre. Returns the (seq, root) published."""
    env = os.environ if env is None else env
    seals = registre.load_state(state_path or registre.STATE)["seals"]
    staged = read_file_checkpoint("staged", staged_path or STAGED_CHECKPOINT)
    anchor = read_file_checkpoint("anchor", anchor_path or ANCHOR_PATH)
    live = read_live_checkpoint(fetcher)
    refusals, warnings = continuity_verdict(seals, staged, anchor, live, full_run=full_run)
    for line in warnings:
        log(f"WARN chain guard: {line}")
    tip = (seals[-1]["seq"], seals[-1]["root"]) if seals else (0, "")
    if not refusals:
        log(f"chain guard: OK seq {tip[0]} {tip[1][:12]} "
            f"(anchor {anchor.status} {anchor.seq}, live {live.status} {live.seq})")
        return tip
    if env.get(SKIP_GUARD_ENV) == "1":
        for line in refusals:
            log(f"WARN !!! {SKIP_GUARD_ENV}=1 overrides a chain refusal: {line}")
        log("WARN !!! publishing a registre the chain guard refused (break-glass)")
        # What ships is the staged checkpoint, not the state tip: the post-deploy
        # check must compare production against what was actually uploaded.
        if staged.status == "ok":
            return (staged.seq, staged.root)
        return tip
    raise GuardRefusal("chain guard refused the deploy: " + "; ".join(refusals)
                       + f" (break-glass: {SKIP_GUARD_ENV}=1)")


def verify_live(expected: tuple[int, str], fetcher=None, *,
                attempts: int = LIVE_ATTEMPTS, spacing: float = LIVE_SPACING,
                sleep=time.sleep) -> None:
    """After a deploy, production must serve the checkpoint we published.

    The last read decides: a disagreement there fails the run (so the alert
    issue opens); an unreachable site there is only a warning.
    """
    reading = Reading("live", "unusable", detail="not read")
    for attempt in range(max(1, attempts)):
        if attempt:
            sleep(spacing)
        reading = read_live_checkpoint(fetcher)
        if reading.status == "ok" and (reading.seq, reading.root) == tuple(expected):
            log(f"live check: OK seq {reading.seq} {reading.root[:12]}")
            return
    if reading.status == "unusable" and reading.detail != NOT_A_CHECKPOINT:
        log(f"WARN live check: production unreachable ({reading.detail}); not verified")
        return
    served = f"seq {reading.seq} {reading.root[:12]}" if reading.status == "ok" else reading.status
    raise RuntimeError(f"live check: production serves {served}, deployed seq "
                       f"{expected[0]} {expected[1][:12]} (after {max(1, attempts)} reads)")


def _deploy(vercel: str, *, full_run: bool, fetcher=None) -> None:
    """Guard, link, deploy, then confirm what production serves."""
    expected = chain_guard(full_run=full_run, fetcher=fetcher)
    run_step(
        "link",
        [vercel, "link", "--yes", "--scope", TEAM, "--project", PROJECT, "--cwd", str(DEPLOY_DIR)],
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
    verify_live(expected, fetcher)


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
    # The lane never mints an edition: regression is refused, advance is not required.
    _deploy(vercel, full_run=False)
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

    if not args.no_deploy:
        refusal = deploy_refusal(os.environ)
        if refusal:
            log(f"FAIL deploy refused: {refusal}")
            return 2
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
        _deploy(vercel, full_run=True)
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
