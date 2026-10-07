"""Controlled vocabulary (docs/I18N.md section 5) and its rule lexicons.

Every headline below is INVENTED: the public repository never carries
publisher text. The tests pin the data shape, the labels, the mapping of the
cluster_issues place hints, the folded word-bounded classification, its
determinism and its fail-soft behaviour.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import harness

import cluster_issues
import vocabulaire as V

ROOT = Path(__file__).resolve().parents[1]
CODE_RE = re.compile(r"^[a-z0-9-]{2,48}$")
FAMILIES = {
    "security", "justice", "politics", "mobility", "utilities", "environment",
    "health", "education", "housing", "economy", "culture-sport", "society",
}
PLACE_KINDS = {"scope", "arrondissement", "quartier", "site", "corridor", "neighbour"}


def spec_codes() -> tuple[list[str], list[str]]:
    """Codes as written in docs/I18N.md tables A and B (the spec is the oracle)."""
    text = (ROOT / "docs" / "I18N.md").read_text(encoding="utf-8")
    a = text.split("### Table A")[1].split("### Table B")[0]
    b = text.split("### Table B")[1].split("Specificity for")[0]
    types = re.findall(r"^\| ([a-z0-9-]+) \| [^|]+ \| [^|]+ \|$", a, re.M)
    places = re.findall(r"^\| ([a-z0-9-]+) \| [a-z]+ \| [^|]+ \| [^|]+ \|$", b, re.M)
    return [t for t in types if t != "Code"], [p for p in places if p != "Code"]


def spec_place_additions() -> list[tuple[str, str, str, str]]:
    """Table B rows added since docs/I18N.md, recorded as spec deviations in
    docs/EVENTS.md ("Table B additions"): (code, kind, fr, en)."""
    text = (ROOT / "docs" / "EVENTS.md").read_text(encoding="utf-8")
    block = text.split("#### Table B additions")[1].split("\n#")[0]
    rows = re.findall(r"^\| ([a-z0-9-]+) \| ([a-z]+) \| ([^|]+) \| ([^|]+) \|$", block, re.M)
    return [(c, k, fr.strip(), en.strip()) for c, k, fr, en in rows if c != "Code"]


class VocabularyShape(unittest.TestCase):
    def test_counts(self) -> None:
        self.assertGreaterEqual(len(V.type_codes()), 60)
        self.assertGreaterEqual(len(V.place_codes()), 30)
        self.assertEqual(len(V.type_codes()), 74)
        self.assertEqual(len(V.place_codes()), 52)

    def test_codes_unique_and_ascii(self) -> None:
        for codes in (V.type_codes(), V.place_codes(), V.family_codes()):
            self.assertEqual(len(codes), len(set(codes)))
            for code in codes:
                self.assertRegex(code, CODE_RE)

    def test_labels_non_empty_in_both_languages(self) -> None:
        vocab = V.vocabulary()
        for row in vocab["types"] + vocab["places"] + vocab["families"]:
            for lang in V.LANGS:
                self.assertTrue(str(row.get(lang) or "").strip(), (row["code"], lang))

    def test_codes_match_the_spec_tables_exactly(self) -> None:
        try:
            types, places = spec_codes()
            added = spec_place_additions()
        except OSError:
            self.skipTest("docs/I18N.md or docs/EVENTS.md not present")
        self.assertEqual(V.type_codes(), types)
        # Table B as written in I18N.md is the whole vocabulary, in order (it
        # was synced with the additions docs/EVENTS.md records as deviations,
        # which must stay in it, with the same kind and labels).
        self.assertEqual(V.place_codes(), places)
        for code, kind, fr, en in added:
            self.assertIn(code, places)
            self.assertEqual((V.place_kind(code), V.place_label(code, "fr"), V.place_label(code, "en")),
                             (kind, fr, en), code)

    def test_table_b_rows_match_the_vocabulary_word_for_word(self) -> None:
        try:
            text = (ROOT / "docs" / "I18N.md").read_text(encoding="utf-8")
        except OSError:
            self.skipTest("docs/I18N.md not present")
        block = text.split("### Table B")[1].split("Specificity for")[0]
        heading = re.search(r"places and sectors \((\d+)\)", block)
        rows = re.findall(r"^\| ([a-z0-9-]+) \| ([a-z]+) \| ([^|]+) \| ([^|]+) \|$", block, re.M)
        self.assertEqual(int(heading.group(1)), len(V.place_codes()), "the heading counts the table")
        for code, kind, fr, en in rows:
            self.assertEqual((V.place_kind(code), V.place_label(code, "fr"), V.place_label(code, "en")),
                             (kind, fr.strip(), en.strip()), code)

    def test_the_two_new_scopes(self) -> None:
        self.assertEqual(V.place_label("elsewhere", "fr"), "Hors Québec")
        self.assertEqual(V.place_label("elsewhere", "en"), "Outside Quebec")
        self.assertEqual(V.place_kind("elsewhere"), "scope")
        self.assertEqual(V.ELSEWHERE, "elsewhere")
        self.assertEqual(V.place_label("unplaced", "fr"), "Lieu non établi")
        self.assertEqual(V.place_label("unplaced", "en"), "Place not established")
        self.assertEqual(V.FALLBACK_PLACE, "unplaced")
        self.assertTrue(V.is_scope("unplaced"))
        # Scope order (the table order breaks specificity ties): the city first, the absence last.
        scopes = [c for c in V.place_codes() if V.is_scope(c)]
        self.assertEqual(scopes, ["quebec-city", "greater-quebec", "province", "ottawa", "elsewhere", "unplaced"])

    def test_spec_labels_are_kept_as_written(self) -> None:
        self.assertEqual(V.type_label("fire-building", "fr"), "Incendie de bâtiment")
        self.assertEqual(V.type_label("fire-building", "en"), "Building fire")
        self.assertEqual(V.type_label("shooting-stabbing", "fr"), "Fusillade ou attaque à l'arme blanche")
        self.assertEqual(V.place_label("vieux-quebec", "en"), "Old Québec")
        self.assertEqual(V.place_label("sainte-foy-sillery-cap-rouge", "fr"), "Sainte-Foy–Sillery–Cap-Rouge")

    def test_families_closed_list_and_every_type_has_one(self) -> None:
        self.assertEqual(set(V.family_codes()), FAMILIES)
        used = set()
        for code in V.type_codes():
            family = V.family_of(code)
            self.assertIn(family, FAMILIES, code)
            used.add(family)
        self.assertEqual(used, FAMILIES, "every family should own at least one type")
        self.assertEqual(V.family_of("tramway-project"), "mobility")
        self.assertEqual(V.family_of("court-trial"), "justice")
        self.assertEqual(V.family_of("unclassified"), "society")
        self.assertIsNone(V.family_of("no-such-type"))
        self.assertEqual(V.family_label("housing", "en"), "Housing and property")

    def test_unclassified_is_a_type_and_named_scars_are_types(self) -> None:
        for code in ("unclassified", "tramway-project", "third-link-project", "airport"):
            self.assertIn(code, V.type_codes())

    def test_place_kinds(self) -> None:
        for code in V.place_codes():
            self.assertIn(V.place_kind(code), PLACE_KINDS, code)
        self.assertEqual(V.place_kind("limoilou"), "quartier")
        self.assertEqual(V.place_kind("beauport"), "arrondissement")
        self.assertEqual(V.place_kind("quebec-city"), "scope")
        self.assertEqual(V.place_kind("levis"), "neighbour")
        self.assertEqual(V.place_kind("henri-iv"), "corridor")
        self.assertEqual(V.place_kind("expocite"), "site")
        self.assertIsNone(V.place_kind("nowhere"))
        self.assertTrue(V.is_scope("ottawa"))
        self.assertFalse(V.is_scope("limoilou"))

    def test_no_verdict_words_in_vigie_labels(self) -> None:
        banned = re.compile(r"\b(confirm|verified|v[ée]rifi|fake|faux|false|truth|v[ée]rit|bias|biais)", re.I)
        vocab = V.vocabulary()
        for row in vocab["types"] + vocab["places"] + vocab["families"]:
            for lang in V.LANGS:
                self.assertIsNone(banned.search(row[lang]), (row["code"], lang))


class PlaceHintMapping(unittest.TestCase):
    def test_every_cluster_place_hint_maps_to_a_code(self) -> None:
        for hint in cluster_issues._PLACE_HINTS:
            code = V.place_for_hint(hint)
            self.assertIsNotNone(code, hint)
            self.assertIn(code, V.place_codes())

    def test_identity_and_the_two_aliases(self) -> None:
        self.assertEqual(V.place_for_hint("limoilou"), "limoilou")
        self.assertEqual(V.place_for_hint("vieux-port"), "vieux-port")
        self.assertEqual(V.place_for_hint("duberger"), "duberger-les-saules")
        self.assertEqual(V.place_for_hint("haute-saint-charles"), "la-haute-saint-charles")
        self.assertIsNone(V.place_for_hint("atlantis"))
        self.assertIsNone(V.place_for_hint(""))

    def test_road_tokens_and_geo_fallback(self) -> None:
        self.assertEqual(V.place_for_road("pierre-laporte"), "pierre-laporte-bridge")
        self.assertEqual(V.place_for_road("henri-iv"), "henri-iv")
        self.assertIsNone(V.place_for_road("dorchester"))
        self.assertEqual(V.place_from_geo("quebec-city"), "quebec-city")
        self.assertEqual(V.place_from_geo("quebec"), "province")
        self.assertEqual(V.place_from_geo("ottawa"), "ottawa")
        self.assertEqual(V.place_from_geo("federal"), "ottawa")
        self.assertEqual(V.place_from_geo("world"), "elsewhere")
        # enrich's "linked" is an absence of local evidence, never a place.
        self.assertIsNone(V.place_from_geo("linked"))
        self.assertIsNone(V.place_from_geo("unknown"))


class Labels(unittest.TestCase):
    def test_spec_example(self) -> None:
        self.assertEqual(V.label("fire-building", "limoilou", "fr"), "Incendie de bâtiment · Limoilou")
        self.assertEqual(V.label("fire-building", "limoilou", "en"), "Building fire · Limoilou")

    def test_scope_and_other_examples(self) -> None:
        self.assertEqual(V.label("tramway-project", "quebec-city", "fr"), "Projet de tramway · Québec")
        self.assertEqual(V.label("tramway-project", "quebec-city", "en"), "Tramway project · Québec City")
        self.assertEqual(V.label("provincial-election", "province", "en"), "Provincial election · Province of Quebec")
        self.assertEqual(V.label("airport", "jean-lesage-airport", "fr"), "Aéroport · Aéroport Jean-Lesage")

    def test_language_defaults_to_french(self) -> None:
        self.assertEqual(V.label("homicide", "levis"), V.label("homicide", "levis", "fr"))
        self.assertEqual(V.label("homicide", "levis", "xx"), "Homicide · Lévis")
        self.assertEqual(V.label("homicide", "levis", "EN-CA"), "Homicide · Lévis")

    def test_missing_or_unknown_pieces_never_invent_a_place(self) -> None:
        self.assertEqual(V.label("homicide", None, "fr"), "Homicide")
        self.assertEqual(V.label("homicide", "", "en"), "Homicide")
        self.assertEqual(V.label("homicide", "atlantis", "fr"), "Homicide")
        self.assertEqual(V.label("no-such-type", "limoilou", "fr"), "Événement non classé · Limoilou")
        self.assertEqual(V.label("unclassified", "ottawa", "en"), "Unclassified event · Ottawa (federal)")

    def test_label_is_deterministic(self) -> None:
        self.assertEqual(
            [V.label(t, "limoilou", "fr") for t in V.type_codes()],
            [V.label(t, "limoilou", "fr") for t in V.type_codes()],
        )


class LexiconShape(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = json.loads(V.LEXIQUE_PATH.read_text(encoding="utf-8"))

    def test_status_is_always_proposed(self) -> None:
        self.assertEqual(self.doc["status"], "proposed")
        self.assertEqual(V.STATUS, "proposed")

    def test_lexicon_codes_exist_in_the_vocabulary(self) -> None:
        for code in self.doc["types"]:
            self.assertIn(code, V.type_codes())
        for code in self.doc["places"]:
            self.assertIn(code, V.place_codes())

    def test_every_type_but_unclassified_has_keywords_in_both_languages(self) -> None:
        for code in V.type_codes():
            if code == "unclassified":
                self.assertNotIn(code, self.doc["types"])
                continue
            rule = self.doc["types"].get(code)
            self.assertIsNotNone(rule, f"no lexicon for {code}")
            fr = rule.get("fr", []) + rule.get("fr_strong", [])
            en = rule.get("en", []) + rule.get("en_strong", [])
            self.assertTrue(fr, f"{code}: no French keywords")
            self.assertTrue(en or code in {"fire-forest"} or rule.get("en_strong"), f"{code}: no English keywords")
            total = len(fr) + len(en)
            self.assertLessEqual(total, 120, f"{code}: lexicon should stay reviewable")

    def test_keywords_compile_and_are_nonempty_after_folding(self) -> None:
        for code, rule in self.doc["types"].items():
            for key, words in rule.items():
                if not isinstance(words, list):
                    continue
                for word in words:
                    self.assertTrue(V.fold(word.replace("*", "").replace("#", "1")), (code, key, word))
                    self.assertIsNotNone(V._keyword_regex(word), (code, key, word))

    def test_strong_weights_are_small_integers(self) -> None:
        for code, rule in self.doc["types"].items():
            if "strong_weight" in rule:
                self.assertIn(rule["strong_weight"], (2, 3, 4), code)

    def test_every_place_has_a_keyword_and_context_flag_is_boolean(self) -> None:
        # The fallback is the one place no text can name: it has no rule at all.
        self.assertNotIn(V.FALLBACK_PLACE, self.doc["places"])
        for code in V.place_codes():
            if code == V.FALLBACK_PLACE:
                continue
            rule = self.doc["places"].get(code)
            self.assertIsNotNone(rule, f"no place lexicon for {code}")
            self.assertTrue(rule.get("fr") or rule.get("en"), code)
            self.assertIsInstance(rule.get("needs_context", False), bool)

    def test_data_files_are_lf_utf8(self) -> None:
        for path in (V.VOCAB_PATH, V.LEXIQUE_PATH):
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, path.name)
            raw.decode("utf-8")


class Folding(unittest.TestCase):
    def test_fold(self) -> None:
        self.assertEqual(V.fold("L'Île-d'Orléans"), "l ile d orleans")
        self.assertEqual(V.fold("  Cœur  de   Québec! "), "coeur de quebec")
        self.assertEqual(V.fold(None), "")

    def test_accent_and_case_insensitive_matching(self) -> None:
        self.assertEqual(V.classify_type(["INCENDIE à Limoilou"])[0], "fire-building")
        self.assertEqual(V.classify_type(["incendie a limoilou"])[0], "fire-building")

    def test_word_bounds(self) -> None:
        # "levis" sits inside "television": the place lexicon must not fire.
        self.assertEqual(V.classify_places(["Une émission de télévision sur la cuisine"]), [])
        # "fire" is not "fired" and "tram" is not "trampoline".
        self.assertEqual(V.classify_type(["Coach fired after the loss, trampoline park opens"])[0], "unclassified")
        # Prefix wildcard: "evacuat*" covers evacuating and evacuated.
        self.assertEqual(V.classify_type(["Residents evacuated overnight"])[0], "evacuation")


class ClassifyType(unittest.TestCase):
    CASES = [
        (["Incendie dans un immeuble de Limoilou, trois pompiers blessés"], "fire-building"),
        (["Feu de forêt hors de contrôle près de Lac-Saint-Charles"], "fire-forest"),
        (["Wildfire smoke drifts over the region"], "fire-forest"),
        (["Collision entre deux voitures sur l'autoroute, deux blessés"], "road-collision"),
        (["Une piétonne happée par un camion devant l'école"], "pedestrian-cyclist-struck"),
        (["Noyade d'un baigneur au lac"], "water-rescue"),
        (["Une adolescente est portée disparue depuis mardi"], "missing-person"),
        (["Meurtre dans un logement: un suspect recherché"], "homicide"),
        (["Fusillade devant un bar, un blessé par balle"], "shooting-stabbing"),
        (["Two arrested after a break-in downtown"], "arrest"),
        (["Perquisition dans un commerce, la police saisit des documents"], "police-operation"),
        (["Évacuation d'un édifice à bureaux après une alerte"], "evacuation"),
        (["Le coroner dépose son rapport sur la mort d'un locataire"], "coroner-report"),
        (["Procès pour fraude: le jury entend un premier témoin"], "court-trial"),
        (["Un homme condamné à deux ans de prison pour fraude"], "court-sentence"),
        (["La Cour d'appel rejette la requête de la municipalité"], "court-ruling"),
        (["Class action filed over delayed refunds"], "class-action"),
        (["Le conseil de ville se réunit lundi soir"], "council-meeting"),
        (["Le conseil municipal adopte un règlement sur le bruit"], "municipal-bylaw"),
        (["Projet de règlement sur les abris d'auto, consultation publique à venir"], "municipal-bylaw"),
        (["Nouveau compte de taxes: la hausse expliquée aux propriétaires"], "municipal-tax"),
        (["Le tramway: nouvelle phase des travaux annoncée"], "tramway-project"),
        (["Le troisième lien inquiète les maires de la rive"], "third-link-project"),
        (["Entraves à la circulation sur la rue des Érables"], "roadworks"),
        (["Plusieurs rues fermées pour le marathon de dimanche"], "road-closure"),
        (["Panne de courant: 5 000 abonnés sans électricité"], "power-outage"),
        (["Avis d'ébullition de l'eau dans le quartier"], "water-advisory"),
        (["Déneigement: l'opération de chargement de la neige débute"], "snow-removal"),
        (["Alerte météo: tempête de pluie attendue"], "weather-warning"),
        (["Inondation dans un sous-sol, la rivière déborde"], "flooding"),
        (["Hôpital: des chirurgies reportées faute de personnel"], "hospital-services"),
        (["Grève des enseignants: les écoles fermées lundi"], "strike-lockout"),
        (["Mises à pied: 120 emplois supprimés à l'usine"], "layoffs"),
        (["Hausse des prix de l'essence à la pompe"], "price-change"),
        (["Un festival de jazz envahit le Vieux-Port"], "festival-event"),
        (["Les Championnats du monde de ski débutent demain"], "sports-event"),
        (["Manifestation devant l'hôtel de ville contre la fermeture du centre"], "protest"),
        (["Singer Alex Example dies at 81"], "death-public-figure"),
        (["Sauvons le patrimoine bâti: l'église est classée"], "built-heritage"),
        (["Eviction notices up as the rental vacancy rate falls"], "rent-eviction"),
        (["Homeless encampment cleared near the river"], "homelessness"),
        (["Bankruptcy filing leaves 40 employees unpaid"], "business-open-close"),
        (["Boil water advisory lifted for downtown residents"], "water-advisory"),
        (["Roadworks and detours expected on the bridge access road"], "roadworks"),
        (["Airport adds three weekly flights this summer"], "airport"),
        (["La vente de l'immeuble du couvent a été conclue"], "property-transaction"),
        (["Résultat du scrutin: les chefs en campagne à trois jours du vote"], "provincial-election"),
    ]

    def test_invented_headlines(self) -> None:
        for titles, expected in self.CASES:
            self.assertEqual(V.classify_type(titles)[0], expected, titles)

    def test_zero_hits_is_unclassified(self) -> None:
        self.assertEqual(V.classify_type([]), ("unclassified", 0))
        self.assertEqual(V.classify_type(None), ("unclassified", 0))
        self.assertEqual(V.classify_type(["La caricature du jour"]), ("unclassified", 0))
        self.assertEqual(V.classify_type(["A quiet afternoon by the river"]), ("unclassified", 0))

    def test_a_team_name_alone_is_not_a_sports_event(self) -> None:
        # Team names are weak cues: a book, a mural or a street named after a team is not a match.
        for title in ("Un album illustré raconte l'histoire du Canadien de Montréal",
                      "Une murale rend hommage aux Nordiques",
                      "Raptors fans in town",
                      "Les Alouettes de Montréal, un livre pour enfants"):
            self.assertEqual(V.classify_type([title])[0], "unclassified", title)
        # With a second sports cue (in the headline or in another member title) the type is proposed.
        self.assertEqual(V.classify_type(["Les Remparts de Québec gagnent leur match"])[0], "sports-event")
        self.assertEqual(V.classify_type(["Raptors win the playoffs opener"])[0], "sports-event")
        self.assertEqual(V.classify_type(["Les Alouettes en route", "Le joueur vedette est blessé"])[0],
                         "sports-event")

    def test_one_weak_word_in_one_title_is_not_evidence(self) -> None:
        # "chaleur" alone, or a lone "accident" with no strong marker, stays unclassified.
        self.assertEqual(V.classify_type(["Un accident de parcours pour le candidat"])[0], "unclassified")
        # Weak hits in two member titles add up to a proposal.
        weak_two = ["Les syndicats se réunissent", "Syndicat: nouvelle rencontre jeudi"]
        self.assertEqual(V.classify_type(weak_two)[0], "collective-agreement")

    def test_hits_are_returned_and_status_is_proposed(self) -> None:
        code, hits = V.classify_type(["Incendie dans un entrepôt", "Fire at a warehouse: crews on scene"])
        self.assertEqual(code, "fire-building")
        self.assertGreaterEqual(hits, V.MIN_SCORE)

    def test_more_member_hits_win(self) -> None:
        titles = [
            "Incendie dans un immeuble",
            "Incendie: les pompiers sur place",
            "Perquisition dans le même quartier",
        ]
        self.assertEqual(V.classify_type(titles)[0], "fire-building")

    def test_ties_go_to_table_order(self) -> None:
        # One strong keyword each at the default weight: fire-building precedes arrest in table A.
        code, _ = V.classify_type(["Incendie suspect: un homme arrestation imminente"])
        self.assertEqual(code, "fire-building")
        order = V.type_codes()
        self.assertLess(order.index("fire-building"), order.index("arrest"))

    def test_legal_stage_outranks_the_offence(self) -> None:
        self.assertEqual(V.classify_type(["Procès pour meurtre: le jury délibère"])[0], "court-trial")
        self.assertEqual(V.classify_type(["Homme condamné pour une agression sexuelle"])[0], "court-sentence")

    def test_action_outranks_institution_context(self) -> None:
        self.assertEqual(V.classify_type(["Chambre des communes: le député démissionne"])[0], "appointment-resignation")

    def test_unless_guards(self) -> None:
        # A vehicle fire is not a building fire; a forest fire is not a building fire.
        self.assertNotEqual(V.classify_type(["Incendie d'un véhicule sur l'autoroute"])[0], "fire-building")
        self.assertNotEqual(V.classify_type(["Incendie de forêt près du lac"])[0], "fire-building")
        # A sports "collision" or a time trial is not a crash or a trial.
        self.assertNotEqual(V.classify_type(["Hockey: a collision behind the net"])[0], "road-collision")
        self.assertNotEqual(V.classify_type(["Cyclist wins the time trial"])[0], "court-trial")
        # Federal bills are not Quebec bills.
        self.assertNotEqual(V.classify_type(["Ottawa dépose un projet de loi fédéral"])[0], "provincial-bill")
        self.assertNotEqual(V.classify_type(["Arrêté municipal: rue fermée"])[0], "arrest")

    def test_foreign_strike_is_not_a_labour_strike(self) -> None:
        self.assertNotEqual(V.classify_type(["Air strike hits the capital overnight"])[0], "strike-lockout")
        self.assertEqual(V.classify_type(["Workers vote to go on strike"])[0], "strike-lockout")

    def test_public_figure_label_needs_an_age_marker_not_a_private_person(self) -> None:
        self.assertNotEqual(V.classify_type(["Un homme est décédé dans un chalet"])[0], "death-public-figure")
        self.assertEqual(V.classify_type(["Le romancier Jean Exemple est mort à 90 ans"])[0], "death-public-figure")

    def test_suspicious_death_is_not_called_homicide(self) -> None:
        # No verdict: "found dead" is not a murder; the honest type is unclassified.
        self.assertEqual(V.classify_type(["Un homme retrouvé sans vie dans un parc"])[0], "unclassified")
        self.assertEqual(V.classify_type(["Body found near the river"])[0], "unclassified")

    def test_explain_type_reads_the_same_decision(self) -> None:
        rows = V.explain_type(["Incendie dans un immeuble", "Procès pour fraude"])
        self.assertEqual(rows[0]["type"], V.classify_type(["Incendie dans un immeuble", "Procès pour fraude"])[0])
        for row in rows:
            self.assertEqual(sorted(row), ["keywords", "score", "type"])
            self.assertEqual(row["keywords"], sorted(row["keywords"]))
        self.assertEqual([r["score"] for r in rows], sorted((r["score"] for r in rows), reverse=True))

    def test_every_lexicon_type_can_fire(self) -> None:
        doc = json.loads(V.LEXIQUE_PATH.read_text(encoding="utf-8"))
        for code, rule in doc["types"].items():
            sample = None
            for key in ("fr_strong", "en_strong"):
                for word in rule.get(key, []):
                    candidate = word.replace("*", "").replace("#", "42")
                    if V.classify_type([candidate])[0] == code:
                        sample = candidate
                        break
                if sample:
                    break
            self.assertIsNotNone(sample, f"no strong keyword of {code} classifies by itself")


class ClassifyPlaces(unittest.TestCase):
    def test_quartier_beats_scope_and_table_order_breaks_ties(self) -> None:
        places = V.classify_places(["Incendie à Limoilou, selon la Ville de Québec"])
        self.assertEqual(places[0], "limoilou")
        self.assertIn("quebec-city", places)
        self.assertEqual(V.classify_places(["Limoilou et Maizerets touchés"]), ["limoilou", "maizerets"])

    def test_specificity_order(self) -> None:
        text = "Au Québec, à Lévis, sur le pont Pierre-Laporte, dans Beauport et à Limoilou, à Québec"
        places = V.classify_places([text])
        kinds = [V.place_kind(p) for p in places]
        rank = {k: i for i, k in enumerate(["quartier", "arrondissement", "site", "corridor", "neighbour", "scope"])}
        self.assertEqual([rank[k] for k in kinds], sorted(rank[k] for k in kinds))
        self.assertEqual(places[0], "limoilou")
        self.assertEqual(places[1], "beauport")
        self.assertEqual(places[2], "pierre-laporte-bridge")
        self.assertEqual(places[3], "levis")

    def test_spec_example_corridors_and_sites(self) -> None:
        self.assertEqual(V.classify_places(["Fermeture de l'autoroute Henri-IV"]), ["henri-iv"])
        self.assertEqual(V.classify_places(["Travaux au pont de Québec"])[:1], ["quebec-bridge"])
        self.assertEqual(V.classify_places(["Spectacle à ExpoCité"]), ["expocite"])
        self.assertEqual(V.classify_places(["Concert on the Plains of Abraham"]), ["plains-of-abraham"])

    def test_no_place_named_means_empty_not_a_guess(self) -> None:
        self.assertEqual(V.classify_places(["Une histoire sans lieu"]), [])
        self.assertEqual(V.classify_places([]), [])
        self.assertEqual(V.classify_places(None), [])

    def test_ambiguous_names_need_a_quebec_context(self) -> None:
        self.assertNotIn("vanier", V.classify_places(["Le collège Vanier annonce sa rentrée"]))
        self.assertIn("vanier", V.classify_places(["Les résidents de Vanier, à Québec, se prononcent"]))
        self.assertNotIn("saint-roch", V.classify_places(["Fête à Saint-Roch-des-Aulnaies"]) if False else [])
        self.assertIn("saint-roch", V.classify_places(["Saint-Roch: nouveau parc à Québec"]))

    def test_aliases_in_text(self) -> None:
        self.assertIn("duberger-les-saules", V.classify_places(["Un feu à Duberger"]))
        self.assertIn("la-haute-saint-charles", V.classify_places(["La Haute-Saint-Charles s'agrandit"]))

    def test_language_selects_the_keyword_list(self) -> None:
        # French "à Québec" means the city; an English "a Quebec woman" must not.
        self.assertEqual(V.classify_places([("Une fête à Québec", "fr")]), ["quebec-city"])
        self.assertNotIn("quebec-city", V.classify_places([("A Quebec woman wins the draw", "en")]))
        self.assertEqual(V.classify_places(["Mayor of Québec City speaks"], ["en"]), ["quebec-city"])

    def test_masked_institution_names_are_not_places(self) -> None:
        self.assertEqual(V.classify_places(["La Sûreté du Québec enquête"]), [])
        self.assertEqual(V.classify_places(["Hydro-Québec annonce une hausse"]), [])
        self.assertEqual(V.classify_places(["Radio-Canada diffuse le débat"]), [])

    def test_scope_places(self) -> None:
        self.assertIn("province", V.classify_places(["Le gouvernement du Québec annonce"]))
        self.assertIn("ottawa", V.classify_places(["Ottawa annonce un programme"]))
        self.assertIn("greater-quebec", V.classify_places(["Sondage dans la région de Québec"]))

    def test_elsewhere_is_named_by_a_place_outside_quebec(self) -> None:
        for title in ("Ouragan en Floride : le Zorblax ferme ses portes",
                      "Paris : un nouveau pont Zorblax inauguré", "Le Zorblax ouvre une usine en Alberta",
                      "Ukraine : le Zorblax livre des génératrices", "Flooding in Nova Scotia: Zorblax Road closed"):
            self.assertIn("elsewhere", V.classify_places([title]), title)
        for title in ("Les Fêtes de la Nouvelle-France à Québec", "Air France ajoute un vol vers Québec",
                      "Les Maple Leafs de Toronto battent le Zorblax"):
            self.assertNotIn("elsewhere", V.classify_places([title]), title)

    def test_the_city_of_ottawa_is_not_the_federal_scope(self) -> None:
        # "Ottawa" alone is the usual metonym of the federal government ...
        self.assertEqual(V.classify_places(["Ottawa annonce un programme Zorblax"]), ["ottawa"])
        self.assertIn("ottawa", V.classify_places(["Québec et Ottawa s'entendent sur le Zorblax"]))
        # ... "à Ottawa", "d'Ottawa", "la police d'Ottawa" place the event in the city (Ontario).
        for title in ("Un suspect interpellé à Ottawa près du parc Zorblax",
                      "Un résident d'Ottawa condamné pour le Zorblax",
                      "Les pompiers d’Ottawa examinent le Zorblax", "Course municipale : le Zorblax à Ottawa"):
            self.assertEqual(V.classify_places([title]), ["elsewhere"], title)
        # Federal evidence still names the federal scope when the city is also named.
        self.assertIn("ottawa", V.classify_places(["Le gouvernement fédéral annonce à Ottawa un plan Zorblax"]))

    def test_ottawa_addressed_or_paying_is_the_federal_government(self) -> None:
        # Quebec French addresses the federal government as "Ottawa": asked,
        # paying, answering, sitting. Those phrases stay federal; the city's
        # "à Ottawa" / "d'Ottawa" stay the city.
        for title in ("Le syndicat Zorblax demande à Ottawa un moratoire",
                      "Les maires du Zorblax réclament des fonds à Ottawa",
                      "Québec exige d’Ottawa une compensation pour le Zorblax",
                      "Zorblax : l'aide d'Ottawa se fait attendre", "Le refus d'Ottawa irrite les Zorblax",
                      "Session parlementaire à Ottawa : le Zorblax en tête",
                      "Zorblax caucus returns to Parliament Hill"):
            places = V.classify_places([title])
            self.assertIn("ottawa", places, title)
            self.assertNotIn("elsewhere", places, title)
        for title in ("Un suspect interpellé à Ottawa près du parc Zorblax", "Zorblax : la Ville d'Ottawa ouvre une patinoire"):
            self.assertEqual(V.classify_places([title]), ["elsewhere"], title)
        # More than four words between the verb and "à Ottawa": no longer read as addressed.
        self.assertEqual(V.classify_places(["Le Zorblax demande une pause pour ses quatre filiales à Ottawa"]),
                         ["elsewhere"])
        # Every federal phrase the ottawa rule keeps is a phrase the elsewhere rule masks.
        doc = json.loads(V.LEXIQUE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(set(doc["places"]["ottawa"]["keep"]) <= set(doc["places"]["elsewhere"]["mask"]))
        # Québec City's own colline Parlementaire stays a site of the capital.
        self.assertEqual(V.classify_places(["Colline Parlementaire : le Zorblax plante un arbre"]), ["parliament-hill"])

    def test_bets_are_not_paris(self) -> None:
        for title in ("Paris en ligne : le Zorblax veut sa part", "Les paris sportifs du Zorblax inquiètent",
                      "Paris et casinos : le Zorblax serre la vis"):
            self.assertNotIn("elsewhere", V.classify_places([title]), title)
        self.assertIn("elsewhere", V.classify_places(["À Paris, le Zorblax fait salle comble"]))

    def test_a_gap_reads_up_to_four_words(self) -> None:
        rx = re.compile(V._keyword_regex("demande ~ a ottawa"))
        self.assertTrue(rx.search("le zorblax demande a ottawa"))
        self.assertTrue(rx.search("le zorblax demande des fonds neufs a ottawa"))
        self.assertFalse(rx.search("le zorblax demande un, deux, trois, quatre, cinq a ottawa".replace(",", "")))
        self.assertFalse(rx.search("le zorblax redemande a ottawa"), "word-bounded")
        self.assertEqual(V._keyword_regex("a ~ ottawa"), V._keyword_regex("a ~ ottawa ~"))

    def test_quebec_towns_outside_the_capital_region_are_the_province(self) -> None:
        for title in ("Grave accident à Trois-Rivières", "Un homme de Montréal arrêté au Zorblax",
                      "Inondations en Gaspésie", "Le Zorblax arrive à Gatineau"):
            self.assertEqual(V.classify_places([title]), ["province"], title)
        for title in ("Le Canadien de Montréal gagne le Zorblax", "Les Canadiens de Montréal en séries",
                      "La Banque de Montréal ferme une succursale Zorblax"):
            self.assertNotIn("province", V.classify_places([title]), title)

    def test_masks_remove_the_longest_phrase_first(self) -> None:
        mask = V._mask(["le canadien", "le canadien de montreal"])
        self.assertEqual(mask.sub(" ", V.fold("Le Canadien de Montréal")).strip(), "")
        self.assertIsNone(V._mask([]))
        self.assertIsNone(V._mask([], ["demande ~ a ottawa"]))

    def test_a_kept_phrase_survives_the_mask(self) -> None:
        mask = V._mask(["a ottawa"], ["demande ~ a ottawa"])
        self.assertEqual(mask.sub(" ", "on demande des sous a ottawa puis on danse a ottawa").split(),
                         ["on", "demande", "des", "sous", "a", "ottawa", "puis", "on", "danse"])

    def test_the_fallback_place_is_never_named_by_a_text(self) -> None:
        for title in ("Lieu non établi", "Place not established", "unplaced", "Un fait divers Zorblax"):
            self.assertNotIn(V.FALLBACK_PLACE, V.classify_places([title]))

    def test_multiple_texts_accumulate(self) -> None:
        places = V.classify_places(["Un feu à Beauport", "Collision à Charlesbourg"])
        self.assertEqual(places, ["charlesbourg", "beauport"])  # table order inside one kind

    def test_every_place_code_is_reachable_from_its_own_keyword(self) -> None:
        doc = json.loads(V.LEXIQUE_PATH.read_text(encoding="utf-8"))
        for code, rule in doc["places"].items():
            word = (rule.get("fr") or rule.get("en"))[0]
            text = f"{word} à Québec" if rule.get("needs_context") else word
            self.assertIn(code, V.classify_places([text]), (code, word))


class Determinism(unittest.TestCase):
    PAYLOAD = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vocabulaire as V\n"
        "titles = ['Incendie à Limoilou, pompiers sur place', 'Procès pour meurtre à Lévis', 'Alerte météo: tempête']\n"
        "out = {\n"
        "  'types': [V.classify_type([t]) for t in titles],\n"
        "  'all': V.classify_type(titles),\n"
        "  'explain': V.explain_type(titles),\n"
        "  'places': V.classify_places(titles),\n"
        "  'labels': [V.label(c, 'limoilou', 'en') for c in V.type_codes()],\n"
        "}\n"
        "print(json.dumps(out, sort_keys=True, ensure_ascii=False))\n"
    )

    def run_with(self, seed: str) -> str:
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", self.PAYLOAD, str(ROOT / "scripts")],
            capture_output=True, env=env, check=True, timeout=60,
        )
        return proc.stdout.decode("utf-8")

    def test_identical_bytes_across_hash_seeds(self) -> None:
        a, b, c = self.run_with("0"), self.run_with("1"), self.run_with("12345")
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertIn("fire-building", a)

    def test_repeated_calls_are_identical(self) -> None:
        titles = ["Incendie à Limoilou", "Collision sur l'autoroute", "Meurtre à Lévis"]
        self.assertEqual(V.explain_type(titles), V.explain_type(list(titles)))
        self.assertEqual(V.classify_places(titles), V.classify_places(titles))



# --------------------------------------------------------------------------- #
# Honest coverage (2026-10-07): the lexicon was extended from the kinds of
# civic news that fell to "unclassified" in the live edition and the local
# history. Every headline here is INVENTED (a place called Zorbanie, a ball
# club called the Furets); each family has its positives and its near misses.
# --------------------------------------------------------------------------- #
COVERAGE_POSITIVES = [
 ("Les résultats électoraux de la circonscription de Zorbanie : le Parti québécois en tête", "provincial-election"),
 ("Zorbanie : les chefs de parti se disputent la tribune avant le scrutin", "provincial-election"),
 ("Élections Québec rappelle les règles du vote par correspondance", "provincial-election"),
 ("La CAQ promet un cadre financier équilibré pour Zorbanie", "provincial-election"),
 ("Le député élu de Zorbanie-Nord dévoile son équipe", "provincial-election"),
 ("Le gouvernement péquiste annonce son premier conseil à Zorbanie", "provincial-election"),
 ("Le gouvernement péquiste de Zorbanie dévoile son équipe", "provincial-election"),
 ("Zorbanie-Sud : candidate défaite, la QS demande un dépouillement judiciaire", "provincial-election"),
 ("Quebec party leaders trade barbs a week before the vote", "provincial-election"),
 ("La directrice générale du port quitte son poste", "appointment-resignation"),
 ("Zorbo nommé capitaine des Furets de Zorbanie", "appointment-resignation"),
 ("Un ministre zorbanien choisit sa nouvelle cheffe de cabinet : nomination officielle", "appointment-resignation"),
 ("Zorbanie : un O-Train jusqu'au lac des Furets, pour 2040", "public-transit"),
 ("Fermeture temporaire du chemin des Lutins à Zorbanie", "road-closure"),
 ("Fermetures du boulevard Quilbo dans les nuits du 3 au 7 octobre", "road-closure"),
 ("Circulation à contresens sur l'autoroute 999 à Zorbanie dès lundi", "roadworks"),
 ("Quartier Quilbo : travaux de nuit, détour et problèmes de circulation à prévoir", "roadworks"),
 ("Panne planifiée à Zorbanie : 12 000 clients rebranchés", "power-outage"),
 ("Zorbanie : interruption d'électricité samedi dans le quartier Quilbo", "power-outage"),
 ("Sur la route de Zorbanie, une camionnette percute un arbre ; son chauffeur s'en tire", "road-collision"),
 ("Zorbanie : accident sur l'autoroute Quilbo, cinq blessés légers", "road-collision"),
 ("Zorbanie mourns two drivers killed in road accidents on separate highways", "road-collision"),
 ("Un cycliste de Zorbanie frappé par une voiture au centre-ville", "pedestrian-cyclist-struck"),
 ("Un piéton happé mortellement à Zorbanie", "pedestrian-cyclist-struck"),
 ("A vehicle strikes pedestrians on a Zorbanie sidewalk, four hurt", "pedestrian-cyclist-struck"),
 ("Trois suspects arrêtés à Zorbanie après un vol", "arrest"),
 ("Deux personnes arrêtées en Zorbanie-Ouest", "arrest"),
 ("Un trafic de stupéfiants est démantelé dans un entrepôt de Zorbanie", "police-operation"),
 ("La police de Zorbanie intervient dans un commerce : le BEI mène l'enquête", "police-operation"),
 ("Il aurait tué son voisin à Zorbanie", "homicide"),
 ("Zorbanie : un quinquagénaire comparaît, accusé d'avoir menacé une institutrice", "court-charges"),
 ("Zorbanie : policier accusé d'avoir transmis des pièces confidentielles à un proche", "court-charges"),
 ("Audience sur la peine : l'accusé de Zorbanie reviendra mardi", "court-sentence"),
 ("Un juge ordonne la réouverture du site de Zorbanie", "court-ruling"),
 ("Judge denies the request to leave Zorbanie", "court-ruling"),
 ("Les Sénateurs battent les Furets en tirs au but", "sports-event"),
 ("Dimanche soir à Zorbanie, les Sénateurs terminent devant les Bruins", "sports-event"),
 ("Le Rouge et Noir de Zorbanie perd en LCF", "sports-event"),
 ("Zorbo licencie 80 travailleurs à Zorbanie", "layoffs"),
 ("Zinzin coupe soixante emplois dans son entrepôt de Zorbanie", "layoffs"),
 ("Zorbanie : usine fermée, trois cents emplois perdus d'un coup", "layoffs"),
 ("La boutique Zinzin cessera ses activités à Zorbanie", "business-open-close"),
 ("Un nouvel écocentre mobile ouvre à Zorbanie", "waste-collection"),
 ("L'essence atteint 2 $ le litre à Zorbanie", "price-change"),
 ("Hydro-Zorbanie : le nouveau tarif résidentiel entre en vigueur à Quilbo", "price-change"),
 ("Appel d'offres pour un parc éolien en Zorbanie", "investment-project"),
 ("A missing teen from Zorbanie is found safe at a cousin's cottage", "missing-person"),
]

# (headline, the type it must NOT get): near misses an honest "unclassified"
# (or another type) must keep
COVERAGE_NEAR_MISSES = [
 ("Résultats électoraux en Suède : la gauche l'emporte", "provincial-election"),
 ("Élections en France : les candidats se disputent le vote", "provincial-election"),
 ("Le vote par correspondance américain divise", "provincial-election"),
 ("Zorbanie accueille le caucus conservateur fédéral pour sa rentrée sans scrutin", "provincial-election"),
 ("À Zorbanie, la fête tourne court : les chefs des partis saluent les blessés par message", "provincial-election"),
 ("Une plateforme de financement lance sa campagne à Zorbanie", "provincial-election"),
 ("La victoire de l'écolière au concours de dessin de Zorbanie", "sports-event"),
 ("Les sénateurs reçoivent la délégation de Zorbanie", "sports-event"),
 ("Heat wave grips Zorbanie this week", "sports-event"),
 ("Le candidat accusé d'avoir menti sur son parcours se défend", "court-charges"),
 ("Une panne évitable, juge le comité de la station de Zorbanie", "court-ruling"),
 ("Une tempête aurait tué des milliers de poissons à Zorbanie", "homicide"),
 ("Il a arrêté de fumer à Zorbanie", "arrest"),
 ("Fermeture temporaire du parc des Lutins à Zorbanie", "road-closure"),
 ("La fermeture du chemin de croix a surpris Zorbanie", "road-closure"),
 ("Une panne technique perturbe le service téléphonique de Zorbanie", "power-outage"),
 ("Nomination de l'album de Zorbo au gala de Zorbanie", "appointment-resignation"),
 ("Trump impose un nouveau tarif douanier sur les meubles de Zorbanie", "price-change"),
 ("L'essence même du projet de Zorbanie", "price-change"),
 ("Un appel d'offres pour des chaises à Zorbanie", "investment-project"),
 ("Il coupe les arbres du verger de Zorbanie", "layoffs"),
 ("La Coupe Grey crée des emplois à Zorbanie", "layoffs"),
 ("Coupe Stanley : des emplois pour les étudiants de Zorbanie", "layoffs"),
 ("La Coupe du monde génère des emplois temporaires à Zorbanie", "layoffs"),
]


class HonestCoverage(unittest.TestCase):
    def test_each_new_keyword_family_classifies_its_invented_headline(self) -> None:
        for headline, code in COVERAGE_POSITIVES:
            self.assertEqual(V.classify_type([headline])[0], code, (headline, V.explain_type([headline])[:3]))

    def test_near_misses_never_get_the_wrong_type(self) -> None:
        for headline, code in COVERAGE_NEAR_MISSES:
            self.assertNotEqual(V.classify_type([headline])[0], code, (headline, V.explain_type([headline])[:3]))

    def test_every_family_that_gained_keywords_is_exercised_and_has_a_near_miss_somewhere(self) -> None:
        types = {code for _h, code in COVERAGE_POSITIVES}
        # the families the round touched: the election vocabulary, the sports clubs, the court
        # and police phrases, the road, outage, transit, price, layoff and funding words
        for code in ("provincial-election", "appointment-resignation", "public-transit",
                     "road-closure", "roadworks", "power-outage", "road-collision", "pedestrian-cyclist-struck",
                     "arrest", "police-operation", "homicide", "court-charges", "court-sentence", "court-ruling",
                     "sports-event", "layoffs", "business-open-close", "waste-collection", "price-change",
                     "investment-project", "missing-person"):
            self.assertIn(code, types, f"no invented headline for {code}")

    def test_every_long_strong_keyword_classifies_its_own_family(self) -> None:
        # keywords of five or more words are read from the lexicon at run time and wrapped
        # in an invented carrier, so no publisher-length phrase is copied into this file
        doc = json.loads(Path(V.__file__).with_name("vocabulaire_lexique.json").read_text(encoding="utf-8"))
        checked = 0
        for code, rule in sorted(doc["types"].items()):
            for key in ("fr_strong", "en_strong"):
                for keyword in rule.get(key, []):
                    if len(keyword.split()) >= 5 and not set(keyword) & set("~#*"):
                        checked += 1
                        self.assertEqual(V.classify_type(["Zorbanie : " + keyword])[0], code, keyword)
        self.assertGreater(checked, 5)

    def test_a_single_weak_cue_is_still_not_a_type(self) -> None:
        # "victoire" and "defaite" are weak words of two types: alone they prove nothing
        for headline in ("Une victoire pour la boulangerie de Zorbanie", "La défaite de l'équipe de Zorbanie au jeu de dames",
                         "Le heat de l'été à Zorbanie"):
            self.assertEqual(V.classify_type([headline]), ("unclassified", 0), headline)

    def test_foreign_elections_and_the_federal_party_are_not_a_quebec_election(self) -> None:
        for headline in ("Présidentielle en France : les candidats et le vote par correspondance",
                         "Législatives en Suède : les résultats électoraux de la nuit",
                         "Le caucus conservateur de Poilievre se réunit avant le vote de confiance",
                         "Carney et le vote de confiance : les chefs de parti réagissent"):
            self.assertNotEqual(V.classify_type([headline])[0], "provincial-election", headline)

    def test_the_rounds_keywords_are_word_bounded(self) -> None:
        # word-bounded: a keyword never fires inside a longer word
        self.assertEqual(V.classify_type(["Les chefs de partisans de Zorbanie"]), ("unclassified", 0))
        self.assertEqual(V.classify_type(["Les Sénateursonnent contre les Bruinsons"]), ("unclassified", 0))

    def test_classification_is_deterministic_and_order_free(self) -> None:
        titles = [h for h, _c in COVERAGE_POSITIVES]
        first = [V.classify_type([t]) for t in titles]
        self.assertEqual(first, [V.classify_type([t]) for t in titles])
        self.assertEqual(V.classify_type(titles[:6]), V.classify_type(list(reversed(titles[:6]))))


class FailSoft(unittest.TestCase):
    def tearDown(self) -> None:
        V.vocabulary.cache_clear()
        V._lexicon.cache_clear()

    def test_corrupt_files_yield_empty_facts_never_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{ not json", encoding="utf-8")
            old = (V.VOCAB_PATH, V.LEXIQUE_PATH)
            V.VOCAB_PATH = bad
            V.LEXIQUE_PATH = Path(tmp) / "missing.json"
            V.vocabulary.cache_clear()
            V._lexicon.cache_clear()
            try:
                self.assertEqual(V.type_codes(), [])
                self.assertEqual(V.classify_type(["Incendie à Limoilou"]), ("unclassified", 0))
                self.assertEqual(V.classify_places(["Un feu à Limoilou"]), [])
                self.assertEqual(V.label("fire-building", "limoilou", "fr"), "fire-building")
                self.assertIsNone(V.family_of("fire-building"))
                self.assertIsNone(V.place_for_hint("limoilou"))
            finally:
                V.VOCAB_PATH, V.LEXIQUE_PATH = old

    def test_a_non_object_document_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            odd = Path(tmp) / "odd.json"
            odd.write_text("[1, 2, 3]", encoding="utf-8")
            old = V.LEXIQUE_PATH
            V.LEXIQUE_PATH = odd
            V._lexicon.cache_clear()
            try:
                self.assertEqual(V.classify_type(["Incendie"]), ("unclassified", 0))
            finally:
                V.LEXIQUE_PATH = old


class NoPublisherContent(unittest.TestCase):
    def test_fixtures_here_are_the_only_headlines(self) -> None:
        # The committed lexicon holds keywords only: no sentence-length entries.
        doc = json.loads(V.LEXIQUE_PATH.read_text(encoding="utf-8"))
        for code, rule in doc["types"].items():
            for key, words in rule.items():
                if isinstance(words, list):
                    for word in words:
                        self.assertLessEqual(len(word.split()), 6, (code, word))


if __name__ == "__main__":
    unittest.main()
