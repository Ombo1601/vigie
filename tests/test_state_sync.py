"""State restore/persist (state_sync) — the only copy of Le Registre, moved fail closed.

House law under test: only a definitive "no release" is a fresh start; every
other failure is retried, then fails the job before a genesis can be sealed; a
download must match its recorded digest and the public git anchor; the chain
never shrinks or forks on upload; a dated copy exists before the rolling asset
is clobbered; pruning never touches the rolling asset and never fails a run.
Hermetic: `gh` is a scripted fake, nothing leaves the process.
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

import state_pack
import state_sync
from state_sync import DIGEST, REGISTRE_MEMBER, ROLLING, Refused, Result

REPO, TAG = "owner/vigie-state", "state"
NOW = datetime(2026, 10, 20, 6, 17, 0, tzinfo=timezone.utc)
NEW_DATED = "state-20261020T061700Z.tar.gz"


def chain_doc(n: int, *, fork: bool = False) -> dict:
    seals, prev = [], ""
    for seq in range(1, n + 1):
        root = hashlib.sha256(f"{'fork' if fork else 'main'}|{seq}|{prev}".encode()).hexdigest()
        seals.append({"seq": seq, "edition": f"e{seq}", "prev": prev, "leaf": "x",
                      "root": root, "record": {"edition": f"e{seq}"}})
        prev = root
    return {"method": "registre-v1 sha256-chain", "seals": seals}


def head_root(n: int) -> str:
    return chain_doc(n)["seals"][-1]["root"]


def write_archive(path: Path, *, seals: int = 57, files: int = 8, filler: int = 1000,
                  registre: bool = True, fork: bool = False) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        def add(name: str, payload: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        if registre:
            add(REGISTRE_MEMBER, json.dumps(chain_doc(seals, fork=fork)).encode("utf-8"))
        for i in range(files):
            add(f"data/raw/src{i}/snap.xml", bytes([65 + i % 26]) * filler)
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


class FakeGh:
    """A scripted `gh` against one in-memory release. Records every call."""

    def __init__(self, *, release: bool = True, assets: dict[str, bytes] | None = None) -> None:
        self.release = release
        self.assets: dict[str, bytes] = dict(assets or {})
        self.server_digest: dict[str, str] = {}
        self.short_upload: set[str] = set()     # store these truncated (a bad upload)
        self.short_download: set[str] = set()   # deliver these truncated
        self.queued: dict[str, list[Result]] = {}
        self.calls: list[list[str]] = []

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

    def __call__(self, args: list[str], timeout: float) -> Result:
        self.calls.append(list(args))
        key = self.key(args)
        if self.queued.get(key):
            return self.queued[key].pop(0)
        if key == "api:repo":
            return http_ok({"full_name": REPO, "private": True})
        if key == "api:release":
            if not self.release:
                return http_error(404, "Not Found")
            return http_ok({"tag_name": TAG, "assets": [
                {"name": name, "size": len(body), "state": "uploaded",
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
            self.assets[path.name] = body[:-1] if path.name in self.short_upload else body
            return Result(0)
        if key.startswith("delete:"):
            return Result(0) if self.assets.pop(key.split(":", 1)[1], None) is not None \
                else Result(1, "", "asset not found")
        if key == "create":
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

    def published(self, **kwargs) -> dict[str, bytes]:
        """A release holding a rolling archive and its recorded digest."""
        body = write_archive(self.tmp / "published.tar.gz", **kwargs).read_bytes()
        return {ROLLING: body, DIGEST: f"{hashlib.sha256(body).hexdigest()}  {ROLLING}\n".encode()}

    def unpack(self, path: Path) -> int:
        self.unpacked.append(Path(path))
        return 9

    def restore(self, gh: FakeGh):
        return quiet(state_sync.restore, REPO, TAG, self.work, gh=gh,
                     sleep=self.slept.append, unpack=self.unpack, anchor=self.anchor)

    def write_anchor(self, seq: int, root: str) -> None:
        self.anchor.write_text(f"vigieqc.com/registre\n{seq}\n{root}\n\nedition e{seq}\n", encoding="utf-8")


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
        baseline, _ = self.restore(FakeGh(assets=assets))
        self.assertEqual(baseline["outcome"], "restored")
        self.assertEqual(baseline["seal_count"], 57)
        self.assertEqual(baseline["head_seq"], 57)
        self.assertEqual(baseline["head_root"], head_root(57))
        self.assertEqual(baseline["sha256"], hashlib.sha256(assets[ROLLING]).hexdigest())
        self.assertEqual(baseline["members"], 9)
        self.assertEqual(self.unpacked, [self.work / ROLLING])

    def test_digest_mismatch_fails_before_unpack(self) -> None:
        assets = self.published()
        assets[DIGEST] = f"{'0' * 64}  {ROLLING}\n".encode()
        result, _ = self.restore(FakeGh(assets=assets))
        self.assertIsInstance(result, Refused)
        self.assertIn("recorded", str(result))
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

    def test_digest_or_dated_copies_without_rolling_is_an_interrupted_persist(self) -> None:
        for leftovers in ({DIGEST: b"x"}, {"state-20261001T061700Z.tar.gz": b"x"}):
            with self.subTest(sorted(leftovers)):
                result, _ = self.restore(FakeGh(assets=leftovers))
                self.assertIsInstance(result, Refused)
                self.assertIn("interrupted persist", str(result))

    def test_archive_without_registre_is_refused(self) -> None:
        result, _ = self.restore(FakeGh(assets=self.published(registre=False)))
        self.assertIsInstance(result, Refused)
        self.assertEqual(self.unpacked, [])

    def test_fresh_start_is_refused_while_the_git_anchor_names_a_seal(self) -> None:
        self.write_anchor(57, head_root(57))
        result, _ = self.restore(FakeGh(release=False))
        self.assertIsInstance(result, Refused)
        self.assertIn("fork", str(result))

    def test_state_older_than_the_anchor_is_refused(self) -> None:
        self.write_anchor(57, head_root(57))
        result, _ = self.restore(FakeGh(assets=self.published(seals=5)))
        self.assertIsInstance(result, Refused)
        self.assertIn("older than what was published", str(result))
        self.assertEqual(self.unpacked, [])

    def test_forked_state_is_refused_and_a_matching_anchor_passes(self) -> None:
        self.write_anchor(57, head_root(57))
        result, _ = self.restore(FakeGh(assets=self.published(seals=57, fork=True)))
        self.assertIsInstance(result, Refused)
        self.assertIn("forked", str(result))
        self.write_anchor(56, head_root(56))   # the anchor may lag the state
        baseline, _ = self.restore(FakeGh(assets=self.published(seals=57)))
        self.assertEqual(baseline["outcome"], "restored")


class Persist(Base):
    def setUp(self) -> None:
        super().setUp()
        self.packed = {"seals": 58, "files": 8, "filler": 1000, "registre": True, "fork": False}

    def pack(self, out: Path) -> int:
        write_archive(Path(out), **self.packed)
        return 1

    def baseline(self, outcome: str = "restored", **extra) -> None:
        facts = state_sync.inspect_archive(write_archive(self.tmp / "restored.tar.gz", seals=57))
        doc = {"outcome": outcome, "release": True, "seal_count": facts["seal_count"],
               "head_seq": facts["head_seq"], "head_root": facts["head_root"],
               "members": facts["members"], "bytes": facts["bytes"], **extra}
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / state_sync.BASELINE).write_text(json.dumps(doc), encoding="utf-8")

    def persist(self, gh: FakeGh, *, dated: bool = True, keep: int = 12, environ=None):
        return quiet(state_sync.persist, REPO, TAG, self.work, dated=dated, keep=keep, gh=gh,
                     sleep=self.slept.append, now=lambda: NOW, pack=self.pack,
                     environ=environ or {})

    def assert_untouched(self, gh: FakeGh, result) -> None:
        self.assertIsInstance(result, Refused)
        self.assertFalse(any(k.startswith(("upload:", "delete:")) for k in gh.keys()))

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

    def test_dated_copy_is_uploaded_and_confirmed_before_the_rolling_asset(self) -> None:
        self.baseline()
        gh = FakeGh(assets=self.published())
        result, _ = self.persist(gh)
        self.assertNotIsInstance(result, Exception)
        keys = gh.keys()
        dated, rolling, digest = (keys.index(f"upload:{NEW_DATED}"),
                                  keys.index(f"upload:{ROLLING}"), keys.index(f"upload:{DIGEST}"))
        self.assertLess(dated, rolling)
        self.assertLess(rolling, digest)
        self.assertIn("api:release", keys[dated:rolling])   # confirmed in between
        upload = next(c for c in gh.calls if FakeGh.key(c) == f"upload:{ROLLING}")
        self.assertIn("--clobber", upload)
        body = gh.assets[ROLLING]
        self.assertEqual(gh.assets[NEW_DATED], body)
        self.assertEqual(gh.assets[DIGEST].decode().split()[0], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["seal_count"], 58)

    def test_unconfirmed_dated_upload_never_touches_the_rolling_asset(self) -> None:
        self.baseline()
        assets = self.published()
        gh = FakeGh(assets=assets)
        gh.short_upload.add(NEW_DATED)
        result, _ = self.persist(gh)
        self.assertIsInstance(result, Refused)
        self.assertNotIn(f"upload:{ROLLING}", gh.keys())
        self.assertEqual(gh.assets[ROLLING], assets[ROLLING])

    def test_server_digest_disagreement_is_refused(self) -> None:
        self.baseline()
        gh = FakeGh(assets=self.published())
        gh.server_digest[NEW_DATED] = "sha256:" + "f" * 64
        result, _ = self.persist(gh)
        self.assertIsInstance(result, Refused)
        self.assertNotIn(f"upload:{ROLLING}", gh.keys())

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

    def test_roads_lane_persists_only_the_rolling_asset_through_the_same_checks(self) -> None:
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
                      members=0, bytes=0)
        self.packed["seals"] = 1
        gh = FakeGh(release=False)
        result, _ = self.persist(gh)
        self.assertNotIsInstance(result, Exception)
        keys = gh.keys()
        self.assertLess(keys.index("create"), keys.index(f"upload:{NEW_DATED}"))
        self.assertIn(ROLLING, gh.assets)


class CommandLine(Base):
    def run_main(self, argv: list[str], gh: FakeGh, environ: dict) -> int:
        code, _ = quiet(state_sync.main, argv, gh=gh, sleep=self.slept.append,
                        now=lambda: NOW, environ=environ)
        return code

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
        code = self.run_main(["restore", *self.args], FakeGh(assets=assets), self.env)
        self.assertEqual(code, 0)
        out = self.outputs(self.github_output)
        self.assertEqual(out["outcome"], "restored")
        self.assertEqual(out["seal_count"], "57")
        self.assertEqual(out["head_root"], head_root(57))
        self.assertEqual(out["sha256"], hashlib.sha256(assets[ROLLING]).hexdigest())
        self.assertEqual(out["members"], "9")
        self.assertGreater(int(out["bytes"]), 0)
        self.assertEqual(set(out), set(state_sync.OUTPUT_KEYS))
        self.assertEqual(state_sync.read_baseline(self.work)["outcome"], "restored")

    def test_fresh_outcome_is_exit_zero(self) -> None:
        code = self.run_main(["restore", *self.args], FakeGh(release=False), self.env)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs(self.github_output)["outcome"], "fresh")

    def test_failed_restore_exits_non_zero_and_persist_then_refuses(self) -> None:
        gh = FakeGh(assets=self.published())
        gh.script("api:release", *[TRANSIENT["401"]] * state_sync.ATTEMPTS)
        self.assertEqual(self.run_main(["restore", *self.args], gh, self.env), 1)
        self.assertEqual(self.outputs(self.github_output)["outcome"], "failed")
        self.assertEqual(state_sync.read_baseline(self.work)["outcome"], "failed")
        packed: list[Path] = []
        with mock.patch.object(state_pack, "pack", side_effect=packed.append):
            code = self.run_main(["persist", *self.args, "--dated"], gh, self.env)
        self.assertEqual(code, 1)
        self.assertEqual(packed, [])
        self.assertFalse(any(k.startswith(("upload:", "delete:")) for k in gh.keys()))

    def test_unexpected_crash_is_a_failed_outcome(self) -> None:
        gh = FakeGh(assets=self.published())
        with mock.patch.object(state_sync, "inspect_archive", side_effect=tarfile.ReadError("bad")):
            self.assertEqual(self.run_main(["restore", *self.args], gh, self.env), 1)
        self.assertEqual(self.outputs(self.github_output)["outcome"], "failed")


if __name__ == "__main__":
    unittest.main()
