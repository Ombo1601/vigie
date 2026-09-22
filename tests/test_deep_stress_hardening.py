"""Deep stress and fuzz test suite for Vigie.

Systematic stress test ensuring every pipeline and record-layer component
handles malformed, None, empty, wrong-type, and hostile inputs fail-soft
without raising unhandled exceptions or corrupting data.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile

import harness  # puts scripts/ on sys.path

import affiche
import ambient_pulse
import change_ledger
import check_claims
import cluster_issues
import compile_anomalies
import compile_metrics
import compile_watchdog
import depart
import dossier_history
import edge_atlas
import enrich
import feed_health
import fetch_brief_media
import fetch_media
import ingest_civic
import ingest_wzdx
import life_facets
import memoire
import method_site
import normalize
import promesse
import rank_display
import recits
import registre
import resident_brief as brief
import store_io
import substrate


class DeepStressFuzzTest(unittest.TestCase):
    def test_depart_edge_inputs(self) -> None:
        for bad in (None, {}, [], 123, "string", [None, 123, "invalid"]):
            self.assertEqual(depart._ordered_events(bad), [])
            self.assertEqual(depart._street_index(bad), [])
            self.assertEqual(depart._rows_html(bad), "")
            self.assertEqual(depart._question_pick(bad), None)

        html = depart.render_depart(
            roadworks={"events": [None, 123, {"event_id": "e1"}]},
            issues={"issues": [None, "str", {"issue_id": "iss1"}]},
            ledger={"new": [None, 123]},
            state={"seals": [None, 456]},
            generated_at="2026-09-21T00:00:00Z",
        )
        self.assertIsInstance(html, str)
        self.assertIn("Avant de partir", html)

    def test_memoire_edge_inputs(self) -> None:
        for bad in (None, {}, [], 123, "str"):
            self.assertIsInstance(memoire.render_index(bad, {}), str)
            self.assertEqual(memoire._ledger_html(bad, bad), '<section class="memoire-section"><h2>Depuis l’édition précédente</h2><p>Première édition scellée : aucune comparaison.</p></section>')
            self.assertIsInstance(memoire._dossiers_html(bad, bad, bad), str)

    def test_affiche_edge_inputs(self) -> None:
        for bad in (None, 123, "bad"):
            self.assertEqual(affiche.area_blocks(bad), [])
            self.assertIsInstance(affiche.render_affiche(bad, bad, bad, bad, "2026-09-21T00:00:00Z"), str)

    def test_recits_edge_inputs(self) -> None:
        for bad in (None, 123, "str"):
            self.assertIsInstance(recits.render_index(bad), str)
            self.assertIsNone(recits._ledger_entry(bad, "some_id"))

    def test_substrate_edge_inputs(self) -> None:
        for bad in (None, 123, "str"):
            self.assertEqual(substrate.items_of(bad, 5), [])
            self.assertEqual(substrate.voices_of(bad, bad), ([], []))
            self.assertIsInstance(substrate.dossier_view(bad, bad, 5), dict)
            delta = substrate.build_delta(bad, bad, bad, bad, bad)
            self.assertIsInstance(delta, dict)
            self.assertEqual(delta["method"], substrate.METHOD)

    def test_resident_brief_edge_inputs(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertEqual(brief.prepare_items(None, now), ([], 0))
        self.assertEqual(brief._rw_street_rows(None), [])
        self.assertEqual(brief._rw_sketch(None), "")
        status = brief.collection_status(None, now)
        self.assertIsInstance(status, dict)
        self.assertEqual(status["ok"], 0)

        # Full render_brief with extreme None/malformed fields
        html = brief.render_brief(
            ranked=[None, 123, {"id": "c1", "title": "Test", "url": "https://example.com/1", "published_at": "2026-09-21T00:00:00Z"}],
            generated_at="2026-09-21T00:00:00Z",
            issues=[None, "str", {"issue_id": "iss1"}],
            run={"results": [None, {"source_id": "s1", "ok": True}]},
            ledger={"new": [None, 123]},
            roadworks={"events": [None, 123]},
            media={"c1": None},
            anomalies={"anomalies": [None, 123]},
            edges={"streets": None},
            civic={"events": [None, 123]},
        )
        self.assertIsInstance(html, str)
        self.assertIn("<!doctype html>", html)

    def test_edge_atlas_edge_inputs(self) -> None:
        self.assertEqual(edge_atlas._display_name(None), "")
        self.assertEqual(edge_atlas._display_name({}), "")
        self.assertEqual(edge_atlas._display_name({"Rue Principale": "invalid_count"}), "Rue Principale")
        self.assertEqual(edge_atlas.build_streets(None), {})
        self.assertEqual(edge_atlas.issue_blob(None), "")
        atlas = edge_atlas.compile_atlas(None, None)
        self.assertIsInstance(atlas, dict)
        self.assertEqual(atlas["method"], edge_atlas.METHOD)

    def test_compile_anomalies_edge_inputs(self) -> None:
        v = compile_anomalies.compile_verdict(None)
        self.assertIsInstance(v, dict)
        self.assertEqual(v["anomaly_count"], 0)
        v_bad = compile_anomalies.compile_verdict({
            "events": [None, 123, {"event_id": "e1"}],
            "diff": {"new": [None, 123], "has_previous": True},
            "fetched_at": "invalid_date",
        })
        self.assertIsInstance(v_bad, dict)

    def test_compile_metrics_edge_inputs(self) -> None:
        snap = compile_metrics.ranked_snapshot(None)
        self.assertEqual(snap, (None, []))
        entry = compile_metrics.edition_entry(
            ranked_doc={"candidates": [None, 123]},
            issues_doc={"issues": [None, 123]},
            history_doc=None,
            roadworks_doc=None,
            media_doc=None,
            feed_doc=None,
        )
        self.assertIsInstance(entry, dict)
        self.assertEqual(entry["dossiers"]["tracked"], 0)

    def test_compile_watchdog_edge_inputs(self) -> None:
        md = compile_watchdog._render_markdown(None)
        self.assertIsInstance(md, str)
        self.assertIn("watchdog", md.lower())
        md2 = compile_watchdog._render_markdown({
            "refresh_facts": None,
            "feed_status": None,
            "pipeline_runs": None,
            "retention": None,
            "state_pack": None,
            "media_health": None,
            "metrics": None,
        })
        self.assertIsInstance(md2, str)

    def test_cluster_issues_edge_inputs(self) -> None:
        self.assertEqual(cluster_issues.institution_of(None), "")
        self.assertEqual(cluster_issues.institution_name_of(None), "")
        self.assertEqual(cluster_issues.feed_to_institution(None), {})
        self.assertEqual(cluster_issues.collapse_institutions(None), [])
        silence = cluster_issues.silence_map(set(), None)
        self.assertIsInstance(silence, dict)
        self.assertEqual(silence["spoke_count"], 0)

    def test_check_claims_edge_inputs(self) -> None:
        self.assertEqual(check_claims._claims_of(None), [])
        audit = check_claims.audit_claims(None, None)
        self.assertIsInstance(audit, dict)
        self.assertEqual(audit["ok"], True)

    def test_enrich_edge_inputs(self) -> None:
        self.assertEqual(enrich.blob(None), "")
        self.assertEqual(enrich.propose_claims(None), [])
        self.assertIsInstance(enrich.propose_geo(None, None), dict)
        self.assertEqual(enrich.propose_topics(None), [{"status": "proposed", "topic": "other"}])
        self.assertEqual(enrich.propose_impacts(None), [])
        self.assertEqual(enrich.enrich_one(None), {})

    def test_feed_health_edge_inputs(self) -> None:
        self.assertEqual(feed_health._streak(None, lambda x: True), 0)
        self.assertIsNone(feed_health._yield_trend(None))

    def test_life_facets_edge_inputs(self) -> None:
        self.assertEqual(life_facets.normalize_facet_ids(None), [])
        self.assertEqual(life_facets.annotate_approaches(None), [])
        self.assertEqual(life_facets.reorder_approaches(None, None), [])
        self.assertTrue(life_facets.same_approach_set(None, None))
        self.assertEqual(life_facets.parse_facets_md_rows(None), [])

    def test_promesse_edge_inputs(self) -> None:
        self.assertIsNone(promesse.status_of(None))
        self.assertIsNone(promesse.status_of({"tracking": {"timeline": 123}}))

    def test_registre_edge_inputs(self) -> None:
        self.assertIsInstance(registre.checkpoint_text(None), str)
        chain = registre.public_chain(None)
        self.assertIsInstance(chain, dict)
        self.assertEqual(chain["size"], 0)
        reg = registre.institution_register(None)
        self.assertEqual(reg, [])

    def test_fetch_media_edge_inputs(self) -> None:
        self.assertEqual(fetch_media.issue_candidate_ids(None), [])
        self.assertEqual(fetch_media.issue_candidate_ids({"tensions": [None, 123, {"items": [None, {"candidate_id": "c1"}]}]}), ["c1"])
        self.assertEqual(fetch_media.map_scope(None, None), [])
        self.assertEqual(fetch_media.map_scope([None, 123, {"id": "c1", "geo": "quebec-city"}], None), [{"id": "c1", "geo": "quebec-city"}])

    def test_ingest_civic_edge_inputs(self) -> None:
        diff = ingest_civic.diff_events(None, None)
        self.assertIsInstance(diff, dict)
        self.assertFalse(diff["has_previous"])
        diff2 = ingest_civic.diff_events([None, 123, {"event_id": "e1"}], [None, 456, {"event_id": "e1"}])
        self.assertIsInstance(diff2, dict)
        self.assertTrue(diff2["has_previous"])

    def test_ingest_wzdx_edge_inputs(self) -> None:
        evt, reason = ingest_wzdx.parse_event(None)
        self.assertIsNone(evt)
        self.assertEqual(reason, "malformed")
        pts = ingest_wzdx._points(None)
        self.assertEqual(pts, [])
        diff = ingest_wzdx.diff_events(None, None, now=datetime.now(timezone.utc))
        self.assertIsInstance(diff, dict)
        self.assertFalse(diff["has_previous"])

    def test_change_ledger_edge_inputs(self) -> None:
        diff = change_ledger.diff_editions(None, None, has_previous=False)
        self.assertIsInstance(diff, dict)
        self.assertFalse(diff["has_previous"])
        diff2 = change_ledger.diff_editions(
            [None, 123, {"issue_id": "iss1", "scar": "s1"}],
            [None, 456, {"issue_id": "iss1", "scar": "s1"}],
            has_previous=True,
        )
        self.assertIsInstance(diff2, dict)
        self.assertTrue(diff2["has_previous"])

    def test_method_site_edge_inputs(self) -> None:
        self.assertEqual(method_site.md_to_html(None), "")
        self.assertEqual(method_site._table(None), "")
        self.assertEqual(method_site._inline(None), "")
        self.assertIsInstance(method_site.md_to_html("# Title\n\nSome text with `code`."), str)

    def test_normalize_edge_inputs(self) -> None:
        self.assertIsNone(normalize.canonical_url(None))
        self.assertIsNone(normalize.canonical_url(""))
        self.assertIsNone(normalize.canonical_url("not_a_valid_url"))
        sid = normalize.stable_id(None, "src1", None, None)
        self.assertIsInstance(sid, str)
        self.assertEqual(len(sid), 24)

    def test_fetch_brief_media_edge_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            health_file = Path(td) / "health.json"
            fetch_brief_media.write_health_ledger({"media": {}}, health_file)
            self.assertTrue(health_file.exists())
            fetch_brief_media.write_health_ledger(None, health_file)

    def test_ingest_rss_edge_inputs(self) -> None:
        import ingest_rss
        self.assertIsNone(ingest_rss._scalar(""))
        self.assertIsNone(ingest_rss._scalar("# comment only"))
        self.assertEqual(ingest_rss._scalar('"val # not comment"'), "val # not comment")
        self.assertEqual(ingest_rss._scalar("http://test#frag"), "http://test#frag")
        self.assertEqual(ingest_rss._scalar("123"), 123)
        self.assertEqual(ingest_rss._scalar("true"), True)
        self.assertEqual(ingest_rss._local("tag"), "tag")
        self.assertEqual(ingest_rss._local("{namespace}tag"), "tag")
        self.assertEqual(ingest_rss._local(None), "")
        self.assertIsNone(ingest_rss._clean_person(None))
        self.assertIsNone(ingest_rss._clean_person(""))
        self.assertEqual(ingest_rss._clean_person("Alice"), "Alice")

    def test_rank_display_edge_inputs(self) -> None:
        self.assertEqual(rank_display.approach_nest(None), "linked")
        self.assertEqual(rank_display.approach_nest({}), "linked")
        self.assertEqual(rank_display.approach_nest({"geo_focus": ["quebec-city"]}), "near")
        self.assertEqual(rank_display.approach_nest({"geo_focus": ["quebec"]}), "province")
        cont = rank_display.build_continuity(None, None)
        self.assertEqual(cont, {"by_id": {}, "id_to_issues": {}})
        delta = rank_display.since_left_delta(None, None)
        self.assertTrue(delta["same"])
        visit = rank_display.since_left_visit(None, {})
        self.assertEqual(visit["kind"], "first")

    def test_state_pack_edge_inputs(self) -> None:
        import state_pack
        self.assertTrue(state_pack._is_process_state(".hidden"))
        self.assertTrue(state_pack._is_process_state("refresh.lock"))
        self.assertFalse(state_pack._is_process_state("latest_issues.json"))


if __name__ == "__main__":
    unittest.main()
