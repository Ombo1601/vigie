"""Life facets v0.1 — opt-in Approaches reorder only."""
from __future__ import annotations

import unittest
from unittest import mock

import harness  # noqa: F401

import life_facets
import rank_display


def _approaches_fixture() -> tuple[list[dict], list[dict]]:
    issues = [
        {
            "issue_id": "a-secure",
            "scar": "maelyne",
            "question": "Security scar?",
            "topic": {"topic": "security"},
            "geo_focus": ["quebec-city"],
            "source_count": 3,
            "silence": {"silent_count": 6, "silent": []},
            "tensions": [],
        },
        {
            "issue_id": "b-house",
            "scar": "housing",
            "question": "Housing scar?",
            "topic": {"topic": "housing"},
            "geo_focus": ["quebec-city"],
            "source_count": 2,
            "silence": {"silent_count": 7, "silent": []},
            "tensions": [
                {
                    "items": [{"candidate_id": "c-rent"}],
                }
            ],
        },
        {
            "issue_id": "c-transit",
            "scar": "tram",
            "question": "Transit scar?",
            "topic": {"topic": "transport"},
            "geo_focus": ["quebec"],
            "source_count": 2,
            "silence": {"silent_count": 5, "silent": []},
            "tensions": [],
        },
    ]
    ranked = [
        {
            "id": "c-rent",
            "title": "Loyer",
            "enrich": {
                "impacts": [
                    {
                        "units": [
                            {
                                "kind": "housing_count",
                                "value": 40,
                                "unit": "logements",
                                "raw": "40 logements",
                            }
                        ]
                    }
                ]
            },
        }
    ]
    return issues, ranked


class FacetCatalog(unittest.TestCase):
    def test_catalog_published_ids(self) -> None:
        ids = [f["id"] for f in life_facets.FACET_CATALOG]
        self.assertEqual(ids, ["renter", "transit", "energy", "civic", "health"])
        payload = life_facets.catalog_payload()
        self.assertEqual(payload["method_file"], "FACETS.md")
        self.assertIn("reorder Approaches only", payload["note"])
        self.assertIn("w_impact", payload["note"])
        self.assertNotIn("for you", payload["note"].lower())
        self.assertNotIn("for-you", payload["note"].lower())

    def test_normalize_drops_unknown_preserves_catalog_order(self) -> None:
        self.assertEqual(
            life_facets.normalize_facet_ids(["health", "nope", "renter", "renter"]),
            ["renter", "health"],
        )
        self.assertEqual(life_facets.normalize_facet_ids(None), [])


class FacetReorder(unittest.TestCase):
    def test_default_keeps_store_order(self) -> None:
        issues, ranked = _approaches_fixture()
        continuity = rank_display.build_continuity(issues, ranked)
        ap = rank_display.build_approaches(issues, continuity)
        out = life_facets.reorder_approaches(ap, [], issues=issues)
        self.assertEqual([a["issue_id"] for a in out], [a["issue_id"] for a in ap])
        self.assertTrue(life_facets.same_approach_set(ap, out))

    def test_renter_boosts_housing_without_dropping(self) -> None:
        issues, ranked = _approaches_fixture()
        continuity = rank_display.build_continuity(issues, ranked)
        ap = rank_display.build_approaches(issues, continuity)
        self.assertEqual(ap[0]["issue_id"], "a-secure")
        out = life_facets.reorder_approaches(ap, ["renter"], issues=issues)
        self.assertEqual(out[0]["issue_id"], "b-house")
        self.assertTrue(life_facets.same_approach_set(ap, out))
        house = next(a for a in out if a["issue_id"] == "b-house")
        self.assertEqual(house["store_index"], 1)
        self.assertEqual(house["index"], 1)

    def test_transit_boosts_transport(self) -> None:
        issues, ranked = _approaches_fixture()
        continuity = rank_display.build_continuity(issues, ranked)
        ap = rank_display.build_approaches(issues, continuity)
        out = life_facets.reorder_approaches(ap, ["transit"], issues=issues)
        self.assertEqual(out[0]["issue_id"], "c-transit")
        self.assertTrue(life_facets.same_approach_set(ap, out))

    def test_unit_weight_adds_for_renter(self) -> None:
        issues, ranked = _approaches_fixture()
        continuity = rank_display.build_continuity(issues, ranked)
        ap = rank_display.build_approaches(issues, continuity)
        annotated = life_facets.annotate_approaches(ap, issues)
        house = next(a for a in annotated if a["issue_id"] == "b-house")
        self.assertIn("housing_count", house["unit_kinds"])
        score = life_facets.facet_score(house, ["renter"])
        self.assertEqual(score, 1.35)

    def test_input_list_not_mutated_order(self) -> None:
        issues, ranked = _approaches_fixture()
        continuity = rank_display.build_continuity(issues, ranked)
        ap = rank_display.build_approaches(issues, continuity)
        before = [a["issue_id"] for a in ap]
        life_facets.reorder_approaches(ap, ["renter"], issues=issues)
        self.assertEqual([a["issue_id"] for a in ap], before)

    def test_does_not_touch_rank_weights(self) -> None:
        self.assertEqual(rank_display.W_IMPACT, 0.0)
        self.assertEqual(rank_display.W_GEO, 0.60)
        self.assertEqual(rank_display.W_RECENCY, 0.40)


class FacetEdges(unittest.TestCase):
    def test_issue_topic_branches(self) -> None:
        self.assertEqual(life_facets.issue_topic(None), "other")
        self.assertEqual(life_facets.issue_topic({}), "other")
        self.assertEqual(life_facets.issue_topic({"topic": "law"}), "law")
        self.assertEqual(life_facets.issue_topic({"topic": {"topic": "health"}}), "health")
        self.assertEqual(life_facets.issue_topic({"topic": {"topic": None}}), "other")

    def test_annotate_fills_missing_topic_from_issues(self) -> None:
        issues = [{"issue_id": "z", "topic": {"topic": "energy/hydro"}}]
        ap = [{"issue_id": "z", "index": 0, "units": []}]
        out = life_facets.annotate_approaches(ap, issues)
        self.assertEqual(out[0]["topic"], "energy/hydro")
        self.assertEqual(out[0]["store_index"], 0)

    def test_facet_score_empty_and_orphan_id(self) -> None:
        self.assertEqual(life_facets.facet_score({"topic": "housing"}, []), 0.0)
        with mock.patch.dict(life_facets.FACET_BY_ID, {}, clear=True):
            self.assertEqual(
                life_facets.facet_score({"topic": "housing"}, ["renter"]),
                0.0,
            )

    def test_scar_fallback_on_issue_id(self) -> None:
        ap = [{"issue_id": "only-scar", "units": [{"kind": ""}, {"kind": "price"}]}]
        issues = [{"scar": "only-scar", "topic": "law"}]
        out = life_facets.annotate_approaches(ap, issues)
        self.assertEqual(out[0]["topic"], "law")
        self.assertEqual(out[0]["unit_kinds"], ["price"])

    def test_parse_md_edges_and_mismatch_errors(self) -> None:
        rows = life_facets.parse_facets_md_rows(
            "## Catalog\n"
            "| id | Label | Topics matched | Unit kinds | topic_weight | unit_weight |\n"
            "|----|-------|----------------|------------|--------------|-------------|\n"
            "| short | x |\n"
            "| bad | Bad | housing | — | nope | 0.1 |\n"
            "| solo | Solo | — | — | 1.0 | 0.0 |\n"
            "## Opt-in\n"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "solo")
        self.assertEqual(rows[0]["topics"], [])
        ok, errors = life_facets.catalog_matches_facets_md(
            "## Catalog\n"
            "| id | Label | Topics | Units | topic_weight | unit_weight |\n"
            "| renter | Wrong | housing | price | 9.0 | 0.0 |\n"
        )
        self.assertFalse(ok)
        self.assertTrue(any("id order" in e or "missing" in e or "mismatch" in e or "topics" in e for e in errors))
        # Force per-field mismatches with full id set, wrong weights/labels
        fake = "## Catalog\n| id | Label | Topics matched | Unit kinds | topic_weight | unit_weight |\n"
        for f in life_facets.FACET_CATALOG:
            units = ", ".join(f["unit_kinds"]) if f["unit_kinds"] else "—"
            topics = ", ".join(f["topics"]) if f["topics"] else "—"
            # scramble renter only
            if f["id"] == "renter":
                fake += (
                    "| renter | NotRenter | transport | bylaw_id | 0.5 | 0.1 |\n"
                )
            else:
                fake += (
                    f"| {f['id']} | {f['label']} | {topics} | {units} | "
                    f"{f['topic_weight']} | {f['unit_weight']} |\n"
                )
        ok2, err2 = life_facets.catalog_matches_facets_md(fake)
        self.assertFalse(ok2)
        joined = " ".join(err2)
        self.assertIn("renter", joined)


class FacetSignalsAndHtml(unittest.TestCase):
    def test_signals_payload(self) -> None:
        issues, ranked = _approaches_fixture()
        continuity = rank_display.build_continuity(issues, ranked)
        ap = rank_display.build_approaches(issues, continuity)
        sig = life_facets.signals_payload(ap, issues)
        self.assertEqual(sig["method"], life_facets.METHOD)
        self.assertEqual(len(sig["approaches"]), 3)
        self.assertEqual(sig["approaches"][1]["topic"], "housing")

    def test_arrival_embeds_facets_ui_and_method(self) -> None:
        issues, ranked = _approaches_fixture()
        ranked_full = ranked + [
            {
                "id": "x",
                "title": "t",
                "url": "https://example.test/x",
                "source_id": "le-soleil",
                "rank_score": 0.1,
                "geo": "quebec-city",
                "enrich": {"geo": {"geo": "quebec-city"}},
            }
        ]
        html = rank_display.render_html(
            ranked_full, "2026-09-16T00:00:00+00:00", issues=issues
        )
        self.assertIn('id="life-facets"', html)
        self.assertIn("Life facets (opt-in)", html)
        self.assertIn('id="vigie-facets"', html)
        self.assertIn("/methode/facettes.html", html)
        self.assertIn("data-facet='renter'", html)
        self.assertIn("data-topic=", html)
        self.assertIn("vigie_facets_v1", html)
        self.assertIn("Approaches reorder only", html)
        self.assertIn("data-unit-kinds=", html)
        self.assertIn("w_impact gated", html)
        low = html.lower()
        self.assertNotIn("for-you", low)
        self.assertIn("never a painted personalization feed", low)
        self.assertEqual(rank_display.W_IMPACT, 0.0)
        pos_sec = html.find("Security scar?")
        pos_house = html.find("Housing scar?")
        self.assertLess(pos_sec, pos_house)


if __name__ == "__main__":
    unittest.main()
