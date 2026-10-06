"""Official-record anchors and the roadworks view (docs/EVENTS.md section 10).

INVENTED fixtures only: no publisher text, no real headline. Street names are
real kinds of names (the matcher must handle them) used in made-up sentences.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import anchors
import composants
import depart
import i18n

ROOT = Path(__file__).resolve().parents[1]


def member(item_id: str, title: str, published: str = "2026-10-05T14:00:00Z", **extra) -> dict:
    m = {"item_id": item_id, "title": title, "summary": extra.pop("summary", ""),
         "published_at": published, "first_seen": "2026-10-05T15:00:00Z"}
    m.update(extra)
    return m


def work(event_id: str, names, start="2026-10-01T10:00:00Z", end="2026-10-30T10:00:00Z", impact="all-lanes-closed",
         status="active", update="2026-10-04T00:00:00Z") -> dict:
    return {"event_id": event_id, "road_names": list(names), "start_date": start, "end_date": end,
            "vehicle_impact": impact, "event_status": status, "event_type": "work-zone", "update_date": update}


class RoadNames(unittest.TestCase):
    def test_parse_declared_names(self):
        cases = {
            "Rue St-Louis": ("rue", ("saint", "louis")),
            "Rue Ste-Anne": ("rue", ("sainte", "anne")),
            "Avenue de l'Amiral": ("avenue", ("amiral",)),
            "Boulevard Charest Est": ("boulevard", ("charest",)),
            "Rue Jeanne-d'Arc": ("rue", ("jeanne", "arc")),
            "Rue Mgr-Gauvreau": ("rue", ("monseigneur", "gauvreau")),
            "RTE-175": ("route", ("175",)),
            "AUT-73": ("autoroute", ("73",)),
            "3e Avenue": ("avenue", ("3e",)),
            "76e Rue O": ("rue", ("76e",)),
            "Grande Allée E": (None, ("grande", "allee")),
            "Saint-Jean Street": ("rue", ("saint", "jean")),
            "Côte du Palais": ("cote", ("palais",)),
        }
        for raw, expected in cases.items():
            self.assertEqual(anchors.parse_road(raw), expected, raw)
        for junk in ("", None, "   ", "Parcours", 42, "Rue"):
            self.assertIsNone(anchors.parse_road(junk), repr(junk))

    def _hit(self, declared: str, text: str) -> bool:
        key = anchors.parse_road(declared)
        self.assertIsNotNone(key, declared)
        return anchors._road_matches(key, *anchors.mentions(text))

    def test_saint_variants_and_prefixes_meet(self):
        self.assertTrue(self._hit("Rue St-Louis", "Fermeture de la rue Saint-Louis jusqu'à vendredi."))
        self.assertTrue(self._hit("Rue St-Louis", "Closure on St. Louis Street"))
        self.assertTrue(self._hit("Rue St-Louis", "Fermeture de la rue St-Louis"))
        self.assertTrue(self._hit("Rue Ste-Anne", "travaux rue Sainte-Anne"))
        self.assertTrue(self._hit("Boulevard Charest Est", "le boul. Charest ferme"))
        self.assertTrue(self._hit("Boulevard Charest Est", "on Charest Boulevard"))
        self.assertTrue(self._hit("Avenue de l'Amiral", "avenue de l’Amiral bloquée"))
        self.assertTrue(self._hit("Rue de la Couronne", "rue Couronne"))
        self.assertTrue(self._hit("Chemin des Quatre-Bourgeois", "sur le chemin des Quatre-Bourgeois"))
        self.assertTrue(self._hit("Avenue Holland", "Holland Avenue is closed"))
        self.assertTrue(self._hit("Rue Mgr-Gauvreau", "la rue Monseigneur-Gauvreau"))

    def test_route_numbers_and_ordinals(self):
        self.assertTrue(self._hit("RTE-175", "la route 175 est fermée"))
        self.assertFalse(self._hit("RTE-175", "l'autoroute 175"))
        self.assertFalse(self._hit("RTE-175", "175 personnes"))
        self.assertTrue(self._hit("3e Avenue", "la 3e Avenue est fermée"))
        self.assertTrue(self._hit("3e Avenue", "closed on 3rd Avenue"))
        self.assertFalse(self._hit("3e Avenue", "la 3e Rue est fermée"))          # same ordinal, other kind of way
        self.assertFalse(self._hit("3e Avenue", "la 13e Avenue"))
        self.assertFalse(self._hit("3e Avenue", "pour la 3e fois, avenue de la Paix"))

    def test_near_misses_are_not_matches(self):
        # a different saint
        self.assertFalse(self._hit("Rue Saint-Joseph", "la rue Saint-Jean est fermée"))
        # the name is only a prefix of a longer compound
        self.assertFalse(self._hit("Rue St-Jean", "la rue Saint-Jean-Baptiste"))
        self.assertFalse(self._hit("Rue St-Louis", "la rue Saint-Louis-de-Gonzague"))
        # same name, another kind of way
        self.assertFalse(self._hit("Rue Holland", "l'avenue Holland est fermée"))
        self.assertFalse(self._hit("Avenue Holland", "Holland Street"))
        # no type word next to the name: a person, a parish, a saint
        self.assertFalse(self._hit("Rue St-Louis", "le roi Saint-Louis a visité"))
        self.assertFalse(self._hit("Boulevard Charest", "Jean Charest a déclaré"))
        # a comma or sentence end breaks the mention
        self.assertFalse(self._hit("Rue Holland", "sur la rue, Holland a dit"))
        self.assertFalse(self._hit("Avenue Bardy", "rue Saint-Jean, avenue Holland"))
        # sentence end between the name and an English type word
        self.assertFalse(self._hit("Rue Bardy", "ferme la rue Saint-Jean. Street Bardy"))
        # the name already has its own type word: "Avenue" that follows is a new one
        self.assertFalse(self._hit("Avenue Saint-Jean", "rue Saint-Jean avenue Holland"))
        # "St" is never read as "Street"
        self.assertFalse(self._hit("Rue Charest", "Charest St"))
        # a one-word name with no type word is not matchable
        self.assertIsNone(anchors.parse_road("Samuel"))

    def test_type_less_declared_names_need_the_whole_name(self):
        self.assertTrue(self._hit("Grande Allée E", "travaux sur la Grande Allée"))
        self.assertFalse(self._hit("Grande Allée E", "la grande salle de l'allée"))
        self.assertFalse(self._hit("Grande Allée E", "une grande fête"))


class Rules(unittest.TestCase):
    def test_official_items_by_id_and_by_kind(self):
        ms = [member("a1", "x"), member("a2", "y", source_kind="official"), member("a3", "z")]
        out = anchors.find_anchors(ms, official_items=["a3", {"id": "zz"}], event_type="unclassified", vocab_places=[])
        self.assertEqual([(a["type"], a["ref"]) for a in out],
                         [("official_item", "a2"), ("official_item", "a3")])
        for a in out:
            self.assertEqual(a["status"], "linked_by_rule")
            self.assertEqual(a["rule"], "membership-official-source")
            self.assertEqual(set(a), {"type", "ref", "rule", "status"})

    def test_no_anchor_is_an_empty_list(self):
        self.assertEqual(anchors.find_anchors([member("a1", "x")], event_type="unclassified"), [])
        self.assertEqual(anchors.find_anchors([]), [])
        self.assertEqual(anchors.find_anchors(None), [])

    def test_roadwork_needs_type_name_and_dates(self):
        ms = [member("a1", "Fermeture de la rue Saint-Louis ce mois-ci", published="2026-10-05T14:00:00Z")]
        rw = {"events": [
            work("w-hit", ["Rue St-Louis"]),
            work("w-before", ["Rue St-Louis"], start="2026-08-01T00:00:00Z", end="2026-09-01T00:00:00Z"),
            work("w-after", ["Rue St-Louis"], start="2026-11-01T00:00:00Z", end="2026-12-01T00:00:00Z"),
            work("w-other-street", ["Rue Saint-Joseph"]),
            work("w-other-kind", ["Avenue St-Louis"]),
            work("w-compound", ["Rue St-Louis-de-Gonzague"]),
            work("w-nodates", ["Rue St-Louis"], start=None, end=None),
            work("w-second-name", ["Rue Bardy", "Rue St-Louis"]),
        ]}
        out = anchors.find_anchors(ms, roadworks=rw, event_type="roadworks", vocab_places=["quebec-city"])
        self.assertEqual([(a["type"], a["ref"]) for a in out],
                         [("roadwork", "w-hit"), ("roadwork", "w-second-name")])
        self.assertEqual({a["rule"] for a in out}, {"roadwork-name-dates-v1"})
        # not in the roads family: no pointer, however well the street matches
        for kind in ("fire-building", "road-collision", "unclassified"):
            self.assertEqual(anchors.find_anchors(ms, roadworks=rw, event_type=kind, vocab_places=[]), [], kind)
        # a bare list of declarations works too
        self.assertEqual(len(anchors.find_anchors(ms, roadworks=rw["events"], event_type="road-closure")), 2)

    def test_a_road_named_as_an_alternative_is_not_the_subject(self):
        rw = [work("w-subject", ["Boulevard Charest"]), work("w-detour", ["Chemin Ste-Foy"]), work("w-abbr", ["Avenue Bardy"])]
        ms = [member("a1", "Entraves dans le boulevard Charest",
                     summary="Les travaux touchent le boul. Charest. Les automobilistes sont invités à privilégier le chemin Sainte-Foy "
                             "ou à éviter le secteur. L'av. Bardy reste ouverte.")]
        out = anchors.find_anchors(ms, roadworks=rw, event_type="roadworks")
        self.assertEqual([a["ref"] for a in out], ["w-abbr", "w-subject"])      # w-detour only appears in a detour sentence
        # English cue
        en = [member("e1", "Charest Boulevard closed", summary="Drivers should use Ste-Foy Road instead.")]
        self.assertEqual([a["ref"] for a in anchors.find_anchors(en, roadworks=rw, event_type="roadworks")], ["w-subject"])
        # the same road named in a plain sentence of another member counts
        both = ms + [member("a2", "Le chemin Sainte-Foy est fermé")]
        self.assertIn("w-detour", [a["ref"] for a in anchors.find_anchors(both, roadworks=rw, event_type="roadworks")])

    def test_cross_streets_that_bound_a_closure_are_not_the_closed_road(self):
        rw = [work("w-closed", ["Avenue Bardy"]), work("w-bound-a", ["Rue Holland"]), work("w-bound-b", ["Avenue Cartier"])]
        ms = [member("a1", "Fermeture de l'avenue Bardy, entre la rue Holland et l'avenue Cartier pour des travaux")]
        self.assertEqual([a["ref"] for a in anchors.find_anchors(ms, roadworks=rw, event_type="roadworks")], ["w-closed"])
        en = [member("e1", "Holland Street closed between Bardy Avenue and Cartier Avenue")]
        self.assertEqual([a["ref"] for a in anchors.find_anchors(en, roadworks=rw, event_type="roadworks")], ["w-bound-a"])

    def test_a_route_number_alone_does_not_anchor_a_declaration_that_names_other_roads(self):
        rw = [work("w-both", ["RTE-138", "Boulevard Wilfrid-Hamel"]), work("w-route", ["RTE-138"]),
              work("w-both-named", ["RTE-138", "Boulevard Sainte-Anne"])]
        ms = [member("a1", "La route 138 : fermeture de la bretelle du boulevard Sainte-Anne")]
        self.assertEqual([a["ref"] for a in anchors.find_anchors(ms, roadworks=rw, event_type="roadworks")],
                         ["w-both-named", "w-route"])

    def test_overall_cap_keeps_the_road_most_members_name(self):
        names = ["Alpha", "Bravo", "Chopin", "Delta", "Echo", "Fortin", "Gamma", "Hotel", "Indigo", "Juliette",
                 "Kilo", "Lima", "Mike", "November", "Zulu"]
        ms = [member("a1", "Travaux : " + ", ".join(f"rue {n}" for n in names)),
              member("a2", "La rue Zulu est bloquée", published="2026-10-05T16:00:00Z")]
        events = [work(f"w-{n.lower()}", [f"Rue {n}"]) for n in names]
        out = anchors.find_anchors(ms, roadworks=events, event_type="roadworks")
        refs = [a["ref"] for a in out]
        self.assertEqual(len(refs), anchors.MAX_ROADWORK_ANCHORS)
        self.assertIn("w-zulu", refs)                  # named by two members: kept although last in id order
        self.assertEqual(refs, sorted(refs))

    def test_roadwork_dates_use_the_members_span(self):
        rw = [work("w", ["Rue Bardy"], start="2026-10-10T00:00:00Z", end="2026-10-20T00:00:00Z")]
        early = [member("a", "rue Bardy fermée", published="2026-10-05T10:00:00Z")]
        late = [member("a", "rue Bardy fermée", published="2026-10-15T10:00:00Z")]
        both = early + [member("b", "rue Bardy toujours fermée", published="2026-10-12T10:00:00Z")]
        self.assertEqual(anchors.find_anchors(early, roadworks=rw, event_type="roadworks"), [])
        self.assertEqual(len(anchors.find_anchors(late, roadworks=rw, event_type="roadworks")), 1)
        self.assertEqual(len(anchors.find_anchors(both, roadworks=rw, event_type="roadworks")), 1)
        # a member with no usable date gives no span: no anchor, never a guess
        undated = [{"item_id": "z", "title": "rue Bardy fermée", "published_at": "0001-01-01T00:00:00Z"}]
        self.assertEqual(anchors.find_anchors(undated, roadworks=rw, event_type="roadworks"), [])

    def test_per_road_cap_and_order_are_deterministic(self):
        ms = [member("a1", "boulevard Charest fermé", published="2026-10-05T14:00:00Z")]
        events = [work(f"w{i:02d}", ["Boulevard Charest E"], impact="some-lanes-closed") for i in range(20)]
        events.append(work("w-closed", ["Boulevard Charest"], impact="all-lanes-closed"))
        out = anchors.find_anchors(ms, roadworks={"events": events}, event_type="roadworks")
        self.assertEqual(len(out), anchors.MAX_PER_ROAD)
        self.assertEqual(out, sorted(out, key=lambda a: (a["type"], a["ref"])))
        self.assertIn("w-closed", [a["ref"] for a in out])         # most restrictive survives the cap
        again = anchors.find_anchors(ms, roadworks={"events": list(reversed(events))}, event_type="roadworks")
        self.assertEqual(out, again)
        # a busy street does not hide another road
        ms2 = [member("a1", "boulevard Charest et rue Bardy fermés")]
        more = events + [work("w-bardy", ["Rue Bardy"], impact="all-lanes-open")]
        refs = [a["ref"] for a in anchors.find_anchors(ms2, roadworks=more, event_type="roadworks")]
        self.assertIn("w-bardy", refs)
        self.assertEqual(len(refs), anchors.MAX_PER_ROAD + 1)

    def test_consultation_needs_place_and_a_shared_word(self):
        civic = {"events": [
            {"event_id": "c-hit", "title": "Jardins communautaires partagés – Arrondissement de La Cité-Limoilou",
             "mode_text": "Consultation publique – Quartier du Vieux-Québec – septembre 2026"},
            {"event_id": "c-other-place", "title": "Jardins communautaires partagés",
             "mode_text": "Consultation publique – Quartier Saint-Roch"},
            {"event_id": "c-other-topic", "title": "Piste cyclable projetée",
             "mode_text": "Consultation publique – Quartier du Vieux-Québec"},
            {"event_id": "c-no-place", "title": "Jardins communautaires partagés", "mode_text": "Consultation publique"},
        ]}
        ms = [member("a1", "Jardins communautaires : consultation publique dans le Vieux-Québec")]
        out = anchors.find_anchors(ms, consultations=civic, event_type="public-consultation", vocab_places=["vieux-quebec"])
        self.assertEqual([(a["type"], a["ref"], a["rule"]) for a in out],
                         [("consultation", "c-hit", "consultation-place-topic-v1")])
        # wrong type: nothing
        self.assertEqual(anchors.find_anchors(ms, consultations=civic, event_type="municipal-bylaw", vocab_places=["vieux-quebec"]), [])
        # a scope place alone is not a place match; three shared distinctive words are needed then
        weak = anchors.find_anchors(ms, consultations=civic, event_type="public-consultation", vocab_places=["quebec-city"])
        self.assertEqual([a["ref"] for a in weak if a["type"] == "consultation"], [])
        broad = [member("a2", "Jardins communautaires partagés : la Ville tient une consultation")]
        out = anchors.find_anchors(broad, consultations=civic, event_type="public-consultation", vocab_places=["quebec-city"])
        # c-other-place names a quartier the lexicon cannot resolve: an unresolved sector is no place match
        self.assertEqual([a["ref"] for a in out], ["c-no-place"])
        # an English text shares no word with a French title: no anchor (precision over recall)
        en = [member("e1", "Allotment plots: public hearing in Old Quebec")]
        self.assertEqual(anchors.find_anchors(en, consultations=civic, event_type="public-consultation", vocab_places=["vieux-quebec"]), [])

    def test_outage_needs_hydro_outage_item_place_and_time(self):
        hydro = [
            {"id": "h-hit", "institution": "hydro-quebec", "title": "Panne d'électricité dans Limoilou", "published_at": "2026-10-05T16:00:00Z"},
            {"id": "h-late", "institution": "hydro-quebec", "title": "Panne d'électricité dans Limoilou", "published_at": "2026-10-20T16:00:00Z"},
            {"id": "h-place", "institution": "hydro-quebec", "title": "Panne d'électricité à Vanier, Québec", "published_at": "2026-10-05T16:00:00Z"},
            {"id": "h-topic", "institution": "hydro-quebec", "title": "Limoilou : nouveau poste de distribution", "published_at": "2026-10-05T16:00:00Z"},
            {"id": "h-not-hydro", "institution": "ville-quebec", "title": "Panne d'électricité dans Limoilou", "published_at": "2026-10-05T16:00:00Z"},
            {"id": "a1", "institution": "hydro-quebec", "title": "Panne d'électricité dans Limoilou", "published_at": "2026-10-05T16:00:00Z"},
        ]
        ms = [member("a1", "Panne d'électricité à Limoilou", published="2026-10-05T14:00:00Z")]
        out = anchors.find_anchors(ms, outages=hydro, event_type="power-outage", vocab_places=["limoilou"])
        self.assertEqual([(a["type"], a["ref"]) for a in out], [("outage", "h-hit")])
        self.assertEqual(anchors.find_anchors(ms, outages=hydro, event_type="fire-building", vocab_places=["limoilou"]), [])
        self.assertEqual(anchors.find_anchors(ms, outages=hydro, event_type="power-outage", vocab_places=["quebec-city"]), [])

    def test_type_and_places_are_derived_when_not_given(self):
        ms = [member("a1", "Fermeture de la rue Saint-Louis : travaux routiers dans Limoilou")]
        rw = {"events": [work("w", ["Rue St-Louis"])]}
        out = anchors.find_anchors(ms, roadworks=rw)
        self.assertEqual([(a["type"], a["ref"]) for a in out], [("roadwork", "w")])

    def test_edition_seal_helper(self):
        self.assertEqual(anchors.edition_seal_anchor(57),
                         {"type": "edition_seal", "ref": "57", "rule": "edition-presence", "status": "linked_by_rule"})

    def test_rows_are_unique_and_sorted_across_types(self):
        ms = [member("o2", "rue Bardy fermée", source_kind="official"), member("o1", "rue Bardy", source_kind="official")]
        rw = [work("w9", ["Rue Bardy"]), work("w1", ["Rue Bardy"])]
        out = anchors.find_anchors(ms, official_items=["o1"], roadworks=rw, event_type="roadworks")
        keys = [(a["type"], a["ref"]) for a in out]
        self.assertEqual(keys, [("official_item", "o1"), ("official_item", "o2"), ("roadwork", "w1"), ("roadwork", "w9")])

    def test_garbage_inputs_never_raise(self):
        junk = [None, 5, "x", [None, 3, "y"], {"events": "nope"}, {"events": [None, 1, {"road_names": 3}]}]
        ms = [member("a1", "rue Bardy fermée"), "junk", None, {"title": None}]
        for j in junk:
            out = anchors.find_anchors(ms, official_items=j if isinstance(j, (list, tuple)) else (),
                                       roadworks=j, consultations=j, outages=j if isinstance(j, (list, tuple)) else (),
                                       event_type="roadworks")
            self.assertIsInstance(out, list)

    def test_a_faulty_rule_is_diagnosed_and_the_others_still_run(self):
        original = anchors._roadwork_rule

        def boom(*a, **k):
            raise ValueError("invented fault")

        anchors._roadwork_rule = boom
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                out = anchors.find_anchors([member("o1", "x", source_kind="official")], event_type="roadworks")
        finally:
            anchors._roadwork_rule = original
        self.assertEqual([a["type"] for a in out], ["official_item"])
        self.assertIn("roadwork-name-dates-v1 skipped", buf.getvalue())

    def test_anchor_is_a_pointer_not_a_confirmation(self):
        doc = (ROOT / "scripts" / "anchors.py").read_text(encoding="utf-8")
        self.assertIn("official_source_is_confirmation`", doc)
        self.assertIn("pointer", doc)
        self.assertNotIn("official_source_is_confirmation\": True", doc)

    def test_output_is_identical_under_other_hash_seeds(self):
        code = (
            "import sys, json; sys.path.insert(0, 'scripts'); import anchors\n"
            "ms=[{'item_id':'a1','title':'rue Saint-Louis fermee, travaux routiers','published_at':'2026-10-05T14:00:00Z'},"
            "{'item_id':'o1','source_kind':'official','title':'x'}]\n"
            "rw={'events':[{'event_id':'w%d'%i,'road_names':['Rue St-Louis'],'start_date':'2026-10-01T00:00:00Z',"
            "'end_date':'2026-11-01T00:00:00Z','vehicle_impact':'some-lanes-closed'} for i in range(12)]}\n"
            "print(json.dumps(anchors.find_anchors(ms, roadworks=rw), sort_keys=True))\n")
        outs = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8")
            res = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            outs.add(res.stdout)
        self.assertEqual(len(outs), 1)
        self.assertIn("roadwork", outs.pop())


STORE = {
    "fetched_at": "2026-10-06T02:53:47.110034+00:00", "institution_name": "Ville de Québec",
    "dataset_url": "https://data.example/set",
    "events": [
        work("b", ["Rue B", "Rue C"], impact="some-lanes-closed", update="2026-10-05T00:00:00Z"),
        work("a", ["Rue A"], impact="all-lanes-closed", status="planned", update="2026-10-01T00:00:00Z"),
        work("c", ["Rue D"], impact="all-lanes-closed", status="pending", update="2026-10-04T00:00:00Z"),
        work("d", [], impact="all-lanes-open"),
        work("e", ["Rue E"], impact="all-lanes-closed", update="2026-10-03T00:00:00Z"),
        {"road_names": ["no id"], "vehicle_impact": "all-lanes-closed"},
        "junk",
    ],
}


class RoadworksView(unittest.TestCase):
    def test_collection_newer_than_the_edition_is_normal(self):
        # the hourly lane refreshed 2 h 50 min after the edition's collection clock
        view = anchors.roadworks_view(STORE, "2026-10-06T00:03:00+00:00")
        self.assertFalse(view["stale"])
        self.assertFalse(view["clock_skew"])
        self.assertEqual(view["age_seconds"], 0)
        self.assertEqual(view["relation_to_edition"], "newer")
        for lang in ("fr", "en"):
            html = composants.roadworks_block(view, lang)
            for bad in ("plus de six heures", "more than six hours", "Horodatage incohérent", "Inconsistent timestamp"):
                self.assertNotIn(bad, html)
        # even a day newer
        far = anchors.roadworks_view(STORE, "2026-10-05T00:00:00Z")
        self.assertFalse(far["stale"] or far["clock_skew"])
        self.assertEqual(far["age_seconds"], 0)

    def test_collection_older_than_the_edition_is_measured_honestly(self):
        within = anchors.roadworks_view(STORE, "2026-10-06T07:53:47Z")           # 5 h older
        self.assertFalse(within["stale"])
        self.assertEqual(within["age_seconds"], 5 * 3600)
        self.assertEqual(within["relation_to_edition"], "older")
        edge = anchors.roadworks_view(STORE, "2026-10-06T08:53:47.110034Z")      # exactly 6 h
        self.assertFalse(edge["stale"])
        old = anchors.roadworks_view(STORE, "2026-10-06T09:00:00Z")
        self.assertTrue(old["stale"])
        self.assertFalse(old["clock_skew"])
        self.assertEqual(old["age_seconds"], 21973)
        self.assertIn("plus de six heures", composants.roadworks_block(old, "fr"))
        same = anchors.roadworks_view(dict(STORE, fetched_at="2026-10-06T02:00:00Z"), "2026-10-06T02:00:00Z")
        self.assertEqual((same["relation_to_edition"], same["age_seconds"], same["stale"]), ("same", 0, False))

    def test_unknown_clocks_are_unknown_never_stale(self):
        for clock in (None, "", "garbage"):
            v = anchors.roadworks_view(STORE, clock)
            self.assertEqual((v["stale"], v["clock_skew"], v["age_seconds"], v["relation_to_edition"]),
                             (False, False, None, "unknown"), repr(clock))
            self.assertEqual(v["collected_at"], STORE["fetched_at"])
        for bad in (None, {}, [], "x", {"events": "nope"}, {"fetched_at": 5, "events": [1, 2]}):
            v = anchors.roadworks_view(bad, "2026-10-06T00:03:00Z")
            self.assertEqual((v["total"], v["rows"], v["collected_at"]), (0, [], ""))
            self.assertFalse(v["stale"] or v["clock_skew"])
            self.assertIsInstance(composants.roadworks_block(v, "fr"), str)

    def test_counts_order_and_local_times(self):
        v = anchors.roadworks_view(STORE, "2026-10-06T00:03:00Z")
        self.assertEqual((v["total"], v["active"], v["closed"], v["closed_active"], v["planned"]), (5, 3, 3, 1, 2))
        self.assertEqual([r["id"] for r in v["rows"]], ["c", "e", "a", "b", "d"])   # severity, newest update, id
        # 02:53 UTC is the evening before in Quebec City (daylight time, UTC-4)
        self.assertEqual(v["collected_local"], "2026-10-05 22:53")
        self.assertEqual(v["edition_local"], "2026-10-05 20:03")
        self.assertEqual(v["edition_clock"], "2026-10-06T00:03:00Z")
        self.assertEqual(v["institution_name"], "Ville de Québec")
        self.assertEqual(v["dataset_url"], "https://data.example/set")
        # winter time is UTC-5
        winter = anchors.roadworks_view(dict(STORE, fetched_at="2026-12-06T02:53:00Z"), "2026-12-06T00:03:00Z")
        self.assertEqual(winter["collected_local"], "2026-12-05 21:53")

    def test_rows_follow_the_departure_screens_order(self):
        events = [e for e in STORE["events"] if isinstance(e, dict) and e.get("event_id")]
        expected = [e["event_id"] for e in depart._ordered_events(events)]
        v = anchors.roadworks_view(STORE, "2026-10-06T00:03:00Z", limit=50)
        self.assertEqual([r["id"] for r in v["rows"]], expected)
        self.assertEqual(anchors.RW_SEVERITY, depart.brief.RW_SEVERITY)
        self.assertEqual([r["id"] for r in anchors.roadworks_view(STORE, "2026-10-06T00:03:00Z", limit=2)["rows"]], expected[:2])

    def test_view_renders_in_the_kit_and_is_json_clean(self):
        v = anchors.roadworks_view(STORE, "2026-10-06T00:03:00Z")
        json.dumps(v)
        self.assertFalse(any(isinstance(x, float) for x in v.values()))
        fr = composants.roadworks_block(v, "fr")
        self.assertIn("5 entraves déclarées, dont 3 avec toutes les voies fermées", fr)
        self.assertIn("Collecte du 5 oct. 2026, 22 h 53", fr)
        self.assertIn("All lanes closed", composants.roadworks_block(v, "en"))

    def test_render_only_lane_needs_no_edition_data(self):
        # the hourly lane has the declarations and a clock; nothing else
        v = anchors.roadworks_view(STORE, STORE["fetched_at"])
        self.assertEqual((v["relation_to_edition"], v["age_seconds"]), ("same", 0))


class LiveData(unittest.TestCase):
    def test_live_roadworks_view_if_present(self):
        path = ROOT / "data" / "roadworks" / "latest_roadworks.json"
        if not path.exists():
            self.skipTest("no local roadworks store")
        doc = json.loads(path.read_text(encoding="utf-8"))
        v = anchors.roadworks_view(doc, "2000-01-01T00:00:00Z")
        self.assertFalse(v["stale"] or v["clock_skew"])
        self.assertEqual(v["total"], len([e for e in doc.get("events", []) if e.get("event_id")]))


if __name__ == "__main__":
    unittest.main()
