"""Deep-audit regressions (2026-09-20).

Each test locks a confirmed defect fixed in this pass: the recits index list
repr, the state-pack lock that could silently freeze every future refresh, the
fail-soft holes in the record layer and the primary store, the departure
unknown-impact claim, unit-extraction false positives, attribution of
publisher headlines in the change ledger, and the published title/excerpt caps.
"""
from __future__ import annotations

import io
import json
import re
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import affiche
import change_ledger
import compile_metrics
import depart
import enrich
import rank_display
import recits
import registre
import resident_brief as brief
import state_pack

ROOT = harness.ROOT
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _story(**over) -> dict:
    base = {
        "id": "s1",
        "url": "https://actualites.example.com/s1",
        "title": "Travaux dans Saint-Roch",
        "summary": "Étapes à venir.",
        "published_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
        "source_name": "Source locale",
        "source_id": "local",
        "language": "fr",
        "enrich": {"geo": {"geo": "quebec-city"}, "topics": [{"topic": "transport"}]},
    }
    base.update(over)
    return base


class RecitsIndex(unittest.TestCase):
    def test_index_contains_cards_not_a_python_list_repr(self) -> None:
        iss = {
            "issue_id": "aa11bb22cc33dd44",
            "question": "Sujet suivi",
            "label_kind": "subject_label",
            "source_count": 2,
            "silence": {"silent_count": 1},
            "official_voice_count": 0,
        }
        page = recits.render_index([iss], {})
        self.assertNotIn("['<article", page)
        self.assertIn('<article class="recit-index-item">', page)
        self.assertIn('href="/dossiers/aa11bb22cc33dd44.html"', page)

    def test_empty_index_keeps_the_no_data_paragraph(self) -> None:
        page = recits.render_index([], {})
        self.assertIn("Aucun dossier cette édition", page)


class StatePack(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        ops = self.root / "data" / "ops"
        ops.mkdir(parents=True)
        (ops / "feed_health.json").write_text("{}", encoding="utf-8")
        (ops / "refresh.lock").write_text("1234", encoding="utf-8")
        (ops / "refresh.lock.takeover").write_text("1234", encoding="utf-8")
        (ops / "partial.tmp").write_text("x", encoding="utf-8")
        self.patch = mock.patch.multiple(
            state_pack, ROOT=self.root, DATA=self.root / "data")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_process_state_is_never_packed(self) -> None:
        names = [p.relative_to(self.root).as_posix() for p in state_pack.members()]
        self.assertIn("data/ops/feed_health.json", names)
        self.assertNotIn("data/ops/refresh.lock", names)
        self.assertNotIn("data/ops/refresh.lock.takeover", names)
        self.assertNotIn("data/ops/partial.tmp", names)

    def test_unpack_never_plants_a_lock_from_a_legacy_archive(self) -> None:
        archive = self.root / "locked.tar.gz"
        payload = b"1234"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo("data/ops/refresh.lock")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        target = Path(tempfile.mkdtemp(prefix="vigie-state-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        with mock.patch.multiple(state_pack, ROOT=target, DATA=target / "data"):
            state_pack.unpack(archive)
            self.assertFalse((target / "data" / "ops" / "refresh.lock").exists())

    def test_unpack_refuses_members_outside_data(self) -> None:
        evil = self.root / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            payload = b"x"
            info = tarfile.TarInfo("scripts/evil.py")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        target = Path(tempfile.mkdtemp(prefix="vigie-state-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        with mock.patch.multiple(state_pack, ROOT=target, DATA=target / "data"):
            with self.assertRaises(ValueError):
                state_pack.unpack(evil)


class RecordLayerFailSoft(unittest.TestCase):
    def test_partial_seals_are_dropped_by_load_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registre.json"
            path.write_text(
                json.dumps({"method": registre.METHOD, "seals": [{"record": {}}]}),
                encoding="utf-8",
            )
            self.assertEqual(registre.load_state(path)["seals"], [])

    def test_affiche_survives_a_partial_seal(self) -> None:
        page = affiche.render_affiche(
            [], [], None, {"seals": [{"record": {"edition": "x"}}]}, NOW.isoformat(), {})
        self.assertIn("Édition non scellée", page)

    def test_verify_cli_fails_soft_on_a_malformed_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chain.json"
            path.write_text(json.dumps({"seals": ["x"]}), encoding="utf-8")
            with mock.patch("sys.argv", ["registre.py", "--verify", str(path)]):
                self.assertEqual(registre.main(), 1)


class DepartUnknownImpact(unittest.TestCase):
    def test_unmapped_impact_is_not_reported_as_an_open_road(self) -> None:
        rw = {
            "fetched_at": NOW.isoformat(),
            "institution_name": "Ville de Québec",
            "events": [{
                "event_id": "E1",
                "road_names": ["Rue Test"],
                "vehicle_impact": "mystery-impact",
            }],
        }
        page = depart.render_depart(rw, [], {}, {"seals": []}, NOW.isoformat())
        island = json.loads(
            re.search(r'id="vigie-streets">(.*?)</script>', page, re.S).group(1))
        row = island["streets"][0]
        self.assertFalse(row["open"])
        self.assertEqual(row["severity"], 6)

    def test_city_open_vocabulary_is_marked_open(self) -> None:
        rw = {
            "fetched_at": NOW.isoformat(),
            "events": [{
                "event_id": "E1",
                "road_names": ["Rue Test"],
                "vehicle_impact": "all-lanes-open",
            }],
        }
        page = depart.render_depart(rw, [], {}, {"seals": []}, NOW.isoformat())
        row = json.loads(
            re.search(r'id="vigie-streets">(.*?)</script>', page, re.S).group(1)
        )["streets"][0]
        self.assertTrue(row["open"])


class UnitExtractionPrecision(unittest.TestCase):
    def test_price_context_is_word_bounded(self) -> None:
        for text in (
            "Le parent doit verser 1 500 $",
            "Un chemin different coute 200 $",
            "The futility costs 40 $",
            "L'hydrogène coûtera 2 000 $",
        ):
            self.assertEqual(enrich.propose_impact_units(text, ""), [], text)

    def test_dollar_per_kwh_keeps_its_denominator(self) -> None:
        units = enrich.propose_impact_units("Le tarif est de 0,15 $ / kWh", "")
        self.assertTrue(units)
        self.assertTrue(any(u["unit"] == "CAD_per_kWh" for u in units), units)

    def test_housing_unity_theater_is_still_denied(self) -> None:
        self.assertEqual(
            enrich.propose_impact_units("150 housing unity in the project", ""), [])


class DigestAnchors(unittest.TestCase):
    def test_no_travaux_glance_without_a_collection_stamp(self) -> None:
        html = brief.digest_html(
            [{"geo": "quebec-city"}], {}, None, {"counts": {"active": 5}}, [], NOW,
            has_changes=False)
        self.assertNotIn("#travaux", html)

    def test_travaux_glance_present_with_a_stamped_store(self) -> None:
        html = brief.digest_html(
            [{"geo": "quebec-city"}], {}, None,
            {"counts": {"active": 5}, "fetched_at": NOW.isoformat()}, [], NOW,
            has_changes=False)
        self.assertIn('href="#travaux"', html)


class PrimaryStoreFailSoft(unittest.TestCase):
    def test_corrupt_enriched_is_none_and_valid_candidates_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "enriched.json"
            bad.write_text("{not json", encoding="utf-8")
            good = Path(tmp) / "candidates.json"
            good.write_text(json.dumps({"candidates": [{"id": "x"}]}), encoding="utf-8")
            self.assertIsNone(rank_display._load_candidate_store(bad))
            self.assertEqual(rank_display._load_candidate_store(good), [{"id": "x"}])

    def test_display_geo_survives_a_foreign_shape(self) -> None:
        self.assertEqual(rank_display.display_geo({"nest_role": 7}), "linked")
        self.assertEqual(rank_display.display_geo({"geo": {"city": "québec"}}), "linked")


class MetricsNoPhantomEdition(unittest.TestCase):
    def test_missing_ranked_store_never_appends_a_none_edition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.json"
            out = root / "metrics.json"
            for _ in range(3):
                doc = compile_metrics.compile_metrics(
                    ranked_path=missing, issues_path=missing, history_path=missing,
                    roadworks_path=missing, media_path=missing,
                    feed_health_path=missing, out_path=out)
            self.assertEqual(doc["edition_count"], 0)


class LedgerAttribution(unittest.TestCase):
    def test_attributed_headline_never_travels_unattributed(self) -> None:
        attributed = {
            "issue_id": "x",
            "question": "Titre d’un éditeur",
            "label_kind": "attributed_headline",
            "label_source": {"source_name": "Le Soleil", "url": "https://soleil.example/a"},
            "geo_focus": [],
        }
        entry = change_ledger.diff_editions([attributed], [], has_previous=False)["new"][0]
        self.assertEqual(entry["question"], "")
        self.assertEqual(entry["label_source"]["source_name"], "Le Soleil")

    def test_subject_label_keeps_its_question(self) -> None:
        subject = {
            "issue_id": "y",
            "question": "Tramway : les positions",
            "label_kind": "subject_label",
            "geo_focus": [],
        }
        entry = change_ledger.diff_editions([subject], [], has_previous=False)["new"][0]
        self.assertEqual(entry["question"], "Tramway : les positions")


class PublishedCaps(unittest.TestCase):
    def test_title_and_excerpt_stay_within_the_published_caps(self) -> None:
        rows, _ = brief.prepare_items(
            [_story(title="T" * 400, summary="S" * 400)], NOW)
        self.assertLessEqual(len(rows[0]["title"]), brief.TITLE_CAP)
        page = brief.article_html(rows[0], 1, [], {})
        excerpt = re.search(r'<p class="excerpt">(.*?)</p>', page)
        self.assertIsNotNone(excerpt)
        self.assertLessEqual(len(excerpt.group(1)), 240)


class CommandPalettePathTargets(unittest.TestCase):
    def test_path_targets_navigate_instead_of_queryselector(self) -> None:
        js = (ROOT / "public" / "assets" / "brief.js").read_text(encoding="utf-8")
        self.assertIn("location.href = target", js)
        self.assertNotIn("document.querySelector(it.id ? '#article-' + it.id : it.target)", js)


class MethodFilesNoBom(unittest.TestCase):
    def test_bom_cannot_swallow_the_first_heading(self) -> None:
        # A BOM before `#` made the first method heading render as a paragraph.
        for name in ("ranking.md", "RENT.md"):
            self.assertFalse((ROOT / name).read_bytes().startswith(b"\xef\xbb\xbf"), name)
        source = (ROOT / "scripts" / "method_site.py").read_text(encoding="utf-8")
        self.assertIn('encoding="utf-8-sig"', source)


class StrictCsp(unittest.TestCase):
    def test_departure_script_is_a_self_hosted_asset(self) -> None:
        self.assertTrue((ROOT / "public" / "assets" / "depart.js").is_file())
        self.assertIn("CORRIDORS_JS_ASSET", (ROOT / "scripts" / "depart.py").read_text(encoding="utf-8"))
        rw = {"fetched_at": NOW.isoformat(), "events": []}
        page = depart.render_depart(rw, [], {}, {"seals": []}, NOW.isoformat())
        self.assertIn('<script src="/assets/depart.js" defer></script>', page)
        self.assertNotIn("<script>\n(function", page)

    def _rules(self, path: str) -> dict:
        cfg = json.loads((ROOT / path).read_text(encoding="utf-8"))
        return {
            rule["source"]: {h["key"].lower(): h["value"] for h in rule["headers"]}
            for rule in cfg["headers"]
        }

    def test_csp_rules_do_not_stack(self) -> None:
        rules = self._rules("vercel.json")
        # A catch-all CSP would be delivered alongside every route CSP, and a
        # browser enforces the intersection — cancelling explorer/morning's
        # 'unsafe-inline'. Every HTML route must match exactly one CSP rule.
        self.assertNotIn("content-security-policy", rules["/(.*)"])

    def test_every_page_carries_its_own_self_csp(self) -> None:
        rules = self._rules("vercel.json")
        for source in ("/", "/index.html", "/partir.html"):
            csp = rules[source]["content-security-policy"]
            self.assertIn("default-src 'none'", csp)
            self.assertIn("script-src 'self'", csp)
            self.assertNotIn("unsafe-inline", csp)
        # Scriptless record surfaces: default-src 'none' is the script block.
        for source in ("/registre.html", "/affiche.html", "/memoire.html",
                       "/dossiers.html", "/dossiers/(.*)", "/methode/(.*)"):
            csp = rules[source]["content-security-policy"]
            self.assertIn("default-src 'none'", csp)
            self.assertNotIn("unsafe-inline", csp)
        # The two experimental pages keep their deliberate allowances.
        self.assertIn("'unsafe-inline'",
                      rules["/explorer.html"]["content-security-policy"])
        self.assertIn("'unsafe-inline'",
                      rules["/morning.html"]["content-security-policy"])

    def test_staged_config_is_headers_only(self) -> None:
        # deploy/public is a prebuilt static tree: a buildCommand/outputDirectory
        # there makes the CLI deploy run a build that cannot succeed.
        staged = json.loads((ROOT / "public" / "vercel.json").read_text(encoding="utf-8"))
        root = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(list(staged.keys()), ["headers"])
        self.assertEqual(staged["headers"], root["headers"])


if __name__ == "__main__":
    unittest.main()
