"""Machine room: edition metrics, the weekly watchdog, and pipeline wiring.

Locks edition-metrics-v1 (churn against the previous edition, idempotent
recompiles, capped history, fail-soft), watchdog-v1 (fixed-threshold
attention rules, weekly bucketing, honest absence of facts), and the
name-based pipeline selection that carries the new stages.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import compile_metrics as cm
import compile_watchdog as cw
import pipeline


def write(path: Path, doc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


def ranked_doc(stamp: str, ids: list[str]) -> dict:
    return {"ranked_at": stamp, "method": "rank-v0", "candidate_count": len(ids),
            "candidates": [{"id": i, "url": f"https://news.example/{i}",
                            "source_id": "src-a" if ids.index(i) % 2 else "src-b"} for i in ids]}


class EditionMetrics(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        self.paths = {
            "ranked_path": d / "latest_ranked.json",
            "issues_path": d / "latest_issues.json",
            "history_path": d / "history.json",
            "roadworks_path": d / "latest_roadworks.json",
            "media_path": d / "brief_manifest.json",
            "feed_health_path": d / "feed_health.json",
            "out_path": d / "edition_metrics.json",
        }
        self.ids = [f"id{i:02d}" for i in range(40)]
        write(self.paths["ranked_path"], ranked_doc("2026-09-19T10:00:00+00:00", self.ids))
        write(self.paths["issues_path"], {"issue_count": 2, "change_ledger": {
            "has_previous": True, "new_count": 1, "developed_count": 1, "quiet_count": 0}})
        write(self.paths["history_path"], {"method": "dossier-history-v1", "edition_count": 5,
                                           "dossiers": {"k1": {"editions_missed": 0},
                                                        "k2": {"editions_missed": 3}}})
        write(self.paths["roadworks_path"], {"counts": {"active": 875}, "diff": {
            "has_previous": True, "new_count": 4, "removed_count": 1, "changed_count": 2}})
        write(self.paths["media_path"], {"method": "brief-media-v2", "with_image": 60,
                                         "scope_count": 60})
        write(self.paths["feed_health_path"], {"method": "feed-health-v1", "attention": ["x: failing"],
                                               "sources": {"x": {"status": "failing"},
                                                           "y": {"status": "healthy"}}})

    def compile(self) -> dict:
        return cm.compile_metrics(**self.paths)

    def test_first_snapshot_has_no_churn(self) -> None:
        doc = self.compile()
        latest = doc["latest"]
        self.assertEqual(latest["edition"], "2026-09-19T10:00:00+00:00")
        self.assertEqual(latest["items"], 40)
        self.assertIsNone(latest["churn_in"])
        self.assertEqual(latest["top_ids"], self.ids[:30])
        self.assertEqual(latest["dossiers"]["issue_count"], 2)
        self.assertEqual(latest["dossiers"]["max_missed"], 3)
        self.assertEqual(latest["roadworks"]["active"], 875)
        self.assertEqual(latest["media"], {"with_image": 60, "scope": 60})
        self.assertEqual(latest["feed_attention"], 1)
        self.assertEqual(latest["feed_statuses"]["failing"], 1)
        self.assertEqual(sum(latest["per_source"].values()), 40)

    def test_churn_measured_against_previous_top(self) -> None:
        self.compile()
        new_ids = [f"new{i}" for i in range(5)] + self.ids[5:]
        write(self.paths["ranked_path"], ranked_doc("2026-09-19T16:00:00+00:00", new_ids))
        doc = self.compile()
        self.assertEqual(doc["latest"]["churn_in"], 5)
        self.assertEqual(doc["latest"]["churn_out"], 5)
        self.assertEqual(doc["edition_count"], 2)
        self.assertEqual(doc["churn_in_avg"], 5.0)

    def test_same_edition_recompile_is_idempotent(self) -> None:
        self.compile()
        doc = self.compile()
        self.assertEqual(doc["edition_count"], 1)
        self.assertEqual(doc["latest"]["edition"], "2026-09-19T10:00:00+00:00")

    def test_same_edition_recompile_does_not_zero_churn(self) -> None:
        self.compile()
        new_ids = [f"new{i}" for i in range(5)] + self.ids[5:]
        write(self.paths["ranked_path"], ranked_doc("2026-09-19T16:00:00+00:00", new_ids))
        first = self.compile()
        self.assertEqual(first["latest"]["churn_in"], 5)
        # Recompiling the same edition must compare against the edition before
        # it, never against itself (which would zero the churn and falsify it).
        again = self.compile()
        self.assertEqual(again["edition_count"], 2)
        self.assertEqual(again["latest"]["churn_in"], 5)
        self.assertEqual(again["churn_in_avg"], 5.0)

    def test_history_is_capped(self) -> None:
        write(self.paths["out_path"], {"method": cm.METHOD, "history": [
            {"edition": f"e{i}", "top_ids": []} for i in range(cm.HISTORY_CAP)]})
        doc = self.compile()
        self.assertEqual(len(doc["history"]), cm.HISTORY_CAP)
        self.assertEqual(doc["history"][0]["edition"], "e1")  # oldest dropped
        self.assertEqual(doc["history"][-1]["edition"], "2026-09-19T10:00:00+00:00")

    def test_foreign_previous_store_starts_fresh(self) -> None:
        write(self.paths["out_path"], {"method": "other-v9", "history": [{"edition": "x"}]})
        doc = self.compile()
        self.assertEqual(doc["edition_count"], 1)
        self.assertIsNone(doc["latest"]["churn_in"])

    def test_missing_stores_yield_an_empty_snapshot(self) -> None:
        empty = Path(self._tmp.name) / "nothing"
        doc = cm.compile_metrics(ranked_path=empty / "r.json", issues_path=empty / "i.json",
                                 history_path=empty / "h.json", roadworks_path=empty / "w.json",
                                 media_path=empty / "m.json", feed_health_path=empty / "f.json",
                                 out_path=empty / "out.json")
        self.assertEqual(doc["latest"]["items"], 0)
        self.assertIsNone(doc["compiled_at"])

    def test_unwritable_out_is_not_fatal(self) -> None:
        blocker = write(Path(self._tmp.name) / "blocker", "file")
        doc = cm.compile_metrics(**{**self.paths, "out_path": blocker / "metrics.json"})
        self.assertEqual(doc["latest"]["items"], 40)

    def test_main_always_exits_zero(self) -> None:
        with mock.patch.multiple(
                cm, RANKED=self.paths["ranked_path"], ISSUES=self.paths["issues_path"],
                DOSSIER_HISTORY=self.paths["history_path"], ROADWORKS=self.paths["roadworks_path"],
                MEDIA_MANIFEST=self.paths["media_path"], FEED_HEALTH=self.paths["feed_health_path"],
                OUT=self.paths["out_path"]):
            self.assertEqual(cm.main([]), 0)
        with mock.patch.object(cm, "compile_metrics", side_effect=ValueError("bad store")):
            self.assertEqual(cm.main([]), 0)


class Watchdog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ops = Path(self._tmp.name) / "ops"
        self.data = Path(self._tmp.name) / "data"
        self.ops.mkdir()
        self.data.mkdir()
        (self.data / "blob.bin").write_bytes(b"x" * 1024)
        self.md = self.ops / "watchdog.md"
        self.js = self.ops / "watchdog.json"

    def compile(self) -> dict:
        return cw.compile_watchdog(ops_dir=self.ops, data_dir=self.data,
                                   out_md=self.md, out_json=self.js)

    def healthy_ledgers(self, **overrides) -> None:
        feed = {"method": "feed-health-v1", "compiled_at": "2026-09-19T10:00:00+00:00",
                "source_count": 2, "status_counts": {"healthy": 2, "degraded": 0, "failing": 0, "dead": 0},
                "attention": [], "sources": {"a": {"status": "healthy", "consecutive_failures": 0,
                                                   "hours_since_ok": 0.0, "yield_avg": 30,
                                                   "yield_trend": "flat", "not_modified_recent": 2},
                                            "b": {"status": "healthy", "consecutive_failures": 0,
                                                   "hours_since_ok": 1.0, "yield_avg": 12,
                                                   "yield_trend": None, "not_modified_recent": 0}}}
        media = {"method": "media-health-v1", "latest": {
            "fetched_at": "2026-09-19T10:00:00+00:00", "missing": 0, "with_image": 60,
            "reasons": {}, "feed_resolved": 7}, "history": []}
        metrics = {"method": "edition-metrics-v1", "compiled_at": "2026-09-19T10:00:00+00:00",
                   "churn_in_avg": 4.0, "edition_count": 12,
                   "latest": {"items": 349, "churn_in": 4, "dossiers": {"issue_count": 1},
                              "roadworks": {"active": 875}}}
        for name, doc in (("feed_health.json", feed), ("media_health.json", media),
                          ("edition_metrics.json", metrics)):
            write(self.ops / name, {**doc, **overrides.get(name, {})})

    def test_healthy_machine_needs_no_human(self) -> None:
        self.healthy_ledgers()
        write(self.ops / "refresh.log",
              "2026-09-19T06:41:00+00:00 START pipeline\n"
              "2026-09-19T06:44:00+00:00 DONE deploy in 5s\n"
              "2026-09-19T06:44:00+00:00 OK production updated\n")
        doc = self.compile()
        self.assertEqual(doc["latest"]["attention"], [])
        text = self.md.read_text(encoding="utf-8")
        self.assertIn("Nothing. The machine is healthy.", text)
        self.assertIn("2026-W38", text)
        self.assertIn("| a | healthy |", text)
        self.assertIn("last production deploy: 2026-09-19T06:44:00+00:00", text)

    def test_rotated_log_still_counts_failures(self) -> None:
        self.healthy_ledgers()
        write(self.ops / "refresh.log.1",
              "2026-09-19T06:00:00+00:00 START pipeline\n"
              "2026-09-19T06:01:00+00:00 FAIL pipeline failed (code 1)\n")
        write(self.ops / "refresh.log",
              "2026-09-19T06:44:00+00:00 OK production updated\n")
        doc = self.compile()
        self.assertGreaterEqual(doc["latest"]["refresh"]["fails"], 1)

    def test_a_missing_log_channel_is_never_health(self) -> None:
        self.healthy_ledgers()  # ledgers present, no refresh.log at all
        doc = self.compile()
        self.assertIn("refresh log", doc["latest"]["blind_channels"])
        self.assertNotIn("the machine is healthy", self.md.read_text(encoding="utf-8"))

    def test_failing_and_dead_sources_raise_attention(self) -> None:
        self.healthy_ledgers()
        feed = json.loads((self.ops / "feed_health.json").read_text(encoding="utf-8"))
        feed["sources"]["c"] = {"status": "dead", "consecutive_failures": 12, "last_error": "HTTP 404"}
        feed["sources"]["d"] = {"status": "degraded", "consecutive_failures": 1}
        write(self.ops / "feed_health.json", feed)
        doc = self.compile()
        self.assertTrue(any("source c is dead" in line for line in doc["latest"]["attention"]))
        self.assertFalse(any("source d" in line for line in doc["latest"]["attention"]))
        self.assertTrue(any("sources degraded: d" in line for line in doc["latest"]["watch"]))

    def test_missing_images_thresholds(self) -> None:
        self.healthy_ledgers()
        media = json.loads((self.ops / "media_health.json").read_text(encoding="utf-8"))
        media["latest"]["missing"] = 5
        media["latest"]["reasons"] = {"article_http_403": 5}
        write(self.ops / "media_health.json", media)
        self.assertEqual(self.compile()["latest"]["attention"], [])  # 5 is not above 5
        media["latest"]["missing"] = 6
        write(self.ops / "media_health.json", media)
        doc = self.compile()
        self.assertTrue(any("6 scoped articles without an image" in l for l in doc["latest"]["attention"]))

    def test_rising_missing_images_across_the_window(self) -> None:
        self.healthy_ledgers()
        media = json.loads((self.ops / "media_health.json").read_text(encoding="utf-8"))
        media["history"] = [{"missing": m} for m in (1, 1, 1, 8)]
        write(self.ops / "media_health.json", media)
        doc = self.compile()
        self.assertTrue(any("rising" in l for l in doc["latest"]["attention"]))

    def test_churn_threshold(self) -> None:
        self.healthy_ledgers()
        metrics = json.loads((self.ops / "edition_metrics.json").read_text(encoding="utf-8"))
        metrics["churn_in_avg"] = 16.0
        write(self.ops / "edition_metrics.json", metrics)
        doc = self.compile()
        self.assertTrue(any("churn" in l for l in doc["latest"]["attention"]))

    def test_refresh_failures_raise_attention(self) -> None:
        self.healthy_ledgers()
        write(self.ops / "refresh.log",
              "2026-09-19T06:41:00+00:00 START pipeline\n"
              "2026-09-19T06:42:00+00:00 FAIL verify exited 1\n"
              "2026-09-19T12:42:00+00:00 FAIL deploy exited 1\n")
        doc = self.compile()
        self.assertTrue(any("2 FAIL line(s)" in l for l in doc["latest"]["attention"]))
        self.assertIsNone(doc["latest"]["refresh"]["last_deploy"])

    def test_recovered_failures_are_a_watch_line(self) -> None:
        self.healthy_ledgers()
        write(self.ops / "refresh.log",
              "2026-09-18T23:23:00+00:00 FAIL deploy exited 1\n"
              "2026-09-19T06:41:00+00:00 START pipeline\n"
              "2026-09-19T06:44:00+00:00 OK production updated\n"
              "2026-09-19T10:30:00+00:00 OK production updated\n")
        doc = self.compile()
        self.assertEqual(doc["latest"]["attention"], [])
        self.assertEqual(doc["latest"]["refresh"]["fails_since_ok"], 0)
        self.assertEqual(doc["latest"]["refresh"]["fails"], 1)
        self.assertTrue(any("recovered by the last deploy" in l for l in doc["latest"]["watch"]))

    def test_failures_older_than_the_window_age_out(self) -> None:
        self.healthy_ledgers()
        write(self.ops / "refresh.log",
              "2026-08-01T06:42:00+00:00 FAIL verify exited 1\n"
              "2026-09-19T06:44:00+00:00 OK production updated\n")
        doc = self.compile()
        self.assertEqual(doc["latest"]["refresh"]["fails"], 0)
        self.assertEqual(doc["latest"]["attention"], [])
        self.assertEqual(doc["latest"]["watch"], [])

    def test_disk_growth_is_a_watch_line(self) -> None:
        self.healthy_ledgers()
        with mock.patch.object(cw, "DISK_WATCH_BYTES", 10):
            doc = self.compile()
        self.assertTrue(any("grown to" in l for l in doc["latest"]["watch"]))

    def test_same_week_replaces_and_history_caps(self) -> None:
        self.healthy_ledgers()
        self.compile()
        doc = self.compile()
        self.assertEqual(len(doc["weeks"]), 1)
        write(self.js, {"method": cw.METHOD, "weeks": [
            {"week": f"2026-W{i:02d}"} for i in range(1, cw.WEEK_HISTORY_CAP + 1)]})
        doc = self.compile()
        self.assertEqual(len(doc["weeks"]), cw.WEEK_HISTORY_CAP)
        self.assertEqual(doc["weeks"][-1]["week"], "2026-W38")
        self.assertEqual(doc["weeks"][0]["week"], "2026-W02")  # oldest dropped

    def test_recompile_is_byte_identical(self) -> None:
        self.healthy_ledgers()
        self.compile()
        first_json, first_md = self.js.read_bytes(), self.md.read_bytes()
        self.compile()
        self.assertEqual(self.js.read_bytes(), first_json)
        self.assertEqual(self.md.read_bytes(), first_md)

    def test_absent_ledgers_report_absence_not_false_health(self) -> None:
        doc = self.compile()
        text = self.md.read_text(encoding="utf-8")
        self.assertEqual(doc["week"], "unknown")
        self.assertIn("(no feed facts yet)", text)
        self.assertIn("unknown", text)
        self.assertFalse(doc["latest"]["facts"])
        self.assertNotIn("the machine is healthy", text)

    def test_foreign_ledger_methods_are_ignored(self) -> None:
        write(self.ops / "feed_health.json", {"method": "other-v9", "sources": {"z": {"status": "dead"}}})
        doc = self.compile()
        attention = doc["latest"]["attention"]
        # The foreign ledger is never read as fact...
        self.assertFalse(any("z is dead" in line for line in attention))
        # ...and with no fact at all the verdict is blindness, never health.
        self.assertFalse(doc["latest"]["facts"])
        self.assertTrue(any("blind" in line for line in attention))
        self.assertNotIn("the machine is healthy", self.md.read_text(encoding="utf-8"))

    def test_main_always_exits_zero(self) -> None:
        self.healthy_ledgers()
        with mock.patch.multiple(cw, OPS=self.ops, DATA=self.data, OUT_MD=self.md, OUT_JSON=self.js):
            self.assertEqual(cw.main([]), 0)
        with mock.patch.object(cw, "compile_watchdog", side_effect=OSError("disk on fire")):
            self.assertEqual(cw.main([]), 0)


class PipelineWiring(unittest.TestCase):
    def test_machine_room_stages_sit_in_the_chain(self) -> None:
        s = pipeline.SCRIPTS
        self.assertLess(s.index("ingest_wzdx.py"), s.index("feed_health.py"))
        self.assertLess(s.index("ingest_civic.py"), s.index("feed_health.py"))
        self.assertLess(s.index("ingest_wzdx.py"), s.index("ingest_civic.py"))
        self.assertLess(s.index("feed_health.py"), s.index("normalize.py"))
        self.assertLess(s.index("rank_display.py"), s.index("compile_metrics.py"))
        self.assertLess(s.index("compile_metrics.py"), s.index("compile_watchdog.py"))

    def test_offline_selection_drops_only_ingest_rss(self) -> None:
        with mock.patch.object(pipeline, "run") as run, \
                mock.patch("sys.argv", ["pipeline.py", "--offline"]):
            self.assertEqual(pipeline.main(), 0)
        calls = [(c.args[0], c.args[1:]) for c in run.call_args_list]
        self.assertEqual([name for name, _ in calls], pipeline.SCRIPTS[1:])
        for name, extra in calls:
            flagged = name in ("ingest_wzdx.py", "ingest_civic.py", "fetch_brief_media.py")
            self.assertEqual(extra, ("--offline",) if flagged else ())

    def test_render_only_is_still_exactly_the_display_step(self) -> None:
        with mock.patch.object(pipeline, "run") as run, \
                mock.patch("sys.argv", ["pipeline.py", "--render-only"]):
            self.assertEqual(pipeline.main(), 0)
        self.assertEqual([c.args[0] for c in run.call_args_list], ["rank_display.py"])


if __name__ == "__main__":
    unittest.main()
