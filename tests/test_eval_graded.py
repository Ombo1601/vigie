"""eval/graded_matcher.py and eval/lexicon_fr_en.py on invented headlines.

Both are now thin layers over the production modules (scripts/event_match.py,
scripts/event_lexicon.py): the evaluation measures what ships."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import graded_matcher as gm  # noqa: E402
import lexicon_fr_en as lex  # noqa: E402
import event_lexicon  # noqa: E402
import event_match  # noqa: E402


def item(i, title, when, lang="fr", summary=""):
    return {"id": i, "title": title, "summary": summary, "published_at": when, "language": lang}


FIRE_FR = item("f1", "Incendie dans un entrepôt de Limoilou: 60 000 litres de mazout menacés",
               "2026-09-20T08:00:00+00:00", summary="Brigade de la Ville de Québec dépêchée.")
FIRE_EN = item("f2", "Fire at Limoilou warehouse threatens 60,000 litres of fuel oil",
               "2026-09-20T09:30:00+00:00", "en", summary="Quebec City firefighters are on site.")
FIRE_FR2 = item("f3", "Limoilou: l'incendie de l'entrepôt est maîtrisé",
                "2026-09-20T15:00:00+00:00")
STRIKE = item("s1", "Grève des chauffeurs d'autobus du RTC à Charlesbourg",
              "2026-09-20T10:00:00+00:00")
FIRE_ELSEWHERE = item("f4", "Incendie dans un entrepôt de Beauport",
                      "2026-09-20T09:00:00+00:00")
OLD_FIRE = item("f5", "Incendie dans un entrepôt de Limoilou", "2026-08-01T08:00:00+00:00")
CORPUS = [FIRE_FR, FIRE_EN, FIRE_FR2, STRIKE, FIRE_ELSEWHERE, OLD_FIRE]


class MeasuresWhatShips(unittest.TestCase):
    def test_eval_uses_the_production_modules(self):
        self.assertIs(gm.em, event_match)
        self.assertIs(lex.ENTRIES, event_lexicon.ENTRIES)
        self.assertIs(lex.WORD_CANON, event_lexicon.WORD_CANON)
        self.assertIs(gm.ItemFeatures, event_match.ItemFeatures)
        self.assertIs(gm.PRIOR_WEIGHTS, event_match.PRIOR_WEIGHTS)

    def test_scores_equal_the_production_score(self):
        m = gm.GradedMatcher(weights=event_match.WEIGHTS, fit=False, guard=True, name="shipped",
                             thresholds=event_match.THRESHOLDS)
        m.prepare(CORPUS)
        ctx = event_match.MatchContext(CORPUS)
        for a in CORPUS:
            for b in CORPUS:
                self.assertEqual(m.score(a, b), event_match.score(a, b, ctx)[0])
                self.assertEqual(m.tier(a, b), event_match.match(a, b, ctx).tier)

    def test_fit_reproduces_on_identical_input(self):
        triples = [(FIRE_FR, FIRE_EN, 1), (FIRE_FR, STRIKE, 0), (FIRE_FR, FIRE_ELSEWHERE, 0)]
        rows = []
        for _ in range(2):
            m = gm.GradedMatcher(fit=True, epochs=50)
            m.prepare(CORPUS)
            m.fit(triples)
            rows.append(m.weights)
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(set(rows[0]), set(event_match.WEIGHTS))


class Lexicon(unittest.TestCase):
    def test_size_and_kinds(self):
        self.assertGreaterEqual(len(lex.ENTRIES), 150)
        ids = [e[0] for e in lex.ENTRIES]
        self.assertEqual(len(ids), len(set(ids)), "canonical ids are unique")
        for eid, kind, fr, en in lex.ENTRIES:
            self.assertIn(kind, lex.KIND_WEIGHT, eid)
            self.assertTrue(fr and en, eid)

    def test_bilingual_surfaces_land_on_one_id(self):
        fr = lex.term_ids("Bouchon sur le pont Pierre-Laporte près de l'Université Laval")
        en = lex.term_ids("Traffic jam on the Pierre Laporte Bridge near Laval University")
        self.assertEqual(fr, en)
        self.assertIn("pont-pierre-laporte", fr)
        self.assertNotIn("laval-ville", fr, "longest surface wins over the city of Laval")

    def test_word_boundaries_and_ambiguity_guards(self):
        self.assertNotIn("levis", lex.term_ids("Une émission de télévision"))
        self.assertNotIn("union-europeenne", lex.term_ids("Il a eu raison"))
        self.assertEqual(lex.WORD_CANON["fire"], lex.WORD_CANON["incendies"])

    def test_fold(self):
        self.assertEqual(lex.fold("Sainte-Foy–Sillery, l’Île"), "sainte foy sillery l ile")


class Features(unittest.TestCase):
    def test_numbers_fold_thousands_in_both_languages(self):
        self.assertIn("60000", gm.numbers_of("60 000 clients"))
        self.assertIn("60000", gm.numbers_of("60,000 customers"))
        self.assertIn("1.5", gm.numbers_of("1,5 million"))
        self.assertIn("4", gm.numbers_of("quatre blessés"))

    def test_dates_and_weekdays(self):
        fr = gm.dates_of(lex.fold("le 1er octobre, dimanche"))
        en = gm.dates_of(lex.fold("on October 1, Sunday"))
        self.assertEqual(fr, en)
        self.assertIn("d:10-01", fr)

    def test_names_skip_sentence_initial_word(self):
        names, _ = gm.names_of("Hier, Bruno Tremblay visite Limoilou", "")
        self.assertIn("bruno tremblay", names)
        self.assertIn("limoilou", names)
        self.assertNotIn("hier", names)

    def test_stem_is_light(self):
        self.assertEqual(gm.stem("incendies"), gm.stem("incendie"))
        self.assertEqual(gm.stem("mot"), "mot")


class Matcher(unittest.TestCase):
    def setUp(self):
        self.m = gm.GradedMatcher(fit=False)
        self.m.prepare(CORPUS)

    def test_scores_are_probabilities_and_symmetric(self):
        for a in CORPUS:
            for b in CORPUS:
                s = self.m.score(a, b)
                self.assertGreaterEqual(s, 0.0)
                self.assertLessEqual(s, 1.0)
                self.assertAlmostEqual(s, self.m.score(b, a), places=9)

    def test_same_event_outranks_neighbours(self):
        same_cross = self.m.score(FIRE_FR, FIRE_EN)
        same_fr = self.m.score(FIRE_FR, FIRE_FR2)
        other_place = self.m.score(FIRE_FR, FIRE_ELSEWHERE)
        other_event = self.m.score(FIRE_FR, STRIKE)
        weeks_apart = self.m.score(FIRE_FR, OLD_FIRE)
        self.assertGreater(same_cross, other_place)
        self.assertGreater(same_fr, other_place)
        self.assertGreater(other_place, other_event)
        self.assertGreater(same_fr, weeks_apart)

    def test_explanations_name_the_evidence(self):
        why = " | ".join(self.m.explain(FIRE_FR, FIRE_EN, top=12))
        self.assertIn("Limoilou", why)
        self.assertIn("60 000 ≙ 60,000", why, "numbers are quoted as each outlet wrote them")
        conflict = " | ".join(self.m.explain(FIRE_FR, FIRE_ELSEWHERE, top=8))
        self.assertIn("different places", conflict)
        far = " | ".join(self.m.explain(FIRE_FR, OLD_FIRE, top=8))
        self.assertIn("days apart", far)

    def test_fit_is_deterministic_and_moves_weights(self):
        triples = [(FIRE_FR, FIRE_EN, 1), (FIRE_FR, FIRE_FR2, 1), (FIRE_EN, FIRE_FR2, 1),
                   (FIRE_FR, STRIKE, 0), (FIRE_FR, FIRE_ELSEWHERE, 0), (FIRE_FR, OLD_FIRE, 0)]
        runs = []
        for _ in range(2):
            m = gm.GradedMatcher(fit=True, epochs=200)
            m.prepare(CORPUS)
            m.fit(triples)
            runs.append(m.weights)
        self.assertEqual(runs[0], runs[1])
        self.assertNotEqual(runs[0], gm.PRIOR_WEIGHTS)
        unfitted = gm.GradedMatcher(fit=False)
        unfitted.fit(triples)
        self.assertEqual(unfitted.weights, gm.PRIOR_WEIGHTS)

    def test_unknown_dates_are_neutral_not_fatal(self):
        a = item("u1", "Incendie à Limoilou", "")
        b = item("u2", "Fire in Limoilou", "", "en")
        x, ev = self.m.components(a, b)
        self.assertIsNone(ev["hours"])
        self.assertEqual(x["time"], 0.5)
        self.assertEqual(x["far"], 0.0)


if __name__ == "__main__":
    unittest.main()
