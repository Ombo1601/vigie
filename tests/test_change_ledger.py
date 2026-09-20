"""Change-ledger diff: deterministic, honest, never inflates or invents change."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import change_ledger
import resident_brief as brief
import stage_public


def issue(iid, *, question="Q", items=2, voices=2, official=0, remix=True,
          latest="2026-09-10T00:00:00+00:00", geo=("quebec-city",), scar="s"):
    return {
        "issue_id": iid, "scar": scar, "question": question,
        "item_count": items, "source_count": voices,
        "official_voice_count": official, "media_remix": remix,
        "geo_focus": list(geo),
        "evidence": {"publication_latest": latest},
    }


class DiffBasics(unittest.TestCase):
    def test_first_edition_flags_no_previous(self):
        led = change_ledger.diff_editions([issue("a")], [], has_previous=False)
        self.assertFalse(led["has_previous"])
        self.assertEqual(led["status"], "proposed")

    def test_new_dossier_detected(self):
        led = change_ledger.diff_editions([issue("a"), issue("b")], [issue("a")])
        self.assertEqual([e["issue_id"] for e in led["new"]], ["b"])
        self.assertEqual(led["new_count"], 1)

    def test_quiet_dossier_detected(self):
        led = change_ledger.diff_editions([issue("a")], [issue("a"), issue("b")])
        self.assertEqual([e["issue_id"] for e in led["quiet"]], ["b"])
        self.assertEqual(led["quiet_count"], 1)

    def test_none_inputs_are_safe(self):
        led = change_ledger.diff_editions(None, None)
        self.assertFalse(led["has_previous"])
        self.assertEqual((led["new_count"], led["developed_count"], led["quiet_count"]), (0, 0, 0))


class Development(unittest.TestCase):
    def test_items_added(self):
        led = change_ledger.diff_editions([issue("a", items=5)], [issue("a", items=2)])
        self.assertEqual(led["developed"][0]["delta"]["items_added"], 3)

    def test_voice_joined(self):
        led = change_ledger.diff_editions([issue("a", voices=3)], [issue("a", voices=2)])
        self.assertEqual(led["developed"][0]["delta"]["voices_added"], 1)

    def test_official_voice_joined(self):
        led = change_ledger.diff_editions(
            [issue("a", official=1, remix=False)], [issue("a", official=0, remix=True)]
        )
        self.assertTrue(led["developed"][0]["delta"]["official_voice_joined"])

    def test_newer_publication(self):
        led = change_ledger.diff_editions(
            [issue("a", latest="2026-09-12T00:00:00+00:00")],
            [issue("a", latest="2026-09-10T00:00:00+00:00")],
        )
        self.assertTrue(led["developed"][0]["delta"]["newer_publication"])

    def test_unchanged_dossier_is_not_developed(self):
        led = change_ledger.diff_editions([issue("a")], [issue("a")])
        self.assertEqual(led["developed_count"], 0)
        self.assertEqual((led["new_count"], led["quiet_count"]), (0, 0))

    def test_shrinking_is_not_development(self):
        # Fewer articles/voices must never be reported as growth.
        led = change_ledger.diff_editions(
            [issue("a", items=2, voices=2)], [issue("a", items=5, voices=3)]
        )
        self.assertEqual(led["developed_count"], 0)

    def test_older_publication_is_not_newer(self):
        led = change_ledger.diff_editions(
            [issue("a", latest="2026-09-08T00:00:00+00:00")],
            [issue("a", latest="2026-09-10T00:00:00+00:00")],
        )
        self.assertEqual(led["developed_count"], 0)


class HonestyAndDeterminism(unittest.TestCase):
    def test_entries_are_proposed_and_carry_no_verdict(self):
        led = change_ledger.diff_editions([issue("a", question="Real Q")], [issue("b")])
        self.assertEqual(led["status"], "proposed")
        for entry in led["new"] + led["quiet"] + led["developed"]:
            self.assertEqual(entry["status"], "proposed")
            for forbidden in ("resolved", "impact_score", "truth", "verdict", "contradiction"):
                self.assertNotIn(forbidden, entry)

    def test_new_entry_keeps_real_question(self):
        led = change_ledger.diff_editions([issue("a", question="Maëlyne Lugez")], [])
        self.assertEqual(led["new"][0]["question"], "Maëlyne Lugez")
        self.assertEqual(led["new"][0]["change"], "new")

    def test_note_frames_quiet_as_absence_not_resolution(self):
        note = change_ledger.diff_editions([issue("a")], [])["note"]
        self.assertIn("pas réglé", note)
        self.assertIn("pas plus important", note)
        self.assertIn("silence éditorial prouvé", note)

    def test_order_is_deterministic_regardless_of_input_order(self):
        cur = [issue("c"), issue("a"), issue("b")]
        led1 = change_ledger.diff_editions(cur, [])
        led2 = change_ledger.diff_editions(list(reversed(cur)), [])
        self.assertEqual([e["issue_id"] for e in led1["new"]], ["a", "b", "c"])
        self.assertEqual(led1, led2)

    def test_malformed_and_id_less_issues_ignored(self):
        led = change_ledger.diff_editions([issue("a"), {"no_id": True}, None, issue("b")], [])
        self.assertEqual(sorted(e["issue_id"] for e in led["new"]), ["a", "b"])

    def test_has_previous_inferred_from_non_empty_previous(self):
        self.assertTrue(change_ledger.diff_editions([issue("a")], [issue("b")])["has_previous"])
        self.assertFalse(change_ledger.diff_editions([issue("a")], [])["has_previous"])

    def test_explicit_has_previous_overrides_inference(self):
        # A prior edition that legitimately had zero dossiers still counts as a comparison.
        led = change_ledger.diff_editions([issue("a")], [], has_previous=True)
        self.assertTrue(led["has_previous"])
        self.assertEqual(led["new_count"], 1)


def _run():
    return {"fetched_at": "2026-09-17T12:00:00+00:00", "enabled_rss": ["local"],
            "results": [{"source_id": "local", "ok": True, "item_count": 1}]}


def _ledger(**over):
    base = {"status": "proposed", "has_previous": True, "new": [], "developed": [],
            "quiet": [], "new_count": 0, "developed_count": 0, "quiet_count": 0,
            "note": "comparaison"}
    base.update(over)
    return base


def _render(ledger):
    return brief.render_brief([], "2026-09-17T12:00:00+00:00", [], _run(), ledger=ledger)


class ChangeSectionRender(unittest.TestCase):
    def test_absent_without_previous_edition(self):
        self.assertNotIn('id="changements"', _render(_ledger(has_previous=False)))

    def test_absent_when_ledger_none(self):
        # Existing 4-arg callers must produce byte-identical HTML (no new section).
        self.assertNotIn('id="changements"', _render(None))

    def test_renders_new_dossier(self):
        page = _render(_ledger(new=[{"issue_id": "b", "question": "Nouveau sujet", "change": "new", "status": "proposed"}], new_count=1))
        self.assertIn('id="changements"', page)
        self.assertIn("Nouveaux dossiers", page)
        self.assertIn("Nouveau sujet", page)

    def test_quiet_is_framed_as_absence_not_resolution(self):
        page = _render(_ledger(quiet=[{"issue_id": "x", "question": "Sujet retire", "change": "quiet", "status": "proposed"}], quiet_count=1))
        self.assertIn("Disparus de cette collecte", page)
        self.assertIn("pas réglé", page)
        self.assertIn("absent de cette collecte", page)

    def test_no_change_state_is_honest(self):
        page = _render(_ledger())
        self.assertIn('id="changements"', page)
        self.assertIn("Aucun dossier", page)
        self.assertIn("depuis la dernière édition", page)

    def test_question_is_escaped(self):
        page = _render(_ledger(new=[{"issue_id": "b", "question": "<svg onload=alert(1)>", "change": "new", "status": "proposed"}], new_count=1))
        self.assertNotIn("<svg onload=alert(1)>", page)
        self.assertIn("&lt;svg onload=alert(1)&gt;", page)

    def test_developed_delta_text(self):
        page = _render(_ledger(developed=[{"issue_id": "a", "question": "Sujet", "change": "developed", "status": "proposed", "delta": {"items_added": 2, "voices_added": 1}}], developed_count=1))
        self.assertIn("Dossiers développés", page)
        self.assertIn("+2 articles", page)
        self.assertIn("+1 source", page)

    def test_change_section_sits_before_dossiers(self):
        page = _render(_ledger(new=[{"issue_id": "b", "question": "Sujet", "change": "new", "status": "proposed"}], new_count=1))
        self.assertLess(page.index('id="changements"'), page.index('id="dossiers"'))

    def test_change_section_navigation_is_valid(self):
        led = _render(_ledger(new=[{"issue_id": "b", "question": "Sujet", "change": "new", "status": "proposed"}], new_count=1))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.html").write_text(led, encoding="utf-8")
            (root / "assets").mkdir()
            for name in ("assets/brief.css", "assets/fonts.css", "assets/brief.js", "favicon.svg", "apple-touch-icon.png", "site.webmanifest", "explorer.html", "morning.html", "llms.txt", "index.html.md", *stage_public.OPTIONAL_PAGES, *stage_public.METHODS):
                (root / name).write_text("placeholder", encoding="utf-8")
            (root / "methode").mkdir(exist_ok=True)
            for name in (*stage_public.METHOD_PAGES, "index"):
                (root / "methode" / f"{name}.html").write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


if __name__ == "__main__":
    unittest.main()
