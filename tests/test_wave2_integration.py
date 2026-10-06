"""Wave 2 integration: the four reviewed branches must hold together.

R9 (honest identity), R10 (takedowns), R1 (authors / machine diet) and the
honest-copy pass each locked their own surface. These tests lock what only
exists once they are merged: a withdrawn institution, an institution whose
feeds answered with nothing, and a collection gap of ours are three distinct,
neutral facts on every surface that lists the voices; and a takedown that
removes one article from a dossier leaves every other relayed title with its
author. Hermetic: no network, no data/ directory.
"""
from __future__ import annotations

import unittest
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import affiche
import registre
import resident_brief as brief
import takedown
from test_author_attribution import ALPHA, BRAVO, RANKED, STAMP, dossier_issue, item
from test_substrate import run as feed_run, state_with_edition


def reg_row(iid, name, current, **extra):
    row = {
        "institution_id": iid, "institution_name": name, "source_kind": "media",
        "current": current, "items_collected": None, "collection_gap_streak": 0,
        "editions_spoke": 0, "editions_published_outside_dossiers": 0,
        "editions_measured": 0, "last_spoke": None,
    }
    row.update(extra)
    return row


REGISTER = [
    reg_row("a", "Voix Dossier", registre.STATE_SPOKE),
    reg_row("b", "Voix Publiée", registre.STATE_PUBLISHED, items_collected=4),
    reg_row("c", "Voix Sans Article", registre.STATE_NO_ITEMS),
    reg_row("d", "Voix Lacune", registre.STATE_COLLECTION_GAP, collection_gap_streak=1),
    reg_row("e", "Voix Retirée", registre.STATE_WITHDRAWN, withdrawn_requested_at="2026-10-05"),
]


class StatesStayDistinct(unittest.TestCase):
    def test_every_state_has_its_own_heading(self):
        headings = list(registre.STATE_HEADING_FR.values())
        self.assertEqual(len(headings), len(set(headings)))
        for state in (registre.STATE_NO_ITEMS, registre.STATE_COLLECTION_GAP, registre.STATE_WITHDRAWN):
            self.assertIn(state, registre.STATE_HEADING_FR)
            self.assertNotEqual(registre.state_heading_fr(state),
                                registre.state_heading_fr(registre.STATE_NOT_ESTABLISHED))

    def test_no_state_wording_is_a_verdict(self):
        for text in (*registre.STATE_HEADING_FR.values(), *registre.STATE_LABEL_FR.values()):
            low = text.lower()
            for word in ("silencieu", "refus", "ignor", "boycott", "censur"):
                self.assertNotIn(word, low, text)

    def test_silence_bar_lists_each_state_under_its_own_heading(self):
        strip = brief.silence_bar([], REGISTER)
        for state, name, cls in (
            (registre.STATE_NO_ITEMS, "Voix Sans Article", "sb-unknown"),
            (registre.STATE_COLLECTION_GAP, "Voix Lacune", "sb-missed"),
            (registre.STATE_WITHDRAWN, "Voix Retirée", "sb-withdrawn"),
        ):
            heading = registre.state_heading_fr(state)
            start = strip.index(brief.esc(heading))
            end = strip.find("sb-row", start)
            block = strip[start:end if end != -1 else len(strip)]
            self.assertIn(name, block, state)
        # Withdrawn never drifts into "not established" nor into our gaps.
        self.assertNotIn(brief.esc(registre.state_heading_fr(registre.STATE_NOT_ESTABLISHED)), strip)
        self.assertIn("1</strong> collecte manquée par Vigie", strip)
        self.assertIn("1</strong> sans article collecté (flux répondus)", strip)

    def test_affiche_names_withdrawn_apart_from_gap_and_no_items(self):
        state, _ = state_with_edition()
        with mock.patch.object(registre, "institution_register", return_value=REGISTER):
            page = affiche.render_affiche(RANKED, [], None, state, STAMP, feed_run())
        self.assertIn("collecte manquée par Vigie : Voix Lacune.", page)
        self.assertIn("aucun article collecté dans la fenêtre de 7 jours (ce n’est pas une lacune)", page)
        self.assertIn(f"{registre.state_heading_fr(registre.STATE_WITHDRAWN)} : Voix Retirée.", page)
        # Neither the withdrawn voice nor the empty feeds are counted as our gap.
        self.assertIn("<strong>1</strong> collecte manquée", page)


class BylinesSurviveTakedownFiltering(unittest.TestCase):
    def _three_voice_dossier(self):
        charlie = item("charlie", "Charlie : un titre retiré à la demande", "Auteur Retiré",
                       source="Le Devoir", sid="le-devoir")
        iss = dossier_issue(attributed=False)
        iss["tensions"].append({
            "institution_id": "le-devoir", "institution_name": "Le Devoir", "source_kind": "media",
            "items": [{k: charlie[k] for k in ("title", "url", "source_name", "source_id", "institution_id",
                                              "published_at", "author", "source_kind")}
                      | {"candidate_id": "charlie"}],
        })
        iss["sources"] = ["le-devoir", "le-soleil", "radio-canada"]
        iss["source_count"] = 3
        return iss, charlie

    def test_withdrawn_item_leaves_with_its_byline_and_the_others_keep_theirs(self):
        iss, charlie = self._three_voice_dossier()
        rules = takedown.Rules([{"id": "t1", "kind": "url", "value": takedown._canon(charlie["url"]),
                                 "requested_at": "2026-10-05", "by": "Le Devoir", "status": "active"}])
        kept = takedown.filter_issues([iss], rules)
        self.assertEqual(len(kept), 1)
        html = brief.dossier_html(kept[0], {c["id"]: c for c in RANKED})
        for it in (ALPHA, BRAVO):
            self.assertIn(it["title"].split(" : ")[1], html)
            self.assertIn(f"Par {it['author']}", html)
        self.assertNotIn(charlie["title"], html)
        self.assertNotIn("Auteur Retiré", html)
        self.assertNotIn(charlie["url"], html)

    def test_attributed_label_of_a_withdrawn_headline_drops_the_dossier(self):
        iss = dossier_issue(attributed=True)
        rules = takedown.Rules([{"id": "t1", "kind": "url", "value": takedown._canon(ALPHA["url"]),
                                 "requested_at": "2026-10-05", "by": "Le Soleil", "status": "active"}])
        self.assertEqual(takedown.filter_issues([iss], rules), [])


if __name__ == "__main__":
    unittest.main()
