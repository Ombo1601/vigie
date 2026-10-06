"""Origin classifier (scripts/origin.py), docs/EVENTS.md section 7.

Invented fixtures only: no publisher text, no real byline, in this file.
"""
from __future__ import annotations

import copy
import itertools
import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import origin  # noqa: E402
from origin import (  # noqa: E402
    CLASSES, OFFICIAL, OWN_REPORTING, PRESS_RELEASE, RULE_IDS, UNKNOWN, WIRE,
    classify_batch, origin_of, tally,
)

# shadow-only guard (multiline: an import on any line counts; also lazy imports/calls)
_SHADOW_IMPORT_RE = re.compile(r"(?m)^\s*(?:import\s+origin\b|from\s+origin\s+import)")
_SHADOW_CALL_RE = re.compile(
    r"""\bimport_module\(\s*['"]origin['"]|\borigin\.(?:origin_of|classify_batch)\(""")

MEDIA = {"id": "journal-x", "name": "Journal X", "institution": "journal-x",
         "institution_name": "Journal X", "source_kind": "media", "language": "fr"}
OFFICIAL_SRC = {"id": "ville-x", "name": "Ville X", "institution": "ville-x",
                "institution_name": "Ville X", "source_kind": "official", "language": "fr"}


_IDS = itertools.count()


def item(author=None, title="Titre inventé", summary="Résumé inventé.", **extra) -> dict:
    d = {"id": "i" + str(next(_IDS)), "title": title,
         "summary": summary, "author": author, "source_id": "journal-x",
         "institution": "journal-x", "source_kind": "media"}
    d.update(extra)
    return d


def cls(it, src=MEDIA):
    return origin_of(it, src)


class OfficialTests(unittest.TestCase):
    def test_official_source_is_official(self):
        it = item(summary="Québec, le 1er oct. 2026 -- La Ville informe la population.")
        self.assertEqual(cls(it, OFFICIAL_SRC), (OFFICIAL, "official.source_kind"))

    def test_cnw_dateline_in_official_source_stays_official(self):
        it = item(summary="MONTRÉAL , le 5 oct. 2026 /CNW/ -- Le ministère informe la population.")
        self.assertEqual(cls(it, OFFICIAL_SRC)[0], OFFICIAL)

    def test_official_wins_even_with_wire_credit(self):
        it = item(author="La Presse Canadienne")
        self.assertEqual(cls(it, OFFICIAL_SRC)[0], OFFICIAL)

    def test_source_registry_beats_item_copy(self):
        it = item(source_kind="official")
        self.assertEqual(cls(it, MEDIA)[0], UNKNOWN)  # registry says media

    def test_item_copy_is_the_fallback_without_a_source(self):
        it = item(source_kind="official")
        self.assertEqual(origin_of(it, None)[0], OFFICIAL)
        self.assertEqual(origin_of(it)[0], OFFICIAL)


class WireAuthorTests(unittest.TestCase):
    def test_closed_list_in_author_field(self):
        cases = {
            "La Presse Canadienne": "cp", "La Presse canadienne": "cp",
            "Presse canadienne": "cp", "The Canadian Press": "cp",
            "Canadian Press": "cp", "PC": "cp", "CP": "cp",
            "Agence France-Presse": "afp", "Agence France Presse": "afp", "AFP": "afp",
            "Reuters": "reuters", "Thomson Reuters": "reuters",
            "Associated Press": "ap", "The Associated Press": "ap", "AP": "ap",
        }
        for author, code in cases.items():
            with self.subTest(author=author):
                self.assertEqual(cls(item(author)), (WIRE, f"wire.author.{code}"))

    def test_mixed_case_accents_and_odd_dashes(self):
        for author in ("la presse canadienne", "LA PRESSE CANADIENNE", "La  Presse Canadienne",
                       "agence france‑presse", "Agence France–Presse", "Agence France-Presse (AFP)",
                       "Par La Presse Canadienne", "By The Canadian Press"):
            with self.subTest(author=author):
                self.assertEqual(cls(item(author))[0], WIRE)

    def test_byline_plus_wire_credit_is_wire(self):
        self.assertEqual(cls(item("Alice Tremblay-Roy, La Presse Canadienne")), (WIRE, "wire.author.cp"))
        self.assertEqual(cls(item("Alice Tremblay-Roy et Bob Lacasse, AFP")), (WIRE, "wire.author.afp"))
        self.assertEqual(cls(item("Carl Dupont with The Associated Press")), (WIRE, "wire.author.ap"))

    def test_other_byline_shapes_with_wire_credit(self):
        cases = {
            "Alice Tremblay (AFP)": "afp",
            "Alice Tremblay - La Presse Canadienne": "cp",
            "Alice Tremblay – AFP": "afp",
            "Alice Tremblay — Reuters": "reuters",
            "Alice Tremblay | AFP": "afp",
            "Alice Tremblay pour La Presse Canadienne": "cp",
            "Alice Tremblay for The Canadian Press": "cp",
            "Alice Tremblay, La Presse canadienne (Ottawa)": "cp",
            "Alice Tremblay, Agence France-Presse (AFP)": "afp",
            "Alice Tremblay [Reuters]": "reuters",
            "La Presse Canadienne (Ottawa)": "cp",
        }
        for author, code in cases.items():
            with self.subTest(author=author):
                self.assertEqual(cls(item(author)), (WIRE, f"wire.author.{code}"))

    def test_new_separators_do_not_invent_wire_or_split_names(self):
        # hyphenated names and hyphenated agency names stay whole
        self.assertEqual(cls(item("Jean-Pierre Tremblay-Roy")), (OWN_REPORTING, "own_reporting.named_byline"))
        self.assertEqual(cls(item("Agence France-Presse"))[0], WIRE)
        # no agency named: never wire, never own_reporting by accident
        for author in ("Alice Tremblay (Politique)", "Alice Tremblay - Ottawa",
                       "Alice Tremblay | Ottawa", "Alice Tremblay for Appleby"):
            with self.subTest(author=author):
                self.assertEqual(cls(item(author))[0], UNKNOWN)

    def test_repeated_credit_is_one_agency(self):
        self.assertEqual(cls(item("La Presse Canadienne, La Presse Canadienne")), (WIRE, "wire.author.cp"))

    def test_several_agencies_are_multi(self):
        self.assertEqual(cls(item("AFP/Reuters")), (WIRE, "wire.author.multi"))
        self.assertEqual(cls(item("Reuters et Associated Press")), (WIRE, "wire.author.multi"))

    def test_wire_name_inside_a_person_is_not_wire(self):
        # a surname that merely contains a wire token is not a credit
        self.assertEqual(cls(item("Alice Reuterswärd"))[0], OWN_REPORTING)
        self.assertEqual(cls(item("Capucine Apel"))[0], OWN_REPORTING)

    def test_photo_credit_never_makes_text_wire(self):
        it = item("Alice Tremblay-Roy", photo_credit="AFP")
        self.assertEqual(cls(it), (OWN_REPORTING, "own_reporting.named_byline"))
        it2 = item(None, photo_credit="Reuters")
        self.assertEqual(cls(it2), (UNKNOWN, "unknown.no_signal"))

    def test_qmi_default_is_not_wire_and_not_a_person(self):
        self.assertEqual(origin.AGENCE_QMI_IS_WIRE, False)
        self.assertEqual(cls(item("Agence QMI")), (UNKNOWN, "unknown.newsroom_credit"))
        self.assertEqual(cls(item("Alice Tremblay-Roy, Agence QMI")), (UNKNOWN, "unknown.byline_mixed_credit"))

    def test_qmi_switch(self):
        origin.AGENCE_QMI_IS_WIRE = True
        try:
            self.assertEqual(cls(item("Agence QMI")), (WIRE, "wire.author.qmi"))
            self.assertEqual(cls(item("Alice Tremblay-Roy, Agence QMI")), (WIRE, "wire.author.qmi"))
        finally:
            origin.AGENCE_QMI_IS_WIRE = False


class WireTextTests(unittest.TestCase):
    def test_parenthesised_agency_tag(self):
        for text, code in (("OTTAWA (AFP) — Le texte inventé.", "afp"),
                           ("PARIS (Reuters) - Texte inventé.", "reuters"),
                           ("DENVER (AP) — Invented text.", "ap"),
                           ("TORONTO (CP) — Invented text.", "cp"),
                           ("OTTAWA (La Presse canadienne) — Texte.", "cp")):
            with self.subTest(text=text):
                self.assertEqual(cls(item(summary=text)), (WIRE, f"wire.text_tag.{code}"))

    def test_tag_in_title_counts(self):
        self.assertEqual(cls(item(title="Invented (AFP)", summary=""))[0], WIRE)

    def test_end_attribution(self):
        self.assertEqual(cls(item(summary="Texte inventé. — AFP")), (WIRE, "wire.text_attribution.afp"))
        self.assertEqual(cls(item(summary="Texte inventé. – La Presse canadienne"))[0], WIRE)

    def test_files_from(self):
        self.assertEqual(cls(item(summary="Texte inventé, avec des informations de La Presse canadienne.")),
                         (WIRE, "wire.text_files_from.cp"))
        self.assertEqual(cls(item(summary="Invented text, with files from The Canadian Press.")),
                         (WIRE, "wire.text_files_from.cp"))
        self.assertEqual(cls(item(summary="Texte, avec des informations d'AFP.")),
                         (WIRE, "wire.text_files_from.afp"))

    def test_attribution_inside_the_outlets_own_text_is_not_a_credit(self):
        for text in ("Les prix baisseront, selon Reuters.", "Il l'a annoncé à l'AFP hier.",
                     "Invented text said Reuters in a report.", "Selon l'Associated Press, rien."):
            with self.subTest(text=text):
                self.assertEqual(cls(item(summary=text)), (UNKNOWN, "unknown.no_signal"))

    def test_short_acronyms_need_exact_case_and_context(self):
        for text in ("Doug Inventé (PC) a dit non.", "les ap… et plus", "un cp isolé", "Voir (ap) ici"):
            with self.subTest(text=text):
                self.assertEqual(cls(item(summary=text))[0], UNKNOWN)

    def test_wire_text_marker_blocks_own_reporting(self):
        it = item("Alice Tremblay-Roy", summary="OTTAWA (AFP) — Texte inventé.")
        self.assertEqual(cls(it), (WIRE, "wire.text_tag.afp"))


class PressReleaseTests(unittest.TestCase):
    def test_cnw_style_datelines_in_media(self):
        for text in ("QUÉBEC , le 4 oct. 2026 /CNW/ -- Texte inventé.",
                     "TORONTO, Oct. 4, 2026 /CNW/ - Invented text.",
                     "MONTRÉAL, le 4 oct. 2026 /CNW Telbec/ - Texte inventé.",
                     "NEW YORK, Oct. 4, 2026 /PRNewswire/ -- Invented text.",
                     "VANCOUVER, Oct. 4, 2026 /GLOBE NEWSWIRE/ -- Invented text."):
            with self.subTest(text=text):
                self.assertEqual(cls(item(summary=text)), (PRESS_RELEASE, "press_release.dateline"))

    def test_french_markers_accent_and_case_insensitive(self):
        for text in ("Annoncé par voie de communiqué lundi.", "ANNONCÉ PAR VOIE DE COMMUNIQUÉ.",
                     "Annonce par voie de communique."):
            with self.subTest(text=text):
                self.assertEqual(cls(item(summary=text)),
                                 (PRESS_RELEASE, "press_release.par_voie_de_communique"))
        self.assertEqual(cls(item(summary="Le geste, selon un communiqué publié lundi.")),
                         (PRESS_RELEASE, "press_release.selon_un_communique"))
        self.assertEqual(cls(item(summary="Il dit, dans un communiqué, vouloir partir.")),
                         (PRESS_RELEASE, "press_release.dans_un_communique"))
        self.assertEqual(cls(item(summary="Selon le récent communiqué du groupe, oui.")),
                         (PRESS_RELEASE, "press_release.selon_un_communique"))

    def test_english_markers(self):
        for text in ("It said in a news release on Monday.", "according to a press release issued today",
                     "In a news release, the company said no."):
            with self.subTest(text=text):
                self.assertEqual(cls(item(summary=text)), (PRESS_RELEASE, "press_release.in_a_news_release"))

    def test_relay_agency_as_author(self):
        self.assertEqual(cls(item("Cision")), (PRESS_RELEASE, "press_release.author_relay"))
        self.assertEqual(cls(item("Canada Newswire")), (PRESS_RELEASE, "press_release.author_relay"))
        self.assertEqual(cls(item("CNW")), (PRESS_RELEASE, "press_release.author_relay"))

    def test_relay_marker_blocks_own_reporting(self):
        it = item("Alice Tremblay-Roy", summary="Texte inventé, par voie de communiqué.")
        self.assertEqual(cls(it)[0], PRESS_RELEASE)

    def test_wire_credit_beats_relay_marker(self):
        it = item("La Presse Canadienne", summary="Dit selon un communiqué.")
        self.assertEqual(cls(it)[0], WIRE)

    def test_unrelated_communiquer_and_decision_are_not_markers(self):
        for text in ("Vous pouvez communiquer avec le 311.", "Une décision de la cour.",
                     "Un communiqué intérimaire du tribunal est attendu.", "Sans lien: communiqués.",
                     "Release the kraken", "a news item"):
            with self.subTest(text=text):
                self.assertEqual(cls(item(summary=text))[0], UNKNOWN)


class OwnReportingTests(unittest.TestCase):
    def test_named_byline(self):
        self.assertEqual(cls(item("Alice Tremblay-Roy")), (OWN_REPORTING, "own_reporting.named_byline"))

    def test_several_names_and_particles_and_initials(self):
        for author in ("Alice Tremblay-Roy et Bob Lacasse", "Alice Tremblay-Roy, Bob Lacasse",
                       "Marie-Ève de la Chevrotière", "John R. Invented", "Zoé O'Brien-Lévesque",
                       "Alice Tremblay-Roy and Bob Lacasse", "Alice Tremblay-Roy & Bob Lacasse"):
            with self.subTest(author=author):
                self.assertEqual(cls(item(author))[0], OWN_REPORTING)

    def test_outlet_self_credit_next_to_a_person(self):
        self.assertEqual(cls(item("Journal X et Alice Tremblay-Roy"))[0], OWN_REPORTING)
        self.assertEqual(cls(item("Alice Tremblay-Roy, Journal X"))[0], OWN_REPORTING)

    def test_outlet_self_credit_alone_is_not_a_byline(self):
        self.assertEqual(cls(item("Journal X")), (UNKNOWN, "unknown.newsroom_credit"))

    def test_other_newsrooms_and_roles_are_not_people(self):
        for author in ("La Tribune", "Rédaction", "Équipe du journal", "Collectif Invention",
                       "Staff", "Invention News Staff", "Le Devoir", "Radio-Canada", "CBC News"):
            with self.subTest(author=author):
                self.assertEqual(cls(item(author))[0], UNKNOWN)
        self.assertEqual(cls(item("Journal Y et Alice Tremblay-Roy"))[0], UNKNOWN)  # foreign outlet

    def test_op_ed_bio_in_author_is_unknown(self):
        for author in ("Alice Tremblay-Roy, L’autrice est professeure à l’Université inventée.",
                       "Alice Tremblay-Roy et Bob Lacasse, Les auteurs sont respectivement chefs.",
                       "Alice Tremblay-Roy, Les signataires signent au nom d’un collectif"):
            with self.subTest(author=author):
                self.assertEqual(cls(item(author))[0], UNKNOWN)

    def test_single_token_and_junk_are_not_names(self):
        for author in ("Alice", "x", "12345", "a@b.ca", "Alice Tremblay-Roy 2", "!!! ???", "---",
                       "Alice <b>Tremblay</b>", "http://example.org/a b"):
            with self.subTest(author=author):
                self.assertEqual(cls(item(author))[0], UNKNOWN)

    def test_empty_author_is_never_own_reporting(self):
        for author in (None, "", "   ", "\t\n", "​​", 0, [], {}, False):
            with self.subTest(author=author):
                self.assertEqual(cls(item(author)), (UNKNOWN, "unknown.no_signal"))

    def test_absence_of_marker_alone_never_infers_own_reporting(self):
        # long, wire-like, markerless text with no author
        text = "Un texte long et factuel sans aucun crédit. " * 20
        self.assertEqual(cls(item(None, summary=text)), (UNKNOWN, "unknown.no_signal"))

    def test_default_is_unknown_without_any_field(self):
        self.assertEqual(cls({}), (UNKNOWN, "unknown.no_signal"))
        self.assertEqual(cls({"title": "x"}, {}), (UNKNOWN, "unknown.no_signal"))


class HostileInputTests(unittest.TestCase):
    def assertValid(self, result):
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIn(result[0], CLASSES)
        self.assertIn(result[1], RULE_IDS)
        self.assertTrue(result[1].startswith(result[0] + "."))

    def test_never_raises_on_garbage(self):
        junk = [None, 0, 1.5, "str", b"bytes", [], [1, 2], {}, {"author": {"x": 1}}, {"author": [1]},
                {"author": b"AFP"}, {"title": 5, "summary": object()}, {"source_kind": ["official"]},
                {"author": float("nan")}, {"author": "x" * 10**6}, {"summary": "\x00" * 1000},
                {"author": "\ud800"}, {"summary": "\udfff(AFP)"}]
        for it in junk:
            for src in (None, {}, [], "s", {"source_kind": 5}, {"name": b"x"}, OFFICIAL_SRC, MEDIA):
                with self.subTest(it=repr(it)[:40], src=repr(src)[:30]):
                    self.assertValid(origin_of(it, src))

    def test_non_dict_item_is_bad_input(self):
        for it in (None, "x", 3, [], ("a",)):
            self.assertEqual(origin_of(it, MEDIA), (UNKNOWN, "unknown.bad_input"))

    def test_control_and_format_characters_are_ignored(self):
        self.assertEqual(cls(item("La​ Presse‮ Canadienne"))[0], WIRE)
        self.assertEqual(cls(item("A\x00lice Tremblay-Roy"))[0], OWN_REPORTING)
        self.assertEqual(cls(item(summary="par voie de\x00 communiqué"))[0], PRESS_RELEASE)

    def test_pathological_inputs_are_fast(self):
        nasty = ["(" * 50000, "-" * 100000, "a" * 200000, ("et " * 50000), ("(AFP" * 20000),
                 "/" * 100000, " " * 100000, "(AF" * 30000 + ")", ("avec des informations de " * 5000),
                 ("par voie de " * 20000), ("à" * 100000)]
        start = time.perf_counter()
        for text in nasty:
            self.assertValid(origin_of(item(text, summary=text, title=text), MEDIA))
        self.assertLess(time.perf_counter() - start, 5.0)

    def test_marker_beyond_scan_cap_is_ignored_not_crashed(self):
        text = "x " * 3000 + "par voie de communiqué"
        self.assertEqual(cls(item(summary=text)), (UNKNOWN, "unknown.no_signal"))

    def test_many_segments_is_not_a_byline(self):
        author = ", ".join(f"Alice Tremblay{n}" for n in range(30))
        self.assertEqual(cls(item(author))[0], UNKNOWN)

    def test_item_is_not_mutated(self):
        it = item("Alice Tremblay-Roy, La Presse Canadienne", summary="(AFP) x")
        before = copy.deepcopy(it)
        cls(it)
        self.assertEqual(it, before)

    def test_output_never_carries_publisher_text(self):
        secret = "Zyxwvut Qponmlk"
        it = item(f"{secret}, La Presse Canadienne", title=secret, summary=f"{secret} par voie de communiqué")
        res = cls(it)
        self.assertNotIn("zyxwvut", json.dumps(res).lower())
        for fixture in (item(secret), item(None, summary=f"/CNW/ {secret}")):
            self.assertNotIn("zyxwvut", json.dumps(cls(fixture)).lower())


class BatchTests(unittest.TestCase):
    def mk(self, iid, author, source_id="journal-x", institution=None, **kw):
        d = item(author, title=f"t{iid}", summary=f"s{iid}", id=iid, source_id=source_id,
                 institution=institution or source_id)
        d.update(kw)
        return d

    def test_result_is_sorted_and_keyed_by_id(self):
        items = [self.mk("b", "Alice Tremblay-Roy"), self.mk("a", None)]
        out = classify_batch(items, {"journal-x": MEDIA})
        self.assertEqual(list(out), ["a", "b"])
        self.assertEqual(out["a"], (UNKNOWN, "unknown.no_signal"))
        self.assertEqual(out["b"], (OWN_REPORTING, "own_reporting.named_byline"))

    def test_sources_as_list_or_dict_agree(self):
        items = [self.mk("1", "x"), self.mk("2", None, source_id="ville-x", institution="ville-x")]
        as_dict = classify_batch(items, {"journal-x": MEDIA, "ville-x": OFFICIAL_SRC})
        as_list = classify_batch(items, [MEDIA, OFFICIAL_SRC])
        self.assertEqual(as_dict, as_list)
        self.assertEqual(as_dict["2"][0], OFFICIAL)

    def test_missing_sources_and_garbage_items_do_not_crash(self):
        items = [self.mk("1", "Alice Tremblay-Roy"), None, "x", 3, {"no": "id"}, self.mk("2", "AFP")]
        out = classify_batch(items, None)
        self.assertEqual(sorted(out), ["1", "2"])
        self.assertEqual(classify_batch(None), {})
        self.assertEqual(classify_batch("x", 5), {})

    def test_byline_seen_with_wire_credit_is_downgraded_not_promoted(self):
        items = [self.mk("1", "Alice Tremblay-Roy, La Presse Canadienne", source_id="a", institution="a"),
                 self.mk("2", "Alice Tremblay-Roy", source_id="b", institution="b"),
                 self.mk("3", "Bob Lacasse", source_id="b", institution="b")]
        out = classify_batch(items, {})
        self.assertEqual(out["1"], (WIRE, "wire.author.cp"))
        self.assertEqual(out["2"], (UNKNOWN, "unknown.byline_seen_with_wire"))  # never wire
        self.assertEqual(out["3"], (OWN_REPORTING, "own_reporting.named_byline"))

    def test_same_byline_in_two_institutions_is_downgraded(self):
        items = [self.mk("1", "Alice Tremblay-Roy", source_id="a", institution="a"),
                 self.mk("2", "Alice Tremblay-Roy", source_id="b", institution="b"),
                 self.mk("3", "Bob Lacasse", source_id="a", institution="a")]
        out = classify_batch(items, {})
        self.assertEqual(out["1"], (UNKNOWN, "unknown.byline_multi_outlet"))
        self.assertEqual(out["2"], (UNKNOWN, "unknown.byline_multi_outlet"))
        self.assertEqual(out["3"][0], OWN_REPORTING)

    def test_sister_feeds_of_one_institution_do_not_trigger_multi_outlet(self):
        items = [self.mk("1", "Alice Tremblay-Roy", source_id="x-montreal", institution="x"),
                 self.mk("2", "Alice Tremblay-Roy", source_id="x-politics", institution="x")]
        out = classify_batch(items, {})
        self.assertEqual(out["1"][0], OWN_REPORTING)
        self.assertEqual(out["2"][0], OWN_REPORTING)

    def test_cross_rules_never_touch_non_own_reporting_classes(self):
        items = [self.mk("1", "Alice Tremblay-Roy, AFP", source_id="a", institution="a"),
                 self.mk("2", None, source_id="b", institution="b", summary="/CNW/ x"),
                 self.mk("3", "Alice Tremblay-Roy, AFP", source_id="b", institution="b")]
        out = classify_batch(items, {})
        self.assertEqual(out["1"][0], WIRE)
        self.assertEqual(out["2"][0], PRESS_RELEASE)
        self.assertEqual(out["3"][0], WIRE)

    def test_batch_equals_single_calls_when_no_cross_signal(self):
        items = [self.mk(str(n), a) for n, a in enumerate(
            [None, "AFP", "Alice Tremblay-Roy", "Rédaction", "Cision", "Bob Lacasse et Carl Dupont"])]
        out = classify_batch(items, {"journal-x": MEDIA})
        for it in items:
            self.assertEqual(out[it["id"]], origin_of(it, MEDIA))

    def test_tally_counts_per_source_and_class(self):
        items = [self.mk("1", "AFP"), self.mk("2", None), self.mk("3", "Alice Tremblay-Roy"),
                 self.mk("4", None, source_id="ville-x", institution="ville-x")]
        out = classify_batch(items, [MEDIA, OFFICIAL_SRC])
        t = tally(out, items)
        self.assertEqual(list(t), ["journal-x", "ville-x"])
        self.assertEqual(t["journal-x"], {"official": 0, "wire": 1, "press_release": 0,
                                          "own_reporting": 1, "unknown": 1})
        self.assertEqual(t["ville-x"]["official"], 1)
        self.assertEqual(sum(sum(r.values()) for r in t.values()), 4)

    def test_every_returned_rule_is_in_the_closed_set(self):
        items = []
        authors = [None, "AFP", "Agence QMI", "Cision", "Alice Tremblay-Roy", "Rédaction", "x",
                   "Alice Tremblay-Roy, L’autrice est x", "La Presse Canadienne", "AFP/Reuters"]
        summaries = ["", "(AP) x", "/CNW/ x", "par voie de communiqué", "dans un communiqué",
                     "in a news release", "avec des informations de AFP", "- AFP"]
        n = 0
        for a in authors:
            for s in summaries:
                items.append(self.mk(f"i{n}", a, summary=s))
                n += 1
        for res in classify_batch(items, {"journal-x": MEDIA}).values():
            self.assertIn(res[1], RULE_IDS)
            self.assertIn(res[0], CLASSES)


class DeterminismTests(unittest.TestCase):
    def test_same_bytes_under_different_hash_seeds(self):
        code = (
            "import sys, json; sys.path.insert(0, %r); import origin;"
            "items=[{'id':str(n),'author':a,'summary':s,'title':'t','source_id':'j','institution':'j'}"
            " for n,(a,s) in enumerate([(None,''),('AFP','x'),('Alice Tremblay-Roy','(AP) y'),"
            "('Alice Tremblay-Roy, La Presse Canadienne','z'),('Alice Tremblay-Roy',''),"
            "('Bob Lacasse','/CNW/ q'),('Cision','')])];"
            "print(json.dumps(origin.classify_batch(items,{'j':{'id':'j','source_kind':'media'}}),sort_keys=True))"
        ) % str(ROOT / "scripts")
        outs = set()
        for seed in ("0", "1", "42", "random"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                                  text=True, env=env, check=True, timeout=60)
            outs.add(proc.stdout)
        self.assertEqual(len(outs), 1)

    def test_module_is_pure_stdlib_without_clock_or_io(self):
        src = (ROOT / "scripts" / "origin.py").read_text(encoding="utf-8")
        for banned in ("import time", "import datetime", "import random", "import os", "import socket",
                       "import urllib", "import requests", "open(", "print(", "write_text", "store_io"):
            self.assertNotIn(banned, src, banned)

    def test_not_wired_into_the_pipeline_yet(self):
        for path in (ROOT / "scripts").glob("*.py"):
            if path.name == "origin.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, _SHADOW_IMPORT_RE, path.name)
            self.assertNotRegex(text, _SHADOW_CALL_RE, path.name)

    def test_shadow_guard_regexes_are_not_vacuous(self):
        # regression: the guard once lacked (?m) and only inspected line 1
        self.assertRegex("import json\nimport origin\n", _SHADOW_IMPORT_RE)
        self.assertRegex("import json\n    from origin import origin_of\n", _SHADOW_IMPORT_RE)
        self.assertRegex("def f():\n    import origin\n", _SHADOW_IMPORT_RE)
        self.assertRegex("x = importlib.import_module('origin')\n", _SHADOW_CALL_RE)
        self.assertRegex("y = origin.classify_batch(items)\n", _SHADOW_CALL_RE)
        self.assertNotRegex("import json\n# origin is shadow only\n", _SHADOW_IMPORT_RE)


class LiveDataGuardedTests(unittest.TestCase):
    """Counts only. Skipped when data/ is absent (CI, fresh clones)."""

    def test_live_candidates_classify_cleanly(self):
        path = ROOT / "data" / "normalized" / "latest_enriched.json"
        if not path.exists():
            self.skipTest("no local data/")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            items = doc["candidates"]
        except (OSError, ValueError, KeyError):
            self.skipTest("unreadable local data/")
        sources = {}
        for it in items:
            sources.setdefault(it["source_id"], {
                "id": it["source_id"], "name": it.get("source_name"), "institution": it.get("institution"),
                "institution_name": it.get("institution_name"), "source_kind": it.get("source_kind")})
        out = classify_batch(items, sources)
        self.assertEqual(len(out), len(items))
        for it in items:
            if it.get("source_kind") == "official":
                self.assertEqual(out[it["id"]][0], OFFICIAL)
            if not it.get("author") and it.get("source_kind") != "official":
                self.assertNotEqual(out[it["id"]][0], OWN_REPORTING)
        for res in out.values():
            self.assertIn(res[1], RULE_IDS)


if __name__ == "__main__":
    unittest.main()
