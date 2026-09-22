"""Feed health ledger: a dying source becomes a fact before coverage degrades.

Locks feed-health-v1: timeline compilation from the per-run metas in
data/raw, failure/parse streaks, fixed-threshold statuses, yield trends,
not-modified rates, determinism (no wall clock) and fail-soft behaviour.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import feed_health as fh


def write_meta(raw: Path, src: str, stamp: str, *, ok: bool = True, items: int | None = 10,
               feature_count: int | None = None, error: str | None = None,
               parse_error: str | None = None, not_modified: bool = False,
               digest: str = "abcdef012345", content: str | None = None) -> Path:
    d = raw / src
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{stamp}_{digest}.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
        return path
    doc = {"ok": ok, "error": error, "parse_error": parse_error,
           "fetched_at": stamp, "bytes": 100, "not_modified": not_modified}
    if items is not None:
        doc["item_count"] = items
    if feature_count is not None:
        doc["feature_count"] = feature_count
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class CompileHealth(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raw = Path(self._tmp.name) / "raw"
        self.raw.mkdir()
        self.out = Path(self._tmp.name) / "feed_health.json"

    def compile(self) -> dict:
        return fh.compile_health(raw_dir=self.raw, out_path=self.out)

    def test_healthy_source(self) -> None:
        for i, stamp in enumerate(("20260918T000000Z", "20260918T060000Z", "20260919T000000Z")):
            write_meta(self.raw, "src", stamp, items=10 + i, digest=f"{i:012d}")
        doc = self.compile()
        src = doc["sources"]["src"]
        self.assertEqual(src["status"], "healthy")
        self.assertEqual(src["consecutive_failures"], 0)
        self.assertEqual(src["item_yields"], [10, 11, 12])
        self.assertEqual(src["hours_since_ok"], 0.0)
        self.assertEqual(doc["compiled_at"], "2026-09-19T00:00:00+00:00")
        self.assertEqual(doc["attention"], [])

    def test_failure_streak_statuses(self) -> None:
        write_meta(self.raw, "two", "20260919T000000Z", ok=True, digest="000000000000")
        write_meta(self.raw, "two", "20260919T060000Z", ok=False, error="HTTP 500", digest="000000000001")
        write_meta(self.raw, "two", "20260919T120000Z", ok=False, error="HTTP 500", digest="000000000002")
        write_meta(self.raw, "three", "20260919T000000Z", ok=True, digest="000000000003")
        for i in range(3):
            write_meta(self.raw, "three", f"20260919T0{i + 1}0000Z", ok=False,
                       error="timeout", digest=f"00000000000{i + 4}")
        doc = self.compile()
        self.assertEqual(doc["sources"]["two"]["status"], "degraded")
        self.assertEqual(doc["sources"]["two"]["consecutive_failures"], 2)
        self.assertEqual(doc["sources"]["three"]["status"], "failing")
        self.assertEqual(doc["sources"]["three"]["consecutive_failures"], 3)
        self.assertEqual(doc["status_counts"]["failing"], 1)

    def test_recovery_clears_the_streak(self) -> None:
        write_meta(self.raw, "src", "20260918T000000Z", ok=False, error="down", digest="000000000000")
        write_meta(self.raw, "src", "20260919T000000Z", ok=True, digest="000000000001")
        doc = self.compile()
        self.assertEqual(doc["sources"]["src"]["status"], "healthy")
        self.assertEqual(doc["sources"]["src"]["consecutive_failures"], 0)

    def test_dead_after_72h_silence(self) -> None:
        write_meta(self.raw, "quiet", "20260915T000000Z", ok=True, digest="000000000000")
        write_meta(self.raw, "active", "20260919T000000Z", ok=True, digest="000000000001")
        doc = self.compile()
        self.assertEqual(doc["sources"]["quiet"]["status"], "dead")
        self.assertEqual(doc["sources"]["quiet"]["hours_since_ok"], 96.0)
        self.assertEqual(doc["sources"]["active"]["status"], "healthy")
        self.assertTrue(any("quiet" in line for line in doc["attention"]))

    def test_never_ok_is_degraded_until_the_dry_spell_is_old(self) -> None:
        write_meta(self.raw, "born-bad", "20260919T000000Z", ok=False, error="HTTP 404", digest="000000000000")
        write_meta(self.raw, "healthy", "20260919T000000Z", ok=True, digest="000000000001")
        doc = self.compile()
        src = doc["sources"]["born-bad"]
        # "dead" means no success for three days; one recent failure is not that.
        self.assertEqual(src["status"], "degraded")
        self.assertIsNone(src["last_ok_at"])
        self.assertEqual(src["last_error"], "HTTP 404")
        self.assertIn("born-bad: degraded", doc["attention"][0])

    def test_never_ok_spanning_three_days_is_dead(self) -> None:
        write_meta(self.raw, "born-bad", "20260915T000000Z", ok=False, error="HTTP 404", digest="000000000000")
        write_meta(self.raw, "born-bad", "20260919T000000Z", ok=False, error="HTTP 404", digest="000000000001")
        write_meta(self.raw, "healthy", "20260919T000000Z", ok=True, digest="000000000002")
        doc = self.compile()
        src = doc["sources"]["born-bad"]
        self.assertEqual(src["status"], "dead")
        self.assertIn("born-bad: dead", doc["attention"][0])

    def test_all_future_stamps_report_a_clock_anomaly(self) -> None:
        write_meta(self.raw, "src", "20300101T000000Z", ok=False, error="HTTP 500", digest="000000000000")
        doc = self.compile()
        self.assertTrue(any("future" in line for line in doc["attention"]))
        self.assertIsNone(doc["sources"]["src"]["hours_since_ok"])

    def test_falling_yield_is_degraded(self) -> None:
        stamps = [f"2026091{i}T000000Z" for i in range(8)]
        yields = [20, 20, 20, 20, 2, 2, 2, 2]
        for stamp, items in zip(stamps, yields, strict=True):
            write_meta(self.raw, "fading", stamp, items=items, digest=stamp[-8:-1] + "00000")
        doc = self.compile()
        src = doc["sources"]["fading"]
        self.assertEqual(src["yield_trend"], "falling")
        self.assertEqual(src["status"], "degraded")

    def test_parse_error_streak_degrades(self) -> None:
        write_meta(self.raw, "src", "20260918T000000Z", ok=True, digest="000000000000")
        write_meta(self.raw, "src", "20260919T000000Z", ok=False,
                   parse_error="ParseError: syntax", digest="000000000001")
        doc = self.compile()
        src = doc["sources"]["src"]
        self.assertEqual(src["status"], "degraded")
        self.assertEqual(src["consecutive_parse_errors"], 1)

    def test_not_modified_rate_counted(self) -> None:
        write_meta(self.raw, "src", "20260918T000000Z", not_modified=True, digest="000000000000")
        write_meta(self.raw, "src", "20260918T060000Z", not_modified=True, digest="000000000000")
        write_meta(self.raw, "src", "20260919T000000Z", not_modified=False, digest="000000000001")
        doc = self.compile()
        self.assertEqual(doc["sources"]["src"]["not_modified_recent"], 2)

    def test_wzdx_feature_count_used_as_yield(self) -> None:
        write_meta(self.raw, "wzdx-quebec", "20260919T000000Z", items=None, feature_count=875)
        doc = self.compile()
        self.assertEqual(doc["sources"]["wzdx-quebec"]["item_yields"], [875])

    def test_corrupt_meta_is_a_diagnosed_run_not_a_crash(self) -> None:
        write_meta(self.raw, "src", "20260918T000000Z", ok=True, digest="000000000000")
        write_meta(self.raw, "src", "20260919T000000Z", content="garbage", digest="000000000001")
        doc = self.compile()
        src = doc["sources"]["src"]
        self.assertEqual(src["consecutive_failures"], 1)
        self.assertEqual(src["last_error"], "unreadable meta")

    def test_private_and_fileless_dirs_are_skipped(self) -> None:
        (self.raw / "_bodies").mkdir()
        (self.raw / "_bodies" / "20260919T000000Z_x.json").write_text("{}", encoding="utf-8")
        (self.raw / "empty-dir").mkdir()
        write_meta(self.raw, "src", "20260919T000000Z")
        doc = self.compile()
        self.assertEqual(sorted(doc["sources"]), ["src"])

    def test_disk_bytes_measured(self) -> None:
        write_meta(self.raw, "src", "20260919T000000Z")
        (self.raw / "src" / "20260919T000000Z_abcdef012345.xml").write_bytes(b"x" * 500)
        doc = self.compile()
        self.assertGreaterEqual(doc["sources"]["src"]["disk_bytes"], 500)

    def test_recompile_is_byte_identical(self) -> None:
        write_meta(self.raw, "src", "20260919T000000Z")
        self.compile()
        first = self.out.read_bytes()
        self.compile()
        self.assertEqual(self.out.read_bytes(), first)

    def test_empty_raw_dir_is_an_empty_ledger(self) -> None:
        doc = self.compile()
        self.assertEqual(doc["source_count"], 0)
        self.assertIsNone(doc["compiled_at"])
        self.assertEqual(doc["attention"], [])

    def test_missing_raw_dir_is_an_empty_ledger(self) -> None:
        doc = fh.compile_health(raw_dir=self.raw / "nope", out_path=self.out)
        self.assertEqual(doc["source_count"], 0)

    def test_unwritable_out_is_not_fatal(self) -> None:
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("file", encoding="utf-8")
        write_meta(self.raw, "src", "20260919T000000Z")
        doc = fh.compile_health(raw_dir=self.raw, out_path=blocker / "feed_health.json")
        self.assertEqual(doc["source_count"], 1)


class MainFailSoft(unittest.TestCase):
    def test_main_always_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            write_meta(raw, "src", "20260919T000000Z")
            with mock.patch.object(fh, "RAW_DIR", raw), \
                    mock.patch.object(fh, "OUT", Path(tmp) / "feed_health.json"):
                self.assertEqual(fh.main([]), 0)
        with mock.patch.object(fh, "compile_health", side_effect=OSError("disk on fire")):
            self.assertEqual(fh.main([]), 0)


if __name__ == "__main__":
    unittest.main()
