"""Conservative FR/EN same-event join.

Precision over recall: a bilingual pair needs a shared date window plus a
shared place or proper name, then complete-link on canonical tokens.
Lévis is not télévision. Same-language complete-link is unchanged.
Two CBC desks remain one institution seat.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import harness  # noqa: F401

import cluster_issues


NOW = "2026-09-20T12:00:00+00:00"


def item(sid: str, title: str, *, lang: str = "fr", geo: str = "quebec-city",
         published: str = "2026-09-20T10:00:00+00:00") -> dict:
    return {
        "id": sid + title[:12],
        "title": title,
        "summary": "",
        "url": "https://example.test/" + sid,
        "source_id": sid,
        "source_name": sid,
        "language": lang,
        "published_at": published,
        "enrich": {"geo": {"geo": geo}, "topics": [{"topic": "law"}]},
    }


class BilingualJoin(unittest.TestCase):
    def test_fr_en_limoilou_fire_joins(self) -> None:
        fr = item("le-soleil", "Incendie majeur : trois immeubles touchés à Limoilou")
        en = item("cbc-montreal", "Major fire hits three buildings in Limoilou", lang="en")
        self.assertTrue(cluster_issues.same_event(fr, en))
        buckets = cluster_issues.event_buckets([fr, en])
        self.assertEqual(len(buckets), 1)

    def test_television_does_not_join_levis(self) -> None:
        tv = item(
            "journal-de-quebec",
            "Une chaîne de télévision filme un hélicoptère à Los Angeles",
        )
        lv = item(
            "le-soleil",
            "Incendie majeur dans trois immeubles à Lévis",
        )
        self.assertFalse(cluster_issues.same_event(tv, lv))
        self.assertFalse(cluster_issues.same_event(
            item("cbc-montreal", "A television helicopter crashes near Los Angeles", lang="en"),
            lv,
        ))

    def test_same_language_hamel_charest_still_split(self) -> None:
        a = item("le-soleil", "Limoilou : fermeture temporaire du boulevard Hamel pour travaux")
        b = item("journal-de-quebec", "Limoilou : fermeture temporaire du boulevard Charest pour travaux")
        self.assertFalse(cluster_issues.same_event(a, b))

    def test_bilingual_without_place_or_name_does_not_join(self) -> None:
        fr = item("le-soleil", "Le conseil adopte un budget austère pour l’année")
        en = item("cbc-montreal", "Council adopts a stern budget for the year", lang="en")
        self.assertFalse(cluster_issues.same_event(fr, en))

    def test_shared_road_name_joins_fr_en(self) -> None:
        fr = item("le-soleil", "Fermeture du boulevard Hamel à Limoilou")
        en = item("cbc-montreal", "Closure of boulevard Hamel in Limoilou", lang="en")
        self.assertTrue(cluster_issues.same_event(fr, en))
        self.assertFalse(cluster_issues.same_event(
            item("le-soleil", "Fermeture du boulevard Hamel à Limoilou"),
            item("cbc-montreal", "Closure of boulevard Charest in Limoilou", lang="en"),
        ))

    def test_complete_link_still_blocks_transitive_glue(self) -> None:
        a = item("a", "Limoilou travaux fermeture réseau Hamel")
        b = item("b", "Limoilou travaux fermeture réseau Hamel chantier urgence")
        c = item("c", "Limoilou fermeture réseau chantier urgence")
        self.assertTrue(cluster_issues.same_event(a, b))
        self.assertTrue(cluster_issues.same_event(b, c))
        self.assertFalse(cluster_issues.same_event(a, c))
        self.assertTrue(all(len(items) < 3 for items in cluster_issues.event_buckets([a, b, c]).values()))


class SisterDesksStayOneSeat(unittest.TestCase):
    def test_two_cbc_feeds_remain_one_voice_and_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            inp = Path(raw) / "in.json"
            out = Path(raw) / "out.json"
            payload = {
                "normalized_at": NOW,
                "candidates": [
                    item("cbc-montreal", "Major fire hits three buildings in Limoilou", lang="en"),
                    item("cbc-politics", "Major fire hits three buildings in Limoilou", lang="en"),
                ],
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["issues"], [])
            self.assertEqual(written["dropped_single_voice"], 1)

    def test_cbc_and_radio_canada_can_open_a_bilingual_dossier(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            inp = Path(raw) / "in.json"
            out = Path(raw) / "out.json"
            payload = {
                "normalized_at": NOW,
                "candidates": [
                    item("cbc-montreal", "Major fire hits three buildings in Limoilou", lang="en"),
                    item("radio-canada-quebec", "Incendie majeur : trois immeubles touchés à Limoilou"),
                ],
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(written["issues"]), 1)
            self.assertIn("bilingual-canonical", written["method"])
            self.assertEqual(set(written["issues"][0]["sources"]), {"cbc", "radio-canada"})

    def tearDown(self) -> None:
        cluster_issues.IN_PATH = harness.ROOT / "data" / "normalized" / "latest_enriched.json"
        cluster_issues.OUT_ISSUES = harness.ROOT / "data" / "issues" / "latest_issues.json"


if __name__ == "__main__":
    unittest.main()
