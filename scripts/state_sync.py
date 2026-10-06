"""Restore and persist the cross-edition state: fail closed, verified, versioned.

The state tarball (scripts/state_pack.py) is the only copy of Le Registre. The
sealed chain cannot be regenerated and a published seal is immutable, so the CI
steps that move the tarball are never allowed to guess:

restore   asks GitHub whether the `state` release exists. "fresh" is chosen only
          on a definitive answer: the repository is visible to the token AND the
          release is a 404, or the release carries no state asset of any kind.
          Every other failure (401/403, 429, 5xx, network, timeout, garbage) is
          retried with a bounded backoff, then FAILS the job before the refresh
          can collect into an empty data/ and seal a genesis. A release holding a
          digest or dated copies but no state.tar.gz is an interrupted persist,
          never a first run. The download must match the listed size and the
          sha256 recorded at persist time (state.tar.gz.sha256; absent only for
          the one-time legacy archive, which is allowed loudly), and its chain
          must contain the seal the public git anchor (anchors/checkpoint.txt)
          witnessed. A fresh start is refused while that anchor names a seal.
persist   never writes over a restore that did not succeed, and refuses an
          archive whose chain shrank or forked, that lost registre/registre.json,
          or whose member count or size collapsed below half of the restored one
          (VIGIE_STATE_ALLOW_SHRINK=1 overrides the size check only, and says
          so). With --dated it uploads an immutable dated copy FIRST and checks
          it on the release, and only then replaces the rolling asset and its
          digest: `gh release upload --clobber` deletes before it uploads, so a
          good copy must already exist. Dated copies beyond the newest --keep are
          pruned; a prune failure is a warning, never a failed run.

The outcome goes to $GITHUB_OUTPUT (outcome=restored|fresh|failed, seal_count,
head_root, sha256, members, bytes) and to <dir>/restore.json, the baseline the
persist step reads. Every gh call goes through an injectable runner, so each
failure mode is a unit test, never a production experiment.

Usage:
  python scripts/state_sync.py restore --repo OWNER/REPO --tag state --dir DIR
  python scripts/state_sync.py persist --repo OWNER/REPO --tag state --dir DIR [--dated] [--keep 12]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import state_pack
import store_io

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = ROOT / "anchors" / "checkpoint.txt"

ROLLING = "state.tar.gz"
DIGEST = ROLLING + ".sha256"
DATED = re.compile(r"^state-\d{8}T\d{6}Z\.tar\.gz$")
REGISTRE_MEMBER = "data/registre/registre.json"
BASELINE = "restore.json"
RESTORED, FRESH, FAILED = "restored", "fresh", "failed"
OUTPUT_KEYS = ("outcome", "seal_count", "head_root", "sha256", "members", "bytes")

ATTEMPTS = 3
BACKOFF = (5, 20)        # seconds between attempts: bounded, the next schedule retries anyway
API_TIMEOUT = 60
TRANSFER_TIMEOUT = 600
KEEP_DATED = 12          # three days of six-hourly editions
MIN_FRACTION = 0.5       # a packed state below half of the restored one is a collapse
ALLOW_SHRINK = "VIGIE_STATE_ALLOW_SHRINK"
RELEASE_TITLE = "Vigie state"
RELEASE_NOTES = ("Rolling snapshot of the cross-edition state (private; publisher "
                 "content never enters the public repo).")


@dataclass(frozen=True)
class Result:
    code: int
    out: str = ""
    err: str = ""


Runner = Callable[[list, float], Result]


class Refused(RuntimeError):
    """The state store must not be trusted or written this run: the job fails."""


def run_gh(args: list[str], timeout: float) -> Result:
    """The real gh. A timeout or a missing binary is a failed call, not a crash."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(124, "", f"timed out after {timeout:g}s")
    except OSError as exc:
        return Result(127, "", f"gh not runnable: {exc}")
    return Result(proc.returncode, proc.stdout or "", proc.stderr or "")


def _say(level: str, message: str) -> None:
    """One log line; warnings and errors also become GitHub run annotations."""
    prefix = f"::{level}::" if level in ("notice", "warning", "error") else ""
    print(f"{prefix}state: {message}", flush=True)


def _tail(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no message"


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# --------------------------------------------------------------------------- #
# GitHub answers
# --------------------------------------------------------------------------- #
_STATUS_LINE = re.compile(r"HTTP/\S+\s+(\d{3})\b")
_STATUS_ERR = re.compile(r"\(HTTP (\d{3})\)")


def http_answer(res: Result) -> tuple[int | None, str]:
    """(status, body) of a `gh api --include` call; status None = no HTTP answer.

    The status line printed by --include is authoritative; gh's stderr
    "(HTTP 404)" is the fallback. A non-zero exit with neither (no network, no
    token, a timeout) is not an answer, so it can never be mistaken for a 404.
    """
    lines = res.out.replace("\r\n", "\n").split("\n")
    head = _STATUS_LINE.match(lines[0])
    if head:
        blank = lines.index("", 1) if "" in lines[1:] else len(lines)
        return int(head.group(1)), "\n".join(lines[blank + 1:])
    err = _STATUS_ERR.search(res.err)
    if err:
        return int(err.group(1)), res.out
    return (200 if res.code == 0 else None), res.out


def api_get(gh: Runner, path: str) -> tuple[int | None, dict | None, str]:
    """GET one API path: (status, JSON object or None, reason to log)."""
    res = gh(["api", "--include", path], API_TIMEOUT)
    status, body = http_answer(res)
    if status is None:
        return None, None, f"no HTTP answer (exit {res.code}): {_tail(res.err)}"
    if not 200 <= status < 300:
        return status, None, f"HTTP {status}: {_tail(res.err)}"
    try:
        doc = json.loads(body)
    except ValueError:
        return status, None, "unparsable response body"
    if not isinstance(doc, dict):
        return status, None, "unexpected response shape"
    return status, doc, ""


def retrying(what: str, attempt: Callable[[], tuple[bool, object, str]],
             sleep: Callable[[float], None]) -> object:
    """Run attempt() until it is done; a bounded number of tries, then refuse."""
    reason = ""
    for n in range(1, ATTEMPTS + 1):
        done, value, reason = attempt()
        if done:
            return value
        print(f"state: {what}: attempt {n}/{ATTEMPTS} failed: {reason}", flush=True)
        if n < ATTEMPTS:
            sleep(BACKOFF[min(n - 1, len(BACKOFF) - 1)])
    raise Refused(f"{what}: {reason} (gave up after {ATTEMPTS} attempts)")


def lookup_release(gh: Runner, repo: str, tag: str, sleep, *, absent_ok: bool) -> dict | None:
    def attempt():
        status, doc, why = api_get(gh, f"repos/{repo}/releases/tags/{tag}")
        if status == 404 and absent_ok:
            return True, None, ""
        if doc is not None and isinstance(doc.get("assets"), list):
            return True, doc, ""
        return False, None, why or "release document without an assets list"
    return retrying(f"release {tag!r} on {repo}", attempt, sleep)


def find_release(gh: Runner, repo: str, tag: str, sleep) -> dict | None:
    """The release, or None only when GitHub definitively says there is none.

    A private repository answers 404 to a token that cannot see it, so the
    release 404 only means "absent" once the repository itself answered 200.
    """
    def visible():
        status, doc, why = api_get(gh, f"repos/{repo}")
        if doc is not None:
            return True, doc, ""
        if status == 404:
            why = "HTTP 404 on the repository itself (missing, or a token without access)"
        return False, None, why
    retrying(f"state repository {repo}", visible, sleep)
    return lookup_release(gh, repo, tag, sleep, absent_ok=True)


def _assets(release: dict | None) -> dict[str, dict]:
    return {
        a["name"]: a for a in (release or {}).get("assets") or []
        if isinstance(a, dict) and isinstance(a.get("name"), str)
    }


# --------------------------------------------------------------------------- #
# The archive and its chain
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def chain(doc: object) -> list[tuple[int, str]]:
    """(seq, root) of every well-formed edition seal, in chain order."""
    seals = doc.get("seals") if isinstance(doc, dict) else None
    if not isinstance(seals, list):
        return []
    return [
        (s["seq"], s["root"]) for s in seals
        if isinstance(s, dict) and _int(s.get("seq")) and isinstance(s.get("root"), str)
    ]


def inspect_archive(path: Path) -> dict:
    """Members, bytes and the registre chain of a packed state, without extracting it."""
    doc = None
    with tarfile.open(path, "r:gz") as tar:
        files = [m for m in tar.getmembers() if m.isfile()]
        registre = next((m for m in files if m.name == REGISTRE_MEMBER), None)
        if registre is not None:
            handle = tar.extractfile(registre)
            try:
                doc = json.loads(handle.read().decode("utf-8")) if handle else None
            except ValueError:
                doc = None
    seals = chain(doc)
    head = seals[-1] if seals else (0, "")
    return {
        "members": len(files),
        "bytes": sum(m.size for m in files),
        "registre": registre is not None,
        "seals": seals,
        "seal_count": len(seals),
        "head_seq": head[0],
        "head_root": head[1],
    }


def read_anchor(path: Path) -> tuple[int, str] | None:
    """(seq, root) the public git anchor witnessed; None when it witnessed nothing."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    try:
        seq = int(lines[1])
    except (IndexError, ValueError):
        _say("warning", f"anchor {Path(path).name} is unreadable; not compared")
        return None
    root = lines[2].strip() if len(lines) > 2 else ""
    return (seq, root) if seq > 0 and root else None


def witness_refusal(seals: list[tuple[int, str]], witnessed: tuple[int, str] | None) -> str:
    """Why a restored chain contradicts the git anchor, or "" when it does not."""
    if not witnessed:
        return ""
    seq, root = witnessed
    have = dict(seals)
    if seq not in have:
        last = seals[-1][0] if seals else 0
        return (f"the restored chain stops at seal n° {last} but the git anchor witnessed "
                f"n° {seq}: this state is older than what was published")
    if have[seq] != root:
        return (f"seal n° {seq} is {have[seq][:12]} in the restored chain but {root[:12]} "
                f"in the git anchor: a forked chain")
    return ""


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #
def _download(gh: Runner, repo: str, tag: str, name: str, workdir: Path, size: object, sleep) -> Path:
    target = workdir / name

    def attempt():
        res = gh(["release", "download", tag, "--repo", repo, "--pattern", name,
                  "--dir", str(workdir), "--clobber"], TRANSFER_TIMEOUT)
        if res.code != 0:
            return False, None, f"exit {res.code}: {_tail(res.err)}"
        got = target.stat().st_size if target.is_file() else -1
        if got != size:
            return False, None, f"got {got} bytes, the release lists {size}"
        return True, target, ""
    return retrying(f"download {name}", attempt, sleep)


def restore(repo: str, tag: str, workdir: Path, *, gh: Runner = run_gh, sleep=time.sleep,
            unpack=None, anchor: Path | None = None) -> dict:
    """Restore the state into data/, or raise Refused. Returns the baseline."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    witnessed = read_anchor(ANCHOR if anchor is None else anchor)
    release = find_release(gh, repo, tag, sleep)
    assets = _assets(release)
    if ROLLING not in assets:
        traces = sorted(n for n in assets if n == DIGEST or DATED.match(n))
        if traces:
            raise Refused(f"release {tag!r} holds {', '.join(traces)} but no {ROLLING}: an "
                          f"interrupted persist, not a first run. Re-upload the newest dated "
                          f"copy as {ROLLING} (and its digest) by hand, then re-run")
        if witnessed:
            raise Refused(f"no state in {repo}, but the git anchor witnessed seal n° "
                          f"{witnessed[0]}: a fresh start would fork the published chain")
        missing = f"release {tag!r}" if release is None else f"{ROLLING} in release {tag!r}"
        _say("warning", f"FRESH START: {repo} has no {missing}; collecting into an empty "
                        f"memory, the registre begins at seal n° 1")
        return {"outcome": FRESH, "release": release is not None, "seal_count": 0,
                "head_seq": 0, "head_root": "", "sha256": "", "members": 0, "bytes": 0}

    archive = _download(gh, repo, tag, ROLLING, workdir, assets[ROLLING].get("size"), sleep)
    digest = sha256_file(archive)
    if DIGEST in assets:
        sidecar = _download(gh, repo, tag, DIGEST, workdir, assets[DIGEST].get("size"), sleep)
        recorded = (sidecar.read_text(encoding="utf-8", errors="replace").split() or [""])[0].lower()
        if recorded != digest:
            raise Refused(f"{ROLLING} is sha256 {digest[:12]} but {DIGEST} recorded "
                          f"{recorded[:12] or 'nothing'}: refusing an unverified state")
    else:
        _say("warning", f"{ROLLING} has no recorded digest ({DIGEST}): the one-time legacy "
                        f"archive, accepted unverified (sha256 {digest[:12]})")
    facts = inspect_archive(archive)
    if not facts["registre"]:
        raise Refused(f"the restored state carries no {REGISTRE_MEMBER}")
    contradiction = witness_refusal(facts["seals"], witnessed)
    if contradiction:
        raise Refused(contradiction)
    count = (state_pack.unpack if unpack is None else unpack)(archive)
    print(f"state: restored {count} file(s) from {ROLLING} (sha256 {digest[:12]}, "
          f"{facts['members']} members, {facts['bytes']} bytes); registre "
          f"{facts['seal_count']} seal(s), head {facts['head_root'][:12] or '-'}", flush=True)
    return {"outcome": RESTORED, "release": True, "seal_count": facts["seal_count"],
            "head_seq": facts["head_seq"], "head_root": facts["head_root"], "sha256": digest,
            "members": facts["members"], "bytes": facts["bytes"]}


def write_outputs(path: str | None, baseline: dict) -> None:
    """Append the step outputs to $GITHUB_OUTPUT (no-op outside Actions)."""
    if not path:
        return
    lines = [f"{key}={' '.join(str(baseline.get(key, '')).split())}" for key in OUTPUT_KEYS]
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Persist
# --------------------------------------------------------------------------- #
def persist_refusals(baseline: dict, facts: dict, *, allow_shrink: bool) -> tuple[list[str], list[str]]:
    """(refusals, warnings) for uploading the packed `facts` over the restored baseline."""
    refusals: list[str] = []
    warnings: list[str] = []
    if not facts["registre"]:
        refusals.append(f"the packed state carries no {REGISTRE_MEMBER}")
    before = _int(baseline.get("seal_count"))
    if facts["seal_count"] < before:
        refusals.append(f"the registre would shrink from {before} to {facts['seal_count']} "
                        f"seal(s); a published chain never shrinks")
    head_seq, head_root = _int(baseline.get("head_seq")), str(baseline.get("head_root") or "")
    if head_seq and dict(facts["seals"]).get(head_seq) != head_root:
        refusals.append(f"the restored head seal n° {head_seq} ({head_root[:12]}) is not in the "
                        f"packed chain: refusing a fork")
    for key, label in (("members", "member count"), ("bytes", "total size")):
        was, now = _int(baseline.get(key)), facts[key]
        if was and now < was * MIN_FRACTION:
            message = (f"{label} fell from {was} to {now}, below "
                       f"{round(MIN_FRACTION * 100)} percent of the restored state")
            if allow_shrink:
                warnings.append(f"{message}; uploading anyway ({ALLOW_SHRINK}=1)")
            else:
                refusals.append(f"{message} (set {ALLOW_SHRINK}=1 to override)")
    return refusals, warnings


def _upload(gh: Runner, repo: str, tag: str, path: Path, sleep) -> None:
    def attempt():
        res = gh(["release", "upload", tag, str(path), "--repo", repo, "--clobber"], TRANSFER_TIMEOUT)
        return res.code == 0, None, f"exit {res.code}: {_tail(res.err)}"
    retrying(f"upload {path.name}", attempt, sleep)


def _confirm(gh: Runner, repo: str, tag: str, sleep, expected: dict[str, tuple[int, str]]) -> dict[str, dict]:
    """The release must list each uploaded asset with its size (and digest, when GitHub reports one)."""
    assets = _assets(lookup_release(gh, repo, tag, sleep, absent_ok=False))
    for name, (size, digest) in expected.items():
        asset = assets.get(name) or {}
        if asset.get("size") != size or asset.get("state", "uploaded") != "uploaded":
            raise Refused(f"upload of {name} not confirmed: the release lists "
                          f"{asset.get('size', 'nothing')} bytes, we sent {size}")
        server = asset.get("digest")
        if digest and isinstance(server, str) and server.startswith("sha256:") \
                and server != f"sha256:{digest}":
            raise Refused(f"upload of {name} not confirmed: GitHub computed {server[:19]}, "
                          f"we sent sha256:{digest[:12]}")
    return assets


def prune_plan(names, keep: int, protect: set[str]) -> list[str]:
    """Dated copies beyond the newest `keep`; never the rolling asset or a protected name."""
    dated = sorted((n for n in names if isinstance(n, str) and DATED.match(n)), reverse=True)
    return [n for n in dated[max(keep, 1):] if n not in protect and n != ROLLING]


def prune(gh: Runner, repo: str, tag: str, names, keep: int, protect: set[str]) -> list[str]:
    """Delete old dated copies. Best effort: a failure is a warning, never a failed run."""
    deleted = []
    for name in prune_plan(names, keep, protect):
        res = gh(["release", "delete-asset", tag, name, "--repo", repo, "--yes"], API_TIMEOUT)
        if res.code == 0:
            deleted.append(name)
        else:
            _say("warning", f"could not prune {name} (exit {res.code}): {_tail(res.err)}; kept")
    return deleted


def read_baseline(workdir: Path) -> dict:
    """What the restore step recorded; missing or corrupt reads as no restore at all."""
    try:
        doc = json.loads((Path(workdir) / BASELINE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _utcnow() -> datetime:
    # The dated asset name is an ops artefact, not a ledger: wall clock is fine.
    return datetime.now(timezone.utc)


def persist(repo: str, tag: str, workdir: Path, *, dated: bool = False, keep: int = KEEP_DATED,
            gh: Runner = run_gh, sleep=time.sleep, now=_utcnow, pack=None,
            environ=None) -> dict:
    """Pack data/ and upload it, or raise Refused with the store untouched."""
    workdir = Path(workdir)
    environ = os.environ if environ is None else environ
    baseline = read_baseline(workdir)
    if baseline.get("outcome") not in (RESTORED, FRESH):
        raise Refused(f"restore outcome was {baseline.get('outcome') or 'unknown'}: the store "
                      f"is only written over a state that was read")
    archive = workdir / ROLLING
    (state_pack.pack if pack is None else pack)(archive)
    facts = inspect_archive(archive)
    allow = str(environ.get(ALLOW_SHRINK, "")).strip() == "1"
    refusals, warnings = persist_refusals(baseline, facts, allow_shrink=allow)
    for warning in warnings:
        _say("warning", warning)
    if refusals:
        raise Refused("; ".join(refusals))

    digest = sha256_file(archive)
    size = archive.stat().st_size
    sidecar = workdir / DIGEST
    sidecar.write_bytes(f"{digest}  {ROLLING}\n".encode("ascii"))
    if not baseline.get("release"):
        res = gh(["release", "create", tag, "--repo", repo, "--title", RELEASE_TITLE,
                  "--notes", RELEASE_NOTES], API_TIMEOUT)
        if res.code != 0 and lookup_release(gh, repo, tag, sleep, absent_ok=True) is None:
            raise Refused(f"could not create release {tag!r}: {_tail(res.err)}")
    copy = ""
    if dated:
        copy = f"state-{now().astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz"
        shutil.copyfile(archive, workdir / copy)
        _upload(gh, repo, tag, workdir / copy, sleep)
        _confirm(gh, repo, tag, sleep, {copy: (size, digest)})
    # Only now may the rolling asset be replaced: --clobber deletes it first.
    _upload(gh, repo, tag, archive, sleep)
    _upload(gh, repo, tag, sidecar, sleep)
    assets = _confirm(gh, repo, tag, sleep,
                      {ROLLING: (size, digest), DIGEST: (sidecar.stat().st_size, "")})
    pruned: list[str] = []
    if dated:
        try:
            pruned = prune(gh, repo, tag, assets, keep, {copy})
        except Exception as exc:  # pruning is housekeeping, never a failed persist
            _say("warning", f"prune skipped: {exc}")
    print(f"state: persisted {ROLLING} (sha256 {digest[:12]}, {facts['members']} members, "
          f"{facts['bytes']} bytes); registre {facts['seal_count']} seal(s)"
          + (f"; dated copy {copy}, pruned {len(pruned)}" if dated else ""), flush=True)
    return {"sha256": digest, "dated": copy, "pruned": pruned, "seal_count": facts["seal_count"],
            "members": facts["members"], "bytes": facts["bytes"]}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None, *, gh: Runner = run_gh, sleep=time.sleep,
         now=_utcnow, environ=None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Restore/persist the Vigie state, failing closed.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("restore", "persist"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repo", required=True)
        cmd.add_argument("--tag", default="state")
        cmd.add_argument("--dir", required=True, type=Path)
        if name == "persist":
            cmd.add_argument("--dated", action="store_true",
                             help="upload an immutable dated copy first (full refresh only)")
            cmd.add_argument("--keep", type=int, default=KEEP_DATED)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "persist":
        try:
            persist(args.repo, args.tag, args.dir, dated=args.dated, keep=args.keep,
                    gh=gh, sleep=sleep, now=now, environ=environ)
        except Exception as exc:  # fail closed: the store keeps its last good copy
            _say("error", f"state NOT persisted: {exc}")
            return 1
        return 0

    args.dir.mkdir(parents=True, exist_ok=True)
    # Written first, so a crash mid-restore can never leave a stale "restored".
    store_io.write_json_atomic(args.dir / BASELINE, {"outcome": FAILED, "reason": "restore did not finish"})
    try:
        baseline = restore(args.repo, args.tag, args.dir, gh=gh, sleep=sleep)
    except Exception as exc:  # fail closed on anything: the refresh must not run
        baseline = {"outcome": FAILED, "reason": str(exc)}
        _say("error", f"state restore FAILED, the refresh will not run: {exc}")
    store_io.write_json_atomic(args.dir / BASELINE, baseline)
    write_outputs(environ.get("GITHUB_OUTPUT"), baseline)
    return 0 if baseline["outcome"] in (RESTORED, FRESH) else 1


if __name__ == "__main__":
    raise SystemExit(main())
