"""Official WZDX roadworks: parsed as data, diffed honestly, rendered with attribution.

House law under test: fetch time is never an event time; removed ≠ ended;
estimated dates stay estimated; a feed outage never kills the pipeline; the
article pipeline and the 12-feed RSS ceiling never see this source.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import harness  # noqa: F401 - puts scripts/ on sys.path

import ingest_rss
import ingest_wzdx
import pipeline
import resident_brief as brief
import stage_public

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
TS = "2026-09-17T12:00:00+00:00"
SRC = {
    "id": "wzdx-test", "name": "Test WZDX", "institution": "ville-quebec",
    "institution_name": "Ville de Québec", "url": "https://quebec.gewi.com/wzdx/pull",
    "homepage": "https://www.donneesquebec.ca/recherche/dataset/entraves-a-la-circulation-en-temps-reel-de-la-ville-de-quebec",
    "license_note": "CC-BY 4.0",
}


def feature(eid="EV-001", *, coords=None, status="active", end="2026-10-01T03:59:59Z",
            impact="some-lanes-closed", roads=("Boulevard Charest Est",), desc="Réfection de la chaussée",
            update="2026-09-17T11:00:00Z", accuracy=None, geometry="LineString",
            etype="work-zone", no_id=False):
    if coords is None:
        coords = [[-71.21, 46.81], [-71.20, 46.81]]
    props = {
        # Feed-level on the real endpoint: identical on every feature, never an event key.
        "data_source_id": "TIC-Quebec/1", "event_type": etype, "event_status": status,
        "vehicle_impact": impact, "road_names": list(roads), "direction": "both-directions",
        "start_date": "2026-09-10T04:00:00Z", "end_date": end,
        "description": desc, "update_date": update, "restrictions": [],
    }
    if accuracy:
        props["start_date_accuracy"] = accuracy
        props["end_date_accuracy"] = accuracy
    geom = {"type": geometry, "coordinates": coords} if geometry is not None else None
    feat = {"type": "Feature", "geometry": geom, "properties": props}
    if not no_id:
        feat["id"] = eid
    return feat


def geojson(*features) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": list(features)}).encode()


def _event(eid="EV-001", **over) -> dict:
    ev = {
        "event_id": eid, "event_type": "work-zone", "event_status": "active",
        "vehicle_impact": "some-lanes-closed", "road_names": ["Boulevard Charest Est"],
        "direction": "both-directions", "start_date": "2026-09-10T04:00:00Z",
        "end_date": "2026-10-01T03:59:59Z", "start_date_accuracy": None,
        "end_date_accuracy": None, "description": "Réfection de la chaussée",
        "update_date": "2026-09-17T11:00:00Z", "restrictions": [],
        "source_id": "wzdx-quebec", "active": True,
    }
    ev.update(over)
    return ev


def _store(**over) -> dict:
    base = {
        "method": "wzdx-roadworks-v2", "status": "proposed", "source_id": "wzdx-quebec",
        "source_name": "Ville de Québec — Entraves à la circulation (WZDX)",
        "institution_name": "Ville de Québec",
        "feed_url": "https://quebec.gewi.com/wzdx/pull",
        "dataset_url": "https://www.donneesquebec.ca/recherche/dataset/entraves-a-la-circulation-en-temps-reel-de-la-ville-de-quebec",
        "license_note": "CC-BY 4.0", "fetched_at": "2026-09-17T11:30:00+00:00",
        "bbox": [-71.85, 46.50, -70.75, 47.25],
        "counts": {"features": 1, "parsed": 1, "active": 1},
        "events": [_event()],
        "diff": {"has_previous": False, "new": [], "removed": [], "changed": [],
                 "new_count": 0, "removed_count": 0, "changed_count": 0},
    }
    base.update(over)
    return base


def _run():
    return {"fetched_at": TS, "enabled_rss": ["local"],
            "results": [{"source_id": "local", "ok": True, "item_count": 1}]}


def _render(roadworks):
    return brief.render_brief([], TS, [], _run(), roadworks=roadworks)


class Parser(unittest.TestCase):
    def test_full_field_preservation(self):
        ev, reason = ingest_wzdx.parse_event(feature("ACL-20260917-EC-001", accuracy="estimated"))
        self.assertIsNone(reason)
        self.assertEqual(ev["event_id"], "ACL-20260917-EC-001")
        self.assertEqual(ev["road_names"], ["Boulevard Charest Est"])
        self.assertEqual(ev["vehicle_impact"], "some-lanes-closed")
        self.assertEqual(ev["start_date"], "2026-09-10T04:00:00Z")
        self.assertEqual(ev["start_date_accuracy"], "estimated")
        self.assertEqual(ev["end_date_accuracy"], "estimated")

    def test_identity_is_feature_id_not_data_source_id(self):
        # The real feed stamps every feature with the same feed-level
        # data_source_id; distinct feature ids must stay distinct events.
        ev1, r1 = ingest_wzdx.parse_event(feature("EV-A"))
        ev2, r2 = ingest_wzdx.parse_event(feature("EV-B"))
        self.assertIsNone(r1)
        self.assertIsNone(r2)
        self.assertEqual((ev1["event_id"], ev2["event_id"]), ("EV-A", "EV-B"))
        self.assertNotIn("data_source_id", ev1)

    def test_missing_identifier(self):
        self.assertEqual(ingest_wzdx.parse_event(feature(no_id=True)), (None, "missing_identifier"))
        self.assertEqual(ingest_wzdx.parse_event(feature("")), (None, "missing_identifier"))

    def test_null_geometry(self):
        self.assertEqual(ingest_wzdx.parse_event(feature(geometry=None)), (None, "missing_geometry"))

    def test_outside_bbox(self):
        ev, reason = ingest_wzdx.parse_event(feature(coords=[[-73.57, 45.50], [-73.56, 45.51]]))
        self.assertEqual((ev, reason), (None, "outside_bbox"))

    def test_point_geometry_supported(self):
        ev, reason = ingest_wzdx.parse_event(feature(coords=[-71.21, 46.81], geometry="Point"))
        self.assertIsNone(reason)
        self.assertEqual(ev["event_id"], "EV-001")

    def test_malformed_features(self):
        self.assertEqual(ingest_wzdx.parse_event(None), (None, "malformed"))
        self.assertEqual(ingest_wzdx.parse_event({"properties": "nope"}), (None, "malformed"))


class ActiveStatus(unittest.TestCase):
    def test_active_status_wins_over_past_end(self):
        self.assertTrue(ingest_wzdx.is_active(_event(end_date="2026-09-01T00:00:00Z"), NOW))

    def test_completed_and_cancelled_are_inactive(self):
        self.assertFalse(ingest_wzdx.is_active(_event(event_status="completed"), NOW))
        self.assertFalse(ingest_wzdx.is_active(_event(event_status="cancelled"), NOW))

    def test_unknown_status_uses_official_end_date(self):
        self.assertTrue(ingest_wzdx.is_active(_event(event_status=None, end_date="2026-10-01T00:00:00Z"), NOW))
        self.assertFalse(ingest_wzdx.is_active(_event(event_status=None, end_date="2026-09-01T00:00:00Z"), NOW))

    def test_no_end_no_status_stays_active(self):
        self.assertTrue(ingest_wzdx.is_active(_event(event_status=None, end_date=None), NOW))

    def test_planned_and_pending_listed_until_official_end_passes(self):
        self.assertTrue(ingest_wzdx.is_active(_event(event_status="planned"), NOW))
        self.assertTrue(ingest_wzdx.is_active(_event(event_status="pending"), NOW))
        self.assertFalse(ingest_wzdx.is_active(_event(event_status="planned", end_date="2026-09-01T00:00:00Z"), NOW))

    def test_archived_is_inactive(self):
        self.assertFalse(ingest_wzdx.is_active(_event(event_status="archived"), NOW))


class DiffEvents(unittest.TestCase):
    def test_no_previous_claims_no_comparison(self):
        d = ingest_wzdx.diff_events([_event("a")], None, now=NOW)
        self.assertFalse(d["has_previous"])
        self.assertEqual((d["new_count"], d["removed_count"], d["changed_count"]), (0, 0, 0))

    def test_new_removed_changed(self):
        cur = [_event("a", description="Nouvelle description"), _event("b")]
        prev = [_event("a"), _event("c", end_date="2026-09-01T00:00:00Z")]
        d = ingest_wzdx.diff_events(cur, prev, now=NOW)
        self.assertEqual([e["event_id"] for e in d["new"]], ["b"])
        self.assertEqual([e["event_id"] for e in d["removed"]], ["c"])
        self.assertTrue(d["removed"][0]["official_end_date_passed"])
        self.assertEqual(d["changed"][0]["fields"], ["description"])

    def test_removed_with_future_end_not_marked_passed(self):
        d = ingest_wzdx.diff_events([], [_event("c", end_date="2026-12-01T00:00:00Z")], now=NOW)
        self.assertFalse(d["removed"][0]["official_end_date_passed"])

    def test_update_date_alone_is_not_a_change(self):
        d = ingest_wzdx.diff_events(
            [_event("a", update_date="2026-09-17T11:59:00Z")],
            [_event("a", update_date="2026-09-16T00:00:00Z")], now=NOW)
        self.assertEqual(d["changed_count"], 0)

    def test_deterministic_order(self):
        cur = [_event("z"), _event("m"), _event("a")]
        d1 = ingest_wzdx.diff_events(cur, [], now=NOW)
        d2 = ingest_wzdx.diff_events(list(reversed(cur)), [], now=NOW)
        self.assertEqual([e["event_id"] for e in d1["new"]], ["a", "m", "z"])
        self.assertEqual(d1, d2)

    def test_entries_are_proposed(self):
        d = ingest_wzdx.diff_events([_event("b")], [_event("a")], now=NOW)
        for entry in d["new"] + d["removed"] + d["changed"]:
            self.assertEqual(entry["status"], "proposed")


class Collect(unittest.TestCase):
    def _paths(self, temp):
        return Path(temp) / "data" / "raw", Path(temp) / "data" / "roadworks" / "latest_roadworks.json"

    def test_online_success_counts_filters_and_stores(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            body = geojson(
                feature("b"), feature("a"),
                feature("a"),                             # duplicate_identifier (feed repeats some ids)
                feature(no_id=True),                      # missing_identifier
                feature("c", geometry=None),              # missing_geometry
                feature("d", coords=[[-73.57, 45.50]]),   # outside_bbox
                feature("e", status="completed"),         # parsed, inactive
            )
            result = ingest_wzdx.collect([SRC], NOW, raw_dir=raw_dir, store_path=store_path,
                                         fetch=lambda url: (body, "application/json"))
            self.assertTrue(result["ok"])
            store = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(store["method"], "wzdx-roadworks-v2")
            self.assertEqual([e["event_id"] for e in store["events"]], ["a", "b"])
            c = store["counts"]
            self.assertEqual((c["features"], c["parsed"], c["active"]), (7, 3, 2))
            self.assertEqual((c["missing_identifier"], c["missing_geometry"], c["outside_bbox"]), (1, 1, 1))
            self.assertEqual(c["duplicate_identifier"], 1)
            self.assertFalse(store["diff"]["has_previous"])
            self.assertEqual(store["fetched_at"], NOW.isoformat())
            snaps = list((raw_dir / "wzdx-test").glob("*.geojson"))
            self.assertEqual(len(snaps), 1)
            meta = json.loads(snaps[0].with_suffix(".json").read_text(encoding="utf-8"))
            self.assertTrue(meta["ok"])
            self.assertEqual(meta["feature_count"], 7)

    def test_previous_store_from_another_method_is_not_compared(self):
        # An identity-model change must not masquerade as mass additions/removals.
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            legacy = _store(method="wzdx-roadworks-v1", events=[_event("legacy")])
            store_path.parent.mkdir(parents=True)
            store_path.write_text(json.dumps(legacy), encoding="utf-8")
            result = ingest_wzdx.collect([SRC], NOW, raw_dir=raw_dir, store_path=store_path,
                                         fetch=lambda url: (geojson(feature("a")), "application/json"))
            self.assertTrue(result["ok"])
            self.assertFalse(result["store"]["diff"]["has_previous"])

    def test_diff_against_previous_store(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            previous = _store(events=[_event("a"), _event("gone", end_date="2026-09-01T00:00:00Z")])
            store_path.parent.mkdir(parents=True)
            store_path.write_text(json.dumps(previous), encoding="utf-8")
            body = geojson(feature("a"), feature("new-one"))
            result = ingest_wzdx.collect([SRC], NOW, raw_dir=raw_dir, store_path=store_path,
                                         fetch=lambda url: (body, "application/json"))
            diff = result["store"]["diff"]
            self.assertTrue(diff["has_previous"])
            self.assertEqual([e["event_id"] for e in diff["new"]], ["new-one"])
            self.assertEqual([e["event_id"] for e in diff["removed"]], ["gone"])
            self.assertTrue(diff["removed"][0]["official_end_date_passed"])

    def test_fetch_failure_keeps_previous_store(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            previous = _store(events=[_event("prev")])
            store_path.parent.mkdir(parents=True)
            store_path.write_text(json.dumps(previous), encoding="utf-8")

            def boom(url):
                raise OSError("network down")

            result = ingest_wzdx.collect([SRC], NOW, raw_dir=raw_dir, store_path=store_path, fetch=boom)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "fetch_failed")
            self.assertEqual(json.loads(store_path.read_text(encoding="utf-8")), previous)
            self.assertEqual(len(list((raw_dir / "wzdx-test").glob("*_error.json"))), 1)

    def test_invalid_geojson_keeps_previous_store(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            previous = _store(events=[_event("prev")])
            store_path.parent.mkdir(parents=True)
            store_path.write_text(json.dumps(previous), encoding="utf-8")
            result = ingest_wzdx.collect([SRC], NOW, raw_dir=raw_dir, store_path=store_path,
                                         fetch=lambda url: (b"not json", "application/json"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "parse_failed")
            self.assertEqual(json.loads(store_path.read_text(encoding="utf-8")), previous)
            meta = json.loads(next((raw_dir / "wzdx-test").glob("*.json")).read_text(encoding="utf-8"))
            self.assertFalse(meta["ok"])
            self.assertIn("parse_error", meta)

    def test_corrupt_previous_store_claims_no_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            store_path.parent.mkdir(parents=True)
            store_path.write_text("{not json", encoding="utf-8")
            result = ingest_wzdx.collect([SRC], NOW, raw_dir=raw_dir, store_path=store_path,
                                         fetch=lambda url: (geojson(feature("a")), "application/json"))
            self.assertTrue(result["ok"])
            self.assertFalse(result["store"]["diff"]["has_previous"])

    def test_offline_reuses_snapshot_with_original_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            snap_dir = raw_dir / "wzdx-test"
            snap_dir.mkdir(parents=True)
            (snap_dir / "20260917T060000Z_aaaaaaaaaaaa.geojson").write_bytes(geojson(feature("a")))
            (snap_dir / "20260917T060000Z_aaaaaaaaaaaa.json").write_text(
                json.dumps({"ok": True, "fetched_at": "2026-09-17T06:00:00+00:00"}), encoding="utf-8")

            def no_fetch(url):
                raise AssertionError("offline mode must not fetch")

            result = ingest_wzdx.collect([SRC], NOW, offline=True, raw_dir=raw_dir,
                                         store_path=store_path, fetch=no_fetch)
            self.assertTrue(result["ok"])
            store = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(store["fetched_at"], "2026-09-17T06:00:00+00:00")
            self.assertEqual([e["event_id"] for e in store["events"]], ["a"])

    def test_offline_without_snapshot_is_an_honest_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)

            def no_fetch(url):
                raise AssertionError("offline mode must not fetch")

            result = ingest_wzdx.collect([SRC], NOW, offline=True, raw_dir=raw_dir,
                                         store_path=store_path, fetch=no_fetch)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "no_snapshot")
            self.assertFalse(store_path.exists())

    def test_no_enabled_sources_is_a_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            raw_dir, store_path = self._paths(temp)
            result = ingest_wzdx.collect([], NOW, raw_dir=raw_dir, store_path=store_path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "no_sources")


class RegistryWiring(unittest.TestCase):
    def test_wzdx_source_is_registered(self):
        srcs = ingest_rss.load_enabled_by_type(harness.ROOT / "sources.yaml", "wzdx")
        self.assertEqual([s["id"] for s in srcs], ["wzdx-quebec"])
        self.assertEqual(srcs[0]["url"], "https://quebec.gewi.com/wzdx/pull")
        self.assertEqual(srcs[0]["source_kind"], "official")

    def test_rss_ceiling_untouched(self):
        self.assertEqual(len(ingest_rss.load_enabled_rss(harness.ROOT / "sources.yaml")), 12)


class PipelineWiring(unittest.TestCase):
    def test_scripts_order(self):
        self.assertEqual(pipeline.SCRIPTS[0], "ingest_rss.py")
        self.assertEqual(pipeline.SCRIPTS[1], "ingest_wzdx.py")
        self.assertEqual(pipeline.SCRIPTS[2], "ingest_civic.py")

    def test_offline_flag_reaches_only_network_fetch_steps(self):
        with patch("sys.argv", ["pipeline.py", "--offline"]), patch.object(pipeline, "run") as run:
            self.assertEqual(pipeline.main(), 0)
        calls = [(c.args[0], c.args[1:]) for c in run.call_args_list]
        self.assertIn(("ingest_wzdx.py", ("--offline",)), calls)
        for name, extra in calls:
            if name in ("ingest_wzdx.py", "ingest_civic.py", "fetch_brief_media.py"):
                self.assertEqual(extra, ("--offline",))
            else:
                self.assertEqual(extra, ())


class RoadworksRender(unittest.TestCase):
    def test_absent_by_default_and_byte_identical(self):
        without = brief.render_brief([], TS, [], _run())
        with_none = _render(None)
        self.assertEqual(without, with_none)
        self.assertNotIn('id="travaux"', without)

    def test_absent_for_empty_or_invalid_stores(self):
        for rw in ({}, {"events": "nope"}, _store(fetched_at=None), _store(fetched_at="garbage")):
            self.assertNotIn('id="travaux"', _render(rw))

    def test_renders_with_attribution_and_map(self):
        page = _render(_store())
        self.assertIn('id="travaux"', page)
        self.assertIn("CC-BY 4.0", page)
        self.assertIn("donneesquebec.ca", page)
        self.assertIn("carte.ville.quebec.qc.ca", page)
        self.assertIn("du flux officiel", page)
        self.assertIn("Boulevard Charest Est", page)

    def test_empty_collection_is_honest(self):
        page = _render(_store(events=[], counts={"features": 0, "parsed": 0, "active": 0}))
        self.assertIn('id="travaux"', page)
        self.assertIn("Aucune entrave déclarée dans cette collecte", page)

    def test_display_cap_and_more_line(self):
        events = [_event(f"e{n:02d}") for n in range(12)]
        page = _render(_store(events=events))
        self.assertEqual(page.count('<li class="rw-item'), 8)
        self.assertIn("+ 4 autres entraves déclarées", page)

    def test_severity_ordering(self):
        events = [_event("calm", vehicle_impact="no-lanes-closed"),
                  _event("hard", vehicle_impact="all-lanes-closed")]
        page = _render(_store(events=events))
        self.assertLess(page.index("rw-sev-0"), page.index("rw-sev-5"))

    def test_planned_and_pending_carry_the_city_status_label(self):
        page = _render(_store(events=[_event("p1", event_status="planned"),
                                      _event("p2", event_status="pending")]))
        self.assertIn("Planifiée", page)
        self.assertIn("En attente", page)

    def test_active_status_gets_no_badge(self):
        page = _render(_store())
        self.assertNotIn("Planifiée", page)
        self.assertNotIn("En attente", page)

    def test_detour_and_alternating_traffic_labels(self):
        page = _render(_store(events=[_event("d1", event_type="detour",
                                             vehicle_impact="alternating-one-way")]))
        self.assertIn("Détour", page)
        self.assertIn("Circulation en alternance", page)
        self.assertIn("rw-sev-2", page)

    def test_xss_escaped(self):
        page = _render(_store(events=[_event(
            description="<script>alert(1)</script>",
            road_names=["<img src=x onerror=alert(1)>"],
        )]))
        # Descriptions go through plain(): markup is stripped, never executed.
        self.assertNotIn("<script>alert", page)
        # Road names go through esc(): markup is neutralized as text.
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)

    def test_stale_collection_warns(self):
        page = _render(_store(fetched_at="2026-09-17T04:00:00+00:00"))
        self.assertIn("Collecte à actualiser", page)

    def test_future_collection_warns(self):
        page = _render(_store(fetched_at="2026-09-17T13:00:00+00:00"))
        self.assertIn("Collecte à actualiser", page)

    def test_fresh_collection_has_no_warning(self):
        self.assertNotIn("Collecte à actualiser", _render(_store()))

    def test_estimated_dates_stay_marked(self):
        page = _render(_store(events=[_event(start_date_accuracy="estimated")]))
        self.assertIn("dates estimées par la Ville", page)

    def test_change_tags_only_with_previous_collection(self):
        diff = {"has_previous": True, "new": [{"event_id": "EV-001"}], "changed": [],
                "removed": [], "new_count": 1, "removed_count": 0, "changed_count": 0}
        self.assertIn("rw-t-new", _render(_store(diff=diff)))
        silent = dict(diff, has_previous=False)
        self.assertNotIn("rw-t-new", _render(_store(diff=silent)))

    def test_removed_is_not_ended(self):
        diff = {"has_previous": True, "new": [], "changed": [],
                "removed": [{"event_id": "x"}], "new_count": 0, "removed_count": 2, "changed_count": 0}
        page = _render(_store(diff=diff))
        self.assertIn("Depuis la dernière collecte", page)
        self.assertIn("pas nécessairement terminée", page)

    def test_section_sits_between_essentiel_and_changements(self):
        ledger = {"status": "proposed", "has_previous": True, "new": [], "developed": [],
                  "quiet": [], "new_count": 0, "developed_count": 0, "quiet_count": 0}
        page = brief.render_brief([], TS, [], _run(), ledger=ledger, roadworks=_store())
        # The departure strip (roadworks) answers the most urgent question first —
        # it precedes the editorial content (essentiel/stories) and changes.
        self.assertLess(page.index('id="travaux"'), page.index('id="essentiel"'))
        self.assertLess(page.index('id="essentiel"'), page.index('id="changements"'))

    def test_roadworks_is_not_promoted_into_the_masthead(self):
        # Design law: the structural / roadworks reading never reaches the hero
        # or the masthead nav. The answer-first digest (below the fold) may
        # still offer one quiet jump link to the section.
        page = _render(_store())
        masthead = page[page.index('class="masthead"'):page.index("</header>")]
        self.assertNotIn('href="#travaux"', masthead)

    def test_rendered_brief_passes_site_validation(self):
        page = _render(_store())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.html").write_text(page, encoding="utf-8")
            (root / "assets").mkdir()
            for name in ("assets/brief.css", "assets/fonts.css", "assets/brief.js", "favicon.svg", "apple-touch-icon.png", "site.webmanifest", "explorer.html", "morning.html", "llms.txt", "index.html.md", *stage_public.OPTIONAL_PAGES, *stage_public.METHODS):
                (root / name).write_text("placeholder", encoding="utf-8")
            (root / "methode").mkdir(exist_ok=True)
            for name in (*stage_public.METHOD_PAGES, "index"):
                (root / "methode" / f"{name}.html").write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


if __name__ == "__main__":
    unittest.main()
