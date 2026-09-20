"""Anomaly beacon + edge joins in the French brief: collapse, honesty, safety.

House law under test: a foreign, corrupt or empty sidecar renders ZERO HTML —
the brief stays byte-identical; the beacon lives inside the Travaux section,
never the first viewport; every join presents itself as « Rapprochement
proposé », never geographic proof; dossier edge lines never point at a
missing #travaux anchor; hostile store text is escaped, never executed.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import resident_brief as brief
import stage_public

TS = "2026-09-17T12:00:00+00:00"
FETCHED = "2026-09-17T11:30:00+00:00"


def _run():
    return {"fetched_at": TS, "enabled_rss": ["local"],
            "results": [{"source_id": "local", "ok": True, "item_count": 1}]}


def _event(eid="EV-001", roads=("Boulevard Charest Est",), **over) -> dict:
    ev = {
        "event_id": eid, "event_type": "work-zone", "event_status": "active",
        "vehicle_impact": "some-lanes-closed", "road_names": list(roads),
        "direction": "both-directions", "start_date": "2026-09-10T04:00:00Z",
        "end_date": "2026-10-01T03:59:59Z", "start_date_accuracy": None,
        "end_date_accuracy": None, "description": "Réfection de la chaussée",
        "update_date": None, "restrictions": [], "source_id": "wzdx-quebec",
        "active": True,
    }
    ev.update(over)
    return ev


def _store(events=None) -> dict:
    return {
        "method": "wzdx-roadworks-v2", "status": "proposed", "source_id": "wzdx-quebec",
        "source_name": "Ville de Québec — Entraves (WZDX)", "institution_name": "Ville de Québec",
        "feed_url": "https://quebec.gewi.com/wzdx/pull",
        "dataset_url": "https://www.donneesquebec.ca/recherche/dataset/entraves-a-la-circulation-en-temps-reel-de-la-ville-de-quebec",
        "license_note": "CC-BY 4.0", "fetched_at": FETCHED,
        "bbox": [-71.85, 46.50, -70.75, 47.25],
        "counts": {"features": 1, "parsed": 1, "active": 1},
        "events": [_event()] if events is None else events,
        "diff": {"has_previous": False, "new": [], "removed": [], "changed": [],
                 "new_count": 0, "removed_count": 0, "changed_count": 0},
    }


def _anomaly(claim="Boulevard Laurier concentre 9 entraves actives déclarées.",
             label="Concentration", rule="concentration", ids=("L-1", "L-2"), **over) -> dict:
    row = {"rule_id": rule, "rule_label": label, "street_key": "boulevard-laurier",
           "street_display": "Boulevard Laurier", "count": len(ids), "claim": claim,
           "evidence_event_ids": list(ids), "status": "proposed"}
    row.update(over)
    return row


def _verdict(rows) -> dict:
    return {"method": "anomaly-beacon-v1", "status": "proposed", "compiled_at": FETCHED,
            "has_store": True, "anomalies": rows, "anomaly_count": len(rows),
            "anomaly_cap": 6}


def _edges(streets=None, issues=None) -> dict:
    return {"method": "edge-atlas-v1", "status": "proposed", "built_at": FETCHED,
            "street_count": len(streets or {}), "streets": streets or {},
            "issues": issues or {}, "matched_issue_count": len(issues or {})}


STREET = {"display": "Boulevard Charest Est", "active_count": 2, "centroid": None,
          "event_ids": ["EV-001"], "matched_issue_ids": ["iss-1"]}


def _issue(iid="iss-1", question="Que disent les sources du centre-ville ?") -> dict:
    return {"issue_id": iid, "question": question, "status": "proposed",
            "geo_focus": ["quebec-city"], "source_count": 2, "label_source": None,
            "tensions": [], "silence": {}, "media_remix": False}


def _render(**kwargs) -> str:
    return brief.render_brief([], TS, kwargs.pop("issues", []), _run(), **kwargs)


class BeaconCollapse(unittest.TestCase):
    def setUp(self):
        self.base = _render(roadworks=_store())

    def test_absent_by_default_and_byte_identical(self):
        self.assertNotIn('id="anomalies"', self.base)
        for bad in (None, {}, [], {"method": "another-model", "anomalies": [_anomaly()]},
                    {"method": "anomaly-beacon-v1"},
                    {"method": "anomaly-beacon-v1", "anomalies": []},
                    {"method": "anomaly-beacon-v1", "anomalies": "nope"},
                    {"method": "anomaly-beacon-v1", "anomalies": ["x", {}, {"claim": "  "}]}):
            with self.subTest(bad=type(bad)):
                page = _render(roadworks=_store(), anomalies=bad)
                self.assertEqual(page, self.base)

    def test_edges_collapse_byte_identical(self):
        self.assertNotIn("rw-edge", self.base)
        for bad in (None, {}, {"method": "another-model", "streets": {"k": STREET}},
                    {"method": "edge-atlas-v1"},
                    {"method": "edge-atlas-v1", "streets": "nope", "issues": []},
                    {"method": "edge-atlas-v1", "streets": {}, "issues": {}}):
            with self.subTest(bad=type(bad)):
                page = _render(roadworks=_store(), edges=bad)
                self.assertEqual(page, self.base)

    def test_no_beacon_without_roadworks_section(self):
        page = _render(anomalies=_verdict([_anomaly()]))
        self.assertNotIn('id="anomalies"', page)
        self.assertNotIn('id="travaux"', page)


class BeaconRender(unittest.TestCase):
    def test_claim_rule_label_and_method_link(self):
        page = _render(roadworks=_store(), anomalies=_verdict([_anomaly()]))
        self.assertIn('id="anomalies"', page)
        self.assertIn("LECTURE STRUCTURELLE", page)
        self.assertIn("Boulevard Laurier concentre 9 entraves actives déclarées.", page)
        self.assertIn("Concentration", page)
        self.assertIn("/anomalies.md", page)
        self.assertIn("2 entraves citées", page)

    def test_honesty_wording_present(self):
        page = _render(roadworks=_store(), anomalies=_verdict([_anomaly()]))
        self.assertIn("pas une prédiction", page)
        self.assertIn("seuils fixes", page)

    def test_display_cap_three(self):
        rows = [_anomaly(claim=f"claim {n}", rule="concentration") for n in range(5)]
        page = _render(roadworks=_store(), anomalies=_verdict(rows))
        self.assertEqual(page.count('<li class="rw-anomaly"'), 3)
        self.assertIn("claim 0", page)
        self.assertIn("claim 2", page)
        self.assertNotIn("claim 3", page)

    def test_beacon_sits_inside_the_travaux_section(self):
        page = _render(roadworks=_store(), anomalies=_verdict([_anomaly()]))
        self.assertLess(page.index('id="travaux"'), page.index('id="anomalies"'))
        self.assertLess(page.index('id="anomalies"'), page.index('<ul class="rw-list">'))

    def test_beacon_never_reaches_the_first_viewport(self):
        page = _render(roadworks=_store(), anomalies=_verdict([_anomaly()]))
        self.assertLess(page.index('id="essentiel"'), page.index('id="anomalies"'))

    def test_xss_in_verdict_is_escaped(self):
        row = _anomaly(claim='<script>alert(1)</script>', label='<b>Boom</b>',
                       street_display='<img src=x onerror=alert(1)>')
        page = _render(roadworks=_store(), anomalies=_verdict([row]))
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;b&gt;Boom&lt;/b&gt;", page)

    def test_evidence_free_row_still_links_the_method(self):
        row = _anomaly(ids=())
        page = _render(roadworks=_store(), anomalies=_verdict([row]))
        self.assertIn("/anomalies.md", page)
        self.assertNotIn("entraves citées", page)


class EdgeJoins(unittest.TestCase):
    def test_obstruction_card_proposes_the_join(self):
        edges = _edges(streets={"boulevard-charest-e": STREET})
        page = _render(roadworks=_store(), edges=edges)
        self.assertIn("Rapprochement proposé", page)
        self.assertIn("un dossier proposé de cette édition", page)
        self.assertIn("« Boulevard Charest Est »", page)
        self.assertIn('href="#dossiers"', page)
        self.assertIn("/edge.md", page)
        self.assertIn("pas une preuve géographique", page)

    def test_no_line_without_a_matched_dossier(self):
        street = dict(STREET, matched_issue_ids=[])
        page = _render(roadworks=_store(), edges=_edges(streets={"boulevard-charest-e": street}))
        self.assertNotIn("rw-edge", page)

    def test_unmatched_street_names_stay_silent(self):
        edges = _edges(streets={"rue-inconnue": dict(STREET, display="Rue Inconnue")})
        page = _render(roadworks=_store(), edges=edges)
        self.assertNotIn("rw-edge", page)

    def test_plural_wording(self):
        street = dict(STREET, matched_issue_ids=["iss-1", "iss-2"])
        page = _render(roadworks=_store(), edges=_edges(streets={"boulevard-charest-e": street}))
        self.assertIn("2 dossiers proposés", page)

    def test_dossier_card_carries_the_reverse_join(self):
        edges = _edges(streets={"boulevard-charest-e": STREET},
                       issues={"iss-1": {"streets": ["boulevard-charest-e"]}})
        page = _render(roadworks=_store(), edges=edges, issues=[_issue()])
        self.assertIn("dossier-edge", page)
        self.assertIn("la collecte officielle déclare 2 entraves actives sur « Boulevard Charest Est »", page)
        self.assertIn('href="#travaux"', page)

    def test_dossier_edge_never_points_at_a_missing_anchor(self):
        # No roadworks store -> no #travaux target -> no dossier edge line.
        edges = _edges(streets={"boulevard-charest-e": STREET},
                       issues={"iss-1": {"streets": ["boulevard-charest-e"]}})
        page = _render(edges=edges, issues=[_issue()])
        self.assertNotIn("dossier-edge", page)
        self.assertNotIn('href="#travaux"', page)

    def test_dossier_edge_skips_streets_without_active_declarations(self):
        street = dict(STREET, active_count=0)
        edges = _edges(streets={"boulevard-charest-e": street},
                       issues={"iss-1": {"streets": ["boulevard-charest-e"]}})
        page = _render(roadworks=_store(), edges=edges, issues=[_issue()])
        self.assertNotIn("dossier-edge", page)

    def test_xss_in_edge_store_is_escaped(self):
        street = dict(STREET, display='<img src=x onerror=alert(1)>')
        edges = _edges(streets={"boulevard-charest-e": street})
        page = _render(roadworks=_store(), edges=edges)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)

    def test_malformed_street_records_are_skipped(self):
        edges = {"method": "edge-atlas-v1", "streets": {"k": "not-a-dict", "j": None},
                 "issues": {"iss-1": "not-a-dict"}}
        page = _render(roadworks=_store(), edges=edges, issues=[_issue()])
        self.assertNotIn("rw-edge", page)
        self.assertNotIn("dossier-edge", page)


class SiteValidation(unittest.TestCase):
    def test_rendered_brief_with_beacon_and_joins_passes_validation(self):
        edges = _edges(streets={"boulevard-charest-e": STREET},
                       issues={"iss-1": {"streets": ["boulevard-charest-e"]}})
        page = _render(roadworks=_store(), anomalies=_verdict([_anomaly()]),
                       edges=edges, issues=[_issue()])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.html").write_text(page, encoding="utf-8")
            (root / "assets").mkdir()
            for name in ("assets/brief.css", "assets/fonts.css", "assets/brief.js", "favicon.svg",
                         "apple-touch-icon.png", "site.webmanifest",
                         "explorer.html", "morning.html", "llms.txt", "index.html.md",
                         *stage_public.OPTIONAL_PAGES, *stage_public.METHODS):
                (root / name).write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


if __name__ == "__main__":
    unittest.main()
