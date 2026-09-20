"""Dossier record pages (recits-v1): complete, addressable, deterministic, honest.

Locks: verbatim titles with attribution, zero scripts, escape safety, the full
silence roster, the collection timeline, measured evidence counters,
edition-scoped retention (stale pages pruned), byte determinism, slug safety,
the brief's links, and release-navigation validity.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import recits
import resident_brief as brief
import stage_public

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def issue(**over):
    base = {
        "issue_id": "aa11bb22cc33dd44",
        "scar": "tramway",
        "question": "Tramway de Québec : les positions rapportées",
        "label_kind": "subject_label",
        "geo_focus": ["quebec-city"],
        "clustered_at": NOW.isoformat(),
        "source_count": 2,
        "sources": ["ville-quebec", "le-soleil"],
        "official_voice_count": 1,
        "media_remix": False,
        "evidence": {
            "relationship": "same_subject_proposed",
            "publication_oldest": (NOW - timedelta(days=2)).isoformat(),
            "publication_latest": (NOW - timedelta(hours=3)).isoformat(),
            "publication_unknown_count": 1,
            "duplicate_headline_count": 2,
            "note": "Plusieurs sources ne prouvent ni une contradiction ni des confirmations indépendantes.",
        },
        "tracking": {
            "status": "proposed",
            "method": "dossier-history-v1",
            "first_seen": (NOW - timedelta(days=2)).isoformat(),
            "last_seen": NOW.isoformat(),
            "editions_seen": 3,
            "editions_missed": 0,
            "timeline": [
                {"ts": (NOW - timedelta(days=2)).isoformat(), "sources": 2, "items": 3, "official": 0},
                {"ts": (NOW - timedelta(days=1)).isoformat(), "sources": 3, "items": 5, "official": 1},
                {"ts": NOW.isoformat(), "sources": 2, "items": 4, "official": 1},
            ],
        },
        "silence": {
            "spoke_count": 2,
            "silent_count": 2,
            "enabled_count": 4,
            "silent": [
                {"institution_id": "ville-quebec", "institution_name": "Ville de Québec", "source_id": "ville-quebec", "source_kind": "official"},
                {"institution_id": "le-devoir", "institution_name": "Le Devoir", "source_id": "le-devoir", "source_kind": "media"},
            ],
        },
        "tensions": [
            {
                "institution_id": "ville-quebec",
                "institution_name": "Ville de Québec",
                "source_kind": "official",
                "items": [
                    {
                        "candidate_id": "c1",
                        "title": "Avis officiel sur le tramway",
                        "url": "https://ville.example/a",
                        "source_name": "Ville de Québec",
                        "published_at": (NOW - timedelta(hours=5)).isoformat(),
                        "claims": [{"quote": "Le tracé reste à confirmer", "speaker": "La Ville"}],
                    },
                ],
            },
            {
                "institution_id": "le-soleil",
                "institution_name": "Le Soleil",
                "source_kind": "media",
                "items": [
                    {
                        "candidate_id": "c2",
                        "title": "Le tracé du tramway contesté",
                        "url": "https://soleil.example/b",
                        "source_name": "Le Soleil",
                        "published_at": (NOW - timedelta(hours=3)).isoformat(),
                    },
                ],
            },
        ],
    }
    base.update(over)
    return base


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class Emit(unittest.TestCase):
    def test_emit_writes_index_and_pages_and_prunes_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "dossiers"
            out_dir.mkdir()
            (out_dir / "ffff000011112222.html").write_text("stale", encoding="utf-8")
            info = recits.emit([issue()], [], {}, None, {}, out_dir=out_dir, out_index=root / "dossiers.html")
            self.assertEqual(info, {"method": "recits-v1", "dossiers": 1, "pages": 1})
            self.assertTrue((root / "dossiers.html").is_file())
            self.assertTrue((out_dir / "aa11bb22cc33dd44.html").is_file())
            self.assertFalse((out_dir / "ffff000011112222.html").exists())

    def test_page_carries_the_complete_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recits.emit([issue()], [], {}, None, {}, out_dir=root / "dossiers", out_index=root / "dossiers.html")
            page = (root / "dossiers" / "aa11bb22cc33dd44.html").read_text(encoding="utf-8")
            self.assertIn("<title>Tramway de Québec : les positions rapportées — Vigie</title>", page)
            self.assertIn("Avis officiel sur le tramway", page)
            self.assertIn("Le tracé du tramway contesté", page)
            self.assertIn("Le tracé reste à confirmer", page)
            self.assertIn("recit-official", page)
            self.assertIn("Ville de Québec", page)
            self.assertIn("Le Devoir", page)  # full silence roster names media too
            self.assertIn("Suivi depuis", page)
            self.assertIn("Repères de collecte (3 éditions)", page)
            self.assertIn("Ce que la collecte a mesuré", page)
            self.assertIn("Doublons de titres", page)
            self.assertIn("titres identiques", page)
            self.assertIn("Indépendance des sources", page)
            self.assertIn("canonical", page)
            self.assertIn("https://vigieqc.com/dossiers/aa11bb22cc33dd44.html", page)
            self.assertIn("pas un silence éditorial prouvé", page)
            self.assertIn("n’est pas une résolution", page)

    def test_index_lists_every_dossier_with_links_and_honesty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recits.emit([issue(), issue(issue_id="bb22cc33dd44ee55", question="Aéroports : le projet")],
                        [], {}, None, {}, out_dir=root / "dossiers", out_index=root / "dossiers.html")
            index = (root / "dossiers.html").read_text(encoding="utf-8")
            self.assertIn("Tramway de Québec", index)
            self.assertIn("Aéroports : le projet", index)
            self.assertIn('href="/dossiers/aa11bb22cc33dd44.html"', index)
            self.assertIn('href="/dossiers/bb22cc33dd44ee55.html"', index)
            self.assertIn("2 dossiers proposés", index)
            self.assertIn("n’est pas une contradiction", index)
            self.assertIn("suivi depuis", index)

    def test_attributed_headline_is_disclosed(self):
        page = recits.render_recit(
            issue(label_kind="attributed_headline",
                  label_source={"source_name": "Le Soleil", "url": "https://soleil.example/b"}),
            {}, {}, slug="aa11bb22cc33dd44", rw_ok=False, edge_streets={}, edge_issues={},
        )
        self.assertIn("Titre d’un éditeur, cité tel quel", page)
        self.assertIn("Le Soleil", page)

    def test_pages_have_no_scripts_and_escape_hostile_text(self):
        evil = issue(
            question='Tramway </h1><script>alert("q")</script>',
            tensions=[
                {
                    "institution_name": "Radio <svg onload=alert(1)>",
                    "source_kind": "media",
                    "items": [
                        {"candidate_id": "c1", "title": 'Titre " onclick="alert(1)', "url": "https://rc.example/b", "source_name": "x"},
                        {"candidate_id": "c2", "title": "Lien dangereux", "url": "javascript:alert(1)"},
                    ],
                }
            ],
            silence={"silent_count": 1, "silent": [{"institution_name": "Ville <img src=x onerror=alert(1)>", "source_id": "v", "source_kind": "official"}]},
        )
        page = recits.render_recit(evil, {}, {}, slug="aa11bb22cc33dd44", rw_ok=False, edge_streets={}, edge_issues={})
        self.assertNotIn("<script", page)
        self.assertNotIn("javascript:", page)
        self.assertNotIn("Lien dangereux", page)
        tags = Tags()
        tags.feed(page)
        self.assertEqual([t for t, _ in tags.tags if t == "script"], [])
        self.assertFalse(any(key.lower().startswith("on") for _, attrs in tags.tags for key in attrs))
        self.assertIn("&lt;svg onload=alert(1)&gt;", page)

    def test_slug_fallback_is_safe_and_injective(self):
        # Same raw id -> same slug (identity by issue_id, even when unsafe);
        # distinct raw ids -> distinct slugs (injective fallback, no collisions).
        self.assertEqual(
            brief.dossier_slug(issue(issue_id="a/b")),
            brief.dossier_slug(issue(issue_id="a/b", question="Autre question")),
        )
        self.assertNotEqual(
            brief.dossier_slug(issue(issue_id="a/b")),
            brief.dossier_slug(issue(issue_id="c/d")),
        )
        self.assertRegex(brief.dossier_slug(issue(issue_id="a/b")), r"^[0-9a-f]{16}$")

    def test_render_is_deterministic(self):
        first = recits.render_recit(issue(), {}, {}, slug="aa11bb22cc33dd44", rw_ok=False, edge_streets={}, edge_issues={})
        second = recits.render_recit(issue(), {}, {}, slug="aa11bb22cc33dd44", rw_ok=False, edge_streets={}, edge_issues={})
        self.assertEqual(first, second)

    def test_fail_soft_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "dossiers"
            out_dir.mkdir()
            (out_dir / "stale.html").write_text("old", encoding="utf-8")
            info = recits.emit([], [], {}, None, {}, out_dir=out_dir, out_index=root / "dossiers.html")
            self.assertEqual(info["dossiers"], 0)
            self.assertFalse((out_dir / "stale.html").exists())
            index = (root / "dossiers.html").read_text(encoding="utf-8")
            self.assertIn("Aucun dossier cette édition", index)


class BriefLinks(unittest.TestCase):
    def test_brief_links_the_index_and_each_record_page(self):
        collection = {"fetched_at": NOW.isoformat(), "enabled_rss": ["local"],
                      "results": [{"source_id": "local", "ok": True, "item_count": 1}]}
        story = {"id": "s1", "url": "https://actualites.example.com/s1",
                 "title": "Travaux dans Saint-Roch", "summary": "Étapes à venir.",
                 "published_at": (NOW - timedelta(hours=2)).isoformat(),
                 "fetched_at": NOW.isoformat(), "source_name": "Source locale",
                 "source_id": "local", "language": "fr", "rank_score": 0.8,
                 "enrich": {"geo": {"geo": "quebec-city"}, "topics": [{"topic": "transport"}]}}
        page = brief.render_brief([story], NOW.isoformat(), [issue()], collection)
        self.assertIn('href="/dossiers.html"', page)
        self.assertIn('href="/dossiers/aa11bb22cc33dd44.html"', page)
        self.assertIn("Récit complet", page)


class ReleaseNavigation(unittest.TestCase):
    def test_record_pages_pass_site_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recits.emit([issue()], [], {}, None, {}, out_dir=root / "dossiers", out_index=root / "dossiers.html")
            (root / "index.html").write_text('<h1 id="travaux">Travaux</h1><h1 id="dossiers">Dossiers</h1>', encoding="utf-8")
            for name in ("assets/brief.css", "assets/fonts.css", "favicon.svg", "llms.txt", "registre.html", "partir.html", "memoire.html"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder", encoding="utf-8")
            (root / "methode").mkdir(exist_ok=True)
            for name in (*stage_public.METHOD_PAGES, "index"):
                (root / "methode" / f"{name}.html").write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])

    def test_sitemap_lists_dossier_pages_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<p>x</p>", encoding="utf-8")
            (root / "dossiers").mkdir()
            (root / "dossiers" / "aa11bb22cc33dd44.html").write_text("<p>d</p>", encoding="utf-8")
            (root / "dossiers.html").write_text("<p>i</p>", encoding="utf-8")
            xml = stage_public.sitemap_xml(root)
            self.assertIn("https://vigieqc.com/dossiers.html", xml)
            self.assertIn("https://vigieqc.com/dossiers/aa11bb22cc33dd44.html", xml)


if __name__ == "__main__":
    unittest.main()
