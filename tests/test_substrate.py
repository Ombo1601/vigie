"""The machine substrate and the affiche: same store, attribution intact, no rewriting."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import affiche
import registre
import stage_public
import substrate
from test_registre import history, issue, payload

NOW = "2026-09-10T14:00:00+00:00"


def story(uid, *, title="Un titre <em>verbatim</em> & fidèle", geo="quebec-city", summary="Résumé de l’éditeur.",
          published="2026-09-10T09:00:00+00:00", area_hint=""):
    return {
        "id": uid, "title": title + (" " + area_hint if area_hint else ""), "url": f"https://presse.example.com/{uid}",
        "source_id": "le-soleil", "source_name": "Le Soleil", "summary": summary, "published_at": published,
        "enrich": {"geo": {"geo": geo}, "topics": [{"topic": "transport"}]},
    }


def run():
    return {"fetched_at": "2026-09-10T13:30:00+00:00", "enabled_rss": ["le-soleil", "ville-quebec"],
            "results": [{"source_id": "le-soleil", "ok": True}, {"source_id": "ville-quebec", "ok": True}]}


def state_with_edition(ledger=None):
    p = payload([issue("a")], ledger=ledger)
    state = registre.empty_state()
    for iid, meta in registre.institution_names(p).items():
        state["names"][iid] = meta
    state, _ = registre.seal_edition(state, registre.edition_record(p, "2026-09-10T13:30:00+00:00"))
    return state, p


class MarkdownTwin(unittest.TestCase):
    def test_twin_keeps_titles_verbatim_with_publisher_and_url(self):
        state, p = state_with_edition()
        rows = substrate.story_rows([story("s1")], registre.parse_ts(NOW))
        md = substrate.render_markdown(rows, p["issues"], p["change_ledger"], None, state, {"at": NOW, "ok": 2, "total": 2})
        self.assertIn("Un titre verbatim & fidèle", md.replace("\\&", "&"))
        self.assertIn("<https://presse.example.com/s1>", md)
        self.assertIn("Le Soleil", md)
        self.assertIn("Ont parlé (2)", md)
        self.assertIn("N’ont pas parlé dans cette collecte (1)", md)
        self.assertIn(state["seals"][0]["root"], md)
        self.assertNotIn("<em>", md)  # markup stripped, words kept

    def test_twin_without_stories_or_dossiers_says_so(self):
        md = substrate.render_markdown([], [], {}, None, registre.empty_state(), {})
        self.assertIn("Aucun article récent", md)
        self.assertIn("Aucun dossier", md)


class Delta(unittest.TestCase):
    def test_delta_is_cursor_addressed_and_attributed(self):
        ledger = {"has_previous": True, "new": [{"issue_id": "a", "question": "Le tramway"}], "developed": [], "quiet": [
            {"issue_id": "gone", "question": "Ancien dossier", "geo_focus": ["quebec-city"]}]}
        state, p = state_with_edition(ledger)
        delta = substrate.build_delta(p["issues"], ledger, {"fetched_at": "2026-09-10T13:00:00+00:00", "events": [
            {"event_id": "e1", "road_names": ["Rue Saint-Jean"], "vehicle_impact": "all-lanes-closed"}]},
            state, {"at": NOW, "ok": 2, "total": 2, "partial": False})
        self.assertEqual(delta["method"], substrate.METHOD)
        self.assertEqual(delta["cursor"], state["seals"][0]["root"])
        self.assertEqual(delta["previous_cursor"], "")
        self.assertEqual([d["issue_id"] for d in delta["dossiers"]["new"]], ["a"])
        self.assertEqual(delta["dossiers"]["quiet"][0]["question"], "Ancien dossier")
        items = delta["dossiers"]["new"][0]["items"]
        self.assertEqual({i["source_name"] for i in items}, {"Le Soleil", "Ville de Québec"})
        self.assertTrue(all(i["url"].startswith("https://exemple.test/") for i in items))
        self.assertEqual({i["title"] for i in items}, {"Titre le-soleil", "Titre ville-quebec"})  # markup stripped, never rewritten
        self.assertEqual([s["institution_id"] for s in delta["institutions"]["silent"]], ["gouv-quebec", "cbc"])  # officials first
        self.assertEqual(delta["roadworks"]["most_restrictive"][0]["impact_label"], "Toutes les voies fermées")
        self.assertTrue(any("silent" in r for r in delta["rules_for_agents"]))
        json.dumps(delta)  # serialisable

    def test_delta_without_previous_has_empty_buckets_but_full_list(self):
        state, p = state_with_edition()
        delta = substrate.build_delta(p["issues"], p["change_ledger"], None, state, {})
        self.assertFalse(delta["dossiers"]["has_previous"])
        self.assertEqual(delta["dossiers"]["new"], [])
        self.assertEqual(len(delta["dossiers"]["all"]), 1)
        self.assertIsNone(delta["roadworks"])


class LlmsTxt(unittest.TestCase):
    def test_llms_txt_follows_v2_shape_and_maps_the_record(self):
        state, _ = state_with_edition()
        text = substrate.render_llms_txt(state, {"at": NOW})
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Vigie")
        self.assertTrue(lines[2].startswith("> "))
        for path in ("/index.html.md", "/delta/latest.json", "/registre/chain.json", "/registre/checkpoint.txt",
                     "/sources.yaml", "/legal.md", "/REGISTRE.md"):
            self.assertIn(f"https://vigieqc.com{path}", text)
        self.assertIn("## Optional", text)
        self.assertLess(len(text.encode("utf-8")), 10_000)


class Emit(unittest.TestCase):
    def test_emit_writes_three_files_deterministically(self):
        state, p = state_with_edition()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = dict(out_llms=root / "llms.txt", out_md=root / "index.html.md", out_delta=root / "delta" / "latest.json")
            substrate.emit([story("s1")], p["issues"], p["change_ledger"], None, state, NOW, run=run(), **paths)
            first = {k: v.read_bytes() for k, v in paths.items()}
            substrate.emit([story("s1")], p["issues"], p["change_ledger"], None, state, NOW, run=run(), **paths)
            for k, v in paths.items():
                self.assertEqual(v.read_bytes(), first[k], k)
            self.assertEqual(json.loads(paths["out_delta"].read_text(encoding="utf-8"))["collection"]["feeds_ok"], 2)


class Affiche(unittest.TestCase):
    def test_sheet_groups_by_quartier_and_carries_the_seal(self):
        state, p = state_with_edition()
        ranked = [story("s1", area_hint="à Limoilou"), story("s2", area_hint="à Beauport"), story("s3", geo="quebec")]
        rw = {"fetched_at": "2026-09-10T13:00:00+00:00", "events": [
            {"event_id": "e1", "road_names": ["Rue Saint-Jean"], "vehicle_impact": "all-lanes-closed",
             "end_date": "2026-09-20T00:00:00+00:00", "end_date_accuracy": "estimated"}]}
        page = affiche.render_affiche(ranked, p["issues"], rw, state, NOW, run())
        self.assertIn("La Cité-Limoilou", page)
        self.assertIn("Beauport", page)
        self.assertIn("Rue Saint-Jean", page)
        self.assertIn("Toutes les voies fermées", page)
        self.assertIn("(estimé)", page)
        self.assertIn(state["seals"][0]["root"][:16], page)
        self.assertIn("Un titre verbatim &amp; fidèle", page)  # publisher markup stripped, words kept, escaped
        self.assertNotIn("<script", page)           # paper needs no JavaScript
        self.assertIn('href="/assets/affiche.css"', page)
        self.assertIn("n’a pas parlé", page)

    def test_sheet_with_nothing_still_prints(self):
        page = affiche.render_affiche([], [], None, registre.empty_state(), NOW, {})
        self.assertIn("Aucun article local", page)
        self.assertIn("Édition non scellée", page)
        self.assertIn("flux officiel indisponible", page)

    def test_affiche_and_registre_pass_release_navigation_validation(self):
        state, p = state_with_edition()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "affiche.html").write_text(affiche.render_affiche([story("s1")], p["issues"], None, state, NOW, run()), encoding="utf-8")
            (root / "registre.html").write_text(registre.render_registre_html(state), encoding="utf-8")
            (root / "assets").mkdir()
            (root / "registre").mkdir()
            (root / "delta").mkdir()
            for name in ("index.html", "assets/brief.css", "assets/fonts.css", "assets/affiche.css", "favicon.svg", "llms.txt",
                         "registre/chain.json", "registre/checkpoint.txt", "registre/institutions.json", "registre/travaux.json",
                         "delta/latest.json", *stage_public.METHODS):
                (root / name).write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


class Staging(unittest.TestCase):
    def test_markdown_twin_is_a_publishable_asset_and_optional_pages_enter_the_sitemap(self):
        self.assertIn(".md", stage_public.ASSET_EXTENSIONS)
        self.assertIn("REGISTRE.md", stage_public.METHODS)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<p>x</p>", encoding="utf-8")
            self.assertNotIn("/registre.html", stage_public.sitemap_xml(root))
            (root / "registre.html").write_text("<p>r</p>", encoding="utf-8")
            (root / "affiche.html").write_text("<p>a</p>", encoding="utf-8")
            xml = stage_public.sitemap_xml(root)
            self.assertIn("https://vigieqc.com/registre.html", xml)
            self.assertIn("https://vigieqc.com/affiche.html", xml)
            self.assertIn("https://vigieqc.com/REGISTRE.md", xml)


if __name__ == "__main__":
    unittest.main()
