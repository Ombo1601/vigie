"""Beauty without fog — Arrival design law (DESIGN.md)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import harness

import rank_display


ROOT = harness.ROOT


def _arrival_slice(html: str) -> str:
    m = re.search(r'<section class="arrival"[^>]*>.*?</section>', html, re.S)
    assert m, "arrival section missing"
    return m.group(0)


class DesignLawPublished(unittest.TestCase):
    def test_design_md_on_disk(self) -> None:
        text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
        self.assertIn("Beauty without fog", text)
        self.assertIn("beauty-without-fog-v0.1", text)
        self.assertIn("Do not", text)
        self.assertIn("Invent news", text)
        self.assertIn("public/place/", text)


class ArrivalBeautyWithoutFog(unittest.TestCase):
    def _html(self) -> str:
        issues = [
            {
                "scar": "marchand",
                "issue_id": "iss1",
                "question": "Quelles priorités pour Québec sous Marchand ?",
                "geo_focus": ["quebec-city"],
                "source_count": 3,
                "media_remix": False,
                "silence": {"silent_count": 6, "silent": []},
                "topic": {"topic": "other"},
                "tensions": [],
            }
        ]
        return rank_display.render_html([], "2026-09-16T00:00:00+00:00", issues=issues)

    def test_composition_order_and_brand_hero(self) -> None:
        html = self._html()
        arrival = _arrival_slice(html)
        self.assertIn('data-design="beauty-without-fog-v0.1"', arrival)
        self.assertIn("/methode/design.html", html)
        # Brand before line before approaches before facets (opt-in after CTA)
        self.assertLess(arrival.find("arrival-brand"), arrival.find("arrival-line"))
        self.assertLess(arrival.find('id="approaches"'), arrival.find('id="life-facets"'))
        self.assertLess(arrival.find('id="approaches"'), arrival.find("arrival-cta"))
        self.assertLess(arrival.find("arrival-cta"), arrival.find('id="life-facets"'))
        # Brand clamp floor > line clamp floor (hero signal)
        self.assertIn("clamp(3.6rem", html)
        self.assertIn("clamp(1.25rem", html)
        self.assertIn("Newsreader", html)
        self.assertIn("Figtree", html)
        self.assertNotIn("#e8e4ef", html.lower())

    def test_refuse_cliche_palette_and_glow_fog(self) -> None:
        html = self._html()
        low = html.lower()
        # Cream / purple-indigo cliche hexes (word "purple" may appear in method prose elsewhere)
        self.assertNotIn("#f3f0e8", low)
        self.assertNotIn("#f4f1ea", low)
        self.assertNotIn("#7c3aed", low)
        self.assertNotIn("#6366f1", low)
        self.assertNotIn("#8b5cf6", low)
        # Multi-layer radial glow theater removed from body wash
        self.assertNotIn("radial-gradient", low)
        self.assertIn("beauty-without-fog-v0.1", low)
        self.assertIn("--nest-near", html)
        self.assertIn("prefers-reduced-motion", low)

    def test_hero_has_no_score_worship_or_bias_meters(self) -> None:
        html = self._html()
        arrival = _arrival_slice(html)
        low = arrival.lower()
        self.assertNotIn("class='score'", low)
        self.assertNotIn('class="score"', low)
        self.assertNotIn("rank_score", low)
        self.assertNotIn("trust meter", low)
        self.assertNotIn("bias", low)
        self.assertNotIn("engagement", low)
        self.assertNotIn("for-you", low)
        self.assertNotIn("for you", low)
        self.assertEqual(rank_display.W_IMPACT, 0.0)

    def test_nest_depth_on_approach_interaction(self) -> None:
        html = self._html()
        self.assertIn("data-nest='near'", html)
        self.assertIn("box-shadow: inset 3px 0 0 var(--nest-near)", html)
        # Approach is the interaction container
        self.assertIn("class='approach'", html)

    def test_approach_button_exposes_nest(self) -> None:
        html = rank_display.approach_button_html(
            {
                "index": 0,
                "issue_id": "x",
                "question": "Q?",
                "nest": "province",
                "voices": 2,
                "silent": 1,
                "units": [],
            }
        )
        self.assertIn("data-nest='province'", html)


if __name__ == "__main__":
    unittest.main()
