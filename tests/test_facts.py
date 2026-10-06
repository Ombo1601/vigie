"""Facts v1 (docs/EVENTS.md section 9): typed slots, integers only, attributed,
never reconciled, no private persons. Every fixture is INVENTED text.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import harness
import facts

ROOT = Path(__file__).resolve().parents[1]


def item(iid: str, title: str, summary: str = "", *, inst: str = "inst-a", published: str = "2026-10-05T12:00:00+00:00") -> dict:
    return {"id": iid, "title": title, "summary": summary, "institution": inst,
            "source_id": inst, "published_at": published, "language": "fr"}


def atoms(title: str, summary: str = "", **kw) -> list[tuple]:
    out = facts.extract_item(item("x", title, summary, **kw))
    return [(a["kind"], a["unit"], a["subject"], a["value"]) for a in out]


def counts(title: str, summary: str = "") -> dict[str, int]:
    return {a[1]: a[3] for a in atoms(title, summary) if a[0] == "count"}


class CountFacts(unittest.TestCase):
    def test_french_person_counts(self) -> None:
        got = counts("Collision sur la rue Gamma : trois morts et 5 blessés, deux personnes arrêtées")
        self.assertEqual(got["persons_dead"], 3)
        self.assertEqual(got["persons_injured"], 5)
        self.assertEqual(got["persons_arrested"], 2)

    def test_english_person_counts(self) -> None:
        got = counts("Two dead, 4 people injured in crash", "Police say 3 suspects were arrested.")
        self.assertEqual(got["persons_dead"], 2)
        self.assertEqual(got["persons_injured"], 4)
        self.assertEqual(got["persons_arrested"], 3)

    def test_arrest_noun_forms(self) -> None:
        self.assertEqual(counts("Cinq arrestations dans un entrepôt de Delta")["persons_arrested"], 5)
        self.assertEqual(counts("Officers arrested 7 men near the old pier")["persons_arrested"], 7)

    def test_housing_units_through_enrich(self) -> None:
        self.assertEqual(counts("Projet de 47 logements abordables")["housing_units"], 47)
        self.assertEqual(counts("A plan for 120 housing units")["housing_units"], 120)

    def test_jobs_vehicles_buildings_customers(self) -> None:
        self.assertEqual(counts("Usine : 200 emplois menacés")["jobs"], 200)
        self.assertEqual(counts("The plant will create 85 jobs")["jobs"], 85)
        self.assertEqual(counts("Collision impliquant 4 véhicules")["vehicles"], 4)
        self.assertEqual(counts("Le feu a détruit 3 bâtiments")["buildings"], 3)
        self.assertEqual(counts("Panne : 2 500 clients sans électricité")["customers_without_power"], 2500)
        self.assertEqual(counts("12 000 customers without power after the storm")["customers_without_power"], 12000)

    def test_thousands_separators_and_decimal_commas(self) -> None:
        self.assertEqual(counts("1 250 logements promis")["housing_units"], 1250)
        self.assertEqual(counts("1 250 logements promis")["housing_units"], 1250)
        self.assertEqual(counts("1,250 housing units planned")["housing_units"], 1250)
        # A decimal is never a count: dropped, not rounded into an integer.
        self.assertEqual(counts("2,5 morts selon la rumeur"), {})

    def test_articles_are_not_counts(self) -> None:
        self.assertEqual(counts("Le conducteur a percuté une voiture"), {})
        self.assertEqual(counts("Un homme a visité une maison"), {})
        # But "un" before a person state is a one.
        self.assertEqual(counts("Une cycliste blessée")["persons_injured"], 1)

    def test_subsets_and_years_are_not_tolls(self) -> None:
        got = counts("Huit blessés, dont trois gravement blessés")
        self.assertEqual(got, {"persons_injured": 8})
        self.assertEqual(counts("Man guilty in 2031 death of a colleague"), {})

    def test_qualifiers_keep_the_hedge(self) -> None:
        out = facts.extract_item(item("q", "Près de 80 personnes blessées", ""))
        injured = [a for a in out if a["unit"] == "persons_injured"][0]
        self.assertEqual(injured["value"], 80)
        self.assertEqual(injured["qualifiers"], ["about"])
        out = facts.extract_item(item("q", "Au moins quatre décès"))
        self.assertEqual([a["qualifiers"] for a in out if a["unit"] == "persons_dead"], [["at_least"]])


class AmountFacts(unittest.TestCase):
    def amounts(self, title: str, summary: str = "") -> list[tuple]:
        return [(a[1], a[2], a[3]) for a in atoms(title, summary) if a[0] == "amount"]

    def test_cents_from_decimal_comma_and_millions(self) -> None:
        self.assertIn(("total", "cost", 3572), self.amounts("Le coût du repas grimpe à 35,72 $"))
        self.assertIn(("total", "damages", 150_000_000), self.amounts("Dommages de 1,5 million $"))
        self.assertIn(("total", "budget", 7_400_000_000), self.amounts("Coupes budgétaires de 74 M$"))
        self.assertIn(("total", "funding", 500_000_000_000), self.amounts("Financement de 5 G$ annoncé"))
        self.assertIn(("total", "fine", 8400), self.amounts("Amende de 84 $ pour stationnement"))

    def test_english_prefix_forms(self) -> None:
        self.assertIn(("total", "cost", 250_000_000), self.amounts("The project could cost $2.5 million"))
        self.assertIn(("total", "cost", 250_000_000), self.amounts("Cost: $2.5M"))
        self.assertIn(("total", "cost", 3_300_000_000_000), self.amounts("Estimated cost of $33-billion"))

    def test_multiplier_words_and_decimal_comma(self) -> None:
        # Beside a multiplier word the comma is decimal whatever its width.
        self.assertIn(("per_year", "cost", 241_700_000_000), self.amounts("Le coût atteindra 2,417 milliard $ par année"))
        self.assertIn(("total", "funding", 400_000_000_000_000), self.amounts("Aiming for $4 trillion in funding"))
        self.assertIn(("total", "cost", 500_000_000_000), self.amounts("Cost of 5 billion dollars"))
        # French "billion" is 10**12: never read as 10**9, so nothing is emitted.
        self.assertEqual(self.amounts("Un coût de 2 billions $"), [])

    def test_annual_cue_before_the_number(self) -> None:
        self.assertIn(("per_year", "savings", 64_000_000),
                      self.amounts("Des économies annuelles de près de 640 000 $ pour l'arrondissement"))

    def test_one_value_one_atom_across_title_and_summary(self) -> None:
        got = self.amounts("Le kérosène grimpe encore", "Coût de 2 millions $ pour le kérosène")
        self.assertEqual(len([a for a in got if a[2] == 200_000_000]), 1)

    def test_per_month_per_year_and_other_denominators(self) -> None:
        self.assertIn(("per_month", "rent", 145_000), self.amounts("Le loyer moyen atteint 1 450 $ par mois"))
        self.assertIn(("per_year", "salary", 6_000_000), self.amounts("Un salaire de 60 000 $ par année"))
        # A rate per day or per litre is not a "total": left out, never relabelled.
        self.assertEqual([a for a in self.amounts("Elle paiera 3100$ de plus par semaine")
                          if a[2] == 310_000], [])
        self.assertEqual([a for a in self.amounts("Le diesel coûte 1,95 $/L") if a[2] == 195], [])
        self.assertEqual([a for a in self.amounts("Le propane se détaille 1,73 $ le litre") if a[2] == 173], [])
        self.assertEqual([a for a in self.amounts("Kérosène à 4,13 dollars le gallon") if a[2] == 413], [])

    def test_percent_basis_points_need_a_cue(self) -> None:
        self.assertIn(("percent_bp", "rent", 350), self.amounts("Hausse de 3,5 % du loyer"))
        self.assertIn(("percent_bp", "change", 800), self.amounts("Une baisse de 8 % comparativement à la période précédente"))
        self.assertEqual(self.amounts("Le projet est réalisé à 70 % par une firme"), [])

    def test_foreign_dollars_are_not_cad(self) -> None:
        self.assertEqual(self.amounts("Une entente de 5 millions US$ a été signée"), [])

    def test_overflowing_digit_runs_are_dropped_not_raised(self) -> None:
        for text in ("9" * 400 + " $", "Prix de " + "9" * 400 + " $ à Delta", "$" + "9" * 400,
                     "9" * 400 + " millions $", "9" * 400 + " %", "Hausse de " + "9" * 400 + " % du loyer"):
            got = facts.extract_item(item("o", text))
            self.assertEqual([a for a in got if a["kind"] == "amount"], [], text[:20])

    def test_amounts_are_integers(self) -> None:
        for a in atoms("Coût de 2,333 millions $ et 12,5 % de taxe, 1 450,75 $ par mois"):
            self.assertNotIsInstance(a[3], float)


class DateFacts(unittest.TestCase):
    def dates(self, title: str, summary: str = "", published: str = "2026-10-05T12:00:00+00:00") -> list[str]:
        return [a[3] for a in atoms(title, summary, published=published) if a[0] == "date"]

    def test_french_forms(self) -> None:
        self.assertEqual(self.dates("Fermeture le 7 octobre 2026"), ["2026-10-07"])
        self.assertEqual(self.dates("Séance le 1er novembre"), ["2026-11-01"])
        self.assertEqual(self.dates("Début le 12 janv. 2027"), ["2027-01-12"])

    def test_english_forms(self) -> None:
        self.assertEqual(self.dates("Vote set for Oct. 20, 2026"), ["2026-10-20"])
        self.assertEqual(self.dates("Closure on 3 November"), ["2026-11-03"])
        self.assertEqual(self.dates("Hearing on March 9"), ["2027-03-09"])

    def test_ranges_and_two_dates(self) -> None:
        self.assertEqual(self.dates("Avis du 12 au 19 mars 2027"), ["2027-03-12", "2027-03-19"])
        self.assertEqual(self.dates("Ramassage du 8 novembre au 21 décembre"), ["2026-11-08", "2026-12-21"])
        self.assertEqual(self.dates("Closure Oct. 5-7"), ["2026-10-05", "2026-10-07"])

    def test_iso_dates(self) -> None:
        self.assertEqual(self.dates("Valable jusqu'au 2026-12-31"), ["2026-12-31"])

    def test_year_inferred_from_publication_not_the_clock(self) -> None:
        self.assertEqual(self.dates("Séance le 3 janvier", published="2026-12-20T00:00:00+00:00"), ["2027-01-03"])
        self.assertEqual(self.dates("Séance le 3 octobre", published="2026-10-05T00:00:00+00:00"), ["2026-10-03"])
        # No publication date and no year: nothing is guessed.
        self.assertEqual(self.dates("Séance le 3 octobre", published=""), [])

    def test_dateline_is_not_the_event_date(self) -> None:
        self.assertEqual(self.dates("Titre", "Lévis, le 14 novembre 2026 – Le comité annonce une fermeture le 21 novembre."), ["2026-11-21"])
        self.assertEqual(self.dates("Titre", "TORONTO , le 3 nov. 2026 /CNW/ -- L'agence prévient que le pont ferme le 12 novembre."), ["2026-11-12"])

    def test_english_modal_may_and_verb_march_are_not_months(self) -> None:
        for text in ("Route 9 may be closed for weeks", "Up to 2 may face charges",
                     "Hundreds of 5 march to the old harbour", "3 may be closed"):
            self.assertEqual(self.dates(text), [], text)
        # The month names themselves still read, in both languages.
        self.assertEqual(self.dates("Vote on May 3, 2026"), ["2026-05-03"])
        self.assertEqual(self.dates("Séance le 3 mai 2026"), ["2026-05-03"])
        self.assertEqual(self.dates("Séance le 3 mars 2027"), ["2027-03-03"])
        self.assertEqual(self.dates("Vote on 3 March 2027"), ["2027-03-03"])

    def test_impossible_dates_are_dropped(self) -> None:
        self.assertEqual(self.dates("Séance le 31 février 2026"), [])
        self.assertEqual(self.dates("Prix de 20 000 mars"), [])


class PlaceAndEntityFacts(unittest.TestCase):
    def test_places_from_existing_vocabulary(self) -> None:
        got = [a for a in atoms("Fermeture de la rue Gamma à Limoilou") if a[0] == "place"]
        self.assertIn(("place", "road", None, "gamma"), got)
        self.assertIn(("place", "area", None, "limoilou"), got)

    def test_a_district_is_not_also_a_road(self) -> None:
        got = [a for a in atoms("Chantier au quartier Saint-Roch") if a[0] == "place"]
        self.assertEqual(got, [("place", "area", None, "saint-roch")])

    def test_function_words_after_a_road_noun_are_not_road_names(self) -> None:
        # The junk the live edition carried: "en route à", "le pont entre",
        # "la route est", "route après", "pont au" (invented headlines).
        for title in ("Le convoi Gamma en route à la frontière", "Un pont entre les deux rives de Gamma",
                      "La route est fermée près de Gamma", "Retour sur la route après la tempête Gamma",
                      "Un pont au-dessus du ruisseau Gamma", "Une école primaire de Gamma rouvre",
                      "L'hôpital général de Gamma déborde", "Sur la route et dans les rues de Gamma",
                      "Rue de l'Église Gamma : travaux", "Le boulevard Samuel-De Gamma fermé"):
            got = [a[3] for a in atoms(title) if a[0] == "place" and a[1] == "road"]
            for junk in ("a", "au", "apres", "entre", "est", "et", "l", "primaire", "general", "samuel-de"):
                self.assertNotIn(junk, got, title)

    def test_real_road_names_and_route_numbers_are_kept(self) -> None:
        got = [a[3] for a in atoms("Collision sur l'autoroute 40 et la route 138 près du boulevard Gamma-Delta")
               if a[0] == "place" and a[1] == "road"]
        for want in ("40", "138", "gamma-delta"):
            self.assertIn(want, got)
        self.assertNotIn("4000", [a[3] for a in atoms("La route 4000 de Gamma") if a[0] == "place"])

    def test_road_token_shape_guard(self) -> None:
        for ok in ("laurentienne", "pierre-laporte", "40", "138", "rene-levesque", "saint-jean-baptiste"):
            self.assertTrue(facts.road_token_ok(ok), ok)
        for bad in ("a", "au", "apres", "entre", "est", "l", "et", "en", "un", "x9", "0", "007", "4000", "",
                    "samuel-de", "marie-de-l", "quebec", "ville", "pont", "primaire", "-gamma", "gamma-", None, 12):
            self.assertFalse(facts.road_token_ok(bad), bad)

    def test_entities_from_the_closed_list(self) -> None:
        got = {(a[1], a[3]) for a in atoms(
            "Rencontre : maire de Québec, ministre et SPVQ",
            "Hydro-Québec collabore avec Sûreté du Québec; coroner enquête.") if a[0] == "entity"}
        for want in (("office", "mayor_quebec"), ("office", "minister"), ("institution", "spvq"),
                     ("institution", "hydro-quebec"), ("institution", "sq"), ("office", "coroner")):
            self.assertIn(want, got)

    def test_other_cities_mayor_and_foreign_courts_are_not_ours(self) -> None:
        got = {a[3] for a in atoms("Le maire de Gatineau parle", "The U.S. Supreme Court rules") if a[0] == "entity"}
        self.assertNotIn("mayor", got)
        self.assertNotIn("mayor_quebec", got)
        self.assertNotIn("cour-supreme", got)

    def test_private_persons_are_never_extracted(self) -> None:
        text = "Paul Dupont, 51 ans, inculpé de l'homicide d'Anne Leclerc; son fils Marc, 14 ans, est blessé"
        out = facts.extract_item(item("p", text, "Mme Lise Roy, victime, et un mineur de 16 ans, témoin."))
        blob = json.dumps(out, ensure_ascii=False).lower()
        for name in ("dupont", "leclerc", "roy", "paul", "anne", "lise", "marc"):
            self.assertNotIn(name, blob)
        self.assertEqual([a for a in out if a["kind"] == "entity"], [])
        # Ages are never facts either.
        self.assertEqual([a for a in out if a["kind"] == "count" and a["value"] in (51, 14, 16)], [])
        named = facts.extract_item(item("p2", "Anne Leclerc retrouvée sans vie"))
        self.assertNotIn("leclerc", json.dumps(named, ensure_ascii=False).lower())

    def test_entity_values_are_codes_from_the_closed_list(self) -> None:
        allowed = {c[0] for c in facts._INSTITUTIONS} | {"mayor", "mayor_quebec", "premier", "minister",
                                                          "legislator", "police_chief", "coroner", "city_council"}
        text = "Le premier ministre, un député, le chef de police et le conseil municipal; GRC, RTC, SAAQ, TSB"
        for a in facts.extract_item(item("e", text)):
            if a["kind"] == "entity":
                self.assertIn(a["value"], allowed)


class SlotsTable(unittest.TestCase):
    def row(self, rows: list[dict], kind: str, unit: str, subject=None) -> dict:
        for r in rows:
            if r["slot"] == {"kind": kind, "unit": unit, "subject": subject}:
                return r
        self.fail(f"no row {kind}/{unit}/{subject} in {[r['slot'] for r in rows]}")

    def test_divergence_kept_side_by_side_never_reconciled(self) -> None:
        rows = facts.build_slots([
            item("a1", "Incendie : 2 blessés", inst="media-a"),
            item("b1", "Fire leaves 3 people injured", inst="media-b"),
            item("c1", "Incendie : 2 personnes blessées", inst="media-c"),
        ])
        r = self.row(rows, "count", "persons_injured")
        self.assertTrue(r["divergent"])
        self.assertTrue(r["comparable"])
        self.assertEqual([v["value"] for v in r["values"]], [2, 3])
        self.assertEqual(r["values"][0]["stated_by"], ["a1", "c1"])
        self.assertEqual(r["values"][0]["institutions"], ["media-a", "media-c"])
        self.assertEqual(r["values"][1]["stated_by"], ["b1"])
        self.assertEqual(r["method"], "facts-v1")
        self.assertEqual(r["status"], "proposed")

    def test_agreement_is_not_divergence(self) -> None:
        rows = facts.build_slots([item("a", "2 morts", inst="x"), item("b", "Two dead", inst="y")])
        r = self.row(rows, "count", "persons_dead")
        self.assertFalse(r["divergent"])
        self.assertEqual(len(r["values"]), 1)
        self.assertEqual(r["values"][0]["stated_by"], ["a", "b"])

    def test_one_item_with_two_values_is_not_a_divergence(self) -> None:
        rows = facts.build_slots([item("a", "2 blessés puis 3 blessés")])
        self.assertFalse(self.row(rows, "count", "persons_injured")["divergent"])

    def test_generic_amounts_are_shown_but_never_flagged(self) -> None:
        rows = facts.build_slots([
            item("a", "Une baisse de 6 % cette année", inst="x"),
            item("b", "La demande augmente de 4 % cette année", inst="y"),
        ])
        r = self.row(rows, "amount", "percent_bp", "change")
        self.assertFalse(r["comparable"])
        self.assertFalse(r["divergent"])
        self.assertEqual([v["value"] for v in r["values"]], [400, 600])

    def test_specific_amount_subjects_can_diverge(self) -> None:
        rows = facts.build_slots([
            item("a", "Dommages de 1 million $", inst="x"),
            item("b", "Dommages estimés à 2 millions $", inst="y"),
        ])
        self.assertTrue(self.row(rows, "amount", "total", "damages")["divergent"])

    def test_set_kinds_list_values_without_divergence(self) -> None:
        rows = facts.build_slots([
            item("a", "Fermeture rue Gamma le 7 octobre", inst="x"),
            item("b", "Fermeture rue Delta le 9 octobre", inst="y"),
        ])
        for kind, unit in (("place", "road"), ("date", "stated")):
            r = self.row(rows, kind, unit)
            self.assertEqual(len(r["values"]), 2)
            self.assertFalse(r["divergent"])

    def test_rows_carry_no_publisher_text_and_no_floats(self) -> None:
        title = "Zorglub brûle rue Gamma : 3 blessés, dommages de 1,5 million $ le 7 octobre"
        rows = facts.build_slots([item("a", title, "Le SPVQ enquête sur la tour Zorglub.")])
        text = json.dumps(rows, ensure_ascii=False)
        for word in ("Zorglub", "brûle", "enquête", "dommages"):
            self.assertNotIn(word, text)

        def walk(x) -> None:
            if isinstance(x, float):
                self.fail(f"float in output: {x}")
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(rows)

    def test_input_order_does_not_matter(self) -> None:
        items = [item("a", "2 blessés rue Gamma", inst="x"), item("b", "3 blessés rue Gamma", inst="y"),
                 item("c", "Fire: 2 injured, damage $1 million", inst="z")]
        self.assertEqual(json.dumps(facts.build_slots(items)), json.dumps(facts.build_slots(list(reversed(items)))))

    def test_fail_soft_on_foreign_shapes(self) -> None:
        for bad in (None, 5, "x", [], {}, {"id": "z"}, {"id": "z", "title": 7, "summary": ["x"]},
                    {"id": "z", "title": None, "summary": None, "published_at": 12}):
            self.assertIsInstance(facts.extract_item(bad), list)
        self.assertEqual(facts.build_slots(None), [])
        self.assertEqual(facts.build_slots([None, 3, {"title": "2 morts"}]), [])  # no id, no attribution

    def test_determinism_across_hash_seeds(self) -> None:
        code = (
            "import sys, json; sys.path.insert(0, r'%s'); import facts;"
            "items=[{'id':'a','title':'Incendie rue Gamma a Limoilou : 2 blessés, le maire, le SPVQ, 1,5 million \\$','summary':'Le 7 octobre. 36 logements.','institution':'x','published_at':'2026-10-05T12:00:00+00:00'},"
            "{'id':'b','title':'Two injured after fire on Gamma street','summary':'Hydro-Québec says 5 000 customers without power on Oct. 8.','institution':'y','published_at':'2026-10-05T13:00:00+00:00'}];"
            "print(json.dumps(facts.build_slots(items), sort_keys=False))"
        ) % (ROOT / "scripts")
        outs = []
        for seed in ("0", "1", "4242"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
            proc = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, env=env, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])
        self.assertTrue(outs[0].strip())


class Spans(unittest.TestCase):
    def test_verbatim_numeric_spans(self) -> None:
        text = "Une femme de 63-year-old, 17 ans, en 1998, 48,15 $ le 9 mars 2027, 7 %, 215 millions $"
        self.assertEqual(
            facts.spans(text),
            ["63-year-old", "17 ans", "1998", "48,15 $", "9 mars 2027", "7 %", "215 millions $"],
        )

    def test_english_forms_and_counts_with_units(self) -> None:
        got = facts.spans("A 63-year-old cyclist was one of 4 people hurt on Nov. 9, 2026; damage $3.5 million")
        self.assertIn("63-year-old", got)
        self.assertIn("4 people", got)
        self.assertIn("Nov. 9, 2026", got)
        self.assertIn("$3.5 million", got)

    def test_spans_are_substrings_in_order(self) -> None:
        text = "Environ 1 250 logements, 2 morts et 12,5 % de hausse en 2026"
        pos = 0
        for s in facts.spans(text):
            i = text.find(s.replace(" ", " "), pos)
            self.assertGreaterEqual(i, 0, s)
            pos = i + len(s)

    def test_not_a_fact_store(self) -> None:
        # spans are publisher text: build_slots must never emit them.
        rows = facts.build_slots([item("a", "Une femme de 63 ans, 17 ans de prison, 3 blessés")])
        self.assertNotIn("ans", json.dumps(rows, ensure_ascii=False))

    def test_fail_soft(self) -> None:
        for bad in (None, 3, [], b"x", ""):
            self.assertEqual(facts.spans(bad), [])


class Shadow(unittest.TestCase):
    def test_summary_is_counts_only(self) -> None:
        s = facts.summarize([item("a", "Incendie rue Gamma : 2 blessés"), item("b", "Rien à signaler"), "junk"])
        self.assertEqual(s["items"], 2)
        self.assertEqual(s["items_with_any_fact"], 1)
        self.assertEqual(s["items_with_kind"]["count"], 1)
        self.assertNotIn("Gamma", json.dumps(s))

    def test_cli_dangling_in_argument_is_fail_soft(self) -> None:
        self.assertEqual(facts.main(["--in"]), 0)

    def test_cli_is_fail_soft_on_a_missing_store(self) -> None:
        self.assertEqual(facts.main(["--in", str(ROOT / "data" / "does-not-exist.json")]), 0)


if __name__ == "__main__":
    unittest.main()
