"""Restore and persist the cross-edition state: fail closed, verified, versioned.

The state tarball (scripts/state_pack.py) is the only copy of Le Registre. The
sealed chain cannot be regenerated and a published seal is immutable, so the CI
steps that move the tarball are never allowed to guess:

restore   asks GitHub whether the `state` release exists. "fresh" is chosen only
          on a definitive answer: the repository is visible to the token AND the
          release is a 404, or the release carries no state asset of any kind.
          Every other failure (401/403, 429, 5xx, network, timeout, garbage) is
          retried with a bounded backoff, then FAILS the job before the refresh
          can collect into an empty data/ and seal a genesis. A fresh start is
          refused while the public git anchor (anchors/checkpoint.txt) names a
          seal. The rolling asset must match its listed size (and GitHub's own
          digest of the asset, when the API reports one) and one of the sha256
          lines recorded in state.tar.gz.sha256 (absent only for the one-time
          legacy archive or a persist killed while replacing the sidecar: then
          accepted loudly). Two witnesses then judge both chains of the
          registre, the edition seals and the hourly roadworks (travaux) seals:
          the git anchor (lines 2-3 and its `travaux` line; it may lag, since
          only the full refresh commits it) and the heads the last persist wrote
          into the sidecar (`# edition|travaux <seq> <root>`, written after its
          deploy, so the roads lane is witnessed too). A chain that forks a
          witness is refused; one that stops before a witnessed seal is
          "behind". A capped travaux chain that kept only later seals is ahead.
          When the rolling asset is missing, unverified, or behind (a persist
          that failed after the deploy), every vouched dated copy (GitHub's
          digest or a sidecar line) whose chains satisfy every witness
          qualifies; the one with the highest head seal (then the newest name)
          is restored with a warning, and this run's persist re-publishes it as
          the rolling asset. Otherwise the refusal lists every dated copy (and
          why it was passed over) with the manual recovery steps (AGENTS.md);
          restoring an older copy would mint already-published seq numbers with
          new roots, a fork of the public chain.
persist   never writes over a restore that did not succeed, and refuses an
          archive whose edition or travaux chain shrank or forked, that lost
          registre/registre.json, or whose member count or size collapsed below
          half of the restored one, regenerated caches excluded
          (VIGIE_STATE_ALLOW_SHRINK=1 overrides the size check only, and says so).
          With --dated it uploads an immutable dated copy FIRST and checks it on
          the release. Then the digest sidecar goes up BEFORE the archive: the
          new digest, the restored one, the earlier lines carried forward
          (bounded: the newest rolling digests and the dated copies' lines, so a
          dated copy stays vouched without GitHub's digest), then the new heads.
          `gh release upload --clobber` deletes before it uploads, and with this
          order a run killed at any point leaves a rolling asset that a recorded
          line vouches for (the old one, the new one), or none at all, which the
          restore answers from the dated copies, or refuses when they are behind
          what the sidecar witnessed. Dated copies beyond the newest --keep are
          pruned; a prune failure is a warning, never a failed run.
inspect   prints what a local archive holds and whether it carries the seals the
          git anchor (and, with --sidecar, the last persist) witnessed: the
          check of the manual recovery runbook.

Each restore or persist runs inside one time budget (DEADLINE): every gh call
gets at most what is left, so a hung transfer fails the step loudly instead of
being cancelled silently by the job timeout.

The outcome goes to $GITHUB_OUTPUT (outcome=restored|fresh|failed, source,
seal_count, head_root, sha256, members, bytes) and to <dir>/restore.json, the
baseline the persist step reads. Every gh call goes through an injectable
runner, so each failure mode is a unit test, never a production experiment.

Usage:
  python scripts/state_sync.py restore --repo OWNER/REPO --tag state --dir DIR
  python scripts/state_sync.py persist --repo OWNER/REPO --tag state --dir DIR [--dated] [--keep 12]
  python scripts/state_sync.py inspect FILE [--anchor anchors/checkpoint.txt] [--sidecar state.tar.gz.sha256]
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
import zlib
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
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REGISTRE_MEMBER = "data/registre/registre.json"
# Regenerated caches (re-hosted brief media, conditional-GET bodies): the next
# run downloads them again, so their size says nothing about the health of the
# memory. The chain checks are the real protection; the collapse rule watches
# the rest.
CACHE_PREFIXES = ("data/media/brief/", "data/raw/_bodies/")
BASELINE = "restore.json"
RESTORED, FRESH, FAILED = "restored", "fresh", "failed"
OUTPUT_KEYS = ("outcome", "source", "seal_count", "head_root", "sha256", "members", "bytes")
BEHIND, FORK = "behind", "fork"
EDITION, TRAVAUX = "edition", "travaux"           # the two chains of the registre
NOUN = {EDITION: "seal", TRAVAUX: "roadworks seal"}
ANCHORED = "the git anchor"
RECORDED = f"the last persist ({DIGEST})"
SEQ = re.compile(r"^[0-9]{1,9}$")
SIDECAR_ROLLING = 8      # rolling digests carried forward in the sidecar
SIDECAR_DATED = 24       # dated-copy lines carried forward (twice the default --keep)

ATTEMPTS = 3
BACKOFF = (5, 20)        # seconds between attempts: bounded, the next schedule retries anyway
API_TIMEOUT = 60
TRANSFER_TIMEOUT = 150   # a ~10 MB asset moves in seconds; 3 attempts + backoff fit in DEADLINE
DEADLINE = 480           # per restore/persist: two of them leave the 30-minute roads job room
KEEP_DATED = 12          # three days of six-hourly editions
MIN_FRACTION = 0.5       # a packed state below half of the restored one is a collapse
ALLOW_SHRINK = "VIGIE_STATE_ALLOW_SHRINK"
RELEASE_TITLE = "Vigie state"
RELEASE_NOTES = ("Rolling snapshot of the cross-edition state (private; publisher "
                 "content never enters the public repo).")
UNREADABLE = (tarfile.TarError, OSError, EOFError, zlib.error)


@dataclass(frozen=True)
class Result:
    code: int
    out: str = ""
    err: str = ""


Runner = Callable[[list, float], Result]


class Refused(RuntimeError):
    """The state store must not be trusted or written this run: the job fails."""


@dataclass(frozen=True)
class Witness:
    """A seal some record outside the archive says exists: (chain, seq, root, by whom)."""
    chain: str      # EDITION | TRAVAUX
    seq: int
    root: str
    source: str     # ANCHORED | RECORDED

    def __str__(self) -> str:
        return f"{NOUN[self.chain]} n° {self.seq} ({self.root[:12]})"


@dataclass(frozen=True)
class Verdict:
    """How an archive's chains answer the witnesses: kind "" (holds them), BEHIND or FORK."""
    kind: str = ""
    why: str = ""       # the refusal sentence
    note: str = ""      # the short reason a dated copy was passed over
    source: str = ""


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
    """One log entry; warnings and errors also become GitHub run annotations.

    A workflow command is one line: newlines are escaped (%0A) so a multi-line
    runbook stays one annotation and renders as lines.
    """
    if level in ("notice", "warning", "error"):
        data = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level}::state: {data}", flush=True)
    else:
        print(f"state: {message}", flush=True)


def _tail(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no message"


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _size(value: object) -> int | None:
    """A byte count from an API document (int, integral float or digits), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,15}", value.strip()):
        return int(value.strip())
    return None


class Deadline:
    """One time budget for a whole restore or persist.

    Every gh call gets at most what is left, and a backoff that would outlive
    the budget is not slept: the step fails with an ::error:: (and the ops
    alert) instead of being cancelled silently by the job's timeout-minutes.
    """

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.seconds = seconds
        self.clock = clock
        self.end = clock() + seconds

    def left(self) -> float:
        return self.end - self.clock()

    def spent(self, what: str) -> Refused:
        return Refused(f"{what}: the {self.seconds:g} s budget of this state step is spent; "
                       f"failing now rather than being cancelled silently by the job timeout")

    def bound(self, gh: Runner) -> Runner:
        def call(args: list, timeout: float) -> Result:
            left = self.left()
            if left < 1:
                raise self.spent("gh " + " ".join(str(a) for a in args[:2]))
            return gh(args, min(timeout, left))
        return call

    def pace(self, sleep: Callable[[float], None]) -> Callable[[float], None]:
        def wait(seconds: float) -> None:
            if seconds >= self.left():
                raise self.spent(f"a {seconds:g} s backoff")
            sleep(seconds)
        return wait


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
            try:
                sleep(BACKOFF[min(n - 1, len(BACKOFF) - 1)])
            except Refused as exc:  # the step's budget is spent: say what was failing
                raise Refused(f"{what}: {reason}; {exc}") from None
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


def _dated(assets) -> list[str]:
    """Dated copies, newest first (the name is a UTC stamp, so text order is time order)."""
    return sorted((n for n in assets if isinstance(n, str) and DATED.match(n)), reverse=True)


def _server_digest(asset: dict) -> str:
    """The sha256 GitHub computed for an uploaded asset, or "" when it lists none."""
    value = asset.get("digest")
    if isinstance(value, str) and value.lower().startswith("sha256:"):
        hexpart = value[len("sha256:"):].strip().lower()
        if HEX64.match(hexpart):
            return hexpart
    return ""


# --------------------------------------------------------------------------- #
# The archive and its chain
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def recorded_lines(text: str) -> list[tuple[str, str]]:
    """(digest, asset name) of every sidecar line, in order (newest first), once each.

    The name is the dated copy a line was written for, else the rolling asset
    (a `sha256sum` line, or one naming anything else).
    """
    found: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        tokens = line.split()
        if not tokens or not HEX64.match(tokens[0].lower()):
            continue
        name = tokens[1].lstrip("*") if len(tokens) > 1 else ROLLING
        pair = (tokens[0].lower(), name if DATED.match(name) else ROLLING)
        if pair not in found:
            found.append(pair)
    return found


def recorded_digests(text: str) -> list[str]:
    """The sha256 digests a sidecar vouches for, in its order (newest first), once each."""
    found: list[str] = []
    for digest, _ in recorded_lines(text):
        if digest not in found:
            found.append(digest)
    return found


def recorded_witnesses(text: str) -> list[Witness]:
    """The chain heads the last persist recorded (`# edition|travaux <seq> <root>`).

    A sidecar written by hand (`sha256sum`) has none. A witness line that does
    not parse is refused: the sidecar is the only record of a roads-lane seal.
    """
    found: dict[str, Witness] = {}
    for line in (text or "").splitlines():
        tokens = line[1:].split() if line.startswith("#") else []
        if not tokens or tokens[0] not in NOUN:
            continue
        ok = (len(tokens) >= 3 and SEQ.match(tokens[1]) and int(tokens[1]) > 0
              and HEX64.match(tokens[2].lower()) and tokens[0] not in found)
        if not ok:
            raise Refused(f"{DIGEST} carries an unreadable witness line {line.strip()[:120]!r}")
        found[tokens[0]] = Witness(tokens[0], int(tokens[1]), tokens[2].lower(), RECORDED)
    return [found[c] for c in (EDITION, TRAVAUX) if c in found]


def chain(doc: object) -> list[tuple[int, str]]:
    """(seq, root) of every well-formed seal of one chain document, in chain order."""
    seals = doc.get("seals") if isinstance(doc, dict) else None
    if not isinstance(seals, list):
        return []
    return [
        (s["seq"], s["root"]) for s in seals
        if isinstance(s, dict) and _int(s.get("seq")) and isinstance(s.get("root"), str)
    ]


def inspect_archive(path: Path) -> dict:
    """Members, bytes and both registre chains of a packed state, without extracting it."""
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
    durable = [m for m in files if not m.name.startswith(CACHE_PREFIXES)]
    seals = chain(doc)
    travaux = chain(doc.get("travaux")) if isinstance(doc, dict) else []
    head = seals[-1] if seals else (0, "")
    road = travaux[-1] if travaux else (0, "")
    return {
        "members": len(files),
        "bytes": sum(m.size for m in files),
        "durable_members": len(durable),
        "durable_bytes": sum(m.size for m in durable),
        "registre": registre is not None,
        "seals": seals,
        "seal_count": len(seals),
        "head_seq": head[0],
        "head_root": head[1],
        "travaux": travaux,
        "travaux_seq": road[0],
        "travaux_root": road[1],
    }


def heads(facts: dict, source: str = RECORDED) -> list[Witness]:
    """The head seal of each non-empty chain of an archive, as witnesses."""
    return [Witness(c, facts[f"{p}_seq"], facts[f"{p}_root"], source)
            for c, p in ((EDITION, "head"), (TRAVAUX, "travaux")) if facts[f"{p}_seq"]]


def read_anchor(path: Path) -> list[Witness]:
    """The seals the public git anchor witnessed: its edition head, its travaux head.

    An absent anchor witnessed nothing (a repository before its first anchor
    commit), as does a genesis checkpoint (seal 0). An anchor that is present but
    unreadable or malformed is refused: it would otherwise silently stop
    witnessing, which is exactly the protection a fork needs.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise Refused(f"anchor {path} is present but unreadable ({exc}); restore it from "
                      f"git history, never delete it") from None

    def bad(why: str) -> Refused:
        return Refused(f"anchor {path.name} is present but malformed ({why}); refusing to "
                       f"restore without its witness. Restore anchors/checkpoint.txt from git "
                       f"history (git log -- anchors/checkpoint.txt), never delete it")

    lines = text.splitlines()
    if len(lines) < 2 or not SEQ.match(lines[1].strip()):
        raise bad("line 2 is not a seal number")
    found: list[Witness] = []
    seq = int(lines[1].strip())
    if seq:
        root = lines[2].strip().lower() if len(lines) > 2 else ""
        if not HEX64.match(root):
            raise bad("line 3 is not the sha256 root of seal n° %d" % seq)
        found.append(Witness(EDITION, seq, root, ANCHORED))
    roads = [line.split() for line in lines[3:] if line.split()[:1] == [TRAVAUX]]
    if len(roads) > 1:
        raise bad("more than one travaux line")
    if roads:
        tokens = roads[0]
        if not (len(tokens) >= 3 and SEQ.match(tokens[1]) and int(tokens[1]) > 0
                and HEX64.match(tokens[2].lower())):
            raise bad("the travaux line is not 'travaux <seq> <root> <time>'")
        found.append(Witness(TRAVAUX, int(tokens[1]), tokens[2].lower(), ANCHORED))
    return found


def witness(facts: dict, w: Witness, label: str = "the restored chain") -> Verdict:
    """How one chain of an archive answers one witness.

    Lagging the archive is fine (the witness names an older seal of the same
    chain). A travaux chain is capped (registre.ROADS_SEAL_CAP), so a witnessed
    seq older than every kept seal means the archive is ahead, not behind.
    """
    seals = facts["seals"] if w.chain == EDITION else facts["travaux"]
    noun = NOUN[w.chain]
    have = dict(seals)
    if w.seq not in have:
        if seals and w.seq < seals[0][0]:
            return Verdict()
        last = seals[-1][0] if seals else 0
        later = "what was published" if w.source == ANCHORED else "what the last persist recorded"
        return Verdict(BEHIND, f"{label} stops at {noun} n° {last} but {w.source} witnessed "
                               f"n° {w.seq}: this state is older than {later}",
                       f"its chain stops at {noun} n° {last} ({w.source} witnessed n° {w.seq})",
                       w.source)
    if have[w.seq] != w.root:
        return Verdict(FORK, f"{noun} n° {w.seq} is {have[w.seq][:12]} in {label} but "
                             f"{w.root[:12]} in {w.source}: a forked chain",
                       f"{noun} n° {w.seq} differs from {w.source} (a fork)", w.source)
    return Verdict()


def judge(facts: dict, witnesses: list[Witness], label: str = "the restored chain") -> Verdict:
    """The worst answer of an archive to every witness: a fork, else behind, else ""."""
    verdicts = [witness(facts, w, label) for w in witnesses]
    return (next((v for v in verdicts if v.kind == FORK), None)
            or next((v for v in verdicts if v.kind), Verdict()))


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #
def _download(gh: Runner, repo: str, tag: str, name: str, workdir: Path, asset: dict, sleep) -> Path:
    """One listed asset: its listed size, and GitHub's digest of it when listed."""
    target = workdir / name
    size = _size(asset.get("size"))
    if size is None:
        raise Refused(f"the release lists no usable size for {name} ({asset.get('size')!r})")
    server = _server_digest(asset)

    def attempt():
        target.unlink(missing_ok=True)
        res = gh(["release", "download", tag, "--repo", repo, "--pattern", name,
                  "--dir", str(workdir), "--clobber"], TRANSFER_TIMEOUT)
        if res.code != 0:
            return False, None, f"exit {res.code}: {_tail(res.err)}"
        got = target.stat().st_size if target.is_file() else -1
        if got != size:
            return False, None, f"got {got} bytes, the release lists {size}"
        if server and sha256_file(target) != server:
            return False, None, f"the bytes received are not GitHub's sha256:{server[:12]}"
        return True, target, ""
    return retrying(f"download {name}", attempt, sleep)


def _wanted(witnesses: list[Witness]) -> str:
    """The seals a usable copy must hold, for the runbook (each chain's highest witness)."""
    best: dict[str, Witness] = {}
    for w in witnesses:
        if w.chain not in best or w.seq > best[w.chain].seq:
            best[w.chain] = w
    return " and ".join(str(best[c]) for c in (EDITION, TRAVAUX) if c in best) or "the longest chain"


def recovery(repo: str, tag: str, assets: dict[str, dict], notes: dict[str, str],
             witnesses: list[Witness]) -> str:
    """What the release holds and the manual recovery steps, for a content refusal."""
    dated = _dated(assets)
    rows = [f"  - {name} ({_size(assets[name].get('size'))} bytes)"
            + (f": {notes[name]}" if name in notes else "") for name in dated]
    others = ", ".join(sorted(n for n in assets if not DATED.match(n))) or "nothing else"
    want = _wanted(witnesses)
    sidecar = DIGEST in assets
    return "\n".join([
        "",
        f"Dated copies on release {tag!r} of {repo}, newest first ({len(dated)}):",
        *(rows or ["  (none)"]),
        f"Also on the release: {others}.",
        "Manual recovery (AGENTS.md, 'When the state restore refuses'):",
        f"  1. gh release download {tag} --repo {repo} --pattern 'state-*.tar.gz'"
        + (f" --pattern '{DIGEST}'" if sidecar else "") + " --dir recovery",
        f"  2. python3 -X utf8 scripts/state_sync.py inspect recovery/<copy>"
        + (f" --sidecar recovery/{DIGEST}" if sidecar else "")
        + f"  (highest seal first, until one holds {want})",
        f"  3. cp recovery/<copy> {ROLLING} && sha256sum {ROLLING} > {DIGEST}",
        f"  4. gh release upload {tag} {DIGEST} --repo {repo} --clobber, and only then "
        f"gh release upload {tag} {ROLLING} --repo {repo} --clobber  (the digest must be on "
        f"the release before the archive)",
        "  5. re-run the workflow.",
        f"  No copy holds {want}? Never start fresh, never move the anchor back and never "
        f"upload an older copy: the missing seals are public in "
        f"https://vigieqc.com/registre/chain.json and /registre/travaux.json (AGENTS.md, last resort).",
    ])


def _dated_fallback(gh: Runner, repo: str, tag: str, workdir: Path, assets: dict[str, dict],
                    recorded: list[str], rejected: set[str], witnesses: list[Witness], sleep,
                    notes: dict[str, str]):
    """(name, path, sha256, facts) of the best vouched dated copy that every witness accepts.

    A copy is vouched for by GitHub's digest of it when the API lists one (the
    download is checked against it), otherwise by a line of the sidecar. Every
    candidate is inspected: among the qualifying ones the highest head seal wins
    (edition, then travaux), then the newest name, so a clock-skewed name cannot
    pick a shorter chain. Each passed-over copy gets a note for the refusal;
    None when none qualifies.
    """
    qualifying: list[tuple[tuple, str, Path, str, dict]] = []
    for name in _dated(assets):
        asset = assets[name]
        server = _server_digest(asset)
        if server and server in rejected:
            notes[name] = "the same bytes as the refused rolling asset"
            continue
        if not server and not recorded:
            notes[name] = f"no digest vouches for it (GitHub lists none, no {DIGEST} line)"
            continue
        try:
            path = _download(gh, repo, tag, name, workdir, asset, sleep)
        except Refused as exc:
            notes[name] = f"not downloaded: {exc}"
            continue
        digest = sha256_file(path)
        verdict = ""
        if digest in rejected:
            verdict = "the same bytes as the refused rolling asset"
        elif not server and digest not in recorded:
            verdict = f"sha256 {digest[:12]} is in neither GitHub's digest nor {DIGEST}"
        else:
            try:
                facts = inspect_archive(path)
            except UNREADABLE as exc:
                verdict = f"unreadable archive ({exc})"
            else:
                if not facts["registre"]:
                    verdict = f"no {REGISTRE_MEMBER}"
                else:
                    verdict = judge(facts, witnesses).note
                    if not verdict:
                        rank = (facts["head_seq"], facts["travaux_seq"], name)
                        qualifying.append((rank, name, path, digest, facts))
                        continue
        notes[name] = verdict
        path.unlink(missing_ok=True)
    if not qualifying:
        return None
    qualifying.sort(key=lambda row: row[0], reverse=True)
    for _, _, other, _, _ in qualifying[1:]:
        other.unlink(missing_ok=True)
    _, name, path, digest, facts = qualifying[0]
    return name, path, digest, facts


def _restored(archive: Path, source: str, digest: str, facts: dict, unpack,
              lines: list[tuple[str, str]]) -> dict:
    count = (state_pack.unpack if unpack is None else unpack)(archive)
    print(f"state: restored {count} file(s) from {source} (sha256 {digest[:12]}, "
          f"{facts['members']} members, {facts['bytes']} bytes); registre "
          f"{facts['seal_count']} seal(s), head {facts['head_root'][:12] or '-'}; travaux "
          f"head n° {facts['travaux_seq']} {facts['travaux_root'][:12] or '-'}", flush=True)
    return {"outcome": RESTORED, "source": source, "release": True,
            "seal_count": facts["seal_count"], "head_seq": facts["head_seq"],
            "head_root": facts["head_root"], "travaux_seq": facts["travaux_seq"],
            "travaux_root": facts["travaux_root"], "sha256": digest,
            "members": facts["members"], "bytes": facts["bytes"],
            "durable_members": facts["durable_members"], "durable_bytes": facts["durable_bytes"],
            "recorded": [list(pair) for pair in lines]}


def restore(repo: str, tag: str, workdir: Path, *, gh: Runner = run_gh, sleep=time.sleep,
            unpack=None, anchor: Path | None = None, deadline: float = DEADLINE,
            clock: Callable[[], float] = time.monotonic) -> dict:
    """Restore the state into data/, or raise Refused. Returns the baseline."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    budget = Deadline(deadline, clock)
    gh, sleep = budget.bound(gh), budget.pace(sleep)
    anchored = read_anchor(ANCHOR if anchor is None else anchor)
    release = find_release(gh, repo, tag, sleep)
    assets = _assets(release)
    notes: dict[str, str] = {}
    if not (ROLLING in assets or DIGEST in assets or _dated(assets)):
        if anchored:
            raise Refused(f"no state in {repo}, but the git anchor witnessed "
                          f"{_wanted(anchored)}: a fresh start would fork the published chain"
                          + recovery(repo, tag, assets, notes, anchored))
        missing = f"release {tag!r}" if release is None else f"{ROLLING} in release {tag!r}"
        _say("warning", f"FRESH START: {repo} has no {missing}; collecting into an empty "
                        f"memory, the registre begins at seal n° 1")
        return {"outcome": FRESH, "source": "", "release": release is not None, "seal_count": 0,
                "head_seq": 0, "head_root": "", "travaux_seq": 0, "travaux_root": "",
                "sha256": "", "members": 0, "bytes": 0,
                "durable_members": 0, "durable_bytes": 0, "recorded": []}

    recorded: list[str] | None = None
    lines: list[tuple[str, str]] = []
    witnessed = list(anchored)
    if DIGEST in assets:
        sidecar = _download(gh, repo, tag, DIGEST, workdir, assets[DIGEST], sleep)
        text = sidecar.read_text(encoding="utf-8", errors="replace")
        lines = recorded_lines(text)
        recorded = recorded_digests(text)
        try:
            witnessed += recorded_witnesses(text)
        except Refused as exc:
            raise Refused(str(exc) + recovery(repo, tag, assets, notes, witnessed)) from None
    rejected: set[str] = set()
    if ROLLING not in assets:
        traces = sorted(n for n in assets if n == DIGEST or DATED.match(n))
        problem = (f"release {tag!r} holds {', '.join(traces)} but no {ROLLING}: an "
                   f"interrupted persist, not a first run")
        cause = "rolling asset missing"
    else:
        archive = _download(gh, repo, tag, ROLLING, workdir, assets[ROLLING], sleep)
        digest = sha256_file(archive)
        problem = cause = ""
        if recorded is None:
            _say("warning", f"{ROLLING} has no recorded digest ({DIGEST}): the one-time legacy "
                            f"archive, or a persist killed while replacing the digest; accepted "
                            f"unverified (sha256 {digest[:12]}) and still checked against the anchor")
        elif digest in recorded:
            line = recorded.index(digest)
            print(f"state: {ROLLING} sha256 {digest[:12]} matches "
                  + ("the digest recorded by the last persist" if line == 0 else
                     f"line {line + 1} of {DIGEST}, the previous state: the last persist stopped "
                     f"between its digest and its archive"), flush=True)
        else:
            rejected.add(digest)
            problem = (f"{ROLLING} is sha256 {digest[:12]} but {DIGEST} recorded "
                       f"{', '.join(d[:12] for d in recorded) or 'nothing'}: refusing an "
                       f"unverified state")
            cause = "rolling asset unverified"
        if not problem:
            facts = inspect_archive(archive)
            if not facts["registre"]:
                raise Refused(f"the restored state carries no {REGISTRE_MEMBER}"
                              + recovery(repo, tag, assets, notes, witnessed))
            verdict = judge(facts, witnessed)
            if verdict.kind == FORK:
                raise Refused(verdict.why + recovery(repo, tag, assets, notes, witnessed))
            if not verdict.kind:
                return _restored(archive, ROLLING, digest, facts, unpack, lines)
            rejected.add(digest)
            problem = verdict.why
            cause = ("rolling asset behind the anchor" if verdict.source == ANCHORED
                     else "rolling asset behind the last persist's record")

    found = _dated_fallback(gh, repo, tag, workdir, assets, recorded or [], rejected,
                            witnessed, sleep, notes)
    if found is None:
        raise Refused(problem + recovery(repo, tag, assets, notes, witnessed))
    name, path, digest, facts = found
    _say("warning", f"{cause}; restored from {name} (sha256 {digest[:12]}, registre "
                    f"{facts['seal_count']} seal(s), head n° {facts['head_seq']}, travaux head "
                    f"n° {facts['travaux_seq']}): {problem}. "
                    f"This run's persist re-publishes it as {ROLLING}")
    return _restored(path, name, digest, facts, unpack, lines)


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
    road_seq, road_root = _int(baseline.get("travaux_seq")), str(baseline.get("travaux_root") or "")
    if road_seq and road_root:
        verdict = witness(facts, Witness(TRAVAUX, road_seq, road_root, "the restored state"))
        if verdict.kind:
            refusals.append(f"the restored roadworks seal n° {road_seq} ({road_root[:12]}) is not "
                            f"in the packed travaux chain: refusing a "
                            f"{'fork' if verdict.kind == FORK else 'shrink'}")
    for key, label in (("durable_members", "member count"), ("durable_bytes", "total size")):
        was, now = _int(baseline.get(key)), facts[key]
        if was and now < was * MIN_FRACTION:
            message = (f"{label} (regenerated caches excluded) fell from {was} to {now}, below "
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
        if _size(asset.get("size")) != size or asset.get("state", "uploaded") != "uploaded":
            raise Refused(f"upload of {name} not confirmed: the release lists "
                          f"{asset.get('size', 'nothing')} bytes, we sent {size}")
        server = _server_digest(asset)
        if digest and server and server != digest:
            raise Refused(f"upload of {name} not confirmed: GitHub computed sha256:{server[:12]}, "
                          f"we sent sha256:{digest[:12]}")
    return assets


def prune_plan(names, keep: int, protect: set[str]) -> list[str]:
    """Dated copies beyond the newest `keep`; never the rolling asset or a protected name."""
    dated = _dated(names)
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


def sidecar_text(digest: str, previous: object, *, dated: str = "", restored_from: str = "",
                 carried=(), witnesses: list[Witness] = ()) -> str:
    """The digest sidecar: what the rolling asset may be, newest first, then the new heads.

    Line 1 is the new archive (and, with --dated, the same digest under its
    dated name). The restored digest follows: it keeps the rolling asset still
    on the release verifiable if this run dies between the sidecar upload and
    the archive upload. The earlier lines are carried forward, bounded (the
    newest SIDECAR_ROLLING rolling digests, the newest SIDECAR_DATED dated-copy
    lines), so the dated copies stay vouched for without GitHub's own digest.
    The `# edition|travaux <seq> <root>` lines record the heads of the new
    archive: written after the deploy, they witness the roads lane's seals,
    which the git anchor (full refresh only) never sees.
    """
    pairs: list[tuple[str, str]] = [(digest, ROLLING)]
    if dated:
        pairs.append((digest, dated))
    prev = str(previous or "").strip().lower()
    if HEX64.match(prev):
        pairs.append((prev, restored_from if DATED.match(restored_from or "") else ROLLING))
    for pair in carried if isinstance(carried, (list, tuple)) else ():
        if (isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[0], str)
                and HEX64.match(pair[0].lower()) and isinstance(pair[1], str)):
            pairs.append((pair[0].lower(), pair[1] if DATED.match(pair[1]) else ROLLING))
    kept: list[tuple[str, str]] = []
    room = {True: SIDECAR_DATED, False: SIDECAR_ROLLING}
    for pair in pairs:
        is_dated = bool(DATED.match(pair[1]))
        if pair in kept or not room[is_dated]:
            continue
        room[is_dated] -= 1
        kept.append(pair)
    return ("".join(f"{d}  {name}\n" for d, name in kept)
            + "".join(f"# {w.chain} {w.seq} {w.root}\n" for w in witnesses))


def _utcnow() -> datetime:
    # The dated asset name is an ops artefact, not a ledger: wall clock is fine.
    return datetime.now(timezone.utc)


def persist(repo: str, tag: str, workdir: Path, *, dated: bool = False, keep: int = KEEP_DATED,
            gh: Runner = run_gh, sleep=time.sleep, now=_utcnow, pack=None,
            environ=None, deadline: float = DEADLINE,
            clock: Callable[[], float] = time.monotonic) -> dict:
    """Pack data/ and upload it, or raise Refused with the store untouched."""
    workdir = Path(workdir)
    environ = os.environ if environ is None else environ
    baseline = read_baseline(workdir)
    if baseline.get("outcome") not in (RESTORED, FRESH):
        raise Refused(f"restore outcome was {baseline.get('outcome') or 'unknown'}: the store "
                      f"is only written over a state that was read")
    # The dated copy a fallback restore left in the workdir is spent once this
    # persist has packed data/ (successful or not); never carry it to a later step.
    source = str(baseline.get("source") or "")
    try:
        return _persist(repo, tag, workdir, baseline, dated=dated, keep=keep, gh=gh,
                        sleep=sleep, now=now, pack=pack, environ=environ,
                        deadline=deadline, clock=clock)
    finally:
        if DATED.match(source):
            (workdir / source).unlink(missing_ok=True)


def _persist(repo: str, tag: str, workdir: Path, baseline: dict, *, dated: bool, keep: int,
             gh: Runner, sleep, now, pack, environ, deadline: float,
             clock: Callable[[], float]) -> dict:
    budget = Deadline(deadline, clock)
    gh, sleep = budget.bound(gh), budget.pace(sleep)
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
    copy = f"state-{now().astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz" if dated else ""
    sidecar = workdir / DIGEST
    sidecar.write_bytes(sidecar_text(
        digest, baseline.get("sha256"), dated=copy, restored_from=str(baseline.get("source") or ""),
        carried=baseline.get("recorded") or (), witnesses=heads(facts)).encode("ascii"))
    if not baseline.get("release"):
        res = gh(["release", "create", tag, "--repo", repo, "--title", RELEASE_TITLE,
                  "--notes", RELEASE_NOTES], API_TIMEOUT)
        if res.code != 0 and lookup_release(gh, repo, tag, sleep, absent_ok=True) is None:
            raise Refused(f"could not create release {tag!r}: {_tail(res.err)}")
    if dated:
        shutil.copyfile(archive, workdir / copy)
        try:
            _upload(gh, repo, tag, workdir / copy, sleep)
            _confirm(gh, repo, tag, sleep, {copy: (size, digest)})
        finally:
            (workdir / copy).unlink(missing_ok=True)
    # Only now may the rolling pair be replaced (--clobber deletes, then uploads).
    # The digest goes first and still names the restored archive: a run killed
    # before the archive upload leaves the old archive vouched for by line 2,
    # after it the new one by line 1, in between no archive (the restore then
    # takes a dated copy that a line or GitHub's digest vouches for, provided it
    # holds the heads recorded here; the roads lane has none newer, so it halts).
    _upload(gh, repo, tag, sidecar, sleep)
    _upload(gh, repo, tag, archive, sleep)
    assets = _confirm(gh, repo, tag, sleep, {ROLLING: (size, digest),
                                             DIGEST: (sidecar.stat().st_size, sha256_file(sidecar))})
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
# Inspect (the manual runbook's check)
# --------------------------------------------------------------------------- #
def inspect_local(path: Path, anchor: Path, sidecar: Path | None = None) -> int:
    """Print what a local state archive holds; 0 only when every witness accepts it.

    The witnesses are the git anchor and, with --sidecar, the heads the last
    persist recorded in the release's state.tar.gz.sha256 (the roads lane's).
    """
    path = Path(path)
    try:
        facts = inspect_archive(path)
        digest = sha256_file(path)
    except UNREADABLE as exc:
        _say("error", f"{path.name}: unreadable state archive ({exc})")
        return 1
    print(f"state: {path.name}: sha256 {digest}, {facts['members']} members, {facts['bytes']} "
          f"bytes; registre {facts['seal_count']} seal(s), head n° {facts['head_seq']} "
          f"{facts['head_root'][:12] or '-'}; travaux head n° {facts['travaux_seq']} "
          f"{facts['travaux_root'][:12] or '-'}", flush=True)
    if not facts["registre"]:
        _say("error", f"{path.name} carries no {REGISTRE_MEMBER}")
        return 1
    try:
        witnessed = read_anchor(anchor)
        if sidecar is not None:
            witnessed += recorded_witnesses(Path(sidecar).read_text(encoding="utf-8"))
    except (Refused, OSError, UnicodeError) as exc:
        _say("error", str(exc))
        return 1
    if not witnessed:
        print("state: no witness names a seal; nothing to compare", flush=True)
        return 0
    verdict = judge(facts, witnessed, label=f"the chain in {path.name}")
    if verdict.kind:
        _say("error", verdict.why)
        return 1
    print(f"state: {path.name} holds {_wanted(witnessed)}, as "
          + " and ".join(sorted({w.source for w in witnessed})) + " witnessed", flush=True)
    return 0


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
    check = sub.add_parser("inspect", help="what a local state archive holds, against the git anchor")
    check.add_argument("file", type=Path)
    check.add_argument("--anchor", type=Path, default=None)
    check.add_argument("--sidecar", type=Path, default=None,
                       help=f"the release's {DIGEST}: also check the heads the last persist recorded")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "inspect":
        return inspect_local(args.file, ANCHOR if args.anchor is None else args.anchor, args.sidecar)

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
