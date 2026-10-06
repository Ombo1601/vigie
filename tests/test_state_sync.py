"""State restore/persist (state_sync) — the only copy of Le Registre, moved fail closed.

House law under test: only a definitive "no release" is a fresh start; every
other failure is retried, then fails the job before a genesis can be sealed; a
download must match a recorded digest and the public git anchor; a rolling
asset that is missing, unverified or behind the anchor is answered from the
newest vouched dated copy that holds the anchored seal, or refused with the
runbook; both registre chains (editions and roadworks) answer the git anchor
and the heads the last persist recorded in the sidecar, so no restore rolls the
roads lane's published seals back; a malformed present anchor fails closed;
the chain never shrinks or forks on upload; a dated copy exists before
the rolling asset is clobbered; the digest sidecar goes up first, names the
previous archive and carries earlier lines forward, so every interleaving of a
killed persist stays restorable (or halts loudly, never rolls back);
pruning never touches the rolling asset and never fails a run; a hung transfer
fails inside the step's budget. Hermetic: `gh` is a scripted fake, nothing
leaves the process.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import registre
import state_pack
import state_sync
from state_sync import DIGEST, REGISTRE_MEMBER, ROLLING, Refused, Result, Witness

REPO, TAG = "owner/vigie-state", "state"
NOW = datetime(2026, 10, 20, 6, 17, 0, tzinfo=timezone.utc)
NEW_DATED = "state-20261020T061700Z.tar.gz"
OLD_DATED = "state-20261019T061700Z.tar.gz"
OLDER_DATED = "state-20261018T061700Z.tar.gz"


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sidecar(*bodies: bytes) -> bytes:
    return "".join(f"{sha(b)}  {ROLLING}\n" for b in bodies).encode()


def travaux_seals(n: int, *, fork: bool = False, first: int = 1) -> list[dict]:
    """A roadworks chain as registre.py keeps it (capped: it may start after n° 1)."""
    seals, prev = [], ""
    for seq in range(1, n + 1):
        root = hashlib.sha256(f"{'tfork' if fork else 'trav'}|{seq}|{prev}".encode()).hexdigest()
        if seq >= first:
            seals.append({"seq": seq, "fetched_at": f"2026-10-05T{seq % 24:02d}:00:00+00:00",
                          "prev": prev, "leaf": "y", "root": root, "signal": "s", "active_count": 3})
        prev = root
    return seals


def chain_doc(n: int, *, fork: bool = False, travaux: int = 0, travaux_fork: bool = False,
              travaux_first: int = 1) -> dict:
    seals, prev = [], ""
    for seq in range(1, n + 1):
        root = hashlib.sha256(f"{'fork' if fork else 'main'}|{seq}|{prev}".encode()).hexdigest()
        seals.append({"seq": seq, "edition": f"e{seq}", "prev": prev, "leaf": "x",
                      "root": root, "record": {"edition": f"e{seq}"}})
        prev = root
    return {"method": "registre-v1 sha256-chain", "seals": seals,
            "travaux": {"method": "registre-travaux-v1 sha256-chain", "latest_record": None,
                        "seals": travaux_seals(travaux, fork=travaux_fork, first=travaux_first)}}


def head_root(n: int) -> str:
    return chain_doc(n)["seals"][-1]["root"]


def travaux_root(n: int) -> str:
    return travaux_seals(n)[-1]["root"]


def write_archive(path: Path, *, seals: int = 57, files: int = 8, filler: int = 1000,
                  registre: bool = True, fork: bool = False, caches: int = 0,
                  travaux: int = 0, travaux_fork: bool = False, travaux_first: int = 1) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        def add(name: str, payload: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        if registre:
            add(REGISTRE_MEMBER, json.dumps(chain_doc(
                seals, fork=fork, travaux=travaux, travaux_fork=travaux_fork,
                travaux_first=travaux_first)).encode("utf-8"))
        for i in range(files):
            add(f"data/raw/src{i}/snap.xml", bytes([65 + i % 26]) * filler)
        for i in range(caches):
            add(f"data/media/brief/{i:040d}.jpg", b"j" * filler)
            add(f"data/raw/_bodies/{i:040d}.body", b"b" * filler)
    return path


def http_ok(doc: dict) -> Result:
    return Result(0, "HTTP/2.0 200 OK\nContent-Type: application/json\r\n"
                     "X-Github-Request-Id: AB:CD\r\n\r\n" + json.dumps(doc), "")


def http_error(status: int, text: str) -> Result:
    return Result(1, f"HTTP/2.0 {status} {text}\nContent-Type: application/json\r\n\r\n"
                     + json.dumps({"message": text}), f"gh: {text} (HTTP {status})\n")


TRANSIENT = {
    "401": http_error(401, "Bad credentials"),
    "403": http_error(403, "Resource not accessible by personal access token"),
    "429": http_error(429, "Too Many Requests"),
    "500": http_error(500, "Internal Server Error"),
    "timeout": Result(124, "", "timed out after 60s"),
    "network": Result(1, "", "error connecting to api.github.com"),
    "garbage": Result(0, "HTTP/2.0 200 OK\r\n\r\n<html>upstream error</html>", ""),
}


class Crash(BaseException):
    """The runner dies mid-persist. Not an Exception, so nothing can swallow it."""


class FakeGh:
    """A scripted `gh` against one in-memory release. Records every call.

    Uploads follow `gh release upload --clobber`: an existing asset is deleted
    first, then the new one is stored (two mutations). `crash_after` kills the
    runner before mutation n+1, so every interleaving of a persist is reachable.
    """

    def __init__(self, *, release: bool = True, assets: dict[str, bytes] | None = None,
                 server_digest: dict[str, str] | None = None, github_digests: bool = False,
                 clock: list[float] | None = None) -> None:
        self.release = release
        self.assets: dict[str, bytes] = dict(assets or {})
        self.server_digest: dict[str, str] = dict(server_digest or {})
        self.github_digests = github_digests     # GitHub computes sha256 on upload
        self.listed_size: dict[str, object] = {}  # override the size the API lists
        self.short_upload: set[str] = set()     # store these truncated (a bad upload)
        self.short_download: set[str] = set()   # deliver these truncated
        self.hang: set[str] = set()             # these calls run until their timeout
        self.clock = clock if clock is not None else [0.0]
        self.queued: dict[str, list[Result]] = {}
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.crash_after: int | None = None
        self.mutations = 0

    def vouch_all(self) -> "FakeGh":
        """GitHub lists its own digest for every asset already on the release."""
        for name, body in self.assets.items():
            self.server_digest[name] = "sha256:" + sha(body)
        return self

    def script(self, key: str, *results: Result) -> None:
        self.queued.setdefault(key, []).extend(results)

    @staticmethod
    def key(args: list[str]) -> str:
        if args[0] == "api":
            return "api:release" if "/releases/" in args[-1] else "api:repo"
        sub = args[1]
        if sub == "download":
            return f"download:{args[args.index('--pattern') + 1]}"
        if sub == "upload":
            return f"upload:{Path(args[3]).name}"
        if sub == "delete-asset":
            return f"delete:{args[3]}"
        return sub

    def keys(self) -> list[str]:
        return [self.key(c) for c in self.calls]

    def mutate(self) -> None:
        if self.crash_after is not None and self.mutations >= self.crash_after:
            raise Crash(f"runner killed after {self.mutations} mutation(s)")
        self.mutations += 1

    def drop(self, name: str) -> None:
        self.assets.pop(name, None)
        self.server_digest.pop(name, None)

    def __call__(self, args: list[str], timeout: float) -> Result:
        self.calls.append(list(args))
        self.timeouts.append(timeout)
        key = self.key(args)
        if key in self.hang:
            self.clock[0] += timeout
            return Result(124, "", f"timed out after {timeout:g}s")
        if self.queued.get(key):
            return self.queued[key].pop(0)
        if key == "api:repo":
            return http_ok({"full_name": REPO, "private": True})
        if key == "api:release":
            if not self.release:
                return http_error(404, "Not Found")
            return http_ok({"tag_name": TAG, "assets": [
                {"name": name, "size": self.listed_size.get(name, len(body)), "state": "uploaded",
                 **({"digest": self.server_digest[name]} if name in self.server_digest else {})}
                for name, body in sorted(self.assets.items())
            ]})
        if key.startswith("download:"):
            name = key.split(":", 1)[1]
            if not self.release or name not in self.assets:
                return Result(1, "", "no assets match the file pattern")
            body = self.assets[name]
            Path(args[args.index("--dir") + 1], name).write_bytes(
                body[:-1] if name in self.short_download else body)
            return Result(0)
        if key.startswith("upload:"):
            if not self.release:
                return Result(1, "", "release not found")
            path = Path(args[3])
            body = path.read_bytes()
            if path.name in self.assets:      # --clobber: delete first ...
                self.mutate()
                self.drop(path.name)
            self.mutate()                     # ... then upload
            stored = body[:-1] if path.name in self.short_upload else body
            self.assets[path.name] = stored
            if self.github_digests:
                self.server_digest[path.name] = "sha256:" + sha(stored)
            return Result(0)
        if key.startswith("delete:"):
            name = key.split(":", 1)[1]
            if name not in self.assets:
                return Result(1, "", "asset not found")
            self.mutate()
            self.drop(name)
            return Result(0)
        if key == "create":
            self.mutate()
            self.release = True
            return Result(0)
        return Result(2, "", f"unexpected gh call: {args}")


def quiet(fn, *args, **kwargs):
    """Run fn, returning (result or exception, captured stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            value = exc
    return value, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.work = self.tmp / "state"
        self.anchor = self.tmp / "checkpoint.txt"   # absent unless a test writes it
        self.slept: list[float] = []
        self.unpacked: list[Path] = []
        self.made = 0

    def archive(self, **kwargs) -> bytes:
        """The bytes of a state archive (a fresh file each call)."""
        self.made += 1
        return write_archive(self.tmp / f"made{self.made}.tar.gz", **kwargs).read_bytes()

    def published(self, **kwargs) -> dict[str, bytes]:
        """A release holding a rolling archive and its recorded digest."""
        body = self.archive(**kwargs)
        return {ROLLING: body, DIGEST: sidecar(body)}

    def unpack(self, path: Path) -> int:
        self.unpacked.append(Path(path))
        return 9

    def restore(self, gh: FakeGh, **kwargs):
        return quiet(state_sync.restore, REPO, TAG, self.work, gh=gh,
                     sleep=self.slept.append, unpack=self.unpack, anchor=self.anchor, **kwargs)

    def write_anchor(self, seq: int, root: str | None = None, *, travaux: int = 0,
                     troot: str | None = None) -> None:
        """anchors/checkpoint.txt exactly as registre.checkpoint_text writes it."""
        root = head_root(seq) if root is None else root
        lines = ["vigieqc.com/registre", str(seq), root, "", f"edition e{seq}"]
        if travaux:
            troot = travaux_root(travaux) if troot is None else troot
            lines.append(f"travaux {travaux} {troot} 2026-10-05T22:32:38.096094+00:00")
        lines.append("method registre-v1 sha256-chain")
        self.anchor.write_text("\n".join(lines) + "\n", encoding="utf-8")


class HttpAnswer(unittest.TestCase):
    def test_status_line_and_body_from_include(self) -> None:
        status, body = state_sync.http_answer(http_ok({"a": 1}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"a": 1})

    def test_404_from_status_line_or_stderr(self) -> None:
        self.assertEqual(state_sync.http_answer(http_error(404, "Not Found"))[0], 404)
        self.assertEqual(state_sync.http_answer(Result(1, "", "gh: Not Found (HTTP 404)"))[0], 404)

    def test_no_answer_is_never_a_status(self) -> None:
        self.assertIsNone(state_sync.http_answer(TRANSIENT["timeout"])[0])
        self.assertIsNone(state_sync.http_answer(TRANSIENT["network"])[0])

    def test_real_runner_turns_timeouts_and_missing_gh_into_failed_calls(self) -> None:
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 60)):
            self.assertEqual(state_sync.run_gh(["api", "x"], 60).code, 124)
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("gh")):
            self.assertEqual(state_sync.run_gh(["api", "x"], 60).code, 127)
        done = subprocess.CompletedProcess(["gh"], 0, "out", "")
        with mock.patch.object(subprocess, "run", return_value=done):
            self.assertEqual(state_sync.run_gh(["api", "x"], 60), Result(0, "out", ""))


class Helpers(unittest.TestCase):
    def test_sizes_compare_as_integers(self) -> None:
        for listed, expected in ((1234, 1234), (1234.0, 1234), ("1234", 1234), (" 7 ", 7), (0, 0)):
            self.assertEqual(state_sync._size(listed), expected)
        for listed in (None, True, False, -1, 1.5, "12a", "", "²", float("nan"), [], {}):
            self.assertIsNone(state_sync._size(listed), listed)

    def test_sidecar_parsing_keeps_order_and_ignores_garbage(self) -> None:
        a, b = "a" * 64, "B" * 64
        text = f"{a}  {ROLLING}\n\nnot-a-digest\n{b}  {ROLLING}\n{a}\n"
        self.assertEqual(state_sync.recorded_digests(text), [a, b.lower()])
        self.assertEqual(state_sync.recorded_digests(""), [])

    def test_sidecar_names_the_new_digest_then_the_restored_one(self) -> None:
        new, old = "1" * 64, "2" * 64
        self.assertEqual(state_sync.sidecar_text(new, old),
                         f"{new}  {ROLLING}\n{old}  {ROLLING}\n")
        for previous in ("", None, new, "not-hex"):
            self.assertEqual(state_sync.sidecar_text(new, previous), f"{new}  {ROLLING}\n")

    def test_three_hung_transfers_and_their_backoff_fit_in_the_budget(self) -> None:
        worst = state_sync.ATTEMPTS * state_sync.TRANSFER_TIMEOUT + sum(state_sync.BACKOFF)
        self.assertLess(worst, state_sync.DEADLINE)
        # restore + persist must leave the 30-minute roads job room for its refresh
        self.assertLessEqual(2 * state_sync.DEADLINE, 20 * 60)


class Restore(Base):
    def test_404_release_is_an_explicit_loud_fresh_start(self) -> None:
        gh = FakeGh(release=False)
        baseline, out = self.restore(gh)
        self.assertEqual(baseline["outcome"], "fresh")
        self.assertFalse(baseline["release"])
        self.assertIn("::warning::", out)
        self.assertIn("FRESH START", out)
        self.assertFalse(any(k.startswith("download:") for k in gh.keys()))
        self.assertEqual(self.unpacked, [])

    def test_release_without_any_state_asset_is_fresh(self) -> None:
        gh = FakeGh(assets={"README.txt": b"hello"})
        baseline, _ = self.restore(gh)
        self.assertEqual(baseline["outcome"], "fresh")
        self.assertTrue(baseline["release"])

    def test_repository_404_is_not_a_first_run(self) -> None:
        gh = FakeGh(release=False)
        gh.script("api:repo", *[http_error(404, "Not Found")] * state_sync.ATTEMPTS)
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        self.assertIn("repository itself", str(result))
        self.assertNotIn("api:release", gh.keys())

    def test_transient_failures_are_retried_then_fail_closed(self) -> None:
        for label, failure in TRANSIENT.items():
            with self.subTest(label):
                self.slept.clear()
                gh = FakeGh(assets=self.published())
                gh.script("api:release", *[failure] * state_sync.ATTEMPTS)
                result, _ = self.restore(gh)
                self.assertIsInstance(result, Refused)
                self.assertEqual(gh.keys().count("api:release"), state_sync.ATTEMPTS)
                self.assertEqual(self.slept, list(state_sync.BACKOFF))
                self.assertEqual(self.unpacked, [])

    def test_one_transient_failure_then_success_restores(self) -> None:
        gh = FakeGh(assets=self.published())
        gh.script("api:release", TRANSIENT["500"])
        baseline, _ = self.restore(gh)
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(self.slept, [state_sync.BACKOFF[0]])

    def test_verified_restore_reports_the_chain_and_unpacks(self) -> None:
        assets = self.published(seals=57)
        baseline, out = self.restore(FakeGh(assets=assets))
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["source"], ROLLING)
        self.assertEqual(baseline["seal_count"], 57)
        self.assertEqual(baseline["head_seq"], 57)
        self.assertEqual(baseline["head_root"], head_root(57))
        self.assertEqual(baseline["sha256"], sha(assets[ROLLING]))
        self.assertEqual(baseline["members"], 9)
        self.assertEqual(self.unpacked, [self.work / ROLLING])
        self.assertIn("recorded by the last persist", out)
        self.assertNotIn("::warning::", out)

    def test_digest_mismatch_without_a_dated_copy_fails_before_unpack(self) -> None:
        assets = self.published()
        assets[DIGEST] = f"{'0' * 64}  {ROLLING}\n".encode()
        result, _ = self.restore(FakeGh(assets=assets))
        self.assertIsInstance(result, Refused)
        message = str(result)
        self.assertIn("recorded", message)
        self.assertIn("refusing an unverified state", message)
        self.assertIn("Manual recovery", message)
        self.assertIn("(none)", message)          # the listing says there is no dated copy
        self.assertEqual(self.unpacked, [])

    def test_legacy_archive_without_digest_is_restored_with_a_warning(self) -> None:
        assets = self.published()
        del assets[DIGEST]
        baseline, out = self.restore(FakeGh(assets=assets))
        self.assertEqual(baseline["outcome"], "restored")
        self.assertIn("::warning::", out)
        self.assertIn("legacy", out)

    def test_truncated_download_is_retried_then_fails(self) -> None:
        gh = FakeGh(assets=self.published())
        gh.short_download.add(ROLLING)
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        self.assertEqual(gh.keys().count(f"download:{ROLLING}"), state_sync.ATTEMPTS)
        self.assertEqual(self.unpacked, [])

    def test_bytes_that_are_not_githubs_digest_are_retried_then_fail(self) -> None:
        gh = FakeGh(assets=self.published())
        gh.server_digest[ROLLING] = "sha256:" + "e" * 64
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        self.assertIn("GitHub", str(result))
        self.assertEqual(gh.keys().count(f"download:{ROLLING}"), state_sync.ATTEMPTS)
        self.assertEqual(self.unpacked, [])

    def test_listed_size_is_compared_as_an_integer(self) -> None:
        for listed in ("{n}", "{n}.0"):
            with self.subTest(listed):
                assets = self.published()
                gh = FakeGh(assets=assets)
                text = listed.format(n=len(assets[ROLLING]))
                gh.listed_size[ROLLING] = float(text) if "." in text else text
                baseline, _ = self.restore(gh)
                self.assertEqual(baseline["outcome"], "restored")
        gh = FakeGh(assets=self.published())
        gh.listed_size[ROLLING] = None
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        self.assertIn("no usable size", str(result))
        self.assertNotIn(f"download:{ROLLING}", gh.keys())   # refused at once, no retries

    def test_sidecar_or_garbage_dated_copy_without_rolling_is_an_interrupted_persist(self) -> None:
        for leftovers in ({DIGEST: b"x"}, {"state-20261001T061700Z.tar.gz": b"x"}):
            with self.subTest(sorted(leftovers)):
                result, _ = self.restore(FakeGh(assets=leftovers))
                self.assertIsInstance(result, Refused)
                self.assertIn("interrupted persist", str(result))
                self.assertEqual(self.unpacked, [])

    def test_archive_without_registre_is_refused(self) -> None:
        result, _ = self.restore(FakeGh(assets=self.published(registre=False)))
        self.assertIsInstance(result, Refused)
        self.assertEqual(self.unpacked, [])

    def test_fresh_start_is_refused_while_the_git_anchor_names_a_seal(self) -> None:
        self.write_anchor(57)
        result, _ = self.restore(FakeGh(release=False))
        self.assertIsInstance(result, Refused)
        self.assertIn("fork", str(result))

    def test_state_older_than_the_anchor_is_refused(self) -> None:
        self.write_anchor(57)
        result, _ = self.restore(FakeGh(assets=self.published(seals=5)))
        self.assertIsInstance(result, Refused)
        self.assertIn("older than what was published", str(result))
        self.assertEqual(self.unpacked, [])

    def test_forked_state_is_refused_and_a_matching_anchor_passes(self) -> None:
        self.write_anchor(57)
        result, _ = self.restore(FakeGh(assets=self.published(seals=57, fork=True)))
        self.assertIsInstance(result, Refused)
        self.assertIn("forked", str(result))
        self.write_anchor(56)   # the anchor may lag the state
        baseline, _ = self.restore(FakeGh(assets=self.published(seals=57)))
        self.assertEqual(baseline["outcome"], "restored")


class TwoLineSidecar(Base):
    """BLOCKING 2: the sidecar goes up first and names the previous archive."""

    def test_second_line_vouches_for_the_archive_a_killed_persist_left(self) -> None:
        old, new = self.archive(seals=57), self.archive(seals=58)
        baseline, out = self.restore(FakeGh(assets={ROLLING: old, DIGEST: sidecar(new, old)}))
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["sha256"], sha(old))
        self.assertIn(f"line 2 of {DIGEST}", out)

    def test_a_digest_on_neither_line_is_still_refused(self) -> None:
        old, new, other = self.archive(seals=57), self.archive(seals=58), self.archive(seals=56)
        result, _ = self.restore(FakeGh(assets={ROLLING: other, DIGEST: sidecar(new, old)}))
        self.assertIsInstance(result, Refused)
        self.assertIn("refusing an unverified state", str(result))
        self.assertEqual(self.unpacked, [])


class DatedFallback(Base):
    """BLOCKING 1: a rolling asset behind the anchor is answered from a dated copy."""

    def test_rolling_behind_the_anchor_restores_the_newest_dated_copy_holding_the_seal(self) -> None:
        rolling, dated58, dated57 = (self.archive(seals=57), self.archive(seals=58),
                                     self.archive(seals=57, files=7))
        self.write_anchor(58)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(dated58, rolling),
                            NEW_DATED: dated58, OLD_DATED: dated57})
        baseline, out = self.restore(gh)
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["source"], NEW_DATED)
        self.assertEqual(baseline["seal_count"], 58)
        self.assertEqual(baseline["head_root"], head_root(58))
        self.assertEqual(baseline["sha256"], sha(dated58))
        self.assertEqual(self.unpacked, [self.work / NEW_DATED])
        self.assertIn(f"::warning::state: rolling asset behind the anchor; restored from {NEW_DATED}", out)
        # Every vouched copy is inspected; the highest head wins, the loser is not kept.
        self.assertIn(f"download:{OLD_DATED}", gh.keys())
        self.assertFalse((self.work / OLD_DATED).exists())

    def test_a_dated_copy_vouched_only_by_githubs_digest_is_accepted(self) -> None:
        # The persist died at the dated copy's confirm: the sidecar still names 57 only.
        rolling, dated58 = self.archive(seals=57), self.archive(seals=58)
        self.write_anchor(58)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling), NEW_DATED: dated58}).vouch_all()
        baseline, _ = self.restore(gh)
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["source"], NEW_DATED)
        self.assertEqual(baseline["seal_count"], 58)

    def test_an_unvouched_dated_copy_is_never_restored(self) -> None:
        rolling, dated58 = self.archive(seals=57), self.archive(seals=58)
        self.write_anchor(58)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling), NEW_DATED: dated58})
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        self.assertIn(f"{NEW_DATED} ({len(dated58)} bytes): sha256", str(result))
        self.assertEqual(self.unpacked, [])

    def test_refuses_when_no_dated_copy_holds_the_anchored_seal_and_lists_them(self) -> None:
        rolling, dated56 = self.archive(seals=57), self.archive(seals=56)
        forked = self.archive(seals=58, fork=True)
        self.write_anchor(58)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling), NEW_DATED: forked,
                            OLD_DATED: rolling, OLDER_DATED: dated56}).vouch_all()
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        message = str(result)
        self.assertIn("older than what was published", message)
        self.assertIn("Dated copies on release 'state'", message)
        self.assertLess(message.index(NEW_DATED), message.index(OLD_DATED))   # newest first
        self.assertIn(f"{NEW_DATED} ({len(forked)} bytes): seal n° 58 differs", message)
        self.assertIn(f"{OLD_DATED} ({len(rolling)} bytes): the same bytes as the refused", message)
        self.assertIn(f"{OLDER_DATED} ({len(dated56)} bytes): its chain stops at seal n° 56", message)
        self.assertIn("Manual recovery", message)
        self.assertIn("scripts/state_sync.py inspect", message)
        self.assertIn("seal n° 58", message)
        self.assertNotIn(f"download:{OLD_DATED}", gh.keys())   # same bytes: not even fetched
        self.assertEqual(self.unpacked, [])

    def test_unverified_rolling_falls_back_to_a_vouched_dated_copy(self) -> None:
        rolling, dated = self.archive(seals=57), self.archive(seals=57, files=7)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(self.archive(seals=1)),
                            NEW_DATED: dated}).vouch_all()
        baseline, out = self.restore(gh)
        self.assertEqual(baseline["source"], NEW_DATED)
        self.assertIn(f"rolling asset unverified; restored from {NEW_DATED}", out)

    def test_missing_rolling_restores_the_dated_copy_the_sidecar_names(self) -> None:
        # The rolling asset was deleted by --clobber and its upload never finished.
        old, dated58 = self.archive(seals=57), self.archive(seals=58)
        gh = FakeGh(assets={DIGEST: sidecar(dated58, old), NEW_DATED: dated58})
        baseline, out = self.restore(gh)
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["source"], NEW_DATED)
        self.assertIn(f"rolling asset missing; restored from {NEW_DATED}", out)

    def test_a_dated_copy_that_fails_to_download_is_passed_over(self) -> None:
        rolling, dated58, dated58b = self.archive(seals=57), self.archive(seals=58), self.archive(seals=58, files=7)
        self.write_anchor(58)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling), NEW_DATED: dated58,
                            OLD_DATED: dated58b}).vouch_all()
        gh.short_download.add(NEW_DATED)
        baseline, _ = self.restore(gh)
        self.assertEqual(baseline["source"], OLD_DATED)


class PersistBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.packed = {"seals": 58, "files": 8, "filler": 1000, "registre": True, "fork": False}

    def pack(self, out: Path) -> int:
        write_archive(Path(out), **self.packed)
        return 1

    def baseline(self, outcome: str = "restored", archive: bytes | None = None, **extra) -> None:
        path = self.tmp / "restored.tar.gz"
        if archive is None:
            write_archive(path, seals=57)
        else:
            path.write_bytes(archive)
        facts = state_sync.inspect_archive(path)
        doc = {"outcome": outcome, "release": True, "seal_count": facts["seal_count"],
               "head_seq": facts["head_seq"], "head_root": facts["head_root"],
               "travaux_seq": facts["travaux_seq"], "travaux_root": facts["travaux_root"],
               "sha256": sha(path.read_bytes()), "members": facts["members"], "bytes": facts["bytes"],
               "durable_members": facts["durable_members"], "durable_bytes": facts["durable_bytes"],
               **extra}
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / state_sync.BASELINE).write_text(json.dumps(doc), encoding="utf-8")

    def persist(self, gh: FakeGh, *, dated: bool = True, keep: int = 12, environ=None,
                sleep=None, when: datetime = NOW, **kwargs):
        return quiet(state_sync.persist, REPO, TAG, self.work, dated=dated, keep=keep, gh=gh,
                     sleep=sleep or self.slept.append, now=lambda: when, pack=self.pack,
                     environ=environ or {}, **kwargs)

    def assert_untouched(self, gh: FakeGh, result) -> None:
        self.assertIsInstance(result, Refused)
        self.assertFalse(any(k.startswith(("upload:", "delete:")) for k in gh.keys()))


class Persist(PersistBase):
    def test_refuses_without_a_successful_restore(self) -> None:
        for outcome in ("failed", None):
            with self.subTest(outcome):
                if outcome:
                    self.baseline(outcome)
                else:
                    (self.work / state_sync.BASELINE).unlink(missing_ok=True)
                gh = FakeGh(assets=self.published())
                result, _ = self.persist(gh)
                self.assert_untouched(gh, result)

    def test_refuses_a_shrinking_chain_with_the_numbers(self) -> None:
        self.baseline()
        self.packed["seals"] = 1
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assert_untouched(gh, result)
        self.assertIn("from 57 to 1", str(result))

    def test_refuses_a_forked_chain_of_equal_length(self) -> None:
        self.baseline()
        self.packed.update(seals=57, fork=True)
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assert_untouched(gh, result)
        self.assertIn("fork", str(result))

    def test_refuses_an_archive_without_the_registre(self) -> None:
        self.baseline()
        self.packed["registre"] = False
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assert_untouched(gh, result)
        self.assertIn(REGISTRE_MEMBER, str(result))

    def test_size_collapse_is_refused_unless_overridden_loudly(self) -> None:
        self.baseline()
        self.packed.update(files=1, filler=10)
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assert_untouched(gh, result)
        self.assertIn(state_sync.ALLOW_SHRINK, str(result))
        gh = FakeGh(assets=self.published())
        result, out = self.persist(gh, environ={state_sync.ALLOW_SHRINK: "1"})
        self.assertNotIsInstance(result, Exception)
        self.assertIn("::warning::", out)
        self.assertIn("uploading anyway", out)

    def test_regenerated_caches_do_not_count_toward_a_collapse(self) -> None:
        # Restored: 8 durable files + 40 cache files. Packed: the caches are gone.
        self.baseline(archive=self.archive(seals=57, caches=20))
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assertNotIsInstance(result, Exception)
        # ... but the durable rest still may not collapse.
        self.baseline(archive=self.archive(seals=57, caches=20))
        self.packed.update(files=1, caches=20)
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assert_untouched(gh, result)
        self.assertIn("member count (regenerated caches excluded) fell from 9 to 2", str(result))

    def test_dated_copy_then_sidecar_then_rolling(self) -> None:
        self.baseline()
        restored = json.loads((self.work / state_sync.BASELINE).read_text(encoding="utf-8"))["sha256"]
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assertNotIsInstance(result, Exception)
        keys = gh.keys()
        dated, digest, rolling = (keys.index(f"upload:{NEW_DATED}"),
                                  keys.index(f"upload:{DIGEST}"), keys.index(f"upload:{ROLLING}"))
        self.assertLess(dated, digest)
        self.assertLess(digest, rolling)
        self.assertIn("api:release", keys[dated:digest])   # the dated copy confirmed in between
        upload = next(c for c in gh.calls if FakeGh.key(c) == f"upload:{ROLLING}")
        self.assertIn("--clobber", upload)
        body = gh.assets[ROLLING]
        self.assertEqual(gh.assets[NEW_DATED], body)
        self.assertEqual(state_sync.recorded_digests(gh.assets[DIGEST].decode()), [sha(body), restored])
        self.assertEqual(result["seal_count"], 58)

    def test_unconfirmed_dated_upload_never_touches_the_rolling_pair(self) -> None:
        self.baseline()
        assets = self.published()
        gh = FakeGh(assets=assets)
        gh.short_upload.add(NEW_DATED)
        result, _ = self.persist(gh)
        self.assertIsInstance(result, Refused)
        self.assertNotIn(f"upload:{ROLLING}", gh.keys())
        self.assertNotIn(f"upload:{DIGEST}", gh.keys())
        self.assertEqual(gh.assets[ROLLING], assets[ROLLING])

    def test_server_digest_disagreement_is_refused(self) -> None:
        self.baseline()
        gh = FakeGh(assets=self.published())
        gh.server_digest[NEW_DATED] = "sha256:" + "f" * 64
        result, _ = self.persist(gh)
        self.assertIsInstance(result, Refused)
        self.assertNotIn(f"upload:{ROLLING}", gh.keys())

    def test_confirm_compares_listed_sizes_as_integers(self) -> None:
        for kind in (str, float):    # an API that lists sizes as "1234" or 1234.0
            with self.subTest(kind.__name__):
                self.baseline()
                gh = FakeGh(assets=self.published())

                def relisted(args, timeout, real=gh.__call__, kind=kind):
                    res = real(args, timeout)
                    if FakeGh.key(args) == "api:release" and res.code == 0:
                        head, body = res.out.split("\r\n\r\n", 1)
                        doc = json.loads(body)
                        for asset in doc["assets"]:
                            asset["size"] = kind(asset["size"])
                        return Result(0, head + "\r\n\r\n" + json.dumps(doc), "")
                    return res
                result, _ = self.persist(relisted)
                self.assertNotIsInstance(result, Exception)
                self.assertIn(ROLLING, gh.assets)

    def test_prune_keeps_the_newest_n_and_never_the_rolling_asset(self) -> None:
        self.baseline()
        assets = self.published()
        old = [f"state-202610{day:02d}T061700Z.tar.gz" for day in range(1, 15)]
        assets.update({name: b"old" for name in old})
        gh = FakeGh(assets=assets)
        result, _ = self.persist(gh, keep=12)
        self.assertNotIsInstance(result, Exception)
        self.assertEqual(sorted(result["pruned"]), old[:3])
        self.assertIn(ROLLING, gh.assets)
        self.assertIn(DIGEST, gh.assets)
        self.assertIn(NEW_DATED, gh.assets)
        self.assertEqual(sum(1 for n in gh.assets if state_sync.DATED.match(n)), 12)

    def test_prune_plan_protects_rolling_and_the_fresh_copy(self) -> None:
        names = [ROLLING, DIGEST, "state-20260101T000000Z.tar.gz",
                 "state-20260102T000000Z.tar.gz", "state-20260103T000000Z.tar.gz"]
        plan = state_sync.prune_plan(names, 1, {"state-20260101T000000Z.tar.gz"})
        self.assertEqual(plan, ["state-20260102T000000Z.tar.gz"])
        self.assertEqual(state_sync.prune_plan(names, 0, set()), names[3:1:-1])

    def test_prune_failure_is_a_warning_not_a_failed_persist(self) -> None:
        self.baseline()
        assets = self.published()
        old = [f"state-202610{day:02d}T061700Z.tar.gz" for day in range(1, 14)]
        assets.update({name: b"old" for name in old})
        gh = FakeGh(assets=assets)
        gh.script(f"delete:{old[0]}", Result(1, "", "gh: Server Error (HTTP 500)"))
        result, out = self.persist(gh, keep=12)
        self.assertNotIsInstance(result, Exception)
        self.assertIn(old[0], gh.assets)
        self.assertIn("could not prune", out)

    def test_roads_lane_persists_only_the_rolling_pair_through_the_same_checks(self) -> None:
        self.baseline()
        assets = self.published()
        assets.update({f"state-202610{day:02d}T061700Z.tar.gz": b"old" for day in range(1, 15)})
        gh = FakeGh(assets=assets)
        result, _ = self.persist(gh, dated=False)
        self.assertNotIsInstance(result, Exception)
        self.assertFalse(any("state-2026" in k for k in gh.keys() if k.startswith(("upload:", "delete:"))))
        self.packed["seals"] = 3
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh, dated=False)
        self.assert_untouched(gh, result)

    def test_fresh_start_creates_the_release_before_uploading(self) -> None:
        self.baseline("fresh", release=False, seal_count=0, head_seq=0, head_root="",
                      sha256="", members=0, bytes=0, durable_members=0, durable_bytes=0)
        self.packed["seals"] = 1
        gh = FakeGh(release=False)
        result, _ = self.persist(gh)
        self.assertNotIsInstance(result, Exception)
        keys = gh.keys()
        self.assertLess(keys.index("create"), keys.index(f"upload:{NEW_DATED}"))
        self.assertIn(ROLLING, gh.assets)
        self.assertEqual(len(state_sync.recorded_digests(gh.assets[DIGEST].decode())), 1)


class KilledPersist(PersistBase):
    """A persist that dies part-way must leave a state the next restore accepts."""

    def restore_after(self, gh: FakeGh):
        """Restore from what the dead persist left (same assets, same GitHub digests)."""
        fresh_work = self.tmp / f"restore{self.made}"
        self.made += 1
        return quiet(state_sync.restore, REPO, TAG, fresh_work,
                     gh=FakeGh(assets=gh.assets, server_digest=gh.server_digest),
                     sleep=self.slept.append, unpack=self.unpack, anchor=self.anchor)

    def test_crash_after_the_sidecar_upload_restores_the_old_rolling_asset(self) -> None:
        assets = self.published(seals=57)
        self.baseline(archive=assets[ROLLING])
        self.packed.update(seals=57, files=9)   # the roads lane: no new edition, new bytes
        gh = FakeGh(assets=assets)
        gh.crash_after = 2                 # sidecar: delete + upload; the rolling delete never runs
        with self.assertRaises(Crash):
            self.persist(gh, dated=False)
        self.assertEqual(gh.assets[ROLLING], assets[ROLLING])
        self.assertNotEqual(gh.assets[DIGEST], assets[DIGEST])
        baseline, out = self.restore_after(gh)
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["sha256"], sha(assets[ROLLING]))
        self.assertIn(f"line 2 of {DIGEST}", out)

    def test_failed_confirm_after_the_rolling_upload_restores_the_new_rolling_asset(self) -> None:
        assets = self.published(seals=57)
        self.baseline(archive=assets[ROLLING])
        gh = FakeGh(assets=assets)
        gh.script("api:release", *[TRANSIENT["500"]] * state_sync.ATTEMPTS)
        result, _ = self.persist(gh, dated=False)
        self.assertIsInstance(result, Refused)
        self.assertNotEqual(gh.assets[ROLLING], assets[ROLLING])
        baseline, out = self.restore_after(gh)
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["sha256"], sha(gh.assets[ROLLING]))
        self.assertEqual(baseline["seal_count"], 58)
        self.assertIn("recorded by the last persist", out)

    def test_persist_failing_after_deploy_and_anchor_recovers_from_its_dated_copy(self) -> None:
        # Seal 58 was deployed and anchored; the persist then failed on the rolling upload.
        assets = self.published(seals=57)
        self.baseline(archive=assets[ROLLING])
        gh = FakeGh(assets=assets)
        gh.script(f"upload:{ROLLING}", *[Result(1, "", "HTTP 502")] * state_sync.ATTEMPTS)
        result, _ = self.persist(gh)
        self.assertIsInstance(result, Refused)
        self.write_anchor(58)
        self.assertEqual(gh.assets[ROLLING], assets[ROLLING])     # still seal 57
        # Before this fix every later restore refused "older than what was published".
        self.work = self.tmp / "next-run"
        gh2 = FakeGh(assets=gh.assets)
        baseline, out = self.restore(gh2)
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["source"], NEW_DATED)
        self.assertEqual(baseline["seal_count"], 58)
        self.assertIn(f"rolling asset behind the anchor; restored from {NEW_DATED}", out)
        # ... and that run's persist re-publishes it as the rolling asset, unrefused.
        state_sync.store_io.write_json_atomic(self.work / state_sync.BASELINE, baseline)
        self.packed["seals"] = 59
        result, _ = self.persist(gh2, when=datetime(2026, 10, 20, 12, 17, tzinfo=timezone.utc))
        self.assertNotIsInstance(result, Exception)
        # New, restored, and the earlier line carried forward.
        self.assertEqual(state_sync.recorded_digests(gh2.assets[DIGEST].decode()),
                         [sha(gh2.assets[ROLLING]), sha(gh2.assets[NEW_DATED]), sha(assets[ROLLING])])
        self.work = self.tmp / "third-run"
        baseline, _ = self.restore(FakeGh(assets=gh2.assets))
        self.assertEqual((baseline["source"], baseline["seal_count"]), (ROLLING, 59))

    def facts_of(self, body: bytes) -> dict:
        self.made += 1
        path = self.tmp / f"facts{self.made}.tar.gz"
        path.write_bytes(body)
        return state_sync.inspect_archive(path)

    def every_interleaving(self, *, dated: bool, packed: dict, deployed: tuple | None = None,
                           github_digests: bool = True, rolling: dict | None = None,
                           earlier: dict | None = None) -> list[tuple]:
        """Kill the persist before each mutation in turn: [(k, restore result, out)].

        The release starts as earlier persists leave it: the rolling asset, the
        dated copy of the last full refresh (which the git anchor witnesses),
        and the sidecar that sidecar_text writes (the rolling digest, the dated
        copy's line carried forward, the rolling heads). The baseline is what
        the real restore of that release returns. A full refresh then anchors
        `deployed` (edition seq, travaux seq) before its persist; the roads lane
        never anchors. With github_digests, GitHub lists its own digest of every
        asset; without, only the sidecar vouches. The last entry is the persist
        that completed.
        """
        rolling = self.archive(**(rolling or {"seals": 57}))
        earlier = self.earlier = self.archive(**(earlier or {"seals": 57, "files": 7}))
        first = self.facts_of(earlier)
        start = {ROLLING: rolling, OLD_DATED: earlier, DIGEST: state_sync.sidecar_text(
            sha(rolling), sha(earlier), restored_from=OLD_DATED,
            witnesses=state_sync.heads(self.facts_of(rolling))).encode()}
        self.packed.update(packed)
        outcomes = []
        for k in range(32):
            self.write_anchor(first["head_seq"], travaux=first["travaux_seq"])
            self.work = self.tmp / f"run{k}"
            seen = FakeGh(assets=start)
            baseline, out = self.restore(seen.vouch_all() if github_digests else seen)
            self.assertIsInstance(baseline, dict, out)
            state_sync.store_io.write_json_atomic(self.work / state_sync.BASELINE, baseline)
            gh = FakeGh(assets=start, github_digests=github_digests)
            if github_digests:
                gh.vouch_all()
            gh.crash_after = k
            try:
                done, _ = self.persist(gh, dated=dated)
            except Crash:
                done = None
            else:
                self.assertNotIsInstance(done, Exception, f"persist failed outright: {done}")
            if deployed:
                self.write_anchor(deployed[0], travaux=deployed[1])
            outcomes.append((k, *self.restore_after(gh)))
            if done is not None:
                self.assertGreaterEqual(k, 4)   # it was killed at every step before
                return outcomes
        self.fail("the persist never completed")

    def test_every_interleaving_of_a_killed_roads_persist_restores(self) -> None:
        for digests in (True, False):
            for k, baseline, out in self.every_interleaving(
                    dated=False, packed={"seals": 57, "files": 9}, github_digests=digests):
                with self.subTest(github_digests=digests, killed_after=k):
                    self.assertIsInstance(baseline, dict, f"killed after {k}: {baseline}")
                    self.assertEqual(baseline["outcome"], "restored")
                    self.assertEqual(baseline["seal_count"], 57)
                    if k == 3:   # the rolling asset deleted: the dated copy, vouched for
                        self.assertEqual(baseline["source"], OLD_DATED)
                        if not digests:  # ... by its line, carried forward through the sidecar
                            self.assertIn(OLD_DATED, out)

    def test_every_interleaving_of_a_killed_full_persist_restores_or_halts_loudly(self) -> None:
        for k, baseline, out in self.every_interleaving(dated=True, packed={"seals": 58},
                                                        deployed=(58, 0)):
            with self.subTest(killed_after=k):
                if k == 0:   # died before its dated copy existed: seal 58 is nowhere in the store
                    self.assertIsInstance(baseline, Refused)
                    self.assertIn("older than what was published", str(baseline))
                    self.assertIn(OLD_DATED, str(baseline))
                    continue
                self.assertIsInstance(baseline, dict, f"killed after {k}: {baseline}")
                self.assertEqual(baseline["seal_count"], 58)

    def test_full_persist_without_githubs_digests_restores_once_its_sidecar_landed(self) -> None:
        # Without GitHub's digest only a sidecar line vouches for the new dated
        # copy: until the new sidecar is on the release (k <= 2: before it, or
        # its --clobber delete), the restore halts loudly with the runbook.
        for k, baseline, out in self.every_interleaving(dated=True, packed={"seals": 58},
                                                        deployed=(58, 0), github_digests=False):
            with self.subTest(killed_after=k):
                if k <= 2:
                    self.assertIsInstance(baseline, Refused)
                    self.assertIn("Manual recovery", str(baseline))
                    self.assertIn(NEW_DATED if k else OLD_DATED, str(baseline))
                    continue
                self.assertIsInstance(baseline, dict, f"killed after {k}: {baseline}")
                self.assertEqual(baseline["seal_count"], 58)

    def test_a_killed_roads_persist_never_rolls_the_travaux_chain_back(self) -> None:
        # The last full refresh (the dated copy, the anchor) sealed roadworks n° 8;
        # the roads lane since sealed and deployed n° 9-10 (rolling) and now n° 11.
        # Restoring the dated copy would re-mint n° 9 with a new root: a fork of
        # the public travaux.json. Killed once the new sidecar is up (k = 2) or at
        # the rolling asset's --clobber delete (k = 3), the restore must halt.
        for digests in (True, False):
            for k, baseline, out in self.every_interleaving(
                    dated=False, packed={"seals": 57, "files": 9, "travaux": 11},
                    rolling={"seals": 57, "travaux": 10},
                    earlier={"seals": 57, "files": 7, "travaux": 8}, github_digests=digests):
                with self.subTest(github_digests=digests, killed_after=k):
                    if k in (2, 3):
                        self.assertIsInstance(baseline, Refused, f"killed after {k}: rolled back")
                        message = str(baseline)
                        self.assertIn("Dated copies on release", message)
                        self.assertIn(f"{OLD_DATED} ({len(self.earlier)} bytes): its chain stops "
                                      f"at roadworks seal n° 8", message)
                        self.assertIn("roadworks seal n° 11", message)
                        self.assertIn("Manual recovery", message)
                        self.assertIn(f"--sidecar recovery/{DIGEST}", message)
                        continue
                    self.assertIsInstance(baseline, dict, f"killed after {k}: {baseline}")
                    # k <= 1: the new sidecar never landed, the old rolling asset is
                    # whole (the seal this run deployed is witnessed nowhere: a
                    # residual risk, AGENTS.md); k = 4: the new state.
                    self.assertEqual(baseline["travaux_seq"], 11 if k >= 4 else 10)
                    self.assertEqual(baseline["source"], ROLLING)


class Budget(PersistBase):
    def ticking(self):
        clock = [0.0]

        def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            clock[0] += seconds
        return clock, sleep

    def test_a_hung_download_fails_loudly_inside_the_budget(self) -> None:
        clock, sleep = self.ticking()
        gh = FakeGh(assets=self.published(), clock=clock)
        gh.hang.add(f"download:{ROLLING}")
        result, _ = quiet(state_sync.restore, REPO, TAG, self.work, gh=gh, sleep=sleep,
                          unpack=self.unpack, anchor=self.anchor, deadline=200,
                          clock=lambda: clock[0])
        self.assertIsInstance(result, Refused)
        self.assertIn("budget", str(result))
        self.assertIn(f"download {ROLLING}", str(result))
        self.assertLessEqual(clock[0], 200)
        self.assertEqual(gh.timeouts[-2:], [state_sync.TRANSFER_TIMEOUT, 45])  # the rest of the budget
        self.assertEqual(self.unpacked, [])

    def test_a_hung_upload_fails_the_persist_loudly_inside_the_budget(self) -> None:
        clock, sleep = self.ticking()
        self.baseline()
        gh = FakeGh(assets=self.published(), clock=clock)
        gh.hang.add(f"upload:{NEW_DATED}")
        result, _ = self.persist(gh, sleep=sleep, deadline=200, clock=lambda: clock[0])
        self.assertIsInstance(result, Refused)
        self.assertIn("budget", str(result))
        self.assertLessEqual(clock[0], 200)
        self.assertNotIn(f"upload:{DIGEST}", gh.keys())
        self.assertNotIn(f"upload:{ROLLING}", gh.keys())

    def test_a_spent_budget_makes_no_further_call(self) -> None:
        gh = FakeGh(assets=self.published())
        result, _ = quiet(state_sync.restore, REPO, TAG, self.work, gh=gh, sleep=self.slept.append,
                          unpack=self.unpack, anchor=self.anchor, deadline=0)
        self.assertIsInstance(result, Refused)
        self.assertIn("budget", str(result))
        self.assertEqual(gh.calls, [])


class CommandLine(Base):
    def run_main(self, argv: list[str], gh: FakeGh, environ: dict) -> tuple[int, str]:
        return quiet(state_sync.main, argv, gh=gh, sleep=self.slept.append,
                     now=lambda: NOW, environ=environ)

    def outputs(self, path: Path) -> dict[str, str]:
        return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())

    def setUp(self) -> None:
        super().setUp()
        for target, value in ((state_sync, ("ANCHOR", self.anchor)),
                              (state_pack, ("unpack", self.unpack))):
            patcher = mock.patch.object(target, *value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.github_output = self.tmp / "github_output"
        self.env = {"GITHUB_OUTPUT": str(self.github_output)}
        self.args = ["--repo", REPO, "--tag", TAG, "--dir", str(self.work)]

    def test_restore_writes_github_outputs_and_the_baseline(self) -> None:
        assets = self.published(seals=57)
        code, _ = self.run_main(["restore", *self.args], FakeGh(assets=assets), self.env)
        self.assertEqual(code, 0)
        out = self.outputs(self.github_output)
        self.assertEqual(out["outcome"], "restored")
        self.assertEqual(out["source"], ROLLING)
        self.assertEqual(out["seal_count"], "57")
        self.assertEqual(out["head_root"], head_root(57))
        self.assertEqual(out["sha256"], sha(assets[ROLLING]))
        self.assertEqual(out["members"], "9")
        self.assertGreater(int(out["bytes"]), 0)
        self.assertEqual(set(out), set(state_sync.OUTPUT_KEYS))
        self.assertEqual(state_sync.read_baseline(self.work)["outcome"], "restored")

    def test_fresh_outcome_is_exit_zero(self) -> None:
        code, _ = self.run_main(["restore", *self.args], FakeGh(release=False), self.env)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs(self.github_output)["outcome"], "fresh")

    def test_failed_restore_exits_non_zero_and_persist_then_refuses(self) -> None:
        gh = FakeGh(assets=self.published())
        gh.script("api:release", *[TRANSIENT["401"]] * state_sync.ATTEMPTS)
        self.assertEqual(self.run_main(["restore", *self.args], gh, self.env)[0], 1)
        self.assertEqual(self.outputs(self.github_output)["outcome"], "failed")
        self.assertEqual(state_sync.read_baseline(self.work)["outcome"], "failed")
        packed: list[Path] = []
        with mock.patch.object(state_pack, "pack", side_effect=packed.append):
            code, _ = self.run_main(["persist", *self.args, "--dated"], gh, self.env)
        self.assertEqual(code, 1)
        self.assertEqual(packed, [])
        self.assertFalse(any(k.startswith(("upload:", "delete:")) for k in gh.keys()))

    def test_unexpected_crash_is_a_failed_outcome(self) -> None:
        gh = FakeGh(assets=self.published())
        with mock.patch.object(state_sync, "inspect_archive", side_effect=tarfile.ReadError("bad")):
            self.assertEqual(self.run_main(["restore", *self.args], gh, self.env)[0], 1)
        self.assertEqual(self.outputs(self.github_output)["outcome"], "failed")

    def test_a_refusal_is_one_annotation_with_the_runbook_as_lines(self) -> None:
        self.write_anchor(58)
        code, out = self.run_main(["restore", *self.args], FakeGh(assets=self.published(seals=57)), self.env)
        self.assertEqual(code, 1)
        errors = [line for line in out.splitlines() if line.startswith("::error::")]
        self.assertEqual(len(errors), 1)
        self.assertIn("older than what was published%0ADated copies", errors[0])
        self.assertIn("Manual recovery", errors[0])

    def test_inspect_checks_a_local_copy_against_the_anchor(self) -> None:
        copy = write_archive(self.tmp / NEW_DATED, seals=58)
        self.write_anchor(58)
        code, out = self.run_main(["inspect", str(copy)], FakeGh(), {})
        self.assertEqual(code, 0)
        self.assertIn(sha(copy.read_bytes()), out)
        self.assertIn("holds seal n° 58", out)
        self.write_anchor(59)
        code, out = self.run_main(["inspect", str(copy), "--anchor", str(self.anchor)], FakeGh(), {})
        self.assertEqual(code, 1)
        self.assertIn("older than what was published", out)
        junk = self.tmp / "junk.tar.gz"
        junk.write_bytes(b"not a tarball")
        self.assertEqual(self.run_main(["inspect", str(junk)], FakeGh(), {})[0], 1)


class Anchor(Base):
    """The git anchor witnesses both chains; a present anchor that does not parse fails closed."""

    def test_reads_the_checkpoint_registre_writes(self) -> None:
        edition, road = head_root(57), travaux_root(58)
        state = {"seals": [{"seq": 57, "root": edition, "edition": "2026-10-06T00:03:00+00:00"}],
                 "travaux": {"seals": [{"seq": 58, "root": road,
                                        "fetched_at": "2026-10-05T22:32:38+00:00"}]}}
        self.anchor.write_text(registre.checkpoint_text(state), encoding="utf-8")
        self.assertEqual(state_sync.read_anchor(self.anchor), [
            Witness(state_sync.EDITION, 57, edition, state_sync.ANCHORED),
            Witness(state_sync.TRAVAUX, 58, road, state_sync.ANCHORED)])
        self.write_anchor(57, travaux=58)   # the fixture writes the same shape
        self.assertEqual(len(state_sync.read_anchor(self.anchor)), 2)

    def test_the_committed_anchor_parses(self) -> None:
        if not state_sync.ANCHOR.is_file():
            self.skipTest("no committed anchor in this checkout")
        found = state_sync.read_anchor(state_sync.ANCHOR)
        self.assertEqual([w.chain for w in found][:1], [state_sync.EDITION])

    def test_absent_or_genesis_anchor_witnesses_nothing(self) -> None:
        self.assertEqual(state_sync.read_anchor(self.anchor), [])
        self.anchor.write_text("vigieqc.com/registre\n0\n\n", encoding="utf-8")
        self.assertEqual(state_sync.read_anchor(self.anchor), [])
        self.write_anchor(57)       # an older anchor without a travaux line
        self.assertEqual([w.chain for w in state_sync.read_anchor(self.anchor)], [state_sync.EDITION])

    MALFORMED = {
        "empty": "",
        "no seal number": "vigieqc.com/registre\nfifty-seven\n" + "a" * 64 + "\n",
        "no root": "vigieqc.com/registre\n57\n\n\nedition e57\n",
        "short root": "vigieqc.com/registre\n57\nabc123\n",
        "bad travaux": "vigieqc.com/registre\n57\n" + "a" * 64 + "\n\ntravaux  " + "b" * 64 + " t\n",
        "travaux without root": "vigieqc.com/registre\n57\n" + "a" * 64 + "\n\ntravaux 58\n",
        "two travaux": ("vigieqc.com/registre\n57\n" + "a" * 64 + "\n\n"
                        + ("travaux 58 " + "b" * 64 + " t\n") * 2),
    }

    def test_a_present_malformed_anchor_is_refused(self) -> None:
        for label, text in self.MALFORMED.items():
            with self.subTest(label):
                self.anchor.write_text(text, encoding="utf-8")
                with self.assertRaises(Refused) as caught:
                    state_sync.read_anchor(self.anchor)
                self.assertIn("malformed", str(caught.exception))
        self.anchor.write_bytes(b"vigieqc.com/registre\n57\n\xff\xfe\n")
        with self.assertRaises(Refused):
            state_sync.read_anchor(self.anchor)

    def test_restore_and_inspect_refuse_under_a_malformed_anchor(self) -> None:
        self.anchor.write_text(self.MALFORMED["no root"], encoding="utf-8")
        gh = FakeGh(assets=self.published())
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        self.assertIn("malformed", str(result))
        self.assertEqual(gh.calls, [])          # refused before touching the release
        self.assertEqual(self.unpacked, [])
        copy = write_archive(self.tmp / NEW_DATED, seals=57)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(state_sync.inspect_local(copy, self.anchor), 1)
        self.assertIn("malformed", out.getvalue())


class TravauxWitness(Base):
    """BLOCKING (r3): the roadworks chain is witnessed like the edition chain."""

    def test_rolling_holding_the_edition_but_not_the_anchored_travaux_head_falls_back(self) -> None:
        rolling = self.archive(seals=57, travaux=10)
        dated = self.archive(seals=57, travaux=12, files=7)
        self.write_anchor(57, travaux=12)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling), NEW_DATED: dated}).vouch_all()
        baseline, out = self.restore(gh)
        self.assertEqual(baseline["source"], NEW_DATED)
        self.assertEqual(baseline["travaux_seq"], 12)
        self.assertIn("rolling asset behind the anchor", out)
        self.assertIn("stops at roadworks seal n° 10 but the git anchor witnessed n° 12", out)

    def test_a_forked_travaux_chain_on_the_rolling_asset_is_refused(self) -> None:
        self.write_anchor(57, travaux=10)
        result, _ = self.restore(FakeGh(assets=self.published(seals=57, travaux=10, travaux_fork=True)))
        self.assertIsInstance(result, Refused)
        self.assertIn("roadworks seal n° 10", str(result))
        self.assertIn("forked", str(result))
        self.assertEqual(self.unpacked, [])

    def test_fallback_refuses_a_dated_copy_lacking_the_anchored_travaux_head(self) -> None:
        rolling = self.archive(seals=57, travaux=12)
        lacking = self.archive(seals=58, travaux=11)
        forked = self.archive(seals=58, travaux=12, travaux_fork=True)
        self.write_anchor(58, travaux=12)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling),
                            NEW_DATED: lacking, OLD_DATED: forked}).vouch_all()
        result, _ = self.restore(gh)
        self.assertIsInstance(result, Refused)
        message = str(result)
        self.assertIn(f"{NEW_DATED} ({len(lacking)} bytes): its chain stops at roadworks seal n° 11", message)
        self.assertIn(f"{OLD_DATED} ({len(forked)} bytes): roadworks seal n° 12 differs", message)
        self.assertIn(f"seal n° 58 ({head_root(58)[:12]}) and roadworks seal n° 12", message)
        self.assertEqual(self.unpacked, [])

    def test_fallback_accepts_a_dated_copy_holding_the_anchored_travaux_head(self) -> None:
        rolling = self.archive(seals=57, travaux=12)
        lacking, holding = self.archive(seals=58, travaux=11), self.archive(seals=58, travaux=12)
        self.write_anchor(58, travaux=12)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling),
                            NEW_DATED: lacking, OLD_DATED: holding}).vouch_all()
        baseline, _ = self.restore(gh)
        self.assertEqual((baseline["source"], baseline["travaux_seq"]), (OLD_DATED, 12))
        self.assertEqual(baseline["travaux_root"], travaux_root(12))

    def test_a_lagging_or_capped_travaux_anchor_is_fine_behind_it_is_not(self) -> None:
        self.write_anchor(57, travaux=9)        # the roads lane sealed 10-14 since
        baseline, _ = self.restore(FakeGh(assets=self.published(seals=57, travaux=14)))
        self.assertEqual(baseline["travaux_seq"], 14)
        # registre caps the roadworks chain: an anchored seal older than every
        # kept one means the archive is ahead, never behind.
        baseline, _ = self.restore(FakeGh(assets=self.published(seals=57, travaux=30, travaux_first=11)))
        self.assertEqual(baseline["outcome"], "restored")
        self.write_anchor(57, travaux=31)
        result, _ = self.restore(FakeGh(assets=self.published(seals=57, travaux=30, travaux_first=11)))
        self.assertIsInstance(result, Refused)
        self.assertIn("roadworks seal n° 30", str(result))


class RecordedWitness(Base):
    """The sidecar's heads witness what the roads lane deployed (the anchor never does)."""

    def test_the_sidecar_round_trips_its_heads(self) -> None:
        facts = state_sync.inspect_archive(write_archive(self.tmp / "a.tar.gz", seals=57, travaux=11))
        text = state_sync.sidecar_text("1" * 64, "2" * 64, witnesses=state_sync.heads(facts))
        self.assertEqual(state_sync.recorded_digests(text), ["1" * 64, "2" * 64])
        self.assertEqual(state_sync.recorded_witnesses(text), [
            Witness(state_sync.EDITION, 57, head_root(57), state_sync.RECORDED),
            Witness(state_sync.TRAVAUX, 11, travaux_root(11), state_sync.RECORDED)])
        self.assertEqual(state_sync.recorded_witnesses(f"{'1' * 64}  {ROLLING}\n"), [])

    def test_an_unreadable_witness_line_is_refused(self) -> None:
        for line in ("# travaux eleven " + "a" * 64, "# edition 57", "# travaux 0 " + "a" * 64,
                     "# edition 57 " + "a" * 64 + "\n# edition 58 " + "b" * 64):
            with self.subTest(line):
                with self.assertRaises(Refused):
                    state_sync.recorded_witnesses(f"{'1' * 64}  {ROLLING}\n{line}\n")
        rolling = self.archive(seals=57)
        bad = sidecar(rolling) + b"# travaux x y\n"
        result, _ = self.restore(FakeGh(assets={ROLLING: rolling, DIGEST: bad}))
        self.assertIsInstance(result, Refused)
        self.assertIn("unreadable witness line", str(result))
        self.assertIn("Manual recovery", str(result))
        self.assertEqual(self.unpacked, [])

    def test_line_two_behind_the_recorded_travaux_head_is_not_restored(self) -> None:
        # The roads persist uploaded its sidecar (travaux 11) and died before the archive.
        old, new = self.archive(seals=57, travaux=10), self.archive(seals=57, travaux=11, files=9)
        facts = state_sync.inspect_archive(write_archive(self.tmp / "n.tar.gz", seals=57, travaux=11, files=9))
        text = state_sync.sidecar_text(sha(new), sha(old), witnesses=state_sync.heads(facts))
        result, _ = self.restore(FakeGh(assets={ROLLING: old, DIGEST: text.encode()}))
        self.assertIsInstance(result, Refused)
        self.assertIn("stops at roadworks seal n° 10 but the last persist", str(result))
        self.assertIn("older than what the last persist recorded", str(result))
        self.assertEqual(self.unpacked, [])


class Selection(Base):
    """(c) the highest head seal wins among qualifying dated copies, then the newest name."""

    def test_a_clock_skewed_name_cannot_pick_a_shorter_chain(self) -> None:
        rolling, short, longer = self.archive(seals=56), self.archive(seals=57), self.archive(seals=58)
        self.write_anchor(57)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling),
                            NEW_DATED: short, OLD_DATED: longer}).vouch_all()
        baseline, _ = self.restore(gh)
        self.assertEqual((baseline["source"], baseline["seal_count"]), (OLD_DATED, 58))
        self.assertFalse((self.work / NEW_DATED).exists())

    def test_travaux_then_name_break_ties(self) -> None:
        rolling = self.archive(seals=56)
        fewer, more = self.archive(seals=58, travaux=3), self.archive(seals=58, travaux=5)
        self.write_anchor(58)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling),
                            NEW_DATED: fewer, OLD_DATED: more}).vouch_all()
        self.assertEqual(self.restore(gh)[0]["source"], OLD_DATED)
        same, twin = self.archive(seals=58, travaux=5), self.archive(seals=58, travaux=5, files=7)
        gh = FakeGh(assets={ROLLING: rolling, DIGEST: sidecar(rolling),
                            NEW_DATED: same, OLD_DATED: twin}).vouch_all()
        self.assertEqual(self.restore(gh)[0]["source"], NEW_DATED)


class CarryForward(PersistBase):
    """(a) the sidecar carries earlier lines forward, so GitHub's digest is never needed."""

    def test_new_first_then_restored_then_carried_bounded(self) -> None:
        new, old = "1" * 64, "2" * 64
        rolling = [[f"{i:064x}", ROLLING] for i in range(100, 130)]
        dated = [[f"{i:064x}", f"state-202610{i - 200 + 1:02d}T061700Z.tar.gz"] for i in range(200, 230)]
        text = state_sync.sidecar_text(new, old, dated=NEW_DATED, carried=rolling + dated)
        pairs = state_sync.recorded_lines(text)
        self.assertEqual(pairs[:3], [(new, ROLLING), (new, NEW_DATED), (old, ROLLING)])
        self.assertEqual(sum(1 for _, n in pairs if n == ROLLING), state_sync.SIDECAR_ROLLING)
        self.assertEqual(sum(1 for _, n in pairs if n != ROLLING), state_sync.SIDECAR_DATED)
        self.assertEqual(pairs[3], (rolling[0][0], ROLLING))   # the newest carried first
        self.assertIn((dated[0][0], dated[0][1]), pairs)
        self.assertNotIn((dated[-1][0], dated[-1][1]), pairs)

    def test_a_restore_from_a_dated_copy_names_it(self) -> None:
        text = state_sync.sidecar_text("1" * 64, "2" * 64, restored_from=OLD_DATED)
        self.assertEqual(state_sync.recorded_lines(text), [("1" * 64, ROLLING), ("2" * 64, OLD_DATED)])

    def test_the_dated_copys_line_survives_a_day_of_roads_persists(self) -> None:
        earlier = self.archive(seals=57, files=7)
        rolling = self.archive(seals=57)
        assets = {ROLLING: rolling, OLD_DATED: earlier, DIGEST: state_sync.sidecar_text(
            sha(rolling), sha(earlier), restored_from=OLD_DATED).encode()}
        for hour in range(24):
            self.work = self.tmp / f"hour{hour}"
            baseline, out = self.restore(FakeGh(assets=assets))
            self.assertIsInstance(baseline, dict, out)
            state_sync.store_io.write_json_atomic(self.work / state_sync.BASELINE, baseline)
            self.packed.update(seals=57, files=8 + hour % 2, filler=1000 + hour)
            gh = FakeGh(assets=assets)
            result, _ = self.persist(gh, dated=False)
            self.assertNotIsInstance(result, Exception)
            assets = gh.assets
        self.assertIn((sha(earlier), OLD_DATED), state_sync.recorded_lines(assets[DIGEST].decode()))
        del assets[ROLLING]   # then a persist dies at the rolling asset's delete
        self.work = self.tmp / "after"
        baseline, _ = self.restore(FakeGh(assets=assets))   # no GitHub digest anywhere
        self.assertEqual(baseline["source"], OLD_DATED)

    def test_persist_writes_the_dated_name_the_carried_lines_and_the_heads(self) -> None:
        carried = [["9" * 64, OLDER_DATED]]
        self.baseline(recorded=carried)
        self.packed.update(seals=58, travaux=4)
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assertNotIsInstance(result, Exception)
        text = gh.assets[DIGEST].decode()
        pairs = state_sync.recorded_lines(text)
        self.assertEqual(pairs[:2], [(result["sha256"], ROLLING), (result["sha256"], NEW_DATED)])
        self.assertIn(("9" * 64, OLDER_DATED), pairs)
        self.assertEqual([(w.chain, w.seq) for w in state_sync.recorded_witnesses(text)],
                         [(state_sync.EDITION, 58), (state_sync.TRAVAUX, 4)])


class PersistTravaux(PersistBase):
    def test_refuses_a_travaux_chain_that_shrank_or_forked(self) -> None:
        self.baseline(archive=self.archive(seals=57, travaux=11))
        for packed, word in (({"travaux": 10}, "shrink"), ({"travaux": 11, "travaux_fork": True}, "fork")):
            with self.subTest(word):
                self.packed.update({"seals": 57, "travaux_fork": False, **packed})
                gh = FakeGh(assets=self.published())
                result, _ = self.persist(gh)
                self.assert_untouched(gh, result)
                self.assertIn("roadworks seal n° 11", str(result))
                self.assertIn(word, str(result))
        self.packed.update(seals=57, travaux=12, travaux_fork=False)
        result, _ = self.persist(FakeGh(assets=self.published()))
        self.assertNotIsInstance(result, Exception)

    def test_the_dated_baseline_and_the_new_dated_copy_leave_no_file_behind(self) -> None:
        self.baseline(source=OLD_DATED)
        (self.work / OLD_DATED).write_bytes(b"restored from a dated copy")
        result, _ = self.persist(FakeGh(assets=self.published()))
        self.assertNotIsInstance(result, Exception)
        self.assertFalse((self.work / OLD_DATED).exists())
        self.assertFalse((self.work / NEW_DATED).exists())
        self.assertTrue((self.work / ROLLING).exists())
        # ... also when the persist is refused
        self.baseline(source=OLD_DATED)
        (self.work / OLD_DATED).write_bytes(b"x")
        self.packed["seals"] = 3
        result, _ = self.persist(FakeGh(assets=self.published()))
        self.assertIsInstance(result, Refused)
        self.assertFalse((self.work / OLD_DATED).exists())


class InspectSidecar(Base):
    def test_inspect_also_checks_the_heads_the_last_persist_recorded(self) -> None:
        copy = write_archive(self.tmp / OLD_DATED, seals=57, travaux=10)
        facts = state_sync.inspect_archive(write_archive(self.tmp / "n.tar.gz", seals=57, travaux=11))
        side = self.tmp / DIGEST
        side.write_text(state_sync.sidecar_text("1" * 64, "", witnesses=state_sync.heads(facts)),
                        encoding="utf-8")
        self.write_anchor(57, travaux=8)
        args = ["inspect", str(copy), "--anchor", str(self.anchor)]
        code, out = quiet(state_sync.main, args)
        self.assertEqual(code, 0, out)
        self.assertIn("roadworks seal n° 8", out)
        code, out = quiet(state_sync.main, [*args, "--sidecar", str(side)])
        self.assertEqual(code, 1)
        self.assertIn("stops at roadworks seal n° 10", out)


if __name__ == "__main__":
    unittest.main()
