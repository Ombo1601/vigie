"""Civic HTML collector — participation calendar, WZDX-shaped.

House law under test: robots first, IdProjet identity, verbatim titles/windows,
no invented ISO dates, fail-soft keep-previous, never ranked with articles,
RSS ceiling untouched.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import harness  # noqa: F401

import ingest_civic
import ingest_rss
import pipeline
import resident_brief as brief

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
PAGE = "https://www.ville.quebec.qc.ca/citoyens/participation-citoyenne/activites/index.aspx"
FICHE = "https://www.ville.quebec.qc.ca/citoyens/participation-citoyenne/activites/fiche.aspx?IdProjet=303"

CITY_HTML = """<!doctype html><html><body>
<h2>Prochaines activités</h2>
<table class="calendrier-activites" id="activites_entrepeneurs_resume">
<tbody>
<tr>
  <th class="date-jour"><span class="date-a-partir">Jusqu'au <br /><span>27</span><br />septembre</span></th>
  <td>
    <h3><a href="/citoyens/participation-citoyenne/activites/fiche.aspx?IdProjet=303">R&eacute;am&eacute;nagement de la 8<sup>e</sup> Rue</a></h3>
    <p>Consultation en ligne, entre la 3<sup>e</sup> Avenue et la 4<sup>e</sup> Avenue (2026)</p>
  </td>
</tr>
<tr>
  <th class="date-jour"><span class="date-a-partir">Jusqu'au <br /><span>5</span><br />octobre</span></th>
  <td>
    <h3><a href="/citoyens/participation-citoyenne/activites/fiche.aspx?IdProjet=1099">Contingentement des restaurants dans le Vieux-Port</a></h3>
    <p>Consultation écrite</p>
  </td>
</tr>
</tbody>
</table>
</body></html>
"""

SRC = {
    "id": "ville-quebec-participation",
    "name": "Ville de Québec — Participation citoyenne",
    "institution_name": "Ville de Québec",
    "url": PAGE,
    "homepage": "https://participationcitoyenne.ville.quebec.qc.ca/",
    "license_note": "Crown copyright",
}


def _404(url: str) -> None:
    raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=io.BytesIO())


def fetch_ok(url: str):
    if url.endswith("robots.txt"):
        _404(url)
    return CITY_HTML.encode("utf-8"), "text/html; charset=utf-8"


class ParseCalendar(unittest.TestCase):
    def test_parses_idprojet_title_window_and_mode_verbatim(self) -> None:
        events, counts = ingest_civic.parse_activities(CITY_HTML, PAGE)
        self.assertIsNone(counts.get("parse_error"))
        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual(first["event_id"], "303")
        self.assertEqual(first["title"], "Réaménagement de la 8e Rue")
        self.assertEqual(first["window_text"], "Jusqu'au 27 septembre")
        self.assertIn("Consultation en ligne", first["mode_text"])
        self.assertEqual(first["url"], FICHE)
        self.assertNotIn("2026-09-27", json.dumps(first))

    def test_foreign_html_is_a_parse_error_not_an_empty_calendar(self) -> None:
        events, counts = ingest_civic.parse_activities("<html><p>bonjour</p></html>", PAGE)
        self.assertEqual(events, [])
        self.assertEqual(counts.get("parse_error"), "no_activity_table")

    def test_javascript_href_is_dropped(self) -> None:
        html = CITY_HTML.replace(
            "/citoyens/participation-citoyenne/activites/fiche.aspx?IdProjet=303",
            "javascript:alert(1)",
        )
        events, counts = ingest_civic.parse_activities(html, PAGE)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "1099")
        self.assertGreaterEqual(counts["unsafe_url"] + counts["missing_identifier"], 1)


class RobotsLaw(unittest.TestCase):
    def test_absent_robots_file_allows(self) -> None:
        self.assertTrue(ingest_civic.path_allowed(None, PAGE))

    def test_disallow_blocks_the_calendar_path(self) -> None:
        text = "User-agent: *\nDisallow: /citoyens/\n"
        self.assertFalse(ingest_civic.path_allowed(text, PAGE))

    def test_unrelated_disallow_does_not_block(self) -> None:
        text = "User-agent: *\nDisallow: /admin/\n"
        self.assertTrue(ingest_civic.path_allowed(text, PAGE))


class CollectFailSoft(unittest.TestCase):
    def test_successful_collect_writes_store_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = root / "civic.json"
            result = ingest_civic.collect(
                [SRC], NOW, raw_dir=root / "raw", store_path=store, fetch=fetch_ok,
            )
            self.assertTrue(result["ok"])
            doc = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(doc["method"], ingest_civic.METHOD)
            self.assertEqual(len(doc["events"]), 2)
            self.assertFalse(doc["diff"]["has_previous"])
            htmls = list((root / "raw" / SRC["id"]).glob("*.html"))
            self.assertEqual(len(htmls), 1)

    def test_robots_disallow_keeps_previous_store(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = root / "civic.json"
            first = ingest_civic.collect(
                [SRC], NOW, raw_dir=root / "raw", store_path=store, fetch=fetch_ok,
            )
            self.assertTrue(first["ok"])
            before = store.read_bytes()

            def blocked(url: str):
                if url.endswith("robots.txt"):
                    return b"User-agent: *\nDisallow: /\n", "text/plain"
                return CITY_HTML.encode("utf-8"), "text/html"

            second = ingest_civic.collect(
                [SRC], NOW, raw_dir=root / "raw", store_path=store, fetch=blocked,
            )
            self.assertFalse(second["ok"])
            self.assertEqual(second["reason"], "robots_disallow")
            self.assertEqual(store.read_bytes(), before)

    def test_fetch_failure_keeps_previous_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = root / "civic.json"
            ingest_civic.collect([SRC], NOW, raw_dir=root / "raw", store_path=store, fetch=fetch_ok)

            def boom(url: str):
                if url.endswith("robots.txt"):
                    _404(url)
                raise TimeoutError("nope")

            result = ingest_civic.collect(
                [SRC], NOW, raw_dir=root / "raw", store_path=store, fetch=boom,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "fetch_failed")
            self.assertTrue(store.exists())

    def test_removed_event_is_absent_never_closed(self) -> None:
        current = [{"event_id": "1", "title": "A", "window_text": "x", "mode_text": "", "url": FICHE}]
        previous = [
            {"event_id": "1", "title": "A", "window_text": "x", "mode_text": "", "url": FICHE},
            {"event_id": "2", "title": "B", "window_text": "x", "mode_text": "", "url": FICHE},
        ]
        diff = ingest_civic.diff_events(current, previous)
        self.assertEqual(diff["removed"], ["2"])
        blob = json.dumps(diff)
        self.assertNotIn("ended", blob)
        self.assertNotIn("resolved", blob)
        self.assertNotIn("closed", blob)

    def test_main_always_exits_zero(self) -> None:
        with patch.object(ingest_civic, "collect", return_value={"ok": False, "reason": "fetch_failed"}):
            self.assertEqual(ingest_civic.main(["--offline"]), 0)


class ChancelleryCeiling(unittest.TestCase):
    def test_civic_source_is_enabled_and_outside_rss_ceiling(self) -> None:
        rss = ingest_rss.load_enabled_rss(harness.ROOT / "sources.yaml")
        civic = ingest_rss.load_enabled_by_type(harness.ROOT / "sources.yaml", "civic-html")
        self.assertEqual(len(rss), 12)
        self.assertEqual([s["id"] for s in civic], ["ville-quebec-participation"])
        self.assertEqual(civic[0]["type"], "civic-html")

    def test_pipeline_runs_civic_before_feed_health(self) -> None:
        s = pipeline.SCRIPTS
        self.assertLess(s.index("ingest_civic.py"), s.index("feed_health.py"))
        self.assertLess(s.index("ingest_wzdx.py"), s.index("ingest_civic.py"))


class CivicBrief(unittest.TestCase):
    def test_section_absent_by_default(self) -> None:
        page = brief.render_brief([], NOW.isoformat(), [], None)
        self.assertNotIn('id="participation"', page)
        self.assertNotIn("Consultations publiques.", page)

    def test_renders_verbatim_with_attribution_never_in_masthead(self) -> None:
        store = {
            "method": ingest_civic.METHOD,
            "fetched_at": NOW.isoformat(),
            "attribution": "Ville de Québec",
            "source_url": PAGE,
            "homepage": "https://participationcitoyenne.ville.quebec.qc.ca/",
            "events": [{
                "event_id": "303",
                "title": "Réaménagement de la 8e Rue",
                "window_text": "Jusqu'au 27 septembre",
                "mode_text": "Consultation en ligne",
                "url": FICHE,
            }],
            "diff": {"has_previous": True, "new_count": 1, "removed_count": 0, "changed_count": 0},
        }
        page = brief.render_brief([], NOW.isoformat(), [], None, civic=store)
        self.assertIn('id="participation"', page)
        self.assertIn("Réaménagement de la 8e Rue", page)
        self.assertIn("Jusqu&#x27;au 27 septembre", page)
        self.assertIn("Consultation en ligne", page)
        self.assertIn(FICHE, page)
        self.assertIn("Une absence n’est pas une clôture", page)
        self.assertIn("n’invente aucune date de clôture", page)
        masthead = page[page.index('class="masthead"'):page.index("</header>")]
        self.assertNotIn("#participation", masthead)
        self.assertIn('href="#participation"', page)
        self.assertIn("Les consultations listées plus haut", page)

    def test_foreign_method_does_not_render(self) -> None:
        page = brief.render_brief(
            [], NOW.isoformat(), [], None,
            civic={"method": "nope", "fetched_at": NOW.isoformat(), "events": []},
        )
        self.assertNotIn('id="participation"', page)

    def test_empty_calendar_is_diagnosed_not_hidden(self) -> None:
        store = {
            "method": ingest_civic.METHOD,
            "fetched_at": NOW.isoformat(),
            "attribution": "Ville de Québec",
            "source_url": PAGE,
            "events": [],
        }
        page = brief.render_brief([], NOW.isoformat(), [], None, civic=store)
        self.assertIn('id="participation"', page)
        self.assertIn("Aucune activité n’était listée", page)
        self.assertIn("Aucune consultation listée par la Ville dans cette collecte", page)
        self.assertIn('href="#participation"', page)

    def test_section_sits_after_travaux_when_both_exist(self) -> None:
        civic = {
            "method": ingest_civic.METHOD,
            "fetched_at": NOW.isoformat(),
            "events": [{
                "event_id": "303", "title": "A", "window_text": "x",
                "mode_text": "", "url": FICHE,
            }],
        }
        roads = {
            "fetched_at": NOW.isoformat(),
            "events": [{"event_id": "rw-1", "road_names": ["Rue A"]}],
        }
        page = brief.render_brief([], NOW.isoformat(), [], None, civic=civic, roadworks=roads)
        self.assertLess(page.index('id="travaux"'), page.index('id="participation"'))
        self.assertLess(page.index('id="participation"'), page.index('id="dossiers"'))
        self.assertIn('maxlength="1000"', page[page.index('id="cmdk-input"'):])


if __name__ == "__main__":
    unittest.main()
