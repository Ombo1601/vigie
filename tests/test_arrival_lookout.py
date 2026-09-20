"""Arrival Lookout — Phase 0/1: first paint is Approaches, not a dashboard."""
from __future__ import annotations

import re
import unittest

import harness  # noqa: F401 — scripts/ on sys.path

import rank_display


class MovedPinIntegrity(unittest.TestCase):
    def test_issue_candidate_ids_are_sorted(self) -> None:
        # Display slices this list, so set-hash order would make rebuilds differ.
        iss = {"tensions": [{"items": [{"candidate_id": "c"}, {"candidate_id": "a"},
                                       {"candidate_id": "b"}]}]}
        self.assertEqual(rank_display.issue_candidate_ids(iss), ["a", "b", "c"])

    def test_moved_pin_links_never_dangle_past_the_cap(self) -> None:
        """The Moved strip is capped at 15; a 16th demoted item must link out,
        not to a #pin- anchor that no radar row rendered (staging rejects dead
        in-page anchors, which would block the whole edition)."""
        ranked = [
            {
                "id": f"b{i}",
                "title": f"Bulletin {i}",
                "url": f"https://ici.radio-canada.ca/ohdio/premiere/{i}",
                "source_id": "radio-canada-quebec",
                "source_name": "Radio-Canada",
                "nest_role": "primary",
                "geo": "quebec-city",
                "rank_score": 0.5 - i * 0.01,
                "enrich": {"geo": {"geo": "quebec-city"}, "topics": [{"topic": "other"}], "impacts": []},
            }
            for i in range(16)
        ]
        html = rank_display.render_html(ranked, "2026-09-16T00:00:00+00:00", issues=[])
        targets = set(re.findall(r'href="#(pin-[^"]+)"', html))
        ids = set(re.findall(r'id="(pin-[^"]+)"', html))
        self.assertTrue(targets)
        self.assertLessEqual(targets, ids)

    def test_issue_counts_are_escaped_in_the_compass(self) -> None:
        issues = [{
            "issue_id": "i1",
            "question": "Que se passe-t-il ?",
            "scar": "scar",
            "source_count": "<img src=x onerror=alert(1)>",
            "tensions": [],
        }]
        html = rank_display.render_html([], "2026-09-16T00:00:00+00:00", issues=issues)
        self.assertNotIn("<img src=x onerror", html)


class ApproachBuilders(unittest.TestCase):
    def test_build_approaches_max_and_order(self) -> None:
        issues = [
            {
                "scar": f"s{i}",
                "question": f"Q{i}?",
                "geo_focus": ["quebec-city"] if i == 0 else ["quebec"],
                "source_count": 3,
                "media_remix": True,
                "silence": {"silent_count": 6},
                "tensions": [],
            }
            for i in range(7)
        ]
        ap = rank_display.build_approaches(issues, {"by_id": {}})
        self.assertEqual(len(ap), 5)
        self.assertEqual(ap[0]["question"], "Q0?")
        self.assertEqual(ap[0]["nest"], "near")
        self.assertEqual(ap[1]["nest"], "province")

    def test_approach_button_has_interaction_hooks(self) -> None:
        html = rank_display.approach_button_html(
            {
                "index": 2,
                "issue_id": "abc123",
                "question": "Faut-il arrêter le tramway?",
                "nest": "near",
                "voices": 3,
                "silent": 6,
                "official_silent": 3,
                "remix": True,
                "units": [{"raw": "loi 96"}],
            }
        )
        self.assertIn("class='approach approach-remix'", html)
        self.assertIn("data-i='2'", html)
        self.assertIn("data-issue-id='abc123'", html)
        self.assertIn("data-fp='3|6|1|1'", html)
        self.assertIn("Near me", html)
        self.assertIn("loi 96", html)
        self.assertIn("chip silence", html)
        self.assertIn("6 silent", html)
        self.assertIn("official quiet", html)
        self.assertIn("media remix", html)
        self.assertIn("approach-badge", html)


class SinceLeftDelta(unittest.TestCase):
    def test_fingerprint_and_delta(self) -> None:
        a = {"issue_id": "a", "voices": 3, "silent": 6, "remix": True}
        b = {"issue_id": "b", "voices": 2, "silent": 7, "remix": False}
        self.assertEqual(rank_display.approach_fingerprint(a), "3|6|1|0")
        prev = [a, b]
        curr = [
            {"issue_id": "a", "voices": 3, "silent": 5, "remix": True},  # changed silent
            {"issue_id": "c", "voices": 2, "silent": 7, "remix": False},  # new
        ]
        d = rank_display.since_left_delta(prev, curr)
        self.assertEqual(d["new"], ["c"])
        self.assertEqual(d["changed"], ["a"])
        self.assertEqual(d["gone"], ["b"])
        self.assertFalse(d["same"])
        same = rank_display.since_left_delta(curr, curr)
        self.assertTrue(same["same"])

    def test_delta_never_reorders_curr(self) -> None:
        prev = [{"issue_id": "z", "voices": 1, "silent": 0, "remix": False}]
        curr = [
            {"issue_id": "a", "voices": 1, "silent": 0, "remix": False},
            {"issue_id": "z", "voices": 1, "silent": 0, "remix": False},
        ]
        d = rank_display.since_left_delta(prev, curr)
        self.assertEqual(d["new"], ["a"])
        # curr order untouched — delta only reports ids
        self.assertEqual([c["issue_id"] for c in curr], ["a", "z"])


class ArrivalFirstPaint(unittest.TestCase):
    def test_render_arrival_not_dashboard_first(self) -> None:
        ranked = [
            {
                "id": "c1",
                "title": "Near me sample",
                "url": "https://example.test/1",
                "source_id": "le-soleil",
                "source_name": "Le Soleil",
                "nest_role": "primary",
                "geo": "quebec-city",
                "rank_score": 0.9,
                "enrich": {
                    "geo": {"geo": "quebec-city"},
                    "topics": [{"topic": "housing"}],
                    "impacts": [
                        {
                            "label": "housing",
                            "units": [
                                {
                                    "kind": "housing_count",
                                    "value": 120,
                                    "unit": "logements",
                                    "raw": "120 logements",
                                }
                            ],
                        }
                    ],
                },
            }
        ]
        issues = [
            {
                "scar": "marchand",
                "question": "Quelles priorités pour Québec sous Marchand ?",
                "geo_focus": ["quebec-city"],
                "source_count": 3,
                "media_remix": True,
                "silence": {"silent_count": 6},
                "topic": {"topic": "other"},
                "tensions": [
                    {
                        "label": "voice:Le Soleil",
                        "source_kind": "media",
                        "items": [{"candidate_id": "c1", "title": "x", "url": "https://example.test/1"}],
                    }
                ],
            }
        ]
        html = rank_display.render_html(ranked, "2026-09-16T00:00:00+00:00", issues=issues)
        # Arrival first
        self.assertIn('class="arrival"', html)
        self.assertIn('class="arrival-brand"', html)
        self.assertIn("What approaches Quebec City life", html)
        self.assertIn('id="approaches"', html)
        self.assertIn("class='approach", html)
        self.assertIn("Open lookout field", html)
        # Field hidden until invitation
        self.assertIn('class="field-shell"', html)
        self.assertIn("field-open", html)
        # Since you left hooks
        self.assertIn('id="since-left"', html)
        self.assertIn('id="vigie-pulse"', html)
        self.assertIn("data-issue-id=", html)
        self.assertIn("data-fp=", html)
        self.assertIn("Near me", html)
        self.assertIn("chip silence", html)
        # No personalization feed product
        low = html.lower()
        self.assertNotIn("for you", low)
        self.assertNotIn("for-you", low)
        self.assertIn("never a painted personalization feed", low)
        # Weights untouched
        self.assertEqual(rank_display.W_IMPACT, 0.0)
        self.assertEqual(rank_display.W_GEO, 0.60)
        # Brand before command field in document order
        self.assertLess(html.find("arrival-brand"), html.find('id="command"'))
        # Approach before Impact Radar panel in document order (not CSS class name)
        self.assertLess(html.find('id="approaches"'), html.find("data-panel='radar'"))
        # Unit from scar item surfaces on Approach
        self.assertIn("120 logements", html)
        # Friction method linked
        self.assertIn("/methode/frictions.html", html)
        self.assertIn("/methode/design.html", html)
        self.assertIn('data-design="beauty-without-fog-v0.1"', html)
        self.assertIn('id="arrival-ops"', html)

    def test_empty_issues_still_arrives(self) -> None:
        html = rank_display.render_html([], "2026-09-16T00:00:00+00:00", issues=[])
        self.assertIn("arrival-brand", html)
        self.assertIn("No Approaches yet", html)
        self.assertNotIn("for you", html.lower())
        self.assertIn("never a painted personalization feed", html.lower())


if __name__ == "__main__":
    unittest.main()
