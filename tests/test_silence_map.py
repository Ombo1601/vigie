"""Silence map — enabled institutions absent from a scar this run.

Voice = institution (CBC Montreal+Politics share one seat).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import harness

import cluster_issues
import ingest_rss


class InstitutionCollapse(unittest.TestCase):
    def test_cbc_sister_feeds_one_institution(self) -> None:
        # Fixture registry: the chancellery with both CBC desks followed (as of 2026-10-05).
        with tempfile.TemporaryDirectory() as tmp:
            srcs = ingest_rss.load_enabled_rss(harness.sources_with_reenabled(Path(tmp), *harness.CBC_DESKS))
        by_id = {s["id"]: s for s in srcs}
        self.assertEqual(by_id["cbc-montreal"]["institution"], "cbc")
        self.assertEqual(by_id["cbc-politics"]["institution"], "cbc")
        self.assertEqual(by_id["radio-canada-quebec"]["institution"], "radio-canada")
        self.assertEqual(by_id["radio-canada-national"]["institution"], "radio-canada")
        self.assertEqual(by_id["radio-canada-ottawa"]["institution"], "radio-canada")
        insts = cluster_issues.collapse_institutions(srcs)
        ids = [i["institution_id"] for i in insts]
        self.assertEqual(len(srcs), 12)
        # Fixture registry: 12 feeds → 9 institutions (CBC×2 + Radio-Canada×3 collapsed)
        self.assertEqual(len(insts), 9)
        self.assertEqual(len(ids), len(set(ids)))
        cbc = next(i for i in insts if i["institution_id"] == "cbc")
        self.assertEqual(sorted(cbc["feed_ids"]), ["cbc-montreal", "cbc-politics"])
        rc = next(i for i in insts if i["institution_id"] == "radio-canada")
        self.assertEqual(rc["nest_role"], "primary")  # quebec desk wins over linked/province


class SilenceMapUnit(unittest.TestCase):
    def test_silent_excludes_speakers(self) -> None:
        chancellery = [
            {"id": "a", "name": "A", "institution": "inst-a", "institution_name": "A Co",
             "nest_role": "primary", "source_kind": "media"},
            {"id": "b", "name": "B", "institution": "inst-b", "institution_name": "B Co",
             "nest_role": "province", "source_kind": "official"},
            {"id": "c", "name": "C", "institution": "inst-c", "institution_name": "C Co",
             "nest_role": "linked", "source_kind": "media"},
        ]
        sm = cluster_issues.silence_map({"inst-a"}, chancellery)
        ids = [s["institution_id"] for s in sm["silent"]]
        self.assertEqual(sm["spoke_count"], 1)
        self.assertEqual(sm["silent_count"], 2)
        self.assertEqual(sm["enabled_count"], 3)
        self.assertNotIn("inst-a", ids)
        self.assertEqual(ids[0], "inst-b")  # official first
        self.assertEqual(sm["scope"], "enabled_institutions")
        self.assertIn("not a bias", sm["note"].lower())

    def test_sister_feeds_one_silence_seat(self) -> None:
        """CBC Montreal spoke → CBC institution absent from silent; Politics feed not a second silence."""
        chancellery = [
            {"id": "cbc-montreal", "name": "CBC Montreal", "institution": "cbc",
             "institution_name": "CBC", "nest_role": "linked", "source_kind": "media"},
            {"id": "cbc-politics", "name": "CBC Politics", "institution": "cbc",
             "institution_name": "CBC", "nest_role": "linked", "source_kind": "media"},
            {"id": "le-devoir", "name": "Le Devoir", "institution": "le-devoir",
             "institution_name": "Le Devoir", "nest_role": "province", "source_kind": "media"},
        ]
        sm = cluster_issues.silence_map({"cbc"}, chancellery)
        self.assertEqual(sm["enabled_count"], 2)
        self.assertEqual(sm["enabled_feed_count"], 3)
        self.assertEqual(sm["spoke_count"], 1)
        self.assertEqual(sm["silent_count"], 1)
        silent_ids = {s["institution_id"] for s in sm["silent"]}
        self.assertEqual(silent_ids, {"le-devoir"})
        self.assertNotIn("cbc-politics", silent_ids)
        self.assertNotIn("cbc-montreal", silent_ids)

    def test_live_chancellery_load(self) -> None:
        # The live registry: never above its documented ceiling, one seat per
        # institution, and sister desks (Radio-Canada) still share theirs.
        srcs = ingest_rss.load_enabled_rss(harness.SOURCES)
        self.assertLessEqual(len(srcs), harness.rss_ceiling())
        insts = cluster_issues.collapse_institutions(srcs)
        self.assertEqual(len(insts), len({s["institution"] for s in srcs}))
        self.assertLess(len(insts), len(srcs))


class SilenceOnIssue(unittest.TestCase):
    def setUp(self) -> None:
        # The mechanism under test needs both CBC desks followed (fixture registry).
        harness.use_cbc_chancellery(self)

    def tearDown(self) -> None:
        cluster_issues.IN_PATH = harness.ROOT / "data" / "normalized" / "latest_enriched.json"
        cluster_issues.OUT_ISSUES = harness.ROOT / "data" / "issues" / "latest_issues.json"

    def test_issue_carries_silence_complement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp = d / "in.json"
            out = d / "out.json"

            def item(sid: str, title: str, geo: str) -> dict:
                return {
                    "id": sid,
                    "title": title,
                    "summary": "",
                    "url": f"https://example.test/{sid}",
                    "source_id": sid,
                    "source_name": sid,
                    "source_kind": "media",
                    "language": "fr",
                    "enrich": {"geo": {"geo": geo}, "topics": [{"topic": "trade"}], "claims": []},
                }

            payload = {
                "candidates": [
                    item("le-devoir", "Le Canada privatisera ses aéroports", "quebec"),
                    item("cbc-politics", "Canada to privatize airports", "quebec"),
                ]
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("institution", written["method"])
            airport = next(i for i in written["issues"] if i["scar"] == "airport")
            silence = airport["silence"]
            spoke = set(airport["sources"])
            self.assertEqual(spoke, {"cbc", "le-devoir"})
            self.assertIn("cbc-politics", airport["source_feeds"])
            silent_ids = {s["institution_id"] for s in silence["silent"]}
            self.assertTrue(spoke.isdisjoint(silent_ids))
            self.assertEqual(silence["spoke_count"] + silence["silent_count"], silence["enabled_count"])
            self.assertEqual(silence["enabled_count"], 9)
            self.assertEqual(silence["enabled_feed_count"], 12)
            if silence["silent"]:
                self.assertEqual(silence["silent"][0]["source_kind"], "official")

    def test_two_cbc_feeds_alone_are_single_institution_theater(self) -> None:
        """Sister feeds from one house do not found a fight."""
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp = d / "in.json"
            out = d / "out.json"

            def item(sid: str, title: str) -> dict:
                return {
                    "id": sid,
                    "title": title,
                    "summary": "",
                    "url": f"https://example.test/{sid}",
                    "source_id": sid,
                    "source_name": sid,
                    "source_kind": "media",
                    "language": "en",
                    "enrich": {"geo": {"geo": "quebec"}, "topics": [{"topic": "trade"}], "claims": []},
                }

            payload = {
                "candidates": [
                    item("cbc-montreal", "Canada to privatize airports — Montreal desk"),
                    item("cbc-politics", "Canada to privatize airports — Politics desk"),
                ]
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["issue_count"], 0)
            self.assertEqual(written["dropped_single_voice"], 1)


class LiveSilenceGuard(unittest.TestCase):
    def test_live_fights_have_silence(self) -> None:
        path = harness.ROOT / "data" / "issues" / "latest_issues.json"
        if not path.is_file():
            self.skipTest("no live issues")
        payload = json.loads(path.read_text(encoding="utf-8"))
        issues = payload.get("issues") or []
        if not issues:
            self.skipTest("no fights")
        for iss in issues:
            silence = iss.get("silence") or {}
            self.assertIn("silent", silence)
            self.assertEqual(silence.get("scope"), "enabled_institutions")
            spoke = set(iss.get("sources") or [])
            silent_ids = {
                s.get("institution_id") or s.get("source_id") for s in silence.get("silent") or []
            }
            self.assertTrue(spoke.isdisjoint(silent_ids), iss.get("scar"))
            self.assertEqual(
                silence.get("spoke_count", -1) + silence.get("silent_count", -1),
                silence.get("enabled_count", -2),
                iss.get("scar"),
            )
            # No feed-id leakage into silence seats when institution collapse is live
            for s in silence.get("silent") or []:
                self.assertNotEqual(s.get("institution_id"), "cbc-montreal")
                self.assertNotEqual(s.get("institution_id"), "cbc-politics")


if __name__ == "__main__":
    unittest.main()
