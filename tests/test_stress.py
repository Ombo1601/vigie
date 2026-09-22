"""Cycle-1 stress hardening: garbage in, honest page out.

Brutal inputs the wild can produce — corrupt JSON, mixed UTC offsets, NaN
coordinates, bidi spoofing, megabyte titles, hostile lock files — must never
crash the pipeline, never invent a fact, and never reach the rendered page.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import ambient_pulse
import cluster_issues
import dossier_history
import ingest_wzdx
import rank_display
import refresh
import resident_brief as brief
import stage_public

from test_resident_brief import collection, story
from test_roadworks import NOW, TS, _store, feature

# 12:00 in Montréal (-04:00) is 16:00Z. The 15:00Z string sorts AFTER the
# -04:00 string lexicographically but happens an hour EARLIER in real time:
# forward-only history must compare chronologically, not as text.
TS_16Z_VIA_MINUS4 = "2026-09-17T12:00:00-04:00"
TS_15Z = "2026-09-17T15:00:00+00:00"
TS_17Z = "2026-09-17T17:00:00+00:00"
TS_06Z = "2026-09-17T06:00:00+00:00"
TS_12Z = "2026-09-17T12:00:00+00:00"


def issue(iid: str, scar: str = "scar", sources: int = 2) -> dict:
    return {"issue_id": iid, "scar": scar, "source_count": sources}


class CoercionNeverCrashes(unittest.TestCase):
    def test_safe_int_absorbs_garbage(self) -> None:
        for bad in ("abc", None, [], {}, object(), float("nan"), float("inf")):
            with self.subTest(bad=repr(bad)):
                self.assertEqual(dossier_history._safe_int(bad), 0)
                self.assertEqual(brief.safe_int(bad), 0)
        self.assertEqual(dossier_history._safe_int(3.9), 3)
        self.assertEqual(dossier_history._safe_int(True), 1)
        self.assertEqual(dossier_history._safe_int(-7), -7)

    def test_sanitize_strips_display_spoofing(self) -> None:
        self.assertEqual(brief.sanitize("a\u202eb\x00c\u00add\ufeff"), "abcd")
        self.assertEqual(brief.esc("<b>\u202e"), "&lt;b&gt;")
        self.assertEqual(brief.sanitize(None), "")

    def test_garbage_counters_in_stored_dossier_record(self) -> None:
        h = {
            "method": dossier_history.METHOD,
            "updated_at": TS_06Z,
            "edition_count": "beaucoup",
            "dossiers": {
                "a": {"scar": "x", "first_seen": TS_06Z, "last_seen": TS_06Z,
                      "editions_seen": "abc", "editions_missed": None, "timeline": None},
                "b": {"scar": "y", "first_seen": TS_06Z, "last_seen": TS_06Z,
                      "editions_seen": 1, "editions_missed": "xyz", "timeline": []},
            },
        }
        out = dossier_history.update_history(
            h, [{"issue_id": "a", "scar": "x", "source_count": "many"}], TS_12Z
        )
        self.assertEqual(out["edition_count"], 1)
        self.assertEqual(out["dossiers"]["a"]["editions_seen"], 1)
        self.assertEqual(out["dossiers"]["a"]["timeline"][-1]["sources"], 0)
        self.assertEqual(out["dossiers"]["b"]["editions_missed"], 1)

    def test_tracking_of_garbage_record(self) -> None:
        h = {"dossiers": {"a": {"editions_seen": "x", "editions_missed": []}}}
        t = dossier_history.tracking_of(h, "a")
        self.assertEqual(t["editions_seen"], 0)
        self.assertEqual(t["editions_missed"], 0)
        self.assertEqual(t["status"], "proposed")

    def test_load_history_garbage_edition_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "history.json"
            p.write_text(json.dumps({
                "method": dossier_history.METHOD, "edition_count": "abc",
                "updated_at": None, "dossiers": {"a": "not-a-dict", "b": {}},
            }), encoding="utf-8")
            h = dossier_history.load_history(p)
            self.assertEqual(h["edition_count"], 0)
            self.assertEqual(list(h["dossiers"]), ["b"])

    def test_garbage_counters_in_stored_event_record(self) -> None:
        h = {
            "method": ingest_wzdx.HISTORY_METHOD,
            "updated_at": TS_06Z,
            "collection_count": "lots",
            "events": {
                "EV1": {"first_seen": TS_06Z, "last_seen": TS_06Z,
                        "collections_seen": "x", "collections_missed": None},
                "EV2": "not-a-dict",
                "EV3": {"first_seen": TS_06Z, "last_seen": TS_06Z,
                        "collections_seen": 1, "collections_missed": "y"},
            },
        }
        out = ingest_wzdx.update_event_history(h, [{"event_id": "EV1"}], TS_12Z)
        self.assertEqual(out["collection_count"], 1)
        self.assertEqual(out["events"]["EV1"]["collections_seen"], 1)
        self.assertNotIn("EV2", out["events"])
        self.assertEqual(out["events"]["EV3"]["collections_missed"], 1)


class TimestampsCompareChronologically(unittest.TestCase):
    def test_lexicographic_trap_is_real(self) -> None:
        # Guards the premise of every test below: text order lies here.
        self.assertGreater(TS_15Z, TS_16Z_VIA_MINUS4)

    def test_dossier_rejects_earlier_edition_across_offsets(self) -> None:
        h = {"method": dossier_history.METHOD, "updated_at": TS_16Z_VIA_MINUS4,
             "edition_count": 1, "dossiers": {}}
        out = dossier_history.update_history(h, [issue("a")], TS_15Z)
        self.assertIs(out, h)

    def test_dossier_accepts_later_edition_across_offsets(self) -> None:
        h = {"method": dossier_history.METHOD, "updated_at": TS_16Z_VIA_MINUS4,
             "edition_count": 1, "dossiers": {}}
        out = dossier_history.update_history(h, [issue("a")], TS_17Z)
        self.assertEqual(out["edition_count"], 2)
        self.assertEqual(out["updated_at"], TS_17Z)

    def test_z_suffix_and_naive_read_as_utc(self) -> None:
        h = {"method": dossier_history.METHOD, "updated_at": "2026-09-17T16:00:00Z",
             "edition_count": 1, "dossiers": {}}
        self.assertIs(dossier_history.update_history(h, [issue("a")], "2026-09-17T15:59:59Z"), h)
        self.assertEqual(
            dossier_history.update_history(h, [issue("a")], "2026-09-17T16:00:01Z")["edition_count"], 2
        )
        naive = {"method": dossier_history.METHOD, "updated_at": "2026-09-17T16:00:00",
                 "edition_count": 1, "dossiers": {}}
        self.assertIs(dossier_history.update_history(naive, [issue("a")], TS_15Z), naive)

    def test_unparseable_falls_back_to_string_order(self) -> None:
        h = {"method": dossier_history.METHOD, "updated_at": "garbage",
             "edition_count": 1, "dossiers": {}}
        self.assertIs(dossier_history.update_history(h, [issue("a")], "aaa"), h)
        self.assertEqual(
            dossier_history.update_history(h, [issue("a")], "zzz")["edition_count"], 2
        )

    def test_is_after_agrees_across_modules(self) -> None:
        for is_after in (dossier_history._is_after, ingest_wzdx._is_after):
            with self.subTest(fn=is_after.__module__):
                self.assertFalse(is_after(TS_15Z, TS_16Z_VIA_MINUS4))
                self.assertTrue(is_after(TS_17Z, TS_16Z_VIA_MINUS4))
                self.assertTrue(is_after(TS_15Z, ""))
                self.assertFalse(is_after("", TS_15Z))
                self.assertFalse(is_after(TS_15Z, TS_15Z))

    def test_wzdx_history_rejects_earlier_collection(self) -> None:
        h = {"method": ingest_wzdx.HISTORY_METHOD, "updated_at": TS_16Z_VIA_MINUS4,
             "collection_count": 1, "events": {}}
        out = ingest_wzdx.update_event_history(h, [{"event_id": "E"}], TS_15Z)
        self.assertIs(out, h)
        self.assertEqual(
            ingest_wzdx.update_event_history(h, [{"event_id": "E"}], TS_17Z)["collection_count"], 2
        )


class CorruptPipelineInputs(unittest.TestCase):
    def tearDown(self) -> None:
        cluster_issues.IN_PATH = harness.ROOT / "data" / "normalized" / "latest_enriched.json"
        cluster_issues.OUT_ISSUES = harness.ROOT / "data" / "issues" / "latest_issues.json"

    def _payload(self) -> dict:
        def item(sid: str, title: str) -> dict:
            return {
                "id": sid + title[:8], "title": title, "summary": "",
                "url": "https://example.test/" + sid, "source_id": sid,
                "source_name": sid, "language": "fr",
                "enrich": {"geo": {"geo": "quebec"}, "topics": [{"topic": "trade"}]},
            }
        return {
            "normalized_at": TS_12Z,
            "candidates": [
                item("le-devoir", "Le Canada privatisera ses aéroports"),
                item("cbc-politics", "Canada to privatize airports"),
            ],
        }

    def _wire(self, d: Path) -> tuple[Path, Path]:
        inp, out = d / "in.json", d / "out.json"
        cluster_issues.IN_PATH = inp
        cluster_issues.OUT_ISSUES = out
        return inp, out

    def test_corrupt_json_exits_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            inp, out = self._wire(Path(raw))
            inp.write_text("{{{not json", encoding="utf-8")
            with self.assertRaises(SystemExit):
                cluster_issues.main()
            self.assertFalse(out.exists())

    def test_non_object_payload_exits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            inp, _ = self._wire(Path(raw))
            inp.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(SystemExit):
                cluster_issues.main()

    def test_non_list_candidates_exits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            inp, _ = self._wire(Path(raw))
            inp.write_text(json.dumps({"candidates": {"a": 1}}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                cluster_issues.main()

    def test_malformed_candidates_counted_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            inp, out = self._wire(Path(raw))
            payload = self._payload()
            payload["candidates"] = ["garbage", None, 42] + payload["candidates"]
            inp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            cluster_issues.main()
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["excluded_candidates"]["malformed_candidate"], 3)

    def test_corrupt_previous_issues_never_blocks_the_edition(self) -> None:
        for corrupt in ("not json", "[1,2]", '{"issues": "garbage"}',
                        '{"issues": [null, "x", 3]}', '{"no_issues": true}'):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as raw:
                inp, out = self._wire(Path(raw))
                inp.write_text(json.dumps(self._payload(), ensure_ascii=False), encoding="utf-8")
                out.write_text(corrupt, encoding="utf-8")
                cluster_issues.main()
                doc = json.loads(out.read_text(encoding="utf-8"))
                self.assertIn("issues", doc)

    def test_corrupt_history_json_starts_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp, out = self._wire(d)
            inp.write_text(json.dumps(self._payload(), ensure_ascii=False), encoding="utf-8")
            (d / "history.json").write_text("garbage", encoding="utf-8")
            cluster_issues.main()
            stored = json.loads((d / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["method"], dossier_history.METHOD)
            self.assertEqual(stored["edition_count"], 1)


class RenderStress(unittest.TestCase):
    def test_garbage_issues_never_crash_render(self) -> None:
        page = brief.render_brief(
            [story()], NOW.isoformat(),
            [None, "garbage", 42, {}, {"issue_id": "x"}, []],
            collection(),
        )
        self.assertIn("<html", page)

    def test_garbage_stories_never_crash_render(self) -> None:
        page = brief.render_brief([None, "garbage", 42, story()], NOW.isoformat(), [], collection())
        self.assertIn("<html", page)

    def test_bidi_and_control_chars_never_reach_the_page(self) -> None:
        evil = story(
            title="Travaux\u202e\u200b\u00ad cach\u00e9\ufeffs\u202c",
            summary="R\u2066\u00e9sum\u00e9\u2069 \x07\x1b",
        )
        page = brief.render_brief([evil], NOW.isoformat(), [], collection())
        for ch in "\u202e\u202c\u200b\u00ad\ufeff\u2066\u2069\x07\x1b":
            self.assertNotIn(ch, page)
        self.assertIn("Travaux", page)
        self.assertIn("cach\u00e9s", page)

    def test_megabyte_title_and_summary_are_capped(self) -> None:
        huge = story(title="T" * 1_000_000, summary="S" * 1_000_000)
        rows, excluded = brief.prepare_items([huge], NOW)
        self.assertEqual(excluded, 0)
        self.assertLessEqual(len(rows[0]["title"]), brief.TITLE_CAP + 1)
        self.assertLessEqual(len(rows[0]["summary"]), brief.SUMMARY_CAP)
        page = brief.render_brief([huge], NOW.isoformat(), [], collection())
        self.assertNotIn("T" * (brief.TITLE_CAP + 10), page)
        self.assertNotIn("S" * (brief.SUMMARY_CAP + 10), page)
        self.assertLess(len(page), 100_000)

    def test_missing_url_excludes_the_story(self) -> None:
        rows, excluded = brief.prepare_items(
            [story(title=None, summary=None, source_name=None, url=None, published_at=None)], NOW
        )
        self.assertEqual(rows, [])
        self.assertEqual(excluded, 1)

    def test_garbage_roadworks_store_renders_without_invented_counts(self) -> None:
        rw = _store(
            diff={"has_previous": True, "new_count": "abc",
                  "changed_count": None, "removed_count": [1]},
            event_history={"method": "foreign-method", "events": "garbage"},
        )
        page = brief.render_brief([], TS, [], collection(), roadworks=rw)
        self.assertIn("<html", page)
        self.assertNotIn("rw-changes", page)
        self.assertNotIn("Dans nos collectes depuis", page)

    def test_malformed_story_facets_never_crash_a_render(self) -> None:
        evil = story()
        evil["enrich"] = {
            "geo": {"geo": 42}, "topics": [{"topic": {"nested": 1}}, {"topic": [1]}],
            "impacts": [None, {"units": [None, {}]}],
        }
        page = brief.render_brief([evil], NOW.isoformat(), [], collection())
        self.assertIn("<html", page)
        # The workbench ranks render raw store rows without prepare_items.
        hostile = story(url=7, rank_score="x", source_name=42)
        page2 = rank_display.render_html([hostile, None, 42], NOW.isoformat(), [], {})
        self.assertIn("<!DOCTYPE html", page2)

    def _nested_evil_issue(self, **overrides) -> dict:
        issue = {
            "issue_id": "x", "scar": "s", "question": "Sujet suivi",
            "geo_focus": ["quebec-city"], "source_count": 2,
            "topic": {"topic": "other"},
        }
        issue.update(overrides)
        return issue

    def test_malformed_nested_issue_fields_never_crash_the_brief(self) -> None:
        for overrides in (
            {"tensions": [None, "junk", 42]},
            {"tensions": [{"items": [None, 3, "x"]}]},
            {"tensions": [{"items": [], "institution_name": None}]},
            {"silence": {"silent": [None, "junk"]}},
            {"silence": "garbage"},
            {"geo_focus": 42, "topic": None},
        ):
            with self.subTest(overrides=overrides):
                page = brief.render_brief(
                    [story()], NOW.isoformat(), [self._nested_evil_issue(**overrides)], collection()
                )
                self.assertIn("<html", page)

    def test_malformed_nested_issue_fields_never_crash_the_workbench(self) -> None:
        for overrides in (
            {"tensions": [None, "junk", 42]},
            {"tensions": [{"items": [None, 3]}]},
            {"silence": {"silent": [None, "junk"], "silent_count": "abc"}},
        ):
            with self.subTest(overrides=overrides):
                page = rank_display.render_html(
                    [story(), None, 42], NOW.isoformat(), [self._nested_evil_issue(**overrides)], {}
                )
                self.assertIn("<!DOCTYPE html", page)

    def test_malformed_pulse_never_crashes_the_morning_twin(self) -> None:
        digest = {"approaches": [None, {"question": "q", "units": [None, {"raw": "1 450 $"}],
                                        "quiet_names": [None, "Ville"]}], "pulse": {}}
        self.assertIn("<html", ambient_pulse.render_morning_html(digest))
        txt = ambient_pulse.render_morning_txt(digest)
        self.assertIn("morning pulse", txt)
        widget = ambient_pulse.render_morning_widget(digest)
        self.assertIn("Vigie morning", widget)


class WzdxHostileGeometry(unittest.TestCase):
    def test_nan_and_inf_coordinates_never_become_events(self) -> None:
        for coords in ([float("nan"), float("nan")], [float("inf"), float("-inf")],
                       [float("nan"), -71.2], [True, False]):
            with self.subTest(coords=coords):
                ev, reason = ingest_wzdx.parse_event(feature(coords=coords, geometry="Point"))
                self.assertIsNone(ev)
                self.assertEqual(reason, "outside_bbox")

    def test_string_coordinates_rejected(self) -> None:
        ev, reason = ingest_wzdx.parse_event(feature(coords=["46.81", "-71.21"], geometry="Point"))
        self.assertIsNone(ev)
        self.assertEqual(reason, "missing_geometry")

    def test_json_nan_literals_from_a_hostile_feed_do_not_crash(self) -> None:
        # json.loads accepts NaN/Infinity literals by default.
        raw = (b'{"type":"FeatureCollection","features":[{"type":"Feature","id":"EV-NAN",'
               b'"geometry":{"type":"Point","coordinates":[NaN, Infinity]},'
               b'"properties":{"event_type":"work-zone","event_status":"active"}}]}')
        doc = json.loads(raw)
        ev, reason = ingest_wzdx.parse_event(doc["features"][0])
        self.assertIsNone(ev)
        self.assertEqual(reason, "outside_bbox")

    def test_partial_nan_linestring_keeps_the_valid_point(self) -> None:
        ev, reason = ingest_wzdx.parse_event(
            feature(coords=[[-71.21, 46.81], [float("nan"), float("nan")]])
        )
        self.assertIsNone(reason)
        self.assertEqual(ev["event_id"], "EV-001")
        # Coordinates never enter the store: geometry is a bbox gate only.
        self.assertNotIn("geometry", ev)
        self.assertNotIn("coordinates", json.dumps(ev))

    def test_malformed_features_get_honest_skip_reasons(self) -> None:
        cases = [
            (None, "malformed"), (42, "malformed"), ("x", "malformed"),
            ([], "malformed"), ({"properties": "no"}, "malformed"),
            ({"type": "Feature", "properties": {}}, "missing_identifier"),
            ({"id": "E", "properties": {}}, "missing_geometry"),
        ]
        for bad, expected in cases:
            with self.subTest(bad=repr(bad)[:40]):
                ev, reason = ingest_wzdx.parse_event(bad)
                self.assertIsNone(ev)
                self.assertEqual(reason, expected)


class RefreshLockStress(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._lock_orig, self._log_orig = refresh.LOCK_PATH, refresh.LOG_PATH
        refresh.LOCK_PATH = d / "refresh.lock"
        refresh.LOG_PATH = d / "refresh.log"

    def tearDown(self) -> None:
        refresh.LOCK_PATH, refresh.LOG_PATH = self._lock_orig, self._log_orig
        self._tmp.cleanup()

    def test_fresh_lock_blocks_a_second_run(self) -> None:
        refresh.LOCK_PATH.write_text("123", encoding="utf-8")
        self.assertFalse(refresh.acquire_lock())

    def test_stale_lock_is_taken_over(self) -> None:
        refresh.LOCK_PATH.write_text("123", encoding="utf-8")
        old = time.time() - refresh.LOCK_STALE_SECONDS - 60
        os.utime(refresh.LOCK_PATH, (old, old))
        self.assertTrue(refresh.acquire_lock())
        self.assertEqual(refresh.LOCK_PATH.read_text(encoding="utf-8"), str(os.getpid()))

    def test_a_fresh_takeover_claim_blocks_a_second_taker(self) -> None:
        refresh.LOCK_PATH.write_text("999", encoding="utf-8")
        old = time.time() - refresh.LOCK_STALE_SECONDS - 60
        os.utime(refresh.LOCK_PATH, (old, old))
        claim = refresh.LOCK_PATH.with_name(refresh.LOCK_PATH.name + ".takeover")
        claim.write_text("other", encoding="utf-8")
        try:
            self.assertFalse(refresh.acquire_lock())
            # the blocked taker must not disturb the lock it did not win
            self.assertEqual(refresh.LOCK_PATH.read_text(encoding="utf-8"), "999")
        finally:
            claim.unlink()

    def test_a_stale_takeover_claim_is_reclaimed(self) -> None:
        refresh.LOCK_PATH.write_text("999", encoding="utf-8")
        old = time.time() - refresh.LOCK_STALE_SECONDS - 60
        os.utime(refresh.LOCK_PATH, (old, old))
        claim = refresh.LOCK_PATH.with_name(refresh.LOCK_PATH.name + ".takeover")
        claim.write_text("dead", encoding="utf-8")
        os.utime(claim, (old, old))
        self.assertTrue(refresh.acquire_lock())
        self.assertEqual(refresh.LOCK_PATH.read_text(encoding="utf-8"), str(os.getpid()))
        self.assertFalse(claim.exists())

    def test_main_skips_cleanly_while_locked(self) -> None:
        refresh.LOCK_PATH.write_text("123", encoding="utf-8")
        self.assertEqual(refresh.main(["--no-deploy"]), 0)
        logged = refresh.LOG_PATH.read_text(encoding="utf-8")
        self.assertIn("SKIP", logged)
        self.assertNotIn("START pipeline", logged)
        # A skipped run must never delete the active run's lock.
        self.assertTrue(refresh.LOCK_PATH.exists())


class SecurityHeadersConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = harness.ROOT / "public" / "vercel.json"
        cls.cfg = json.loads(path.read_text(encoding="utf-8"))
        cls.rules = {
            rule["source"]: {h["key"].lower(): h["value"] for h in rule["headers"]}
            for rule in cls.cfg["headers"]
        }

    def test_shape_is_valid(self) -> None:
        self.assertIsInstance(self.cfg["headers"], list)
        for rule in self.cfg["headers"]:
            self.assertIsInstance(rule["source"], str)
            self.assertTrue(rule["headers"])
            for h in rule["headers"]:
                self.assertIsInstance(h["key"], str)
                self.assertIsInstance(h["value"], str)

    def test_catch_all_hardening(self) -> None:
        common = self.rules["/(.*)"]
        self.assertEqual(common["x-content-type-options"], "nosniff")
        self.assertEqual(common["x-frame-options"], "DENY")
        self.assertEqual(common["referrer-policy"], "no-referrer")
        self.assertIn("geolocation=()", common["permissions-policy"])

    def test_brief_csp_is_strict(self) -> None:
        for source in ("/", "/index.html"):
            csp = self.rules[source]["content-security-policy"]
            self.assertIn("default-src 'none'", csp)
            self.assertIn("script-src 'self'", csp)
            self.assertIn("img-src 'self' data:;", csp)
            self.assertNotIn("unsafe-inline", csp)
            self.assertNotIn("fonts.googleapis", csp)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertIn("form-action 'none'", csp)
            self.assertIn("base-uri 'none'", csp)

    def test_experimental_pages_keep_inline_and_fonts_working(self) -> None:
        explorer = self.rules["/explorer.html"]["content-security-policy"]
        self.assertIn("script-src 'self' 'unsafe-inline'", explorer)
        self.assertIn("style-src 'self' 'unsafe-inline'", explorer)
        morning = self.rules["/morning.html"]["content-security-policy"]
        # morning.html carries only a non-executing JSON data block: it has no
        # reason to allow inline script.
        self.assertIn("script-src 'self';", morning)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", morning)
        for csp in (explorer, morning):
            # Type is self-hosted now: no external font origin may remain.
            self.assertNotIn("fonts.googleapis", csp)
            self.assertNotIn("fonts.gstatic", csp)
            self.assertIn("font-src 'self'", csp)
            self.assertIn("connect-src 'self'", csp)
            self.assertIn("frame-ancestors 'none'", csp)

    def test_method_and_media_paths_carry_a_csp(self) -> None:
        for source in ("/(.*).md", "/sources.yaml", "/media/(.*)"):
            self.assertIn("default-src 'none'",
                          self.rules[source]["content-security-policy"])

    def test_media_is_cached_immutably(self) -> None:
        media = self.rules["/media/(.*)"]
        self.assertEqual(media["cache-control"], "public, max-age=31536000, immutable")
        self.assertIn("default-src 'none'", media["content-security-policy"])

    def test_root_and_staged_configs_agree(self) -> None:
        # The git-build root config and the CLI-uploaded staged copy must not
        # drift: whichever deploy path ran last decides production headers.
        root = json.loads((harness.ROOT / "vercel.json").read_text(encoding="utf-8"))
        root_rules = {
            rule["source"]: {h["key"].lower(): h["value"] for h in rule["headers"]}
            for rule in root["headers"]
        }
        self.assertEqual(root_rules, self.rules)

    def test_vercel_json_is_stageable(self) -> None:
        self.assertIn(".json", stage_public.ASSET_EXTENSIONS)


class BriefJsShape(unittest.TestCase):
    """The front-door script must stay lean and keep its storage contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (harness.ROOT / "public" / "assets" / "brief.js").read_text(encoding="utf-8")

    def test_no_quadratic_scans(self) -> None:
        self.assertNotIn("state.saved.includes", self.js)
        self.assertNotIn("rows.some", self.js)
        self.assertNotIn("state.seen.includes", self.js)

    def test_search_is_debounced(self) -> None:
        self.assertIn("setTimeout(render, 150)", self.js)

    def test_uses_sets_and_cached_cards(self) -> None:
        for needle in ("savedSet", "seenSet", "idSet", "new Set("):
            self.assertIn(needle, self.js)

    def test_storage_contract_unchanged(self) -> None:
        self.assertIn("vigie.resident.v1", self.js)
        self.assertIn("/^[a-f0-9]{20}$/", self.js)
        self.assertIn("1200", self.js)

    def test_french_ui_strings_survived_the_rewrite(self) -> None:
        for needle in ("Gardé ✓", "Garder ＋", "articles dans cette vue",
                       "Mémoriser ce point de lecture", "ne figurent plus dans cette collecte",
                       "La limite de 1 200 repères est atteinte"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.js)

    def test_record_emitters_stress_and_edge_cases(self) -> None:
        import depart
        import recits
        import memoire
        import affiche
        import substrate
        import registre
        import compile_anomalies
        import edge_atlas

        # Test empty/malformed inputs to depart
        d_res = depart.emit({}, [], {}, {}, "2026-09-20T12:00:00+00:00", out=Path(tempfile.mkdtemp()) / "partir.html")
        self.assertTrue(d_res.get("written") or "written" in d_res)

        # Test empty/malformed inputs to recits
        r_out = Path(tempfile.mkdtemp())
        r_res = recits.emit([], [], {}, {}, {}, out_dir=r_out / "dossiers", out_index=r_out / "dossiers.html")
        self.assertIsInstance(r_res, dict)

        # Test empty/malformed inputs to memoire
        m_out = Path(tempfile.mkdtemp())
        m_res = memoire.emit({}, [], out_dir=m_out / "memoire", out_index=m_out / "memoire.html")
        self.assertIsInstance(m_res, dict)

        # Test empty/malformed inputs to affiche
        a_out = Path(tempfile.mkdtemp()) / "affiche.html"
        affiche.emit([], [], None, {}, "2026-09-20T12:00:00+00:00", out_html=a_out)
        self.assertTrue(a_out.exists())

        # Test empty/malformed inputs to substrate
        s_out = Path(tempfile.mkdtemp())
        substrate.emit([], [], {}, None, {}, "2026-09-20T12:00:00+00:00",
                       out_llms=s_out / "llms.txt", out_md=s_out / "index.html.md",
                       out_delta=s_out / "delta" / "latest.json")
        self.assertTrue((s_out / "llms.txt").exists())
        self.assertTrue((s_out / "index.html.md").exists())
        self.assertTrue((s_out / "delta" / "latest.json").exists())

        # Test compile_anomalies with empty street group
        sg = compile_anomalies._StreetGroup()
        self.assertEqual(sg.display(), "")

        # Test edge_atlas with empty variant_counts
        self.assertEqual(edge_atlas._display_name({}), "")


if __name__ == "__main__":
    unittest.main()
