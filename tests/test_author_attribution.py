"""R1 across every surface: a relayed publisher title never travels without its author.

Attribution law (LEGAL_RISK.md R1): s. 29.2 fair dealing for news reporting
requires the source AND the author when the publisher's feed gives one. The
byline used to be rendered on the main article card only. These tests render
every surface that shows a publisher title - front door (dossier rows, peers,
attributed dossier label), record pages, departure screen, sheet, explorer,
Markdown twin, delta - for items whose author is known, and fail when a surface
shows a title but not its author. They also lock the machine-layer diet
(publisher, author, title, URL only), the licence notice, and the durable
history never storing an outlet's headline.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

import harness  # noqa: F401 - puts scripts/ on sys.path

import affiche
import ambient_pulse
import cluster_issues
import depart
import dossier_history
import rank_display
import recits
import registre
import resident_brief as brief
import substrate
from test_evidence_integrity import article  # hermetic cluster fixtures
from test_substrate import run as feed_run, state_with_edition

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


def item(cid, title, author, source="Le Soleil", sid="le-soleil", **over):
    row = {
        "id": cid, "url": f"https://presse.example.com/{cid}", "title": title,
        "summary": f"Résumé de l’éditeur pour {cid}, jamais copié ailleurs que sur la carte.",
        "published_at": (NOW - timedelta(hours=3)).isoformat(), "fetched_at": STAMP,
        "source_name": source, "source_id": sid, "language": "fr", "rank_score": 0.8,
        "author": author, "institution_id": sid, "source_kind": "media",
        "enrich": {"geo": {"geo": "quebec-city"}, "topics": [{"topic": "transport"}]},
    }
    row.update(over)
    return row


ALPHA = item("alpha", "Alpha : le chantier de la rue Hamel s’étire", "Auteure Alpha")
BRAVO = item("bravo", "Bravo : la fermeture du pont inquiète", "Auteur Bravo",
             source="Radio-Canada", sid="radio-canada")
CHARLIE = item("charlie", "Charlie : un article sans signature", None, source="Le Devoir", sid="le-devoir")
RANKED = [ALPHA, BRAVO, CHARLIE]
SIGNED = [ALPHA, BRAVO]


def dossier_issue(attributed=True):
    def voice(it):
        return {
            "institution_id": it["institution_id"], "institution_name": it["source_name"],
            "source_kind": "media",
            "items": [{k: it[k] for k in ("title", "url", "source_name", "source_id", "institution_id",
                                          "published_at", "author", "source_kind")} | {"candidate_id": it["id"]}],
        }
    base = {
        "issue_id": "aa11bb22cc33dd44", "scar": "event-1", "question": ALPHA["title"],
        "label_kind": "attributed_headline" if attributed else "subject_label",
        "label_source": {"id": "alpha", "title": ALPHA["title"], "url": ALPHA["url"],
                         "source_id": "le-soleil", "source_name": "Le Soleil",
                         "author": ALPHA["author"]} if attributed else None,
        "geo_focus": ["quebec-city"], "clustered_at": STAMP, "source_count": 2, "item_count": 2,
        "sources": ["le-soleil", "radio-canada"], "official_voice_count": 0, "media_remix": True,
        "evidence": {}, "silence": {"silent": [], "silent_count": 0},
        "tensions": [voice(ALPHA), voice(BRAVO)],
    }
    return base


def assert_attributed(case, surface, text, items):
    """The cross-surface rule: any shown title of a known-author item shows its author."""
    shown = [it for it in items if it["title"] in text]
    case.assertTrue(shown, f"{surface}: renders none of the fixture titles (vacuous check)")
    for it in shown:
        if it.get("author"):
            case.assertIn(it["author"], text, f"{surface}: shows {it['title']!r} without its author")


class Checker(unittest.TestCase):
    def test_the_rule_fails_when_a_surface_drops_the_author(self):
        with self.assertRaises(AssertionError):
            assert_attributed(self, "fixture", f"<a>{ALPHA['title']}</a> Le Soleil", SIGNED)
        assert_attributed(self, "fixture", f"<a>{ALPHA['title']}</a> Par {ALPHA['author']}", SIGNED)


class FrontDoor(unittest.TestCase):
    def test_dossier_rows_carry_the_author(self):
        eligible = {c["id"]: c for c in RANKED}
        html = brief.dossier_html(dossier_issue(attributed=False), eligible)
        assert_attributed(self, "dossier_html", html, SIGNED)
        self.assertIn("Par Auteure Alpha", html)

    def test_peers_carry_the_author_of_the_peer_not_only_the_card(self):
        card = brief.prepare_items([CHARLIE], NOW)[0][0]
        peer = brief.prepare_items([BRAVO], NOW)[0][0]
        html = brief.article_html(card, 1, [peer])
        assert_attributed(self, "article peers", html, [BRAVO])
        self.assertNotIn("Par None", html)
        # the unsigned card itself prints no byline at all
        self.assertNotIn("Par Le Devoir", html)

    def test_attributed_dossier_title_names_publisher_and_author(self):
        html = brief.dossier_html(dossier_issue(), {c["id"]: c for c in RANKED})
        self.assertIn('class="dossier-attrib fine"', html)
        self.assertIn("Titre d’un éditeur, cité tel quel — Par Auteure Alpha · Le Soleil.", html)
        self.assertNotIn("dossier-attrib", brief.dossier_html(dossier_issue(attributed=False), {}))

    def test_headline_rows_and_question_are_plain_and_capped(self):
        long_title = "<b>Titre</b> " + "mot " * 120
        iss = dossier_issue()
        iss["question"] = long_title
        iss["tensions"][0]["items"][0]["title"] = long_title
        html = brief.dossier_html(iss, {})
        self.assertNotIn("<b>", html)
        self.assertIn("…", html)
        for text in (brief.capped_title(long_title), brief._headline_rows(iss)[0]["title"]):
            self.assertLessEqual(len(text), brief.TITLE_CAP)
            self.assertTrue(text.endswith("…"))

    def test_full_page_renders_authors_without_inventing_one(self):
        page = brief.render_brief(RANKED, STAMP, [dossier_issue()], {
            "fetched_at": STAMP, "enabled_rss": ["le-soleil"],
            "results": [{"source_id": "le-soleil", "ok": True, "item_count": 3}]})
        assert_attributed(self, "render_brief", page, SIGNED)
        self.assertNotIn("Par None", page)


class RecordSurfaces(unittest.TestCase):
    def test_recit_page_and_index(self):
        iss = dossier_issue()
        page = recits.render_recit(iss, {}, {}, slug="aa11bb22cc33dd44", rw_ok=False,
                                   edge_streets={}, edge_issues={})
        assert_attributed(self, "recits page", page, SIGNED)
        self.assertIn("Titre d’un éditeur, cité tel quel — Par Auteure Alpha · Le Soleil.", page)
        index = recits.render_index([iss], {iss["issue_id"]: "aa11bb22cc33dd44"})
        assert_attributed(self, "recits index", index, [ALPHA])

    def test_departure_screen_question(self):
        html = depart._question_html([dossier_issue()])
        assert_attributed(self, "depart", html, [ALPHA])

    def test_sheet(self):
        state, _ = state_with_edition()
        page = affiche.render_affiche(RANKED, [], None, state, STAMP, feed_run())
        assert_attributed(self, "affiche", page, SIGNED)
        self.assertNotIn("Par None", page)


class Explorer(unittest.TestCase):
    def setUp(self):
        self.iss = dossier_issue()
        self.continuity = rank_display.build_continuity([self.iss], RANKED)

    def test_cards_rail_deck_and_stage_carry_the_author(self):
        alpha = RANKED[0]
        for name, html in (
            ("card_html", rank_display.card_html(alpha, self.continuity)),
            ("near_rail_item", rank_display.near_rail_item(alpha, self.continuity)),
            ("news_deck_html", rank_display.news_deck_html(alpha, self.continuity)),
            ("issue_stage_html", rank_display.issue_stage_html(self.iss, self.continuity)),
        ):
            with self.subTest(surface=name):
                assert_attributed(self, name, html, SIGNED)

    def test_same_fight_links_carry_the_peer_author(self):
        html = rank_display.card_html(CHARLIE | {"id": "charlie"}, rank_display.build_continuity(
            [dict(self.iss, tensions=self.iss["tensions"] + [{
                "institution_id": "le-devoir", "institution_name": "Le Devoir", "source_kind": "media",
                "items": [{"candidate_id": "charlie", "title": CHARLIE["title"], "url": CHARLIE["url"]}]}])],
            RANKED))
        assert_attributed(self, "same-fight", html, SIGNED)
        self.assertEqual([len(t) for t in rank_display.same_fight_links(
            CHARLIE | {"id": "charlie"}, rank_display.build_continuity(
                [dict(self.iss, tensions=self.iss["tensions"] + [{
                    "institution_id": "le-devoir", "institution_name": "Le Devoir", "source_kind": "media",
                    "items": [{"candidate_id": "charlie", "title": "x", "url": "https://x.example/y"}]}])],
                RANKED))], [3, 3])

    def test_stage_names_the_publisher_of_an_attributed_headline(self):
        html = rank_display.issue_stage_html(self.iss, self.continuity)
        self.assertIn("Titre d’un éditeur, cité tel quel — Par Auteure Alpha · Le Soleil.", html)

    def test_morning_origin_line(self):
        details = {"label_kind": "attributed_headline", "label_source": self.iss["label_source"]}
        row = {"question": "q", "issue_id": "i", "nest": "quebec-city", "index": 0}
        self.assertIn("Par Auteure Alpha", ambient_pulse.digest_approach_link_html(row, details))
        txt = ambient_pulse.render_morning_txt({
            "approaches": [row | {"issue_id": "aa11bb22cc33dd44"}],
            "source_details": {"aa11bb22cc33dd44": details}})
        self.assertIn("Par Auteure Alpha", txt)


class MachineLayer(unittest.TestCase):
    def setUp(self):
        self.state, self.p = state_with_edition()
        self.iss = dossier_issue()

    def markdown(self):
        rows = substrate.story_rows(RANKED, NOW)
        return substrate.render_markdown(rows, [self.iss], {}, None, self.state,
                                         {"at": STAMP, "ok": 2, "total": 2})

    def test_markdown_twin_carries_authors_and_no_excerpt(self):
        md = self.markdown()
        assert_attributed(self, "index.html.md", md, SIGNED)
        self.assertNotIn("Résumé de l’éditeur", md)
        self.assertIn("Les titres appartiennent à leurs éditeurs ; voir https://vigieqc.com/methode/legal.html.", md)
        self.assertIn("par Auteure Alpha", md)  # the attributed dossier heading

    def test_delta_carries_authors_and_no_excerpt(self):
        delta = substrate.build_delta([self.iss], {}, None, self.state, {"at": STAMP})
        text = json.dumps(delta, ensure_ascii=False)
        assert_attributed(self, "delta", text, SIGNED)
        self.assertNotIn("summary", text)
        self.assertNotIn("Résumé de l’éditeur", text)
        view = delta["dossiers"]["all"][0]
        self.assertEqual({i["author"] for i in view["items"]}, {"Auteure Alpha", "Auteur Bravo"})
        self.assertEqual(view["label_source"]["author"], "Auteure Alpha")
        self.assertEqual(delta["attribution"], substrate.LICENCE_NOTE_EN)
        self.assertIn(substrate.LICENCE_NOTE_EN, delta["rules_for_agents"])

    def test_delta_shape_change_is_versioned_and_additive(self):
        self.assertEqual(substrate.METHOD, "delta-v1.1 edition-cursor")
        delta = substrate.build_delta([self.iss], {}, None, self.state, {"at": STAMP})
        # every delta-v1 key survives
        for key in ("method", "site", "edition", "previous_edition", "cursor", "previous_cursor",
                    "checkpoint", "collection", "dossiers", "institutions", "correction",
                    "roadworks", "links", "rules_for_agents"):
            self.assertIn(key, delta)

    def test_an_item_without_an_author_gets_an_empty_author_not_a_made_up_one(self):
        iss = dossier_issue()
        iss["tensions"][0]["items"][0]["author"] = None
        items = substrate.dossier_view(iss, {}, 5)["items"]
        self.assertIn("", {i["author"] for i in items})
        self.assertNotIn("None", json.dumps(items))

    def test_llms_txt_carries_the_notice(self):
        text = substrate.render_llms_txt(self.state, {"at": STAMP})
        self.assertIn("Titles belong to their publishers; see https://vigieqc.com/methode/legal.html.", text)
        self.assertLess(len(text.encode("utf-8")), 10_000)
        self.assertIn("delta-v1.1", text)
        self.assertNotIn("(delta-v1)", text)

    def test_no_summary_cap_survives_in_the_machine_layer(self):
        self.assertFalse(hasattr(substrate, "SUMMARY_CAP"))


class AuthorOfIsFailSoft(unittest.TestCase):
    def test_only_a_string_yields_a_byline(self):
        from resident_brief import author_of
        self.assertEqual(author_of({"author": "Jeanne Roy"}), "Jeanne Roy")
        for bad in (42, {"name": "x"}, ["x"], None, True):
            self.assertEqual(author_of({"author": bad}), "")


class ClusterThreadsTheByline(unittest.TestCase):
    def test_author_reaches_issue_items_and_the_attributed_label(self):
        from test_evidence_integrity import EventIntegrity
        title = "Limoilou fermeture temporaire boulevard Hamel travaux"
        a = article("le-soleil", title, hours=2, author="Marie Tremblay")
        b = article("journal-de-quebec", title, hours=1, author=None)
        out = EventIntegrity("test_generic_id_survives_added_earlier_article").run_cluster([a, b])
        issue = out["issues"][0]
        authors = {it["source_id"]: it["author"] for t in issue["tensions"] for it in t["items"]}
        self.assertEqual(authors, {"le-soleil": "Marie Tremblay", "journal-de-quebec": None})
        self.assertEqual(issue["label_source"]["author"], "Marie Tremblay")


class DurableHistory(unittest.TestCase):
    TS = ["2026-09-17T06:00:00+00:00", "2026-09-17T12:00:00+00:00", "2026-09-17T18:00:00+00:00"]

    def attributed(self, outlet="Le Soleil"):
        return {"issue_id": "a", "scar": "event-1", "source_count": 2, "item_count": 2,
                "question": "Un titre d’éditeur repris tel quel", "label_kind": "attributed_headline",
                "label_source": {"source_name": outlet, "author": "Auteure Alpha"}}

    def test_attributed_headline_is_never_stored_only_its_outlet(self):
        h = dossier_history.update_history(dossier_history.empty_history(), [self.attributed()], self.TS[0])
        h = dossier_history.update_history(h, [self.attributed()], self.TS[1])
        h = dossier_history.update_history(h, [self.attributed("Radio-Canada")], self.TS[2])
        self.assertNotIn("titre d’éditeur", json.dumps(h, ensure_ascii=False))
        timeline = h["dossiers"]["a"]["timeline"]
        self.assertEqual([e.get("label_source") for e in timeline], ["Le Soleil", None, "Radio-Canada"])
        tracking = dossier_history.tracking_of(h, "a")
        self.assertEqual([r.get("label_source") for r in tracking["timeline"]], ["Le Soleil", None, "Radio-Canada"])
        self.assertTrue(all("question" not in r for r in tracking["timeline"]))

    def test_subject_labels_still_store_their_question(self):
        iss = {"issue_id": "s", "scar": "tramway", "question": "Le tramway : les positions", "label_kind": "subject_label"}
        h = dossier_history.update_history(dossier_history.empty_history(), [iss], self.TS[0])
        self.assertEqual(h["dossiers"]["s"]["timeline"][0]["question"], "Le tramway : les positions")

    def test_old_history_loads_and_stored_headlines_are_scrubbed_on_the_next_edition(self):
        legacy = {"method": dossier_history.METHOD, "updated_at": self.TS[0], "edition_count": 1, "dossiers": {
            "a": {"scar": "event-1", "first_seen": self.TS[0], "last_seen": self.TS[0], "editions_seen": 1,
                  "editions_missed": 0, "timeline": [
                      {"ts": self.TS[0], "sources": 2, "question": "Titre d’éditeur déjà stocké"}]}}}
        h = dossier_history.update_history(legacy, [self.attributed()], self.TS[1])
        self.assertNotIn("Titre d’éditeur déjà stocké", json.dumps(h, ensure_ascii=False))
        self.assertEqual(h["dossiers"]["a"]["editions_seen"], 2)
        # the input history was not mutated
        self.assertEqual(legacy["dossiers"]["a"]["timeline"][0]["question"], "Titre d’éditeur déjà stocké")

    def test_timeline_view_never_replays_a_headline_for_an_attributed_dossier(self):
        iss = dossier_issue()
        iss["tracking"] = {"editions_seen": 2, "timeline": [
            {"ts": self.TS[0], "sources": 2, "question": "Titre d’éditeur périmé"},
            {"ts": self.TS[1], "sources": 2, "label_source": "Le Soleil"}]}
        html = brief.dossier_timeline_html(iss)
        self.assertNotIn("Titre d’éditeur périmé", html)
        self.assertIn("Titre d’un éditeur le", html)
        self.assertIn("Le Soleil", html)
        iss["label_kind"] = "subject_label"
        self.assertIn("Titre d’éditeur périmé", brief.dossier_timeline_html(iss))


class SealedRecordsUntouched(unittest.TestCase):
    def test_the_seal_does_not_depend_on_authors_or_label_source(self):
        base = {"clustered_at": STAMP, "chancellery_institutions": ["le-soleil", "radio-canada"],
                "change_ledger": {"has_previous": False, "new": [], "developed": [], "quiet": []}}
        with_author = dict(base, issues=[dossier_issue()])
        stripped = dossier_issue()
        stripped["label_source"].pop("author")
        for t in stripped["tensions"]:
            for it in t["items"]:
                it.pop("author")
        without = dict(base, issues=[stripped])
        a = registre.edition_record(with_author, "2026-09-17T12:00:00+00:00")
        b = registre.edition_record(without, "2026-09-17T12:00:00+00:00")
        self.assertEqual(registre.canonical(a), registre.canonical(b))
        self.assertNotIn("Auteure Alpha", json.dumps(a, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
