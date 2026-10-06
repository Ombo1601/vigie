"""Deploy authority and chain continuity: refresh.py is the only production writer.

House law under test: a published seal is immutable, so a deploy must never
publish a registre that is shorter than, or forked from, what the committed
anchor and the live site already witnessed; a full refresh must seal a new
edition; only GitHub Actions publishes (break-glass is logged). The network is
never evidence: an unreachable site is a warning, a disagreeing one a refusal.
Every fetch and every subprocess is injected; nothing here touches production.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import refresh
import registre


def chain(n: int, salt: str = "a") -> list[dict]:
    """n valid seals hashed by registre.py itself."""
    seals: list[dict] = []
    prev = ""
    for seq in range(1, n + 1):
        record = {"edition": f"2026-10-{seq:02d}T00:00:00+00:00", "salt": salt}
        seal = registre._seal(seq, prev, record)
        seals.append(seal)
        prev = seal["root"]
    return seals


def checkpoint(seals: list[dict]) -> str:
    return registre.checkpoint_text({"seals": seals})


def ok(name: str, seals: list[dict]) -> refresh.Reading:
    return refresh.Reading(name, "ok", seals[-1]["seq"], seals[-1]["root"])


ABSENT_LIVE = refresh.Reading("live", "absent")
DOWN_LIVE = refresh.Reading("live", "unusable", detail="URLError: timed out")


class _Logged(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        patch = mock.patch.multiple(
            refresh,
            LOG_PATH=self.dir / "refresh.log",
            LOCK_PATH=self.dir / "refresh.lock",
            ROADS_SIGNAL_PATH=self.dir / "roads_signal.json",
        )
        patch.start()
        self.addCleanup(patch.stop)

    def logged(self) -> str:
        try:
            return refresh.LOG_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""


class DeployAuthority(_Logged):
    def test_outside_github_actions_a_deploy_is_refused(self) -> None:
        reason = refresh.deploy_refusal({})
        self.assertIn("VIGIE_ALLOW_LOCAL_DEPLOY", reason)
        steps: list[str] = []
        with mock.patch.dict(refresh.os.environ, {}, clear=True), \
                mock.patch.object(refresh, "run_step",
                                  side_effect=lambda name, *a, **k: steps.append(name)):
            self.assertEqual(refresh.main([]), 2)
            self.assertEqual(refresh.main(["--roads-only"]), 2)
        self.assertEqual(steps, [])  # refused before any collection
        self.assertIn("deploy refused", self.logged())
        self.assertIn("VIGIE_ALLOW_LOCAL_DEPLOY", self.logged())

    def test_github_actions_and_logged_break_glass_may_deploy(self) -> None:
        self.assertEqual(refresh.deploy_refusal({"GITHUB_ACTIONS": "true"}), "")
        self.assertEqual(refresh.deploy_refusal({"VIGIE_ALLOW_LOCAL_DEPLOY": "1"}), "")
        self.assertIn("break-glass", self.logged())
        self.assertNotEqual(refresh.deploy_refusal({"VIGIE_ALLOW_LOCAL_DEPLOY": "yes"}), "")

    def test_no_deploy_runs_anywhere(self) -> None:
        steps: list[str] = []
        with mock.patch.dict(refresh.os.environ, {}, clear=True), \
                mock.patch.object(refresh, "acquire_lock", return_value=True), \
                mock.patch.object(refresh, "run_step",
                                  side_effect=lambda name, *a, **k: steps.append(name)):
            self.assertEqual(refresh.main(["--no-deploy"]), 0)
        self.assertEqual(steps, ["pipeline", "verify"])


class CheckpointParsing(unittest.TestCase):
    def test_round_trips_registre_output(self) -> None:
        seals = chain(3)
        self.assertEqual(refresh.parse_checkpoint(checkpoint(seals)),
                         (3, seals[-1]["root"]))
        self.assertEqual(refresh.parse_checkpoint(checkpoint([])), (0, ""))

    def test_the_committed_anchor_parses(self) -> None:
        # The workflow's anchor step reads lines 2 and 3 with sed: same contract.
        path = harness.ROOT / "anchors" / "checkpoint.txt"
        if not path.is_file():
            self.skipTest("no committed anchor")
        reading = refresh.read_file_checkpoint("anchor", path)
        self.assertEqual(reading.status, "ok")
        self.assertGreater(reading.seq, 0)

    def test_a_leading_bom_is_tolerated(self) -> None:
        seals = chain(3)
        self.assertEqual(refresh.parse_checkpoint("﻿" + checkpoint(seals)),
                         (3, seals[-1]["root"]))

    def test_garbage_is_not_a_checkpoint(self) -> None:
        for text in (None, "", "<html>404</html>", "vigieqc.com/registre\nx\nroot\n",
                     "vigieqc.com/registre\n3\nnot-hex\n", "elsewhere\n3\n" + "a" * 64 + "\n"):
            self.assertIsNone(refresh.parse_checkpoint(text))


class ContinuityVerdict(unittest.TestCase):
    def verdict(self, seals, anchor, live=None, *, full_run=True, staged=None):
        staged = staged or (ok("staged", seals) if seals else refresh.Reading("staged", "ok"))
        live = live if live is not None else anchor
        return refresh.continuity_verdict(seals, staged, anchor, live, full_run=full_run)

    def test_advance_is_accepted(self) -> None:
        seals = chain(58)
        refusals, warnings = self.verdict(seals, ok("anchor", seals[:57]))
        self.assertEqual((refusals, warnings), ([], []))

    def test_genesis_reset_is_refused_with_both_numbers(self) -> None:
        fresh = chain(1, salt="empty-data")
        refusals, _ = self.verdict(fresh, ok("anchor", chain(57)))
        text = " ".join(refusals)
        self.assertIn("genesis reset", text)
        self.assertIn("seq 1", text)
        self.assertIn("seq 57", text)

    def test_shrink_is_refused(self) -> None:
        seals = chain(7)
        refusals, _ = self.verdict(seals[:5], ok("anchor", seals))
        self.assertTrue(any("shrink" in r and "seq 5" in r and "seq 7" in r for r in refusals))

    def test_equal_seq_with_another_root_is_refused(self) -> None:
        refusals, _ = self.verdict(chain(5, "b"), ok("anchor", chain(5, "a")),
                                   full_run=False)
        self.assertTrue(any(r.startswith("fork") for r in refusals))

    def test_a_longer_fork_is_refused(self) -> None:
        refusals, _ = self.verdict(chain(6, "b"), ok("anchor", chain(5, "a")))
        self.assertTrue(any("not in the local chain" in r for r in refusals))

    def test_first_run_needs_no_anchor_and_no_live(self) -> None:
        refusals, warnings = self.verdict(chain(1), refresh.Reading("anchor", "absent"),
                                          ABSENT_LIVE)
        self.assertEqual(refusals, [])
        self.assertIn("first run", " ".join(warnings))

    def test_missing_anchor_is_not_a_first_run_when_live_exists_or_is_unknown(self) -> None:
        absent = refresh.Reading("anchor", "absent")
        refusals, _ = self.verdict(chain(1), absent, ok("live", chain(57)))
        self.assertTrue(any("anchor checkpoint missing" in r for r in refusals))
        refusals, _ = self.verdict(chain(1), absent, DOWN_LIVE)
        self.assertTrue(any("cannot establish a first run" in r for r in refusals))

    def test_unreachable_live_is_a_warning_and_the_anchor_decides(self) -> None:
        seals = chain(58)
        refusals, warnings = self.verdict(seals, ok("anchor", seals[:57]), DOWN_LIVE)
        self.assertEqual(refusals, [])
        self.assertIn("relying on the anchor", " ".join(warnings))
        refusals, _ = self.verdict(chain(1), ok("anchor", seals[:57]), DOWN_LIVE)
        self.assertTrue(refusals)

    def test_live_ahead_of_a_missed_anchor_still_counts(self) -> None:
        seals = chain(59)
        anchor = ok("anchor", seals[:57])
        self.assertEqual(self.verdict(seals, anchor, ok("live", seals[:58]))[0], [])
        # Stalled at the live seq even though the anchor lags.
        refusals, _ = self.verdict(seals[:58], anchor, ok("live", seals[:58]))
        self.assertTrue(any("stalled" in r for r in refusals))

    def test_roads_lane_needs_no_advance_but_a_full_run_does(self) -> None:
        seals = chain(57)
        anchor = ok("anchor", seals)
        self.assertEqual(self.verdict(seals, anchor, full_run=False)[0], [])
        refusals, _ = self.verdict(seals, anchor, full_run=True)
        self.assertTrue(any("stalled" in r for r in refusals))
        refusals, _ = self.verdict(seals[:56], anchor, full_run=False)
        self.assertTrue(any("shrink" in r for r in refusals))

    def test_staged_checkpoint_must_be_the_state_tip(self) -> None:
        seals = chain(58)
        stale = ok("staged", seals[:57])  # the registre emit failed: old file staged
        refusals, _ = self.verdict(seals, ok("anchor", seals[:57]), staged=stale)
        self.assertTrue(any("did the registre emit fail" in r for r in refusals))
        missing = refresh.Reading("staged", "absent")
        refusals, _ = self.verdict(seals, ok("anchor", seals[:57]), staged=missing)
        self.assertTrue(any("staged checkpoint absent" in r for r in refusals))

    def test_a_tampered_local_chain_is_refused(self) -> None:
        seals = chain(58)
        seals[10] = dict(seals[10], record={"edition": seals[10]["edition"], "salt": "x"})
        refusals, _ = self.verdict(seals, ok("anchor", seals[:57]))
        self.assertTrue(any("does not verify" in r for r in refusals))


class ChainGuardIO(_Logged):
    """chain_guard reads real files in temp dirs and an injected fetcher."""

    def write(self, local: list[dict], anchor: list[dict] | None,
              staged: list[dict] | None = None) -> dict:
        state = registre.empty_state()
        state["seals"] = local
        (self.dir / "registre.json").write_text(json.dumps(state), encoding="utf-8")
        (self.dir / "staged.txt").write_text(
            checkpoint(local if staged is None else staged), encoding="utf-8")
        if anchor is not None:
            (self.dir / "anchor.txt").write_text(checkpoint(anchor), encoding="utf-8")
        return {
            "state_path": self.dir / "registre.json",
            "anchor_path": self.dir / "anchor.txt",
            "staged_path": self.dir / "staged.txt",
        }

    def test_corrupt_state_is_refused_against_the_anchor(self) -> None:
        paths = self.write(chain(57), chain(57))
        paths["state_path"].write_text("{not json", encoding="utf-8")  # load_state -> empty
        with self.assertRaises(refresh.GuardRefusal) as ctx:
            refresh.chain_guard(full_run=False, fetcher=lambda u, t: None, env={}, **paths)
        self.assertIn("seq 0", str(ctx.exception))
        self.assertIn("seq 57", str(ctx.exception))
        self.assertIn("VIGIE_SKIP_CHAIN_GUARD", str(ctx.exception))

    def test_live_fetch_error_is_only_a_warning(self) -> None:
        seals = chain(58)
        paths = self.write(seals, seals[:57])

        def down(url, timeout):
            raise urllib.error.URLError("timed out")

        tip = refresh.chain_guard(full_run=True, fetcher=down, env={}, **paths)
        self.assertEqual(tip, (58, seals[-1]["root"]))
        self.assertIn("WARN chain guard: live checkpoint unusable", self.logged())

    def test_the_live_url_is_the_registre_checkpoint(self) -> None:
        seals = chain(58)
        paths = self.write(seals, seals[:57])
        seen: list[tuple[str, float]] = []

        def live(url, timeout):
            seen.append((url, timeout))
            return checkpoint(seals[:57])

        refresh.chain_guard(full_run=True, fetcher=live, env={}, **paths)
        self.assertEqual(seen, [("https://vigieqc.com/registre/checkpoint.txt",
                                 refresh.LIVE_TIMEOUT)])

    def test_break_glass_publishes_loudly(self) -> None:
        fresh = chain(1, salt="empty")
        paths = self.write(fresh, chain(57))
        tip = refresh.chain_guard(full_run=True, fetcher=lambda u, t: None,
                                  env={"VIGIE_SKIP_CHAIN_GUARD": "1"}, **paths)
        self.assertEqual(tip[0], 1)
        self.assertIn("WARN !!! VIGIE_SKIP_CHAIN_GUARD=1 overrides", self.logged())

    def test_break_glass_expects_the_staged_checkpoint_not_the_state_tip(self) -> None:
        local, staged = chain(1, salt="empty"), chain(5, salt="shipped")
        paths = self.write(local, chain(57), staged=staged)
        tip = refresh.chain_guard(full_run=True, fetcher=lambda u, t: None,
                                  env={"VIGIE_SKIP_CHAIN_GUARD": "1"}, **paths)
        self.assertEqual(tip, (5, staged[-1]["root"]))

    def test_a_refused_run_never_reaches_link_or_deploy(self) -> None:
        paths = self.write(chain(1, salt="empty"), chain(57))
        steps: list[str] = []
        with mock.patch.dict(refresh.os.environ, {"GITHUB_ACTIONS": "true"}, clear=True), \
                mock.patch.multiple(refresh, ANCHOR_PATH=paths["anchor_path"],
                                    STAGED_CHECKPOINT=paths["staged_path"]), \
                mock.patch.object(registre, "STATE", paths["state_path"]), \
                mock.patch.object(refresh, "fetch_text", return_value=None), \
                mock.patch.object(refresh, "acquire_lock", return_value=True), \
                mock.patch.object(refresh, "write_roads_signal") as written, \
                mock.patch.object(refresh.shutil, "which", return_value="vercel"), \
                mock.patch.object(refresh, "run_step",
                                  side_effect=lambda name, *a, **k: steps.append(name)):
            code = refresh.main([])
        self.assertEqual(code, 1)
        self.assertEqual(steps, ["pipeline", "verify"])
        written.assert_not_called()
        self.assertIn("FAIL chain guard refused", self.logged())

    def test_an_accepted_run_deploys_then_checks_live(self) -> None:
        seals = chain(58)
        paths = self.write(seals, seals[:57])
        served = iter([checkpoint(seals[:57]), checkpoint(seals)])  # guard, then live check
        steps: list[str] = []
        with mock.patch.dict(refresh.os.environ, {"GITHUB_ACTIONS": "true"}, clear=True), \
                mock.patch.multiple(refresh, ANCHOR_PATH=paths["anchor_path"],
                                    STAGED_CHECKPOINT=paths["staged_path"]), \
                mock.patch.object(registre, "STATE", paths["state_path"]), \
                mock.patch.object(refresh, "fetch_text", side_effect=lambda u, t: next(served)), \
                mock.patch.object(refresh, "acquire_lock", return_value=True), \
                mock.patch.object(refresh, "write_roads_signal"), \
                mock.patch.object(refresh, "ping_indexnow"), \
                mock.patch.object(refresh.shutil, "which", return_value="vercel"), \
                mock.patch.object(refresh, "run_step",
                                  side_effect=lambda name, *a, **k: steps.append(name)):
            code = refresh.main([])
        self.assertEqual(code, 0)
        self.assertEqual(steps, ["pipeline", "verify", "link", "deploy"])
        self.assertIn("live check: OK seq 58", self.logged())


class PostDeployCheck(_Logged):
    def test_a_persistent_mismatch_fails_after_the_retries(self) -> None:
        seals = chain(58)
        naps: list[float] = []
        with self.assertRaises(RuntimeError) as ctx:
            refresh.verify_live((58, seals[-1]["root"]),
                                fetcher=lambda u, t: checkpoint(seals[:57]),
                                attempts=3, spacing=7, sleep=naps.append)
        self.assertEqual(naps, [7, 7])
        self.assertIn("seq 57", str(ctx.exception))
        self.assertIn("deployed seq 58", str(ctx.exception))

    def test_propagation_lag_then_match_passes(self) -> None:
        seals = chain(58)
        served = iter([checkpoint(seals[:57]), checkpoint(seals)])
        refresh.verify_live((58, seals[-1]["root"]), fetcher=lambda u, t: next(served),
                            attempts=3, spacing=0, sleep=lambda s: None)
        self.assertIn("live check: OK", self.logged())

    def test_a_network_error_is_a_warning(self) -> None:
        def down(url, timeout):
            raise urllib.error.URLError("unreachable")

        refresh.verify_live((58, "a" * 64), fetcher=down, attempts=2, sleep=lambda s: None)
        self.assertIn("WARN live check: production unreachable", self.logged())

    def test_a_missing_checkpoint_after_deploy_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            refresh.verify_live((58, "a" * 64), fetcher=lambda u, t: None,
                                attempts=2, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
