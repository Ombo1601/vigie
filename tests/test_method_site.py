"""La Méthode (methode-v1): method files as pages, humans never land on .md.

Locks: every page renders with the house chrome; no href in any rendered page
points at a .md/.yaml file; internal cross-links are mapped to pages; the
sources page is data, not raw YAML; the founder is never named; determinism;
fail-soft per file; and the whole brief links the pages, never the files.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import method_site
import resident_brief as brief
import recits
import registre
import stage_public

ROOT = harness.ROOT
MD_HREF = re.compile(r'href="([^"]*\.(?:md|yaml)[^"]*)"')


def _rendered_pages(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted((root / "methode").glob("*.html")):
        out[path.name] = path.read_text(encoding="utf-8")
    return out


class Emit(unittest.TestCase):
    def test_all_pages_render_deterministically_with_chrome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "methode"
            method_site.emit(out)
            pages = _rendered_pages(root)
            self.assertEqual(len(pages), len(method_site.PAGES) + 1)  # + index
            first = {k: v for k, v in pages.items()}
            method_site.emit(out)
            self.assertEqual({k: v for k, v in _rendered_pages(root).items()}, first)
            for name, html in pages.items():
                self.assertIn("class=\"masthead\"", html)
                self.assertIn("canonical", html)
                self.assertNotIn("<script", html)
                self.assertNotIn("Ombinos", html)
                self.assertNotIn("Onesphore", html)
                self.assertFalse(MD_HREF.search(html), f"{name} links a raw file")

    def test_index_lists_every_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            method_site.emit(root / "methode")
            index = (root / "methode" / "index.html").read_text(encoding="utf-8")
            for slug, _file, title, _eyebrow, _intro in method_site.PAGES:
                self.assertIn(f'href="/methode/{slug}.html"', index)
                self.assertIn(title.replace("'", "&#x27;"), index)

    def test_cross_links_map_to_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            method_site.emit(root / "methode")
            legal = (root / "methode" / "legal.html").read_text(encoding="utf-8")
            self.assertIn('href="/methode/classement.html"', legal)
            self.assertIn('href="/methode/sources.html"', legal)

    def test_sources_page_is_data_not_raw_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            method_site.emit(root / "methode")
            page = (root / "methode" / "sources.html").read_text(encoding="utf-8")
            self.assertIn("Le Devoir", page)
            self.assertIn("Ville de Lévis", page)  # deferred entry with its reason
            self.assertIn("officiel", page)
            self.assertNotIn("nest_role:", page)  # no raw YAML keys

    def test_emit_skips_a_missing_file_without_failing(self):
        bad = [("mystere", "missing-file.md", "Le mystère", "TEST", "Fichier absent.")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "methode"
            with mock.patch.object(method_site, "PAGES", bad):
                info = method_site.emit(out)
            self.assertEqual(info["pages"], 0)
            self.assertTrue((out / "index.html").is_file())


class NoRawLinks(unittest.TestCase):
    def test_brief_links_method_pages_never_raw_files(self):
        collection = {"fetched_at": "2026-09-20T12:00:00+00:00", "enabled_rss": ["local"],
                      "results": [{"source_id": "local", "ok": True, "item_count": 1}]}
        story = {"id": "s1", "url": "https://actualites.example.com/s1", "title": "Un titre",
                 "summary": "Un résumé.", "published_at": "2026-09-20T10:00:00+00:00",
                 "fetched_at": "2026-09-20T12:00:00+00:00", "source_name": "Source locale",
                 "source_id": "local", "language": "fr", "rank_score": 0.8,
                 "enrich": {"geo": {"geo": "quebec-city"}, "topics": [{"topic": "transport"}]}}
        page = brief.render_brief([story], "2026-09-20T12:00:00+00:00", [], collection)
        raw = [m.group(1) for m in MD_HREF.finditer(page)]
        self.assertEqual(set(raw), {"/index.html.md"})  # the labelled machine twin only
        for needle in ("/methode/classement.html", "/methode/sources.html",
                       "/methode/financement.html", "/methode/legal.html",
                       "/methode/vision.html", "/methode/registre.html"):
            self.assertIn(f'href="{needle}"', page)
        self.assertNotIn("fondateur", page)
        self.assertNotIn("Ombinos", page)

    def test_registre_and_recit_pages_never_link_raw_files(self):
        page = registre.render_registre_html(registre.empty_state())
        self.assertFalse(MD_HREF.search(page))
        recit = recits.render_recit({}, {}, {}, slug="aa11bb22cc33dd44",
                                    rw_ok=False, edge_streets={}, edge_issues={})
        self.assertFalse(MD_HREF.search(recit))

    def test_no_served_method_source_names_the_founder(self):
        for name in stage_public.METHODS:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("Ombinos", text, name)
            self.assertNotIn("Onesphore", text, name)

    def test_method_pages_enter_the_sitemap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<p>x</p>", encoding="utf-8")
            method_site.emit(root / "methode")
            xml = stage_public.sitemap_xml(root)
            self.assertIn("https://vigieqc.com/methode/classement.html", xml)
            self.assertIn("https://vigieqc.com/methode/legal.html", xml)


if __name__ == "__main__":
    unittest.main()
