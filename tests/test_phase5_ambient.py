"""Phase 5 — Ambient pulse: same store as Arrival + Stage; widget twin; no second ranking."""
from __future__ import annotations

import json
import re
import unittest

import harness  # noqa: F401

import ambient_pulse
import rank_display


class Phase5AmbientPulse(unittest.TestCase):
    def test_method_names_arrival_and_stage(self) -> None:
        self.assertIn("arrival-and-stage", ambient_pulse.METHOD)
        self.assertIn("Stage fights", ambient_pulse.NOTE)
        self.assertIn("Not a second ranking", ambient_pulse.NOTE)
        low = ambient_pulse.NOTE.lower()
        self.assertNotIn("for you", low)
        self.assertEqual(rank_display.W_IMPACT, 0.0)

    def test_pulse_and_stage_twins_on_sample(self) -> None:
        ranked = [
            {
                "id": "c1",
                "title": "t",
                "url": "https://example.test/1",
                "enrich": {"geo": {"geo": "quebec-city"}, "impacts": []},
            }
        ]
        issues = [
            {
                "issue_id": "iss-a",
                "scar": "marchand",
                "question": "Quelles priorités pour Québec sous Marchand ?",
                "geo_focus": ["quebec-city"],
                "source_count": 2,
                "clustered_at": "2026-09-16T12:00:00+00:00",
                "silence": {"silent_count": 4, "silent": []},
                "tensions": [],
            },
            {
                "issue_id": "iss-b",
                "scar": "airport",
                "question": "Le Canada doit-il privatiser ses grands aéroports ?",
                "geo_focus": ["quebec"],
                "source_count": 2,
                "silence": {"silent_count": 5, "silent": []},
                "tensions": [],
            },
        ]
        digest = ambient_pulse.build_digest(issues, ranked, built_at="t0")
        approaches = rank_display.build_approaches(
            issues, rank_display.build_continuity(issues, ranked)
        )
        self.assertTrue(ambient_pulse.digest_matches_approaches(digest, approaches))
        self.assertTrue(ambient_pulse.digest_matches_arrival_pulse(digest, approaches))
        self.assertTrue(ambient_pulse.digest_matches_stage_fights(digest, issues))
        self.assertEqual(
            ambient_pulse.stage_fight_issue_ids(issues),
            ["iss-a", "iss-b"],
        )

    def test_widget_is_store_copy_not_llm(self) -> None:
        digest = ambient_pulse.build_digest(
            [
                {
                    "issue_id": "iss-a",
                    "scar": "housing",
                    "question": "Le logement abordable avance-t-il ?",
                    "geo_focus": ["quebec"],
                    "source_count": 2,
                    "clustered_at": "2026-09-16T22:40:14+00:00",
                    "silence": {
                        "silent_count": 3,
                        "silent": [
                            {
                                "source_kind": "official",
                                "institution_name": "Ville de Québec",
                            }
                        ],
                    },
                    "tensions": [],
                }
            ],
            [],
            built_at="t0",
        )
        widget = ambient_pulse.render_morning_widget(digest)
        self.assertIn("Vigie morning", widget)
        self.assertIn("same pulse as Arrival + Stage", widget)
        self.assertIn("no second ranking", widget)
        self.assertIn("Le logement abordable avance-t-il ?", widget)
        self.assertIn("2v/3s", widget)
        self.assertNotIn("for you", widget.lower())
        # No invented prose beyond store question
        self.assertNotIn("Here's what matters", widget)
        self.assertNotIn("AI summary", widget.lower())
        empty_w = ambient_pulse.render_morning_widget({"clustered_at": "", "approaches": []})
        self.assertIn("no Approaches", empty_w)
        long_q = "Q" * 80
        long_w = ambient_pulse.render_morning_widget(
            {
                "clustered_at": "t",
                "approaches": [
                    {
                        "nest": "linked",
                        "question": long_q,
                        "voices": 1,
                        "silent": 0,
                        "units": [{"raw": "x"}],
                    }
                ],
            }
        )
        self.assertIn("…", long_w)
        self.assertIn("1v/0s", long_w)
        self.assertIn(" · x", long_w)

    def test_store_clock_is_shared_twin_when_issues_empty(self) -> None:
        """An empty issues store still carries the collection clock at its top
        level; both pulses must read it — never the build clock."""
        clock = "2026-09-24T14:38:03.081754+00:00"
        self.assertEqual(rank_display.resolve_store_clock(clock, []), clock)
        self.assertEqual(rank_display.resolve_store_clock(None, []), "")
        self.assertEqual(
            rank_display.resolve_store_clock(None, [{"clustered_at": clock}]), clock
        )
        digest = ambient_pulse.build_digest([], [], clustered_at=clock, built_at="t0")
        self.assertEqual(digest["clustered_at"], clock)
        self.assertEqual(digest["pulse"]["clustered_at"], clock)
        approaches = rank_display.build_approaches([], rank_display.build_continuity([], []))
        arrival = rank_display.pulse_payload(
            approaches, rank_display.resolve_store_clock(clock, [])
        )
        self.assertEqual(arrival["clustered_at"], digest["pulse"]["clustered_at"])

    def test_live_files_twin_arrival_pulse(self) -> None:
        morning = harness.ROOT / "data" / "pulse" / "latest_morning.json"
        widget = harness.ROOT / "data" / "pulse" / "latest_morning.widget.txt"
        index = harness.ROOT / "public" / "explorer.html"
        if not index.is_file():
            index = harness.ROOT / "public" / "index.html"
        if not morning.is_file() or not index.is_file():
            self.skipTest("store/lookout missing")
        digest = json.loads(morning.read_text(encoding="utf-8"))
        raw = re.search(
            r'id="vigie-pulse">(.*?)</script>',
            index.read_text(encoding="utf-8"),
            re.S,
        )
        self.assertIsNotNone(raw)
        arrival_pulse = json.loads(raw.group(1))
        self.assertEqual(arrival_pulse.get("approaches"), (digest.get("pulse") or {}).get("approaches"))
        self.assertEqual(arrival_pulse.get("clustered_at"), digest.get("clustered_at"))
        self.assertIn("arrival-and-stage", str(digest.get("method") or ""))
        if widget.is_file():
            text = widget.read_text(encoding="utf-8")
            self.assertIn("no second ranking", text.lower())
            self.assertIn("Vigie morning", text)


if __name__ == "__main__":
    unittest.main()
