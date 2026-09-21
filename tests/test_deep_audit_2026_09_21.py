"""Deep-audit regressions (2026-09-21).

Each test locks a confirmed defect: publication stamps compared as text,
dossier and record titles over the published cap, compass-blind street
joins, a re-render that ignored the same collection clock spelled two ways,
a roads lane that called a failed fetch "unchanged", a foreign dollar that
erased a separate local price, grocery "Metro" labelled as transit, world
fog cloaked as Québec because the text said Canada, client/server fold
drift on ß and fi-ligatures, and a roadworks store committed without a
durable raw snapshot.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import change_ledger
import cluster_issues
import enrich
import ingest_civic
import ingest_rss
import ingest_wzdx
import normalize
import rank_display
import recits
import refresh
import registre
import resident_brief as brief
import store_io
import substrate

from test_registre import issue as reg_issue
from test_registre import payload
from test_roadworks import NOW, SRC, _store, feature, geojson

ROOT = harness.ROOT


def _issue(latest: str) -> dict:
    return {
        "issue_id": "a",
        "question": "Q",
        "item_count": 2,
        "source_count": 2,
        "official_voice_count": 0,
        "media_remix": True,
        "geo_focus": ["quebec-city"],
        "evidence": {"publication_latest": latest},
    }


class PublicationClock(unittest.TestCase):
    def test_same_instant_with_z_and_offset_is_not_development(self) -> None:
        led = change_ledger.diff_editions(
            [_issue("2026-09-12T00:00:00Z")],
            [_issue("2026-09-12T00:00:00+00:00")],
        )
        self.assertEqual(led["developed_count"], 0)

    def test_local_offset_that_sorts_first_can_still_be_newer(self) -> None:
        # 01:00-04:00 is 05:00Z, which is after 04:00Z. Text order says the
        # opposite because "T01" < "T04".
        led = change_ledger.diff_editions(
            [_issue("2026-09-12T01:00:00-04:00")],
            [_issue("2026-09-12T04:00:00+00:00")],
        )
        self.assertTrue(led["developed"][0]["delta"]["newer_publication"])


class PublishedTitleCap(unittest.TestCase):
    def test_dossier_headlines_are_plain_and_within_the_cap(self) -> None:
        huge = "Pont &amp; chauss\u00e9e \u202e" + ("T" * 500)
        rows = brief._headline_rows({
            "tensions": [{
                "institution_name": "Radio\u202eCanada",
                "items": [{
                    "title": huge,
                    "url": "https://rc.example/a",
                    "published_at": "2026-09-20T12:00:00+00:00",
                }],
            }],
        })
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0]["title"]), brief.TITLE_CAP)
        self.assertNotIn("\u202e", rows[0]["title"])
        self.assertNotIn("\u202e", rows[0]["inst"])
        self.assertIn("&", rows[0]["title"])
        self.assertNotIn("&amp;", rows[0]["title"])

    def test_recits_and_substrate_ellipsis_counts_toward_the_cap(self) -> None:
        title = "T" * 400
        block = recits._voice_block({
            "institution_name": "Le Soleil",
            "items": [{"title": title, "url": "https://soleil.example/a"}],
        })
        self.assertNotIn("T" * (brief.TITLE_CAP + 1), block)
        self.assertLessEqual(len(substrate._plain(title, brief.TITLE_CAP)), brief.TITLE_CAP)

    def test_explorer_voice_link_is_capped(self) -> None:
        html = rank_display.issue_stage_html({
            "question": "Sujet",
            "tensions": [{
                "institution_name": "Le Soleil",
                "items": [{"title": "T" * 400, "url": "https://soleil.example/a"}],
            }],
        })
        self.assertIn("voice-link", html)
        self.assertNotIn("T" * (brief.TITLE_CAP + 1), html)
        self.assertIn("…", html)


class CompassRoads(unittest.TestCase):
    def _item(self, title: str) -> dict:
        return {
            "title": title,
            "summary": "",
            "language": "fr",
            "published_at": "2026-09-20T10:00:00+00:00",
        }

    def test_opposite_compass_points_are_not_the_same_street(self) -> None:
        est = self._item("Fermeture du boulevard Hamel Est pour des travaux majeurs ce soir")
        ouest = self._item("Fermeture du boulevard Hamel Ouest pour des travaux majeurs ce soir")
        self.assertIn("hamel|est", cluster_issues.road_places(est))
        self.assertIn("hamel|ouest", cluster_issues.road_places(ouest))
        self.assertFalse(cluster_issues.same_event(est, ouest))

    def test_lowercase_est_is_the_verb_not_a_compass(self) -> None:
        verb = self._item("Le boulevard Hamel est fermé pour des travaux majeurs ce soir")
        ouest = self._item("Fermeture du boulevard Hamel Ouest pour des travaux majeurs ce soir")
        self.assertEqual(cluster_issues.road_places(verb), {"hamel"})
        self.assertTrue(cluster_issues.same_event(verb, ouest))


class RegistreSameClock(unittest.TestCase):
    def test_z_and_offset_spellings_replace_the_same_seal(self) -> None:
        state = registre.empty_state()
        state, first = registre.seal_edition(
            state, registre.edition_record(payload([reg_issue("a")]), "2026-09-10T12:00:00Z"))
        state, second = registre.seal_edition(
            state, registre.edition_record(
                payload([reg_issue("a"), reg_issue("b")]), "2026-09-10T12:00:00+00:00"))
        self.assertEqual((first, second), ("appended", "replaced"))
        self.assertEqual(len(state["seals"]), 1)
        self.assertEqual(len(state["voice"]), 1)
        self.assertEqual(state["seals"][0]["edition"], "2026-09-10T12:00:00+00:00")
        self.assertEqual(len(state["seals"][0]["record"]["dossiers"]), 2)


class RoadsFailureIsNotNoChange(unittest.TestCase):
    def test_strict_flag_fails_the_lane(self) -> None:
        with mock.patch.object(ingest_wzdx, "load_enabled_by_type", return_value=[SRC]), \
                mock.patch.object(ingest_wzdx, "collect", return_value={"ok": False, "reason": "fetch_failed"}):
            self.assertEqual(ingest_wzdx.main(["--strict"]), 1)
            self.assertEqual(ingest_wzdx.main([]), 0)

    def test_roads_lane_passes_strict_and_does_not_call_a_failure_unchanged(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name)
        commands: list[list[str]] = []

        def step(name, command, timeout, cwd=None):
            commands.append(command)
            if name == "wzdx":
                raise RuntimeError("wzdx failed (code 1)")

        with mock.patch.multiple(
            refresh,
            LOCK_PATH=directory / "refresh.lock",
            LOG_PATH=directory / "refresh.log",
            ROADS_SIGNAL_PATH=directory / "roads_signal.json",
        ), mock.patch.object(refresh, "acquire_lock", return_value=True), \
                mock.patch.object(refresh, "run_step", side_effect=step):
            code = refresh.main(["--roads-only"])
        self.assertEqual(code, 1)
        self.assertIn("--strict", commands[0])
        log = (directory / "refresh.log").read_text(encoding="utf-8")
        self.assertNotIn("NOCHANGE", log)


class PriceAndGeoPrecision(unittest.TestCase):
    def test_a_later_us_dollar_does_not_erase_a_local_price(self) -> None:
        units = enrich.propose_impact_units(
            "Au Québec, le loyer moyen est de 1 450 $ par mois, contre 2 000 $ US ailleurs",
            "",
        )
        values = [u["value"] for u in units]
        self.assertIn(1450.0, values)
        self.assertNotIn(2000.0, values)

    def test_grocery_metro_is_not_transit(self) -> None:
        labels = [t["topic"] for t in enrich.propose_topics("Metro annonce des rabais sur le panier")]
        self.assertNotIn("transport", labels)
        transit = [t["topic"] for t in enrich.propose_topics("Le métro de Montréal est interrompu")]
        self.assertIn("transport", transit)

    def test_world_fog_plus_canada_stays_linked(self) -> None:
        text = "Trump impose des tarifs au Canada"
        geo = enrich.propose_geo({"title": text, "nest_role": "linked", "geo": "linked"}, text)
        self.assertEqual(geo["geo"], "linked")

    def test_official_world_fog_plus_canada_stays_linked(self) -> None:
        text = "Trump impose des tarifs au Canada"
        geo = enrich.propose_geo(
            {"title": text, "nest_role": "primary", "source_kind": "official", "geo": "quebec-city"},
            text,
        )
        self.assertEqual(geo["geo"], "linked")


class SnapshotMustLand(unittest.TestCase):
    def test_wzdx_does_not_commit_a_store_without_a_raw_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / "raw"
            store = Path(temp) / "store.json"
            previous = _store(events=[])
            store.write_text(json.dumps(previous), encoding="utf-8")
            with mock.patch.object(ingest_wzdx.store_io, "write_bytes_dedup", side_effect=OSError("disk full")):
                result = ingest_wzdx.collect(
                    [SRC], NOW, raw_dir=raw, store_path=store,
                    fetch=lambda url: (geojson(feature("a")), "application/json"),
                )
            self.assertEqual(result.get("reason"), "snapshot_not_archived")
            self.assertEqual(json.loads(store.read_text(encoding="utf-8")), previous)

    def test_civic_does_not_commit_a_store_without_a_raw_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / "raw"
            store = Path(temp) / "store.json"
            store.write_text("{}", encoding="utf-8")
            src = {"id": "ville-test", "url": "https://www.ville.quebec.qc.ca/citoyens/participation/",
                   "name": "Ville de Québec"}
            with mock.patch.object(ingest_civic, "read_robots", return_value=(True, None)), \
                    mock.patch.object(ingest_civic.store_io, "write_bytes_dedup", side_effect=OSError("disk full")):
                result = ingest_civic.collect(
                    [src], NOW, raw_dir=raw, store_path=store,
                    fetch=lambda url: (b"<table></table>", "text/html"),
                )
            self.assertEqual(result.get("reason"), "snapshot_not_archived")
            self.assertEqual(store.read_text(encoding="utf-8"), "{}")


class CapsAndCaches(unittest.TestCase):
    def test_plain_text_limit_includes_the_ellipsis(self) -> None:
        text = normalize.plain_text("T" * 50, 10)
        self.assertEqual(text, "T" * 9 + "…")
        self.assertEqual(normalize.plain_text("Santé", 50), "Santé")

    def test_http_cache_write_leaves_no_fixed_temp_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(ingest_rss, "RAW_DIR", root):
                ingest_rss._save_http_cache({"https://example.test/": {"etag": "abc"}})
                ingest_rss._write_body_cache("https://example.test/a", b"<rss/>")
            self.assertTrue((root / "_http_cache.json").is_file())
            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_bytes_atomic_cleans_its_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "body.bin"
            store_io.write_bytes_atomic(path, b"abc")
            self.assertEqual(path.read_bytes(), b"abc")
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])


class FoldAgreement(unittest.TestCase):
    def test_client_fold_matches_python_casefold(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is required to execute the client fold")
        samples = ["Straße", "straße", "ﬁchier", "ﬃ", "ﬂeuve", "œuvre", "L’œuvre", "café", "Noël", "ﬄ"]
        script = r"""
const fs = require('fs');
function briefFold() {
  const src = fs.readFileSync('public/assets/brief.js', 'utf8');
  const map = src.match(/const LIGATURES = (\{[\s\S]*?\});/);
  const fn = src.match(/const fold = (\([\s\S]*?toLowerCase\(\));/);
  if (!map || !fn) throw new Error('brief fold not found');
  const LIGATURES = Function('return ' + map[1])();
  return Function('LIGATURES', 'return ' + fn[1])(LIGATURES);
}
function departFold() {
  const src = fs.readFileSync('public/assets/depart.js', 'utf8');
  const map = src.match(/var foldMap = (\{[\s\S]*?\});/);
  const fn = src.match(/var fold = (function \(text\) \{[\s\S]*?toLowerCase\(\);\s*\};)/);
  if (!map || !fn) throw new Error('depart fold not found');
  const foldMap = Function('return ' + map[1])();
  return Function('foldMap', 'return ' + fn[1])(foldMap);
}
const folds = [briefFold(), departFold()];
const samples = JSON.parse(process.argv[1]);
for (const fold of folds) {
  for (const sample of samples) process.stdout.write(fold(sample) + '\n');
}
"""
        proc = subprocess.run(
            [node, "-e", script, json.dumps(samples)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line != ""]
        expected = [brief.folded(sample) for sample in samples]
        self.assertEqual(lines, expected + expected)
