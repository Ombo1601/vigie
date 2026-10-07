"""Wave 2 integration: the four reviewed branches must hold together.

R9 (honest identity), R10 (takedowns), R1 (authors / machine diet) and the
honest-copy pass each locked their own surface. These tests lock what only
exists once they are merged: a withdrawn institution, an institution whose
feeds answered with nothing, and a collection gap of ours are three distinct,
neutral facts on every surface that lists the voices; and a takedown that
removes one article from a dossier leaves every other relayed title with its
author. It also locks the CBC cut (2026-10-06): a source Vigie cuts itself is
listed with its reason on the public sources page and reported as "plus
suivie" in the register views, never as "not established", while the sealed
records stay byte-identical. Hermetic: no network, no data/ directory.
"""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import affiche
import ingest_rss
import method_site
import rank_display
import registre
import resident_brief as brief
import substrate
import takedown
from test_author_attribution import ALPHA, BRAVO, RANKED, STAMP, dossier_issue, item
from test_registre import issue as reg_issue, payload as reg_payload
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

    def test_a_store_clustered_before_r1_still_renders_bylines(self):
        """The render-only lanes re-render the last full edition's stores: a
        dossier store written before items carried `author` gets the byline
        from the same collection's candidate (by URL), never a guessed one."""
        old = dossier_issue(attributed=True)
        for t in old["tensions"]:
            for it in t["items"]:
                del it["author"]
        del old["label_source"]["author"]
        signed_none = item("delta", "Delta : un titre sans signature connue", None, sid="le-soleil")
        old["tensions"][0]["items"].append({"title": signed_none["title"], "url": signed_none["url"],
                                            "source_name": "Le Soleil", "author": None})
        frozen = copy.deepcopy(old)
        filled = rank_display.backfill_dossier_authors([old], RANKED + [signed_none | {"author": "Inventé"}])
        self.assertEqual(old, frozen)  # the store (registre input) is never mutated
        html = brief.dossier_html(filled[0], {c["id"]: c for c in RANKED})
        self.assertIn(f"Par {ALPHA['author']}", html)
        self.assertIn(f"Par {BRAVO['author']}", html)
        self.assertIn(f"Titre d’un éditeur, cité tel quel — Par {ALPHA['author']} · Le Soleil.", html)
        self.assertNotIn("Inventé", html)  # an explicit "no author" is never overridden
        self.assertIsNone(filled[0]["tensions"][0]["items"][-1]["author"])
        self.assertEqual(filled[0]["tensions"][0]["items"][0]["author"], ALPHA["author"])
        # A store that already carries authors is returned as is.
        current = dossier_issue()
        self.assertIs(rank_display.backfill_dossier_authors([current], RANKED)[0], current)

    def test_attributed_label_of_a_withdrawn_headline_drops_the_dossier(self):
        iss = dossier_issue(attributed=True)
        rules = takedown.Rules([{"id": "t1", "kind": "url", "value": takedown._canon(ALPHA["url"]),
                                 "requested_at": "2026-10-05", "by": "Le Soleil", "status": "active"}])
        self.assertEqual(takedown.filter_issues([iss], rules), [])


CUT_REASON = "Ne répond pas à l'identité honnête de Vigie (R9) ; hors zone."


class CbcCutIsLoggedNeverSilent(unittest.TestCase):
    def test_a_cut_never_changes_the_facts_of_an_edition_collected_before_it(self):
        """Re-rendering the current edition after the cut (the hourly roads lane
        does exactly that) must reproduce its published leaf: the collection
        facts follow the feeds that edition measured, not today's registry."""
        with tempfile.TemporaryDirectory() as tmp:
            live = harness.sources_with_reenabled(Path(tmp), *harness.CBC_DESKS)
            status = {sid: {"status": "ok", "candidate_count": 3}
                      for sid in [s["id"] for s in ingest_rss.load_enabled_rss(live)]}
            before = registre.institution_collection(status, live)
            self.assertEqual(before["cbc"], {"items": 6, "feeds_ok": 2, "feeds_total": 2})
            after_cut = registre.institution_collection(status, harness.SOURCES)
            self.assertEqual(after_cut, before)
            payload = reg_payload([reg_issue("d1")])
            edition = "2026-10-05T12:00:00+00:00"
            self.assertEqual(registre.leaf_of(registre.edition_record(payload, edition, before)),
                             registre.leaf_of(registre.edition_record(payload, edition, after_cut)))
        # The next edition, collected under the cut, no longer measures CBC.
        fresh = {s["id"]: {"status": "ok", "candidate_count": 1}
                 for s in ingest_rss.load_enabled_rss(harness.SOURCES)}
        self.assertNotIn("cbc", registre.institution_collection(fresh, harness.SOURCES))

    def test_sources_page_lists_both_cbc_desks_with_reason_and_date(self):
        page = method_site._sources_html()
        cuts = page[page.index('id="coupes"'):page.index('id="retraits"')]
        for name in ("CBC News — Montreal", "CBC News — Politics"):
            self.assertIn(f"<strong>{brief.esc(name)}</strong> — {brief.esc(CUT_REASON)} (coupé le 2026-10-06)", cuts)
        # Neither desk is in the active table any more, and the counter says so.
        table = page[:page.index('id="coupes"')]
        self.assertNotIn("CBC News", table)
        active = len(ingest_rss.load_enabled_by_type(harness.SOURCES, "rss")
                     + ingest_rss.load_enabled_by_type(harness.SOURCES, "wzdx")
                     + ingest_rss.load_enabled_by_type(harness.SOURCES, "civic-html"))
        self.assertIn(f"<strong>{active}</strong> actives", page)
        rss_now = len(ingest_rss.load_enabled_rss(harness.SOURCES))
        self.assertLessEqual(rss_now, harness.rss_ceiling())
        self.assertIn(f"Plafond : au plus {harness.rss_ceiling()} flux RSS actifs ({rss_now} aujourd’hui).", page)

    def test_cut_institutions_are_derived_from_the_registry(self):
        cut = registre.cut_institutions()
        self.assertEqual(sorted(cut), ["cbc"])
        self.assertEqual(cut["cbc"]["feed_ids"], list(harness.CBC_DESKS))
        self.assertEqual(cut["cbc"]["cut_at"], "2026-10-06")
        self.assertEqual(cut["cbc"]["institution_name"], "CBC")

    def _two_editions(self, *, cbc_followed_in_newest: bool) -> dict:
        state = registre.empty_state()
        older = reg_payload([reg_issue("d1")])
        for iid, meta in registre.institution_names(older).items():
            state["names"][iid] = meta
        state, _ = registre.seal_edition(state, registre.edition_record(older, "2026-10-05T12:00:00+00:00"))
        followed = ("ville-quebec", "le-soleil", "gouv-quebec") + (("cbc",) if cbc_followed_in_newest else ())
        newer = reg_payload([reg_issue("d1")], followed=followed)
        newer["clustered_at"] = "2026-10-06T06:00:00+00:00"
        state, action = registre.seal_edition(state, registre.edition_record(newer, "2026-10-06T06:00:00+00:00"))
        self.assertEqual(action, "appended")
        return state

    def test_register_reports_the_cut_never_not_established_and_seals_stay(self):
        state = self._two_editions(cbc_followed_in_newest=False)
        before = copy.deepcopy(state)
        register = registre.institution_register(state, {})
        row = {r["institution_id"]: r for r in register}["cbc"]
        self.assertEqual(row["current"], registre.STATE_CUT)
        self.assertEqual(row["cut_at"], "2026-10-06")
        self.assertEqual(registre.state_label_fr(row),
                         "plus suivie : source coupée par Vigie, raison consignée (coupe du 2026-10-06)")
        page = registre.render_registre_html(state)
        self.assertIn("plus suivie — source coupée par Vigie le 2026-10-06", page)
        self.assertIn('href="/methode/sources.html#coupes"', page)
        self.assertIn(registre.STATE_CUT, registre.public_institutions(state)["states"])
        # Views only: the chain still verifies and no leaf moved.
        self.assertEqual(state, before)
        self.assertTrue(registre.verify_chain(state["seals"])[0])
        for seal in state["seals"]:
            self.assertEqual(seal["leaf"], registre.leaf_of(seal["record"]))
        # Every surface lists it under its own heading, apart from gaps and silences.
        strip = brief.silence_bar([], register)
        heading = brief.esc(registre.state_heading_fr(registre.STATE_CUT))
        self.assertIn(heading, strip)
        self.assertIn("CBC", strip[strip.index(heading):])
        delta = substrate.build_delta([], None, None, state, {})
        self.assertEqual([r["institution_id"] for r in delta["institutions"]["cut_by_vigie"]], ["cbc"])
        self.assertNotIn("cbc", [r["institution_id"] for r in delta["institutions"]["not_established"]])
        with mock.patch.object(registre, "institution_register", return_value=register):
            sheet = affiche.render_affiche(RANKED, [], None, state, STAMP, feed_run())
        self.assertIn(f"{registre.state_heading_fr(registre.STATE_CUT)} : CBC.", sheet)

    def test_a_measured_state_wins_while_an_edition_still_follows_it(self):
        state = self._two_editions(cbc_followed_in_newest=True)
        row = {r["institution_id"]: r for r in registre.institution_register(state, {})}["cbc"]
        self.assertNotEqual(row["current"], registre.STATE_CUT)
        self.assertNotIn("cut_at", row)

    def test_a_publisher_takedown_outranks_vigies_own_cut(self):
        state = self._two_editions(cbc_followed_in_newest=False)
        withdrawn = {"cbc": {"institution_name": "CBC", "requested_at": "2026-10-07"}}
        row = {r["institution_id"]: r for r in registre.institution_register(state, withdrawn)}["cbc"]
        self.assertEqual(row["current"], registre.STATE_WITHDRAWN)


if __name__ == "__main__":
    unittest.main()
