"""La mémoire (memoire-v1): the sealed editions, readable and honest.

Locks: index newest-first; per-edition pages carry dossiers, voices and the
ledger; the chain's own law holds (attributed-headline dossiers never
reproduce a publisher's words — the label stays empty); current-edition
dossiers link to their récit page; zero scripts; determinism; fail-soft;
stale edition pages are pruned; links validate.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import memoire
import registre
import stage_public
from test_registre import issue, payload


def _state(editions):
    state = registre.empty_state()
    for edition, doc in editions:
        record = registre.edition_record(doc, edition)
        assert record is not None
        state, _ = registre.seal_edition(state, record)
        for iid, meta in registre.institution_names(doc).items():
            state["names"][iid] = meta
    return state


def _two_editions():
    ledger = {
        "has_previous": True,
        "new": [{"issue_id": "a", "question": "Le tramway"}],
        "developed": [],
        "quiet": [{"issue_id": "gone", "question": "Ancien dossier"}],
    }
    first = payload([issue("a")])
    second = payload([issue("a")], ledger=ledger)
    return _state([
        ("2026-09-09T12:00:00+00:00", first),
        ("2026-09-10T12:00:00+00:00", second),
    ])


class Index(unittest.TestCase):
    def test_lists_editions_newest_first_with_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memoire.emit(_two_editions(), out_index=root / "memoire.html", out_dir=root / "memoire")
            index = (root / "memoire.html").read_text(encoding="utf-8")
            self.assertIn("La mémoire de la ville.", index)
            self.assertLess(index.index("N° 2"), index.index("N° 1"))
            self.assertIn('href="/memoire/2.html"', index)
            self.assertIn("ont parlé", index)
            self.assertIn("2 éditions scellées", index)

    def test_empty_chain_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            info = memoire.emit({}, out_index=root / "memoire.html", out_dir=root / "memoire")
            self.assertEqual(info["editions"], 0)
            index = (root / "memoire.html").read_text(encoding="utf-8")
            self.assertIn("La chaîne commence à la prochaine édition", index)
            self.assertFalse(list((root / "memoire").glob("*.html")))

    def test_stale_edition_pages_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = _two_editions()
            memoire.emit(state, out_index=root / "memoire.html", out_dir=root / "memoire")
            self.assertTrue((root / "memoire" / "2.html").is_file())
            one = _state([("2026-09-09T12:00:00+00:00", payload([issue("a")]))])
            memoire.emit(one, out_index=root / "memoire.html", out_dir=root / "memoire")
            self.assertFalse((root / "memoire" / "2.html").exists())
            self.assertTrue((root / "memoire" / "1.html").is_file())


class EditionPage(unittest.TestCase):
    def test_carries_dossiers_voices_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memoire.emit(_two_editions(), out_index=root / "memoire.html", out_dir=root / "memoire")
            page = (root / "memoire" / "2.html").read_text(encoding="utf-8")
            self.assertIn("Édition n° 2", page)
            self.assertIn("Les dossiers", page)
            self.assertIn("Le tramway", page)
            self.assertIn("Ont parlé", page)
            self.assertIn("N’ont pas parlé", page)
            self.assertIn("Nouveaux dossiers", page)
            self.assertIn("Disparus de cette collecte", page)
            self.assertIn("jamais « réglés »", page)
            self.assertIn('href="/memoire/1.html"', page)  # prev
            self.assertNotIn("<script", page)

    def test_first_edition_says_no_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memoire.emit(_two_editions(), out_index=root / "memoire.html", out_dir=root / "memoire")
            first = (root / "memoire" / "1.html").read_text(encoding="utf-8")
            self.assertIn("Première édition scellée", first)
            self.assertIn("Début de la chaîne", first)
            self.assertIn('href="/memoire/2.html"', first)
            last = (root / "memoire" / "2.html").read_text(encoding="utf-8")
            self.assertIn("Dernière édition scellée", last)
            self.assertIn('href="/memoire/1.html"', last)

    def test_attributed_headlines_are_never_reproduced(self):
        doc = payload([issue("a")])
        doc["issues"][0]["label_kind"] = "attributed_headline"
        doc["issues"][0]["question"] = ""
        state = _state([("2026-09-09T12:00:00+00:00", doc)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memoire.emit(state, out_index=root / "memoire.html", out_dir=root / "memoire")
            page = (root / "memoire" / "1.html").read_text(encoding="utf-8")
            self.assertIn("non repris dans le registre", page)

    def test_current_edition_links_to_its_recit(self):
        state = _two_editions()
        current = [{"issue_id": "a", "question": "Le tramway"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memoire.emit(state, current, out_index=root / "memoire.html", out_dir=root / "memoire")
            page = (root / "memoire" / "2.html").read_text(encoding="utf-8")
            self.assertIn("/dossiers/a.html", page)

    def test_deterministic(self):
        state = _two_editions()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memoire.emit(state, out_index=root / "memoire.html", out_dir=root / "memoire")
            first = {p.name: p.read_text(encoding="utf-8") for p in sorted((root / "memoire").glob("*.html"))}
            memoire.emit(state, out_index=root / "memoire.html", out_dir=root / "memoire")
            second = {p.name: p.read_text(encoding="utf-8") for p in sorted((root / "memoire").glob("*.html"))}
            self.assertEqual(first, second)


class Release(unittest.TestCase):
    def test_memory_pages_pass_navigation_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memoire.emit(_two_editions(), out_index=root / "memoire.html", out_dir=root / "memoire")
            (root / "index.html").write_text("<p>x</p>", encoding="utf-8")
            (root / "assets").mkdir()
            for name in ("assets/brief.css", "assets/fonts.css", "favicon.svg", "llms.txt",
                         "registre.html", "methode/legal.html"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder", encoding="utf-8")
            (root / "methode" / "index.html").write_text("placeholder", encoding="utf-8")
            (root / "registre").mkdir()
            (root / "registre" / "chain.json").write_text("{}", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


if __name__ == "__main__":
    unittest.main()
