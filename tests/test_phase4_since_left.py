"""Phase 4 — Since you left: store-timestamp delta on returning visit."""
from __future__ import annotations

import json
import re
import unittest

import harness  # noqa: F401

import rank_display


class Phase4SinceLeft(unittest.TestCase):
    def test_fingerprint_includes_units_and_normalizes_silent(self) -> None:
        self.assertEqual(
            rank_display.approach_fingerprint(
                {"voices": 2, "silent": None, "remix": False, "units": []}
            ),
            "2|0|0|0",
        )
        self.assertEqual(
            rank_display.approach_fingerprint(
                {
                    "voices": 3,
                    "silent": 6,
                    "remix": True,
                    "units": [{"raw": "120 logements"}],
                }
            ),
            "3|6|1|1",
        )
        self.assertEqual(
            rank_display.approach_fingerprint(
                {"voices": 3, "silent": 6, "remix": True, "unit_count": 2}
            ),
            "3|6|1|2",
        )

    def test_first_look_shows_store_clock(self) -> None:
        pulse = {
            "clustered_at": "2026-09-16T22:40:14.277469+00:00",
            "approaches": [
                {"issue_id": "a", "voices": 2, "silent": 7, "remix": False, "fp": "2|7|0|0"}
            ],
        }
        visit = rank_display.since_left_visit(None, pulse)
        self.assertEqual(visit["kind"], "first")
        self.assertIn("First look", visit["message"])
        self.assertIn("2026-09-16 22:40 UTC", visit["message"])
        self.assertEqual(visit["badges"], {})

    def test_same_timestamp_does_not_hide_changed_fingerprint(self) -> None:
        """A reused store timestamp cannot hide a changed content fingerprint."""
        pulse = {
            "clustered_at": "2026-09-16T22:40:14+00:00",
            "approaches": [
                {"issue_id": "a", "voices": 3, "silent": 6, "remix": True, "fp": "3|6|1|0"}
            ],
        }
        prev = {
            "clustered_at": "2026-09-16T22:40:14+00:00",
            "approaches": [
                # Old 3-part fp would otherwise look "changed"
                {"issue_id": "a", "voices": 3, "silent": 6, "remix": True, "fp": "3|6|1"}
            ],
        }
        visit = rank_display.since_left_visit(prev, pulse)
        self.assertEqual(visit["kind"], "delta")
        self.assertIn("1 changed", visit["message"])
        self.assertIn("2026-09-16 22:40 UTC", visit["message"])
        self.assertEqual(visit["badges"], {"a": "changed"})

    def test_returning_visit_shows_delta_badges(self) -> None:
        prev = {
            "clustered_at": "2026-09-15T12:00:00+00:00",
            "approaches": [
                {"issue_id": "a", "voices": 3, "silent": 6, "remix": True, "fp": "3|6|1|0"},
                {"issue_id": "b", "voices": 2, "silent": 7, "remix": False, "fp": "2|7|0|0"},
            ],
        }
        pulse = {
            "clustered_at": "2026-09-16T22:40:14+00:00",
            "approaches": [
                {"issue_id": "a", "voices": 3, "silent": 5, "remix": True, "fp": "3|5|1|0"},
                {"issue_id": "c", "voices": 2, "silent": 7, "remix": False, "fp": "2|7|0|0"},
            ],
        }
        visit = rank_display.since_left_visit(prev, pulse)
        self.assertEqual(visit["kind"], "delta")
        self.assertEqual(visit["badges"], {"a": "changed", "c": "new"})
        self.assertEqual(visit["delta"]["gone"], ["b"])
        self.assertIn("1 new", visit["message"])
        self.assertIn("1 changed", visit["message"])
        self.assertIn("1 left Approaches", visit["message"])
        self.assertIn("2026-09-16 22:40 UTC", visit["message"])
        self.assertIn("2026-09-15 12:00 UTC", visit["message"])
        self.assertIn("order unchanged", visit["message"])
        # Curr order untouched
        self.assertEqual(
            [a["issue_id"] for a in pulse["approaches"]],
            ["a", "c"],
        )

    def test_pulse_payload_carries_store_clock_and_unit_count(self) -> None:
        ap = [
            {
                "issue_id": "x",
                "scar": "marchand",
                "voices": 3,
                "silent": 6,
                "remix": True,
                "units": [{"raw": "loi 96"}],
            }
        ]
        pulse = rank_display.pulse_payload(ap, "2026-09-16T22:40:14+00:00")
        self.assertEqual(pulse["clustered_at"], "2026-09-16T22:40:14+00:00")
        self.assertEqual(pulse["approaches"][0]["unit_count"], 1)
        self.assertEqual(pulse["approaches"][0]["fp"], "3|6|1|1")

    def test_live_arrival_hooks_store_timestamp_contract(self) -> None:
        path = harness.ROOT / "public" / "explorer.html"
        if not path.is_file():
            self.skipTest("no lookout")
        html = path.read_text(encoding="utf-8")
        self.assertIn('id="since-left"', html)
        self.assertIn('id="vigie-pulse"', html)
        self.assertIn("formatStoreClock", html)
        self.assertIn("prev.clustered_at", html)
        self.assertIn("store pulse", html)
        self.assertIn("left Approaches", html)
        raw = re.search(r'id="vigie-pulse">(.*?)</script>', html, re.S)
        self.assertIsNotNone(raw)
        pulse = json.loads(raw.group(1))
        self.assertIn("clustered_at", pulse)
        # Contract: the arrival pulse carries the issues store's collection
        # clock verbatim (never the build clock). When a store with a clock is
        # on disk, the embedded pulse must match it exactly.
        issues_store = harness.ROOT / "data" / "issues" / "latest_issues.json"
        if issues_store.is_file():
            try:
                doc = json.loads(issues_store.read_text(encoding="utf-8"))
            except ValueError:
                doc = {}
            if isinstance(doc, dict) and doc.get("clustered_at"):
                self.assertEqual(pulse.get("clustered_at"), str(doc["clustered_at"]))
        for a in pulse.get("approaches") or []:
            self.assertIn("fp", a)
            self.assertIn("unit_count", a)
            # fp v2: voices|silent|remix|unit_count, plus an optional
            # content-aware fingerprint segment when the store carries one.
            self.assertIn(len(str(a["fp"]).split("|")), (4, 5))
        self.assertEqual(rank_display.W_IMPACT, 0.0)
        low = html.lower()
        self.assertNotIn("for you", low)


if __name__ == "__main__":
    unittest.main()
