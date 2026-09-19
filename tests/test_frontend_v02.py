"""Front-end v0.2 (2026-09-19): answer-first digest, self-hosted type,
dark/contrast adaptation, command palette, continuity, sticky wayfinding.

House law under test: no external request on any surface; the digest is
sentences (never a stat strip), links only to sections that exist, and is
byte-identical for the same inputs; the masthead never promotes roadworks.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import harness

import dossier_history
import ingest_wzdx
import resident_brief as brief

ROOT = harness.ROOT
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _rows(local: int = 2, province: int = 1) -> list[dict]:
    return [{"geo": "quebec-city"}] * local + [{"geo": "quebec"}] * province


class AnswerFirstDigest(unittest.TestCase):
    def _digest(self, **over) -> str:
        args = dict(
            rows=_rows(),
            status={},
            ledger={"has_previous": True, "new_count": 1, "developed_count": 0, "quiet_count": 2},
            roadworks={"counts": {"active": 5}, "fetched_at": NOW.isoformat()},
            issues=[{"silence": {"silent": [{"institution_name": "Ville de Québec"}]}}],
            now=NOW,
            has_changes=True,
        )
        args.update(over)
        return brief.digest_html(**args)

    def test_measures_are_sentences_with_jump_links(self) -> None:
        html = self._digest()
        self.assertIn('<nav class="glance"', html)
        for needle in ('href="#stories"', 'href="#travaux"', 'href="#changements"', 'href="#dossiers"'):
            self.assertIn(needle, html)
        self.assertIn("<strong>2</strong> articles touchent Québec", html)
        self.assertIn("<strong>5</strong> entraves", html)
        self.assertIn("disparu", html)
        self.assertIn("Jamais « résolu »", html)

    def test_quiet_digest_is_empty_not_fabricated(self) -> None:
        self.assertEqual(self._digest(rows=[], ledger=None, roadworks=None, issues=[]), "")

    def test_no_link_to_a_section_that_is_absent(self) -> None:
        html = self._digest(ledger=None, roadworks=None, issues=[], has_changes=False)
        self.assertIn('href="#stories"', html)
        self.assertNotIn('href="#travaux"', html)
        self.assertNotIn('href="#changements"', html)
        self.assertNotIn('href="#dossiers"', html)

    def test_digest_is_deterministic(self) -> None:
        self.assertEqual(self._digest(), self._digest())


class DigestInPage(unittest.TestCase):
    def _page(self) -> str:
        issues = [{
            "issue_id": "i1", "scar": "s", "question": "Q ?",
            "geo_focus": ["quebec-city"], "source_count": 2,
            "silence": {"silent_count": 1, "silent": [{"institution_name": "Ville de Québec"}]},
            "tensions": [],
        }]
        page = brief.render_brief([], "2026-09-19T12:00:00+00:00", issues, None)
        self.assertIn('class="glance"', page)
        return page

    def test_digest_sits_before_the_controls(self) -> None:
        page = self._page()
        self.assertLess(page.index('class="glance"'), page.index('class="visit-strip'))


class SelfHostedType(unittest.TestCase):
    def test_brief_css_declares_the_voice(self) -> None:
        css = (ROOT / "public" / "assets" / "brief.css").read_text(encoding="utf-8")
        self.assertIn("'Newsreader'", css)
        self.assertIn("'Figtree'", css)
        self.assertIn("prefers-color-scheme:dark", css)
        self.assertIn("color-scheme:light dark", css)
        self.assertIn("position:sticky", css)
        self.assertIn("prefers-contrast:more", css)

    def test_font_faces_are_local_woff2(self) -> None:
        css = (ROOT / "public" / "assets" / "fonts.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("@font-face"), 4)
        self.assertIn("/assets/fonts/newsreader-latin.woff2", css)
        self.assertIn("/assets/fonts/figtree-latin.woff2", css)
        self.assertNotIn("http", css.replace("http://www.w3.org", ""))

    def test_no_surface_contacts_an_external_font_origin(self) -> None:
        for name in ("vercel.json", "public/vercel.json"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("fonts.googleapis", text)
            self.assertNotIn("fonts.gstatic", text)
        for name in ("index.html", "explorer.html", "morning.html"):
            path = ROOT / "public" / name
            if not path.is_file():
                continue
            html = path.read_text(encoding="utf-8")
            self.assertNotIn("fonts.googleapis", html)
            self.assertNotIn("fonts.gstatic", html)
            self.assertIn("/assets/fonts.css", html)


class WayfindingAndContinuity(unittest.TestCase):
    def test_index_carries_palette_and_continuity_hooks(self) -> None:
        path = ROOT / "public" / "index.html"
        if not path.is_file():
            self.skipTest("no generated index")
        html = path.read_text(encoding="utf-8")
        for needle in ('id="cmdk"', 'id="cmdk-input"', 'id="cmdk-open"', 'id="visit-list"'):
            self.assertIn(needle, html)
        head = html[: html.index("</head>")]
        self.assertIn('media="(prefers-color-scheme: dark)"', head)
        self.assertIn("/assets/fonts.css", head)

    def test_script_implements_palette_and_scout(self) -> None:
        js = (ROOT / "public" / "assets" / "brief.js").read_text(encoding="utf-8")
        for needle in ("cmdkChoose", "cmdkDraw", "IntersectionObserver", "aria-current", "visit-more"):
            self.assertIn(needle, js)
        # Storage contract and lean-scan guardrails must survive.
        self.assertIn("vigie.resident.v1", js)
        self.assertIn("setTimeout(render, 150)", js)
        self.assertNotIn("rows.some", js)

    def test_roadworks_never_in_the_masthead(self) -> None:
        path = ROOT / "public" / "index.html"
        if not path.is_file():
            self.skipTest("no generated index")
        html = path.read_text(encoding="utf-8")
        masthead = html[html.index('class="masthead"'):html.index("</header>")]
        self.assertNotIn('href="#travaux"', masthead)


class SpatialSketch(unittest.TestCase):
    """Phase E: a distribution scheme over official coordinates, never a map."""

    def _rw(self) -> dict:
        return {"bbox": list(ingest_wzdx.METRO_BBOX)}

    def test_plots_only_usable_in_bbox_points(self) -> None:
        events = [
            {"event_id": "a", "point": [-71.2, 46.8], "vehicle_impact": "all-lanes-closed"},
            {"event_id": "b", "point": [-71.3, 46.9], "vehicle_impact": "some-lanes-closed"},
            {"event_id": "c", "point": [-99.0, 10.0], "vehicle_impact": "some-lanes-closed"},
            {"event_id": "d", "point": "nope", "vehicle_impact": "some-lanes-closed"},
            {"event_id": "e"},
        ]
        html = brief._rw_sketch(self._rw(), events)
        self.assertEqual(html.count("<circle"), 2)
        self.assertIn("2 entraves actives déclarées", html)
        self.assertIn("1 fermeture complète", html)
        self.assertIn('role="img"', html)
        self.assertIn("pas une carte routière", html)
        self.assertIn("pas une preuve géographique", html)
        self.assertNotIn("http", html)

    def test_collapses_without_bbox_or_points(self) -> None:
        event = {"event_id": "a", "point": [-71.2, 46.8]}
        self.assertEqual(brief._rw_sketch(self._rw(), []), "")
        self.assertEqual(brief._rw_sketch({}, [event]), "")
        self.assertEqual(brief._rw_sketch({"bbox": [1, 2, 3]}, [event]), "")
        self.assertEqual(brief._rw_sketch({"bbox": ["x", 2, 3, 4]}, [event]), "")

    def test_is_deterministic_and_id_sorted(self) -> None:
        first = [{"event_id": "b", "point": [-71.3, 46.8]},
                 {"event_id": "a", "point": [-71.2, 46.8]}]
        self.assertEqual(brief._rw_sketch(self._rw(), first),
                         brief._rw_sketch(self._rw(), list(reversed(first))))

    def test_ingest_carries_the_declared_point(self) -> None:
        feature = {"id": "X", "properties": {},
                   "geometry": {"type": "Point", "coordinates": [-71.21, 46.81]}}
        event, reason = ingest_wzdx.parse_event(feature)
        self.assertIsNone(reason)
        self.assertEqual(event["point"], [-71.21, 46.81])


class DossierVoicesAndTimeline(unittest.TestCase):
    """Phase F: the full chambre at a glance, and collection counters over time."""

    def _issue(self) -> dict:
        return {
            "tensions": [
                {"institution_name": "Le Soleil", "source_kind": "media",
                 "items": [{"title": "A", "url": "https://a.example/x"},
                           {"title": "B", "url": "javascript:alert(1)"}]},
                {"institution_name": "Ville de Québec", "source_kind": "official",
                 "items": [{"title": "C", "url": "https://v.example/y"}]},
            ],
            "silence": {"silent": [
                {"institution_name": "Le Devoir", "source_kind": "media"},
                {"institution_name": "Hydro-Québec", "source_kind": "official"},
            ]},
        }

    def test_roster_names_every_institution_and_its_state(self) -> None:
        html = brief.dossier_voices_html(self._issue())
        for name in ("Le Soleil", "Ville de Québec", "Le Devoir", "Hydro-Québec"):
            self.assertIn(name, html)
        self.assertIn("officiel", html)
        self.assertIn("2 institutions ont parlé", html)
        self.assertIn("2 n’ont pas parlé", html)
        self.assertIn("1 article", html)  # the javascript: item is not counted
        self.assertIn("n’a pas parlé dans cette collecte", html)

    def test_roster_escapes_and_is_deterministic(self) -> None:
        evil = {
            "tensions": [{"institution_name": "<img src=x>", "items": []}],
            "silence": {"silent": [{"institution_name": "<b>x</b>"}]},
        }
        html = brief.dossier_voices_html(evil)
        self.assertNotIn("<img src=x>", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertEqual(html, brief.dossier_voices_html(evil))

    def test_timeline_needs_two_editions_and_speaks_honestly(self) -> None:
        one = {"tracking": {"timeline": [{"ts": "2026-09-19T00:00:00+00:00", "sources": 2}]}}
        self.assertEqual(brief.dossier_timeline_html(one), "")
        two = {"tracking": {"timeline": [
            {"ts": "2026-09-18T00:00:00+00:00", "sources": 2, "items": 4, "official": 0},
            {"ts": "2026-09-19T00:00:00+00:00", "sources": 3, "items": 6, "official": 1},
        ]}}
        html = brief.dossier_timeline_html(two)
        self.assertIn("2 éditions", html)
        self.assertIn("3 sources", html)
        self.assertIn("6 articles", html)
        self.assertIn("1 source officielle", html)
        self.assertIn("pas une escalade", html)
        self.assertIn("pas une résolution", html)

    def test_tracking_exposes_only_fields_the_record_carried(self) -> None:
        history = {"dossiers": {"a": {"editions_seen": 2, "timeline": [
            {"ts": "t1", "sources": 2},
            {"ts": "t2", "sources": 3, "items": 5, "official": 1},
        ]}}}
        timeline = dossier_history.tracking_of(history, "a")["timeline"]
        self.assertEqual(timeline[0], {"ts": "t1", "sources": 2})
        self.assertEqual(timeline[1], {"ts": "t2", "sources": 3, "items": 5, "official": 1})


class SavedCorridors(unittest.TestCase):
    """Last roadmap item: opt-in corridors, on-device, literal street match."""

    def test_street_rows_count_folded_literals(self) -> None:
        events = [
            {"event_id": "a", "road_names": ["Rue Saint-Jean", "Boulevard René-Lévesque O"]},
            {"event_id": "b", "road_names": ["rue saint-jean"]},
            {"event_id": "c", "road_names": ["   "]},
            {"event_id": "d"},
            None,
        ]
        rows = brief._rw_street_rows(events)
        by_key = {row["key"]: row for row in rows}
        self.assertEqual(by_key["rue saint-jean"]["n"], 2)
        self.assertEqual(by_key["boulevard rene-levesque o"]["n"], 1)
        self.assertNotIn("", by_key)
        self.assertEqual(rows[0]["key"], "rue saint-jean")  # highest count first

    def test_corridors_markup_and_island(self) -> None:
        html = brief._rw_corridors_html([{"name": "Rue <Test>", "key": "rue test", "n": 2}])
        for needle in ('id="vigie-streets"', 'id="rw-corridors"', 'id="rw-corridor-input"',
                       'id="rw-street-options"', 'id="rw-corridor-list"'):
            self.assertIn(needle, html)
        self.assertIn("aucune position", html)
        self.assertNotIn("http", html)
        raw = html[: html.index("</script>")]
        self.assertNotIn("<Test>", raw)  # neutralized inside the JSON island
        island = json.loads(raw[raw.index(">") + 1:])
        self.assertEqual(island["method"], "rw-streets-v1")
        self.assertEqual(island["streets"][0]["n"], 2)

    def test_corridors_collapse_without_streets(self) -> None:
        self.assertEqual(brief._rw_corridors_html([]), "")

    def test_card_carries_folded_road_keys_for_marking(self) -> None:
        card = brief._rw_card({"event_id": "e", "road_names": ["Rue Saint-Jean"]}, set(), {}, False)
        self.assertIn('data-roads="rue saint-jean"', card)

    def test_client_stays_on_device_and_literal(self) -> None:
        js = (ROOT / "public" / "assets" / "brief.js").read_text(encoding="utf-8")
        for needle in ("vigie.corridors.v1", "rw-corridor-add", "dataset.roads", "rw-hit"):
            self.assertIn(needle, js)
        self.assertNotIn("geolocation", js)


class EditionStampHonesty(unittest.TestCase):
    def test_section_note_uses_the_collection_not_the_render_clock(self) -> None:
        # An hourly roads-only re-render must never relabel the edition.
        run = {
            "fetched_at": "2026-09-19T18:41:00+00:00",
            "enabled_rss": ["x"],
            "results": [{"source_id": "x", "ok": True, "item_count": 1}],
        }
        page = brief.render_brief([], "2026-09-19T22:21:00+00:00", [], run)
        note = page[page.index('class="section-note"'):]
        note = note[: note.index("</p>")]
        self.assertIn("18:41 UTC", note)
        self.assertNotIn("22:21 UTC", note)


if __name__ == "__main__":
    unittest.main()
