"""Named-scar clustering: word boundaries, no TV hitchhike, no single-feed theater."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import harness

import cluster_issues


class ScarOf(unittest.TestCase):
    def test_maelyne_name(self) -> None:
        self.assertEqual(
            cluster_issues.scar_of({"title": "Mort de Maëlyne Lugez", "summary": ""}),
            "maelyne-levis",
        )

    def test_tramway_needs_marchand_or_duhaime_in_title(self) -> None:
        self.assertIsNone(
            cluster_issues.scar_of({"title": "Le tramway avance", "summary": "Marchand dit non"})
        )
        self.assertEqual(
            cluster_issues.scar_of({"title": "Marchand défend le tramway", "summary": ""}),
            "tramway",
        )

    def test_airport_privatisation(self) -> None:
        self.assertEqual(
            cluster_issues.scar_of(
                {"title": "Le Canada privatisera ses aéroports", "summary": ""}
            ),
            "airport",
        )

    def test_marchand_title(self) -> None:
        self.assertEqual(
            cluster_issues.scar_of({"title": "Les priorités de Marchand", "summary": ""}),
            "marchand",
        )

    def test_marchand_mayor_agenda_in_summary(self) -> None:
        self.assertEqual(
            cluster_issues.scar_of(
                {
                    "title": "Le maire de Québec dépose sa liste",
                    "summary": "Bruno Marchand cible le logement",
                }
            ),
            "marchand",
        )

    def test_marchand_summary_hitchhiker_without_title_gate_dropped(self) -> None:
        self.assertIsNone(
            cluster_issues.scar_of(
                {
                    "title": "Élections dans la Capitale-Nationale",
                    "summary": "Marchand sera interrogé plus tard",
                }
            )
        )

    def test_no_scar(self) -> None:
        self.assertIsNone(cluster_issues.scar_of({"title": "Un concert à Lyon", "summary": ""}))

    def test_tramway_scar_beats_marchand_when_both(self) -> None:
        self.assertEqual(
            cluster_issues.scar_of({"title": "Duhaime veut arrêter le tramway", "summary": ""}),
            "tramway",
        )


class Helpers(unittest.TestCase):
    def test_geo_of_prefers_enrich(self) -> None:
        c = {"geo": "linked", "enrich": {"geo": {"geo": "quebec-city"}}}
        self.assertEqual(cluster_issues.geo_of(c), "quebec-city")

    def test_geo_of_fallback(self) -> None:
        self.assertEqual(cluster_issues.geo_of({"geo": "quebec"}), "quebec")
        self.assertEqual(cluster_issues.geo_of({}), "unknown")

    def test_topic_of(self) -> None:
        self.assertEqual(cluster_issues.topic_of({}), "other")
        self.assertEqual(
            cluster_issues.topic_of({"enrich": {"topics": [{"topic": "housing"}]}}),
            "housing",
        )

    def test_neutral_question_locked_and_fallback(self) -> None:
        self.assertIn("aéroport", cluster_issues.neutral_question([], 2, "airport").lower())
        self.assertEqual(
            cluster_issues.neutral_question([], 1, "newscar"),
            "Que disent plusieurs sources sur newscar ?",
        )

    def test_issue_id_stable(self) -> None:
        self.assertEqual(
            cluster_issues.issue_id("airport", 5),
            cluster_issues.issue_id("airport", 5),
        )
        self.assertEqual(
            cluster_issues.issue_id("airport", 5),
            cluster_issues.issue_id("airport", 4),
        )


class ClusterMain(unittest.TestCase):
    def tearDown(self) -> None:
        cluster_issues.IN_PATH = harness.ROOT / "data" / "normalized" / "latest_enriched.json"
        cluster_issues.OUT_ISSUES = harness.ROOT / "data" / "issues" / "latest_issues.json"

    def test_main_missing_input_exits(self) -> None:
        cluster_issues.IN_PATH = Path("/no/such/enriched.json")
        with self.assertRaises(SystemExit):
            cluster_issues.main()

    def test_main_drops_single_voice_and_writes_multi(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp = d / "in.json"
            out = d / "out.json"
            def item(sid: str, title: str, geo: str) -> dict:
                return {
                    "id": sid + title[:8],
                    "title": title,
                    "summary": "",
                    "url": "https://example.test/" + sid,
                    "source_id": sid,
                    "source_name": sid,
                    "language": "fr",
                    "enrich": {
                        "geo": {"geo": geo},
                        "topics": [{"topic": "trade"}],
                    },
                }

            payload = {
                "candidates": [
                    item("le-devoir", "Le Canada privatisera ses aéroports", "quebec"),
                    item("cbc-politics", "Canada to privatize airports", "quebec"),
                    item("radio-canada-quebec", "Seul sur le tramway et Marchand", "quebec-city"),
                ]
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            written = json.loads(out.read_text(encoding="utf-8"))
            scars = [i["scar"] for i in written["issues"]]
            self.assertIn("airport", scars)
            self.assertNotIn("tramway", scars)
            self.assertEqual(written["dropped_single_voice"], 1)
            self.assertIn("word-boundary", written["method"])
            self.assertIn("official", written["method"])
            self.assertIn("media_remix", written["method"])
            self.assertIn("institution", written["method"])
            self.assertIn("silence", written["method"])
            airport = next(i for i in written["issues"] if i["scar"] == "airport")
            self.assertEqual(set(airport["sources"]), {"cbc", "le-devoir"})
            labels = [t["label"] for t in airport["tensions"]]
            self.assertTrue(any("CBC" in lab for lab in labels))

    def _pair(self, geo_a: str, geo_b: str):
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp, out = d / "in.json", d / "out.json"

            def item(sid: str, title: str, geo: str) -> dict:
                return {
                    "id": sid + title[:8], "title": title, "summary": "",
                    "url": "https://example.test/" + sid, "source_id": sid,
                    "source_name": sid, "language": "fr",
                    "published_at": "2026-09-19T10:00:00+00:00",
                    "enrich": {"geo": {"geo": geo}, "topics": [{"topic": "law"}]},
                }

            payload = {
                "normalized_at": "2026-09-19T12:00:00+00:00",
                "candidates": [
                    item("le-devoir",
                         "La ministre Hajdu réduit les interventions de l’État en cas de grève", geo_a),
                    item("la-presse",
                         "La ministre Hajdu compte réduire les interventions de l’État en cas de grève", geo_b),
                ],
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            return json.loads(out.read_text(encoding="utf-8"))

    def test_linked_voice_can_join_a_province_anchored_event(self) -> None:
        written = self._pair("linked", "quebec")
        self.assertEqual(len(written["issues"]), 1)
        self.assertEqual(written["issues"][0]["source_count"], 2)
        self.assertEqual(set(written["issues"][0]["geo_focus"]), {"linked", "quebec"})

    def test_pure_linked_group_never_becomes_a_dossier(self) -> None:
        written = self._pair("linked", "linked")
        self.assertEqual(written["issues"], [])
        self.assertEqual(written["dropped_no_city_anchor"], 1)


class DossierRecall(unittest.TestCase):
    """Nest law + light plural folding: more genuine dossiers, never world fog."""

    def _item(self, geo: str) -> dict:
        return {"enrich": {"geo": {"geo": geo}}}

    def test_city_or_province_anchors_a_dossier(self) -> None:
        self.assertTrue(cluster_issues.dossier_anchor_ok("event-x", [self._item("quebec-city")]))
        self.assertTrue(cluster_issues.dossier_anchor_ok(
            "event-x", [self._item("linked"), self._item("quebec")]))

    def test_pure_linked_never_anchors(self) -> None:
        self.assertFalse(cluster_issues.dossier_anchor_ok(
            "event-x", [self._item("linked"), self._item("linked")]))

    def test_airport_is_province_ok(self) -> None:
        self.assertTrue(cluster_issues.dossier_anchor_ok("airport", [self._item("linked")]))

    def test_plural_folding_unifies_the_same_event_title(self) -> None:
        singular = cluster_issues.headline_tokens({"title": "Quatre arrestation à Sainte-Foy"})
        plural = cluster_issues.headline_tokens({"title": "Quatre arrestations à Sainte-Foy"})
        self.assertEqual(singular, plural)

    def test_distinct_events_still_do_not_merge(self) -> None:
        a = {"title": "Incendie majeur dans Limoilou", "summary": "",
             "published_at": "2026-09-19T10:00:00+00:00"}
        b = {"title": "Un cycliste blessé à Sainte-Foy", "summary": "",
             "published_at": "2026-09-19T11:00:00+00:00"}
        self.assertFalse(cluster_issues.same_event(a, b))


if __name__ == "__main__":
    unittest.main()
