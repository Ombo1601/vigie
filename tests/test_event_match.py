"""scripts/event_match.py: scores, tiers, FR/EN guard, reasons, blocking and
the sticky clusterer. Every headline below is invented for the test."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

import harness  # noqa: F401  (hermetic ingest_rss policy path; puts scripts/ on sys.path)
import event_match as em

ROOT = Path(__file__).resolve().parents[1]


def item(i, title, when, lang="fr", summary="", **extra):
    out = {"id": i, "title": title, "summary": summary, "published_at": when, "language": lang}
    out.update(extra)
    return out


# Invented coverage of invented happenings.
FIRE_FR = item("f1", "Incendie dans un entrepôt de Limoilou: 60 000 litres de mazout menacés",
               "2026-09-20T08:00:00+00:00", summary="Les pompiers de la Ville de Québec sont sur place.")
FIRE_EN = item("f2", "Fire at Limoilou warehouse threatens 60,000 litres of fuel oil",
               "2026-09-20T09:30:00+00:00", "en", summary="Quebec City firefighters are on site.")
FIRE_FR2 = item("f3", "Limoilou: l'incendie de l'entrepôt est maîtrisé", "2026-09-20T15:00:00+00:00",
                summary="Les 60 000 litres de mazout sont hors de danger.")
FIRE_ELSEWHERE = item("f4", "Incendie dans un entrepôt de Beauport", "2026-09-20T09:00:00+00:00")
STRIKE = item("s1", "Grève des chauffeurs d'autobus du RTC à Charlesbourg", "2026-09-20T10:00:00+00:00",
              summary="Le syndicat déclenche une grève de trois jours.")
STRIKE2 = item("s2", "Charlesbourg: la grève des chauffeurs d'autobus du RTC se poursuit",
               "2026-09-21T09:00:00+00:00", summary="Deuxième journée de grève au RTC.")
OLD_FIRE = item("f5", "Incendie dans un entrepôt de Limoilou", "2026-08-01T08:00:00+00:00")
PRIV_FR = item("p1", "Privatisation des aéroports : Québec dépose son projet de loi",
               "2026-09-22T08:00:00+00:00", summary="Le ministre a présenté le projet le 1er octobre.")
PRIV_EN = item("p2", "Quebec tables airport privatization bill",
               "2026-09-22T10:00:00+00:00", "en", summary="The minister presented the bill on October 1.")
CORPUS = [FIRE_FR, FIRE_EN, FIRE_FR2, FIRE_ELSEWHERE, STRIKE, STRIKE2, OLD_FIRE, PRIV_FR, PRIV_EN]


def ctx_for(items=CORPUS, **kw):
    return em.MatchContext(items, **kw)


class Scores(unittest.TestCase):
    def setUp(self):
        self.ctx = ctx_for()

    def test_scores_are_probabilities_and_exactly_symmetric(self):
        for a in CORPUS:
            for b in CORPUS:
                s, _ = em.score(a, b, self.ctx)
                self.assertGreaterEqual(s, 0.0)
                self.assertLessEqual(s, 1.0)
                self.assertEqual(s, em.score(b, a, self.ctx)[0])
                self.assertEqual(s, em.probability(a, b, self.ctx))

    def test_same_event_outranks_neighbours(self):
        p = lambda a, b: em.probability(a, b, self.ctx)  # noqa: E731
        self.assertGreater(p(FIRE_FR, FIRE_EN), p(FIRE_FR, FIRE_ELSEWHERE))
        self.assertGreater(p(FIRE_FR, FIRE_FR2), p(FIRE_FR, FIRE_ELSEWHERE))
        self.assertGreater(p(FIRE_FR, FIRE_ELSEWHERE), p(FIRE_FR, STRIKE))
        self.assertGreater(p(FIRE_FR, FIRE_FR2), p(FIRE_FR, OLD_FIRE))
        self.assertEqual(em.match(FIRE_FR, FIRE_EN, self.ctx).tier, "certain")
        self.assertIsNone(em.match(FIRE_FR, STRIKE, self.ctx).tier)

    def test_scoring_is_pure(self):
        before = copy.deepcopy(CORPUS)
        for a in CORPUS:
            for b in CORPUS:
                em.match(a, b, self.ctx)
        self.assertEqual(before, CORPUS)

    def test_ownership_never_enters_the_score(self):
        """Same-owner pairs (CBC / Radio-Canada) match like any other pair:
        independence is counted elsewhere, never by the matcher."""
        a = dict(FIRE_FR, institution="radio-canada", source_id="radio-canada-quebec", owner_group="cbc-radio-canada")
        b = dict(FIRE_EN, institution="cbc", source_id="cbc-quebec", owner_group="cbc-radio-canada")
        self.assertEqual(em.match(a, b, self.ctx).score, em.match(FIRE_FR, FIRE_EN, self.ctx).score)

    def test_unknown_dates_are_neutral_not_fatal(self):
        a = item("u1", "Incendie à Limoilou", "")
        b = item("u2", "Fire in Limoilou", "", "en")
        x, ev = em.components(a, b, self.ctx)
        self.assertIsNone(ev["hours"])
        self.assertEqual((x["time"], x["far"]), (0.5, 0.0))
        self.assertIn(em.match(a, b, self.ctx).tier, em.TIERS + (None,))

    def test_frozen_constants_are_sane(self):
        self.assertEqual(set(em.WEIGHTS), {"bias", *em.FEATURES})
        t = em.THRESHOLDS
        self.assertGreater(t["certain"], t["probable"])
        self.assertGreater(t["probable"], t["possible"])
        self.assertGreater(t["possible"], 0.0)
        self.assertLess(t["certain"], 1.0)


class Tiers(unittest.TestCase):
    def test_tiers_are_monotone_in_the_score(self):
        last = {True: 0, False: 0}
        for k in range(0, 1001):
            s = k / 1000.0
            for ok in (True, False):
                rank = em.TIER_RANK[em.tier(s, ok)]
                self.assertGreaterEqual(rank, last[ok], (s, ok))
                last[ok] = rank
                self.assertLessEqual(em.TIER_RANK[em.tier(s, False)], em.TIER_RANK[em.tier(s, True)])

    def test_tier_boundaries(self):
        t = {"certain": 0.9, "probable": 0.5, "possible": 0.2}
        self.assertEqual(em.tier(0.9, True, t), "certain")
        self.assertEqual(em.tier(0.8999, True, t), "probable")
        self.assertEqual(em.tier(0.5, True, t), "probable")
        self.assertEqual(em.tier(0.4999, True, t), "possible")
        self.assertEqual(em.tier(0.2, True, t), "possible")
        self.assertIsNone(em.tier(0.1999, True, t))

    def test_failed_guard_caps_at_possible_never_merges(self):
        t = {"certain": 0.9, "probable": 0.5, "possible": 0.2}
        self.assertEqual(em.tier(0.99, False, t), "possible")
        self.assertEqual(em.tier(0.6, False, t), "possible")
        self.assertIsNone(em.tier(0.1, False, t))
        self.assertFalse(em.at_least(em.tier(0.99, False, t), em.MERGE_TIER))


class FrEnGuard(unittest.TestCase):
    def test_same_language_pairs_are_not_guarded(self):
        g = em.fr_en_guard(FIRE_FR, FIRE_FR2, ctx_for())
        self.assertFalse(g.applies)
        self.assertTrue(g.ok)

    def test_specific_place_or_salient_number_passes(self):
        g = em.fr_en_guard(FIRE_FR, FIRE_EN, ctx_for())
        self.assertTrue(g.applies and g.ok)
        self.assertIn(("place", "limoilou"), g.anchors)
        self.assertIn(("number", "60000"), g.anchors)

    def test_broad_places_years_and_small_numbers_do_not_pass(self):
        a = item("g1", "Le Québec annonce 7 mesures pour 2027", "2026-09-20T08:00:00+00:00")
        b = item("g2", "Quebec unveils 7 measures for 2027", "2026-09-20T09:00:00+00:00", "en")
        ctx = ctx_for([a, b])
        g = em.fr_en_guard(a, b, ctx)
        self.assertTrue(g.applies)
        self.assertFalse(g.ok, g.anchors)
        m = em.match(a, b, ctx)
        self.assertIn(m.tier, ("possible", None))
        if m.score >= ctx.thresholds["probable"]:
            self.assertEqual(m.reasons[-1]["code"], "guard")

    def test_a_common_name_does_not_pass_a_rare_one_does(self):
        def pair(n_others):
            a = item("n1", "Zorblax Quillimet visite une usine", "2026-09-20T08:00:00+00:00")
            b = item("n2", "Zorblax Quillimet visits a factory", "2026-09-20T09:00:00+00:00", "en")
            others = [item("o%d" % k, "Le ministre Zorblax parle de l'économie numéro %d" % k,
                           "2026-09-1%dT08:00:00+00:00" % (k % 9)) for k in range(n_others)]
            return a, b, ctx_for([a, b] + others)

        a, b, ctx = pair(0)
        self.assertTrue(em.fr_en_guard(a, b, ctx).ok)
        a, b, ctx = pair(5)  # "Zorblax" now in 7 of 7 items; "Quillimet" still rare
        g = em.fr_en_guard(a, b, ctx)
        self.assertTrue(g.ok)
        self.assertNotIn(("name", "zorblax"), g.anchors)
        self.assertIn(("name", "quillimet"), g.anchors)

    def test_guard_can_be_measured_off(self):
        a = item("g1", "Le Québec annonce des mesures", "2026-09-20T08:00:00+00:00")
        b = item("g2", "Quebec announces measures", "2026-09-20T08:30:00+00:00", "en")
        on = ctx_for([a, b], thresholds={"certain": 0.99, "probable": 0.0001, "possible": 0.00001})
        off = ctx_for([a, b], thresholds={"certain": 0.99, "probable": 0.0001, "possible": 0.00001}, guard=False)
        self.assertEqual(em.pair_tier(a, b, on)[1], "possible")
        self.assertEqual(em.pair_tier(a, b, off)[1], "probable")


class Reasons(unittest.TestCase):
    def setUp(self):
        self.ctx = ctx_for()

    def _text(self, it):
        return it["title"] + " " + it["summary"]

    def test_anchors_quote_published_words_never_stems(self):
        _, reasons = em.score(PRIV_FR, PRIV_EN, self.ctx)
        anchors = [p for r in reasons if r["code"] not in ("dates",) for p in r["anchors"]]
        self.assertTrue(anchors)
        for left, right in anchors:
            self.assertIn(left, self._text(PRIV_FR) + self._text(PRIV_EN))
            self.assertIn(right, self._text(PRIV_FR) + self._text(PRIV_EN))
        words = " ".join(r["fr"] + " " + r["en"] for r in reasons)
        self.assertNotRegex(words, r"\bprivatis\b")
        self.assertIn("privatisation", words.lower())
        self.assertIn("1er octobre", words)
        self.assertIn("October 1", words)

    def test_headline_reason_cites_headline_words_only(self):
        a = item("h1", "Zorblax ferme son usine", "2026-09-20T08:00:00+00:00",
                 summary="Quillimet et Vrandel commentent la nouvelle.")
        b = item("h2", "Zorblax: l'usine ferme", "2026-09-20T09:00:00+00:00",
                 summary="Quillimet et Vrandel réagissent.")
        _, reasons = em.score(a, b, ctx_for([a, b]))
        title = next(r for r in reasons if r["code"] == "title")
        for left, right in title["anchors"]:
            self.assertIn(left, a["title"])
            self.assertIn(right, b["title"])
        self.assertNotIn("Quillimet", " ".join(title["fr"] for _ in [0]))
        body = next(r for r in reasons if r["code"] == "tokens")
        self.assertTrue(any("Quillimet" in p for pair in body["anchors"] for p in pair) or
                        any("Vrandel" in p for pair in body["anchors"] for p in pair) or body["anchors"])

    def test_reason_weights_add_up_to_the_log_odds(self):
        for a, b in ((FIRE_FR, FIRE_EN), (FIRE_FR, STRIKE), (PRIV_FR, PRIV_EN), (FIRE_FR, OLD_FIRE)):
            s, reasons = em.score(a, b, self.ctx)
            total = sum(r["weight"] for r in reasons)
            self.assertAlmostEqual(total, em.logit(em.components(a, b, self.ctx)[0], self.ctx.weights), delta=0.01)
            self.assertTrue(all(r["fr"] and r["en"] and r["sign"] in "+-" for r in reasons))
            self.assertEqual(reasons[-1]["code"], "base")

    def test_negative_evidence_is_explained(self):
        why = " | ".join(r["en"] for r in em.score(FIRE_FR, FIRE_ELSEWHERE, self.ctx)[1])
        self.assertIn("different places", why)
        far = " | ".join(r["fr"] for r in em.score(FIRE_FR, OLD_FIRE, self.ctx)[1])
        self.assertIn("jours d'intervalle", far)


class Blocking(unittest.TestCase):
    def test_every_pair_at_a_tier_is_a_candidate(self):
        ctx = ctx_for()
        cands = set(em.candidate_pairs(CORPUS, ctx))
        for i, a in enumerate(CORPUS):
            for b in CORPUS[i + 1:]:
                hours = abs((em.published_when(a) - em.published_when(b)).total_seconds()) / 3600
                if hours > em.FAR_HOURS:
                    self.assertNotIn(tuple(sorted((a["id"], b["id"]))), cands)
                    continue
                if em.pair_tier(a, b, ctx)[1] is not None:
                    self.assertIn(tuple(sorted((a["id"], b["id"]))), cands, (a["id"], b["id"]))

    def test_candidates_are_sorted_unique_pairs(self):
        cands = em.candidate_pairs(CORPUS, ctx_for())
        self.assertEqual(cands, sorted(set(cands)))
        self.assertTrue(all(a < b for a, b in cands))


def _iso(day, hour):
    return "2026-09-%02dT%02d:00:00+00:00" % (day, hour)


class Sticky(unittest.TestCase):
    def setUp(self):
        self.ed1 = [FIRE_FR, FIRE_EN, STRIKE]
        self.ed2 = [FIRE_FR2, STRIKE2, FIRE_FR]  # FIRE_FR is already a member: ignored
        self.ctx = ctx_for(self.ed1 + self.ed2)

    def test_existing_members_never_move_and_ids_are_minted_once(self):
        ev1, dec1 = em.attach([], self.ed1, self.ctx, edition="2026-09-20T12:00:00+00:00")
        frozen = copy.deepcopy(ev1)
        texts = {it["id"]: it for it in self.ed1}
        ev2, dec2 = em.attach(ev1, self.ed2, self.ctx, edition="2026-09-21T12:00:00+00:00", items_by_id=texts)
        self.assertEqual(ev1, frozen, "input events are never mutated")
        before = {m["item_id"]: e["event_id"] for e in ev1 for m in e["members"]}
        after = {m["item_id"]: e["event_id"] for e in ev2 for m in e["members"]}
        for item_id, eid in before.items():
            self.assertEqual(after[item_id], eid)
        self.assertEqual(sorted(after), sorted({"f1", "f2", "f3", "s1", "s2"}))
        self.assertEqual(after["f3"], after["f1"])
        self.assertEqual(after["s2"], after["s1"])
        self.assertNotIn("f1", [d.get("item_id") for d in dec2], "a member is never re-attached")
        for e in ev2:
            self.assertRegex(e["event_id"], r"^ev-[0-9a-f]{16}$")
        fire = next(e for e in ev2 if e["event_id"] == after["f1"])
        anchor = fire["members"][0]["item_id"]
        self.assertEqual(fire["event_id"],
                         "ev-" + hashlib.sha256(("event-v1|" + anchor).encode()).hexdigest()[:16])
        self.assertEqual(fire["born_edition"], "2026-09-20T12:00:00+00:00")
        self.assertEqual(fire["last_edition"], "2026-09-21T12:00:00+00:00")

    def test_rerun_is_identical(self):
        ev1, _ = em.attach([], self.ed1, self.ctx, edition="2026-09-20T12:00:00+00:00")
        texts = {it["id"]: it for it in self.ed1}
        runs = [em.attach(ev1, self.ed2, self.ctx, edition="2026-09-21T12:00:00+00:00", items_by_id=texts)
                for _ in range(2)]
        self.assertEqual(json.dumps(runs[0], sort_keys=True), json.dumps(runs[1], sort_keys=True))

    def test_member_without_text_keeps_membership(self):
        ev1, _ = em.attach([], self.ed1, self.ctx, edition="2026-09-20T12:00:00+00:00")
        ev2, _ = em.attach(ev1, [FIRE_FR2], self.ctx, edition="2026-09-21T12:00:00+00:00", items_by_id={})
        owners = {m["item_id"]: e["event_id"] for e in ev2 for m in e["members"]}
        self.assertEqual(owners["f1"], owners["f2"])
        self.assertIn("f3", owners)

    def test_an_event_closes_after_the_window(self):
        late = item("f9", "Limoilou: l'incendie de l'entrepôt est maîtrisé, 60 000 litres sauvés",
                    "2026-09-28T15:00:00+00:00")
        ctx = ctx_for([FIRE_FR, FIRE_EN, late])
        ev1, _ = em.attach([], [FIRE_FR, FIRE_EN], ctx, edition="2026-09-20T12:00:00+00:00")
        ev2, dec = em.attach(ev1, [late], ctx, edition="2026-09-28T16:00:00+00:00",
                             items_by_id={"f1": FIRE_FR, "f2": FIRE_EN})
        self.assertEqual(dec[0]["action"], "minted")


class _Table:
    """A scripted pair_tier for testing the clusterer's rules exactly."""

    def __init__(self, table, thresholds):
        self.table = {tuple(sorted(k)): v for k, v in table.items()}
        self.t = thresholds

    def __call__(self, a, b, ctx):
        s = self.table.get(tuple(sorted((a["id"], b["id"]))), 0.0)
        return s, em.tier(s, True, self.t)


class Rules(unittest.TestCase):
    T = {"certain": 0.9, "probable": 0.4, "possible": 0.1}

    def _items(self, ids, day=20):
        # one shared rare word so blocking proposes every pair
        return [item(i, "Zorblax %s" % i, _iso(day, 8 + k)) for k, i in enumerate(ids)]

    def _run(self, events, new, table, texts=(), edition=_iso(21, 0)):
        ctx = em.MatchContext(list(texts) + new, thresholds=self.T)
        saved = em.pair_tier
        em.pair_tier = _Table(table, self.T)
        try:
            return em.attach(events, new, ctx, edition=edition, items_by_id={t["id"]: t for t in texts})
        finally:
            em.pair_tier = saved

    def _events(self):
        e1 = {"event_id": "ev-" + "1" * 16, "born_edition": _iso(19, 0),
              "members": [{"item_id": "a1"}, {"item_id": "a2"}], "lineage": {"merged_into": "", "absorbed": []}}
        e2 = {"event_id": "ev-" + "2" * 16, "born_edition": _iso(19, 6),
              "members": [{"item_id": "b1"}], "lineage": {"merged_into": "", "absorbed": []}}
        return [e1, e2]

    def test_merge_needs_two_cross_pairs_at_probable(self):
        texts = self._items(["a1", "a2", "b1"])
        new = self._items(["n1"], day=20)
        base = {("n1", "b1"): 0.95, ("n1", "a1"): 0.5, ("n1", "a2"): 0.05}
        events, dec = self._run(self._events(), new, {**base, ("a1", "b1"): 0.6}, texts)
        by = {e["event_id"]: e for e in events}
        self.assertEqual(by["ev-" + "2" * 16]["lineage"]["merged_into"], "ev-" + "1" * 16, "older keeps its id")
        self.assertIn("ev-" + "2" * 16, by["ev-" + "1" * 16]["lineage"]["absorbed"])
        self.assertEqual(dec[-1]["action"], "merged")
        self.assertEqual(sorted(len(g) for g in em.groups(events)), [4])
        events, dec = self._run(self._events(), new, {**base, ("a1", "b1"): 0.2}, texts)
        self.assertFalse(any(e["lineage"]["merged_into"] for e in events), "one cross pair never merges")
        self.assertEqual(sorted(len(g) for g in em.groups(events)), [2, 2])

    def test_average_link_and_tie_breaks(self):
        texts = self._items(["a1", "a2", "b1"])
        new = self._items(["n1"])
        # E1 mean (0.5 + 0.3) / 2 = 0.4 >= probable; E2 mean 0.45: E2 wins on mean
        events, dec = self._run(self._events(), new, {("n1", "a1"): 0.5, ("n1", "a2"): 0.3, ("n1", "b1"): 0.45}, texts)
        self.assertEqual(dec[0]["event_id"], "ev-" + "2" * 16)
        # equal means (0.5): E1 has more matching members
        events, dec = self._run(self._events(), new, {("n1", "a1"): 0.5, ("n1", "a2"): 0.5, ("n1", "b1"): 0.5}, texts)
        self.assertEqual(dec[0]["event_id"], "ev-" + "1" * 16)
        # one strong member but a weak average: no link, a new event is born
        events, dec = self._run(self._events(), new, {("n1", "a1"): 0.5, ("n1", "a2"): 0.05}, texts)
        self.assertEqual(dec[0]["action"], "minted")
        self.assertEqual(dec[0]["event_id"], em.event_id_for("n1"))

    def test_attach_order_is_publication_then_id(self):
        new = [item("z2", "Zorblax deux", _iso(20, 9)), item("z1", "Zorblax un", _iso(20, 9)),
               item("z0", "Zorblax zéro", _iso(20, 10))]
        events, dec = self._run([], new, {("z1", "z2"): 0.95, ("z0", "z1"): 0.95, ("z0", "z2"): 0.95})
        self.assertEqual([d["item_id"] for d in dec if "item_id" in d], ["z1", "z2", "z0"])
        self.assertEqual(events[0]["event_id"], em.event_id_for("z1"))


_DETERMINISM = r"""
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
import event_match as em
items = json.loads(sys.stdin.read())
ctx = em.MatchContext(items)
out = {"pairs": [], "cands": em.candidate_pairs(items, ctx)}
for i, a in enumerate(items):
    for b in items[i + 1:]:
        m = em.match(a, b, ctx)
        out["pairs"].append([a["id"], b["id"], repr(m.score), m.tier, m.reasons, list(m.guard)])
events, decisions = em.attach([], items, ctx, edition="2026-09-23T00:00:00+00:00")
out["events"], out["decisions"] = events, decisions
out["neighbours"] = em.neighbours(events, {it["id"]: it for it in items}, ctx)
print(hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest())
"""


class Determinism(unittest.TestCase):
    def test_identical_under_two_hash_seeds(self):
        digests = []
        for seed in ("0", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            proc = subprocess.run([sys.executable, "-X", "utf8", "-c", _DETERMINISM, str(ROOT / "scripts")],
                                  input=json.dumps(CORPUS), capture_output=True, text=True, encoding="utf-8",
                                  env=env, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            digests.append(proc.stdout.strip())
        self.assertRegex(digests[0], r"^[0-9a-f]{64}$")
        self.assertEqual(digests[0], digests[1])

    def test_no_wall_clock_in_the_module(self):
        source = (ROOT / "scripts" / "event_match.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"datetime\.now|time\.time\(|date\.today|utcnow", source))


class Scale(unittest.TestCase):
    def test_a_week_of_invented_items_clusters_quickly(self):
        places = ["Limoilou", "Beauport", "Charlesbourg", "Sillery", "Lévis", "Vanier", "Duberger", "Montcalm"]
        kinds = [("Incendie", "Fire"), ("Collision", "Crash"), ("Grève", "Strike"), ("Panne", "Outage"),
                 ("Inondation", "Flood")]
        items = []
        for k in range(600):
            p, (fr, en) = places[k % len(places)], kinds[(k // len(places)) % len(kinds)]
            day, hour = 14 + (k % 7), k % 24
            if k % 5 == 0:
                items.append(item("x%04d" % k, "%s in %s, report %d" % (en, p, k % 37), _iso(day, hour), "en",
                                  summary="Officials in %s say %d people were affected." % (p, k % 37)))
            else:
                items.append(item("x%04d" % k, "%s à %s, bilan %d" % (fr, p, k % 37), _iso(day, hour),
                                  summary="Les autorités de %s rapportent %d personnes touchées." % (p, k % 37)))
        started = time.perf_counter()
        ctx = em.MatchContext(items)
        events, _ = em.attach([], items, ctx, edition=_iso(21, 0))
        elapsed = time.perf_counter() - started
        self.assertEqual(sum(len(e["members"]) for e in events), 600)
        self.assertLess(elapsed, 60.0, "600 items should cluster in seconds, not minutes")


if __name__ == "__main__":
    unittest.main()
