"""Audit regressions (2026-09-22).

Locks defects the stress pass confirmed: the phone masthead dropping its last
link, unmapped road impacts sorting as milder than an open road, the glance
counting a speaker as silent, singular silence-ledger grammar, roadwork
descriptions over the 200-character cap, memory-edition pages without a CSP,
and the method index (and sealed memory pages) missing from the sitemap.
"""
from __future__ import annotations

import html
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import affiche
import depart
import ingest_civic
import method_site
import recits
import resident_brief as brief
import stage_public
import substrate

ROOT = harness.ROOT
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _event(eid: str, impact: str, road: str) -> dict:
    return {
        "event_id": eid,
        "event_type": "work-zone",
        "event_status": "active",
        "vehicle_impact": impact,
        "road_names": [road],
        "direction": "both-directions",
        "start_date": "2026-09-10T04:00:00Z",
        "end_date": "2026-10-01T03:59:59Z",
        "description": "Réfection",
        "update_date": "2026-09-22T11:00:00Z",
    }


def _store(events: list[dict]) -> dict:
    return {
        "method": "wzdx-roadworks-v2",
        "fetched_at": NOW.isoformat(),
        "institution_name": "Ville de Québec",
        "events": events,
        "counts": {"active": len(events)},
    }


class MastheadAndToast(unittest.TestCase):
    def test_phone_nav_keeps_every_link_and_toast_wraps(self) -> None:
        css = (ROOT / "public" / "assets" / "brief.css").read_text(encoding="utf-8")
        self.assertNotIn("nav a:last-child{display:none}", css)
        self.assertIn("overflow-x:auto", css)
        toast = css[css.index("#toast{"):css.index("#toast:empty")]
        self.assertIn("white-space:normal", toast)
        self.assertNotIn("nowrap", toast)


class UnmappedImpactSortsBeforeOpenRoads(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            _event("open", "all-lanes-open", "Rue Ouverte"),
            _event("mystery", "mystery-impact", "Rue Inconnue"),
            _event("closed", "all-lanes-closed", "Rue Fermee"),
        ]

    def test_rank_puts_unknown_between_closures_and_open_roads(self) -> None:
        self.assertLess(brief.rw_sort_rank("all-lanes-closed"), brief.rw_sort_rank("mystery-impact"))
        self.assertLess(brief.rw_sort_rank("mystery-impact"), brief.rw_sort_rank("all-lanes-open"))
        self.assertLess(brief.rw_sort_rank("all-lanes-open"), brief.rw_sort_rank("no-lanes-closed"))
        # The island and the CSS class still treat unknown as severity 6.
        self.assertEqual(brief.RW_SEVERITY.get("mystery-impact", 6), 6)

    def test_brief_depart_affiche_and_substrate_share_that_order(self) -> None:
        page = brief.roadworks_section(_store(self.events), NOW)
        self.assertLess(page.index("Rue Fermee"), page.index("Rue Inconnue"))
        self.assertLess(page.index("Rue Inconnue"), page.index("Rue Ouverte"))

        ordered = depart._ordered_events(self.events)
        self.assertEqual([e["event_id"] for e in ordered], ["closed", "mystery", "open"])

        shown = affiche.roads_rows(_store(self.events))
        self.assertEqual([e["event_id"] for e in shown], ["closed", "mystery", "open"])

        view = substrate.roadworks_view(_store(self.events), cap=8)
        self.assertEqual(
            [row["event_id"] for row in view["most_restrictive"]],
            ["closed", "mystery", "open"],
        )

    def test_a_street_with_an_open_and_an_unmapped_event_is_not_called_open(self) -> None:
        rows = depart._street_index([
            _event("open", "all-lanes-open", "Rue Mixte"),
            _event("mystery", "mystery-impact", "Rue Mixte"),
        ])
        row = rows[0]
        self.assertEqual(row["n"], 2)
        self.assertFalse(row["open"])
        self.assertEqual(row["impact"], "mystery-impact")
        self.assertEqual(row["severity"], 6)


class DescriptionCap(unittest.TestCase):
    def test_machine_plain_counts_the_ellipsis(self) -> None:
        clipped = substrate._plain("A" * 50, 10)
        self.assertEqual(len(clipped), 10)
        self.assertTrue(clipped.endswith("…"))

    def test_ellipsis_counts_toward_the_200_character_cap(self) -> None:
        word = "A" * 250
        event = _event("e", "some-lanes-closed", "Rue Test")
        event["description"] = word
        page = brief.roadworks_section(_store([event]), NOW)
        desc = html.unescape(re.search(r'class="rw-desc">(.*?)</p>', page).group(1))
        self.assertLessEqual(len(desc), 200)
        self.assertTrue(desc.endswith("…"))
        self.assertEqual(len(desc), 200)

    def test_recit_and_civic_titles_count_the_ellipsis(self) -> None:
        block = recits._voice_block({
            "institution_name": "Le Soleil",
            "items": [{"title": "T" * 400, "url": "https://example.com/a"}],
        })
        self.assertIn("T" * (brief.TITLE_CAP - 1) + "…", block)
        self.assertNotIn("T" * brief.TITLE_CAP, block)
        civic = ingest_civic._cap("A" * 400, ingest_civic.TITLE_CAP)
        self.assertEqual(len(civic), ingest_civic.TITLE_CAP)
        self.assertTrue(civic.endswith("…"))
        self.assertEqual(ingest_civic._cap("court", 300), "court")

    def test_a_short_description_is_relayed_whole(self) -> None:
        page = brief.roadworks_section(_store([_event("e", "some-lanes-closed", "Rue Test")]), NOW)
        self.assertIn("Réfection", page)
        self.assertNotIn("…", re.search(r'class="rw-desc">(.*?)</p>', page).group(1))


class SilenceFacts(unittest.TestCase):
    def test_glance_does_not_count_a_speaker_as_silent(self) -> None:
        issues = [{
            "tensions": [{"institution_name": "Le Soleil"}],
            "silence": {"silent": [
                {"institution_name": "Ville de Québec"},
                {"institution_name": "Le Soleil"},
            ]},
        }]
        page = brief.digest_html(
            [{"geo": "quebec-city"}], {}, None, None, issues, NOW, has_changes=False)
        self.assertIn("<strong>1</strong> institution suivie n’a pas parlé", page)
        self.assertNotIn("2 institutions suivies", page)

    def test_ledger_labels_agree_with_a_single_voice(self) -> None:
        page = brief.silence_bar([{
            "tensions": [{"institution_name": "Le Soleil", "source_kind": "media"}],
            "silence": {"silent": [{"institution_name": "Ville de Québec", "source_kind": "official"}]},
        }])
        self.assertIn(">A parlé<", page)
        self.assertIn(">N’a pas parlé<", page)
        self.assertNotIn(">Ont parlé<", page)
        self.assertNotIn(">N’ont pas parlé<", page)

    def test_official_mark_survives_a_media_row_for_the_same_seat(self) -> None:
        page = brief.silence_bar([
            {"tensions": [{"institution_name": "Ville de Québec", "source_kind": "media"}]},
            {
                "tensions": [{"institution_name": "Ville de Québec", "source_kind": "official"}],
                "silence": {"silent": [{"institution_name": "Le Devoir"}]},
            },
        ])
        self.assertIn('class="sb-name sb-official" title="Ville de Québec institution officielle"', page)


class Discoverability(unittest.TestCase):
    def test_method_index_and_memory_editions_are_in_the_sitemap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<p>x</p>", encoding="utf-8")
            method_site.emit(root / "methode")
            (root / "memoire").mkdir()
            (root / "memoire" / "3.html").write_text("<p>e</p>", encoding="utf-8")
            xml = stage_public.sitemap_xml(root)
        self.assertIn("https://vigieqc.com/methode/index.html", xml)
        self.assertIn("https://vigieqc.com/memoire/3.html", xml)
        self.assertIn("https://vigieqc.com/methode/legal.html", xml)


if __name__ == "__main__":
    unittest.main()
