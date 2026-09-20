"""Beauty without fog — deep Do / Do-not verification (DESIGN.md)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import harness  # noqa: F401

import rank_display

ROOT = harness.ROOT


def _arrival(html: str) -> str:
    m = re.search(r'<section class="arrival"[^>]*>.*?</section>', html, re.S)
    assert m, "missing arrival"
    return m.group(0)


def _font_stacks(html: str) -> str:
    bits = re.findall(r"--font-[^:]+:\s*([^;]+);", html)
    bits += re.findall(r"font-family:\s*([^;]+);", html)
    return "\n".join(bits)


class BeautyWithoutFogDoD(unittest.TestCase):
    def test_design_md_law_complete(self) -> None:
        text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
        self.assertIn("beauty-without-fog-v0.1", text)
        for needle in (
            "One composition",
            "Brand as hero",
            "Expressive type",
            "Atmospheric nest depth",
            "Full-bleed place atmosphere",
            "Cards only as interaction",
            "Dashboard chrome",
            "Bias rainbows",
            "Inset hero collage",
            "Invent news",
            "For You",
            "w_impact",
            "Named place images",
            "None yet",
        ):
            self.assertIn(needle, text)

    def test_no_invented_place_image(self) -> None:
        place = ROOT / "public" / "place"
        if place.is_dir():
            files = [p for p in place.rglob("*") if p.is_file()]
            self.assertEqual(
                files,
                [],
                f"unnamed place files not allowed until listed in DESIGN.md: {files}",
            )

    def test_live_and_rendered_composition(self) -> None:
        issues = [
            {
                "scar": "marchand",
                "issue_id": "iss1",
                "question": "Quelles priorités pour Québec sous Marchand ?",
                "geo_focus": ["quebec-city"],
                "source_count": 3,
                "silence": {"silent_count": 6, "silent": []},
                "topic": {"topic": "other"},
                "tensions": [],
            }
        ]
        html = rank_display.render_html([], "2026-09-16T00:00:00+00:00", issues=issues)
        arrival = _arrival(html)
        # Precise DOM markers — not CSS id mentions
        order = [
            'class="arrival-brand"',
            'class="arrival-line"',
            'class="arrival-sub"',
            'id="approaches"',
            'class="arrival-cta"',
            'id="life-facets"',
            'id="arrival-ops"',
        ]
        positions = [html.find(m) for m in order]
        self.assertTrue(all(p >= 0 for p in positions), positions)
        self.assertEqual(positions, sorted(positions))
        self.assertIn('data-design="beauty-without-fog-v0.1"', arrival)
        self.assertIn("/methode/design.html", html)
        # Brand hero floors
        self.assertIn("clamp(3.6rem", html)
        self.assertIn("clamp(1.25rem", html)
        brand_floor = float(
            re.search(r"\.arrival-brand\s*\{[^}]*clamp\(([0-9.]+)rem", html).group(1)
        )
        line_floor = float(
            re.search(r"\.arrival-line\s*\{[^}]*clamp\(([0-9.]+)rem", html).group(1)
        )
        self.assertGreater(brand_floor, line_floor)
        # Interaction card only
        self.assertNotIn("class='card'", arrival)
        self.assertNotIn('class="card"', arrival)
        self.assertIn("class='approach", arrival)
        self.assertIn("data-nest=", arrival)
        self.assertEqual(rank_display.W_IMPACT, 0.0)

    def test_refuse_fog_palette_type_glow(self) -> None:
        html = rank_display.render_html([], "2026-09-16T00:00:00+00:00", issues=[])
        low = html.lower()
        for bad in (
            "#f3f0e8",
            "#f4f1ea",
            "#7c3aed",
            "#6366f1",
            "#8b5cf6",
            "#e8e4ef",  # lavender silence chrome removed
            "radial-gradient",
            "text-shadow",
        ):
            self.assertNotIn(bad, low)
        stacks = _font_stacks(html)
        for banned in ("Inter", "Roboto", "Arial", "Helvetica Neue", "system-ui"):
            self.assertNotIn(banned, stacks)
        self.assertIn("Newsreader", stacks)
        self.assertIn("Figtree", stacks)
        self.assertIn("--nest-near", html)
        self.assertIn("prefers-reduced-motion", low)
        # Single-layer nest wash only
        self.assertEqual(low.count("linear-gradient"), 1)

    def test_arrival_do_not_score_bias_promo(self) -> None:
        html = rank_display.render_html(
            [],
            "2026-09-16T00:00:00+00:00",
            issues=[
                {
                    "issue_id": "i",
                    "scar": "s",
                    "question": "Q?",
                    "geo_focus": ["quebec"],
                    "source_count": 2,
                    "silence": {"silent_count": 1, "silent": []},
                    "tensions": [],
                }
            ],
        )
        arrival = _arrival(html).lower()
        for bad in (
            "rank_score",
            "class='score'",
            'class="score"',
            "trust meter",
            "bias",
            "engagement",
            "for you",
            "for-you",
            "crowned",
            "top story",
            "promo",
            "streak",
        ):
            self.assertNotIn(bad, arrival)

    def test_live_public_index_if_present(self) -> None:
        path = ROOT / "public" / "explorer.html"
        if not path.is_file():
            self.skipTest("no lookout")
        html = path.read_text(encoding="utf-8")
        self.assertIn('data-design="beauty-without-fog-v0.1"', html)
        self.assertLess(html.find('class="arrival-cta"'), html.find('id="life-facets"'))
        self.assertNotIn("radial-gradient", html.lower())
        self.assertNotIn("#e8e4ef", html.lower())
        self.assertTrue((ROOT / "DESIGN.md").is_file())


if __name__ == "__main__":
    unittest.main()
