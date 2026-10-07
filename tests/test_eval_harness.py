"""eval/run_eval.py: metrics, protocol, privacy and graceful skips.

Every headline below is invented for the test; no publisher text.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import harness  # noqa: F401  (hermetic ingest_rss policy path)

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import run_eval  # noqa: E402

# Invented items. "Zorblax" and "Quillimet" are marker words that must never
# reach a report or the JSON results.
ITEMS = [
    {"id": "a1", "title": "Incendie majeur dans un entrepôt Zorblax de Limoilou: 40 pompiers mobilisés",
     "summary": "Les pompiers ont combattu les flammes toute la nuit à Limoilou.",
     "published_at": "2026-09-20T08:00:00+00:00", "source_id": "s1", "language": "fr"},
    {"id": "a2", "title": "Major fire at Zorblax warehouse in Limoilou, 40 firefighters called in",
     "summary": "Firefighters battled the blaze overnight in Limoilou.",
     "published_at": "2026-09-20T10:00:00+00:00", "source_id": "s2", "language": "en"},
    {"id": "a3", "title": "Entrepôt Zorblax de Limoilou: incendie maîtrisé après la nuit",
     "summary": "Quarante pompiers ont été mobilisés.",
     "published_at": "2026-09-20T12:00:00+00:00", "source_id": "s3", "language": "fr"},
    {"id": "b1", "title": "Le conseil municipal adopte le budget Quillimet à Charlesbourg",
     "summary": "Le budget prévoit une hausse de taxes.",
     "published_at": "2026-09-22T18:00:00+00:00", "source_id": "s1", "language": "fr"},
    {"id": "b2", "title": "Grève des chauffeurs d'autobus Quillimet à Beauport",
     "summary": "Le syndicat déclenche une grève.",
     "published_at": "2026-09-25T09:00:00+00:00", "source_id": "s2", "language": "fr"},
]


def gold_doc() -> dict:
    pairs = [
        ("p1", "a1", "a2", "same_event", "dev"),
        ("p2", "a1", "a3", "same_event", "dev"),
        ("p3", "a1", "b1", "different", "dev"),
        ("p4", "b1", "b2", "related", "dev"),
        ("p5", "a2", "a3", "same_event", "test"),
        ("p6", "a2", "b2", "different", "test"),
        ("p7", "a3", "b1", "related", "test"),
        ("p8", "a3", "zz", "same_event", "test"),  # unresolvable id
    ]
    return {"version": "gold_test", "kappa": 0.9, "agreement": 0.95,
            "pairs": [{"pair_id": p, "a_id": a, "b_id": b, "label": lab, "split": sp}
                      for p, a, b, lab, sp in pairs]}


class FixedScorer:
    name = "fixed"
    binary = False

    def __init__(self, table):
        self.table = table

    def score(self, a, b):
        return self.table.get(tuple(sorted((a["id"], b["id"]))), 0.0)


class Metrics(unittest.TestCase):
    def test_prf_and_confusion_strict_counts_related_as_negative(self):
        rows = [(0.9, "same_event"), (0.8, "related"), (0.2, "same_event"), (0.1, "different")]
        m = run_eval.confusion(rows, 0.5)
        self.assertEqual((m["tp"], m["fp"], m["fn"], m["tn"]), (1, 1, 1, 1))
        self.assertAlmostEqual(m["f1"], 0.5)
        lenient = run_eval.confusion(rows, 0.5, drop_related=True)
        self.assertEqual((lenient["tp"], lenient["fp"], lenient["n"]), (1, 0, 3))

    def test_tune_threshold_prefers_plateau_median(self):
        rows = [(0.9, "same_event"), (0.7, "same_event"), (0.4, "different"), (0.1, "different")]
        t = run_eval.tune_threshold(rows)
        self.assertEqual(run_eval.confusion(rows, t)["f1"], 1.0)
        self.assertEqual(run_eval.tune_threshold([]), 0.5)

    def test_pr_curve_and_average_precision(self):
        rows = [(0.9, "same_event"), (0.8, "different"), (0.7, "same_event")]
        curve, ap = run_eval.pr_curve(rows)
        self.assertEqual([round(p["recall"], 2) for p in curve], [0.5, 0.5, 1.0])
        self.assertAlmostEqual(ap, 0.5 * 1.0 + 0.5 * (2 / 3))
        self.assertEqual(run_eval.pr_curve([(0.3, "different")]), ([], 0.0))

    def test_bcubed_perfect_and_merged(self):
        gold = [["a", "b"], ["c"]]
        self.assertEqual(run_eval.bcubed(gold, gold)["f1"], 1.0)
        merged = run_eval.bcubed([["a", "b", "c"]], gold)
        self.assertAlmostEqual(merged["recall"], 1.0)
        self.assertAlmostEqual(merged["precision"], (2 / 3 + 2 / 3 + 1 / 3) / 3)

    def test_gold_clusters_are_transitive_closure(self):
        pairs = [{"a_id": "a", "b_id": "b", "label": "same_event"},
                 {"a_id": "b", "b_id": "c", "label": "same_event"},
                 {"a_id": "c", "b_id": "d", "label": "related"}]
        self.assertEqual(run_eval.gold_clusters(pairs, ["a", "b", "c", "d"]), [["a", "b", "c"], ["d"]])

    def test_bootstrap_is_seeded(self):
        rows = [(0.9, "same_event"), (0.2, "different"), (0.6, "related"), (0.7, "same_event")] * 5
        self.assertEqual(run_eval.bootstrap_f1(rows, 0.5), run_eval.bootstrap_f1(rows, 0.5))

    def test_average_link_groups_only_above_threshold(self):
        table = {("a1", "a2"): 0.9, ("a1", "a3"): 0.8, ("a2", "a3"): 0.85, ("a1", "b1"): 0.1}
        groups = run_eval.average_link(ITEMS[:4], FixedScorer(table), 0.5)
        self.assertEqual(groups, [["a1", "a2", "a3"], ["b1"]])


class Thresholds(unittest.TestCase):
    # Three thresholds tie for the best F1 (0.1, 0.4 and 0.7 each give 2/3).
    TIED = [(1.0, "same_event"), (0.9, "different"), (0.8, "same_event"), (0.7, "same_event"),
            (0.6, "different"), (0.5, "different"), (0.4, "same_event"), (0.3, "different"),
            (0.2, "different"), (0.1, "same_event")]

    def test_plateau_median_is_not_the_arg_max(self):
        self.assertEqual(run_eval.argmax_threshold(self.TIED), 0.1)
        self.assertEqual(run_eval.tune_threshold(self.TIED), 0.4, "median of the plateau 0.1 / 0.4 / 0.7")
        best = run_eval.confusion(self.TIED, 0.1)["f1"]
        for t in (0.1, 0.4, 0.7):
            self.assertAlmostEqual(run_eval.confusion(self.TIED, t)["f1"], best)

    def test_plateau_includes_thresholds_within_one_point_of_f1(self):
        rows = [(0.9, "same_event"), (0.7, "same_event"), (0.4, "different"), (0.1, "different")]
        self.assertEqual(run_eval.confusion(rows, run_eval.tune_threshold(rows))["f1"], 1.0)
        self.assertEqual(run_eval.argmax_threshold([]), 0.5)

    def test_certain_floor_ignores_a_lucky_pocket(self):
        rows = [(0.9, True), (0.8, True), (0.7, False), (0.6, True), (0.5, True), (0.4, True),
                (0.3, True), (0.2, True), (0.1, False)]
        # precision >= 0.8 holds again from 0.5 down to 0.2, but dips at 0.7
        # and 0.6: the tier starts where it holds at every higher threshold.
        self.assertEqual(run_eval.stable_floor(rows, 0.8), 0.8)
        self.assertIsNone(run_eval.stable_floor([(0.9, False)], 0.95))

    def test_choose_tiers_orders_and_never_groups_a_failed_guard(self):
        rows = [(0.0, 0.99, "same_event"), (0.97, 0.97, "same_event"), (0.95, 0.95, "same_event"),
                (0.9, 0.9, "same_event"), (0.6, 0.6, "related"), (0.5, 0.5, "same_event"),
                (0.3, 0.3, "related"), (0.2, 0.2, "different"), (0.1, 0.1, "different")]
        t = run_eval.choose_tiers(rows)
        self.assertGreaterEqual(t["certain"], t["probable"])
        self.assertGreaterEqual(t["probable"], t["possible"])
        self.assertEqual(t["certain"], 0.9)
        failed = {"raw": 0.99, "guard_ok": False}
        self.assertEqual(run_eval.row_tier(failed, t), "possible")

    def test_wilson_interval(self):
        self.assertEqual(run_eval.wilson(0, 0), (0.0, 1.0))
        lo, hi = run_eval.wilson(10, 10)
        self.assertAlmostEqual(lo, 0.7225, places=3)
        self.assertAlmostEqual(hi, 1.0, places=12)
        lo, hi = run_eval.wilson(33, 34)
        self.assertLess(lo, 0.95)
        self.assertGreater(hi, 0.97)


class ComponentSplit(unittest.TestCase):
    PAIRS = [{"pair_id": "p%d" % k, "a_id": a, "b_id": b, "label": "same_event", "split": "dev"}
             for k, (a, b) in enumerate([("a", "b"), ("b", "c"), ("d", "e"), ("f", "g"), ("g", "h"),
                                         ("i", "j"), ("k", "l"), ("m", "n"), ("o", "p"), ("q", "r")])]

    def test_no_item_on_both_sides_and_deterministic(self):
        side = run_eval.component_split(self.PAIRS)
        self.assertEqual(side, run_eval.component_split(list(reversed(self.PAIRS))))
        seen: dict[str, set] = {}
        for p in self.PAIRS:
            for i in (p["a_id"], p["b_id"]):
                seen.setdefault(i, set()).add(side[p["pair_id"]])
        self.assertTrue(all(len(s) == 1 for s in seen.values()))
        self.assertEqual(side["p0"], side["p1"], "a component moves as a whole")
        comps = run_eval.pair_components(self.PAIRS)
        self.assertEqual(len(comps), 8)


class Protocol(unittest.TestCase):
    def test_run_tunes_on_dev_and_reports_test_and_missing(self):
        items = {c["id"]: c for c in ITEMS}
        table = {("a1", "a2"): 0.9, ("a1", "a3"): 0.8, ("a1", "b1"): 0.3, ("b1", "b2"): 0.6,
                 ("a2", "a3"): 0.7, ("a2", "b2"): 0.2, ("a3", "b1"): 0.75}
        gold = run_eval.load_gold_from_doc(gold_doc())
        res = run_eval.run(gold, items, [FixedScorer(table)], log=lambda *a: None)
        self.assertEqual(res["gold"]["missing"], 1)
        e = res["scorers"]["fixed"]
        self.assertEqual(e["dev"]["n"], 4)
        self.assertEqual(e["test"]["n"], 3)
        # Dev F1 is perfect for any threshold in (0.6, 0.8]; test then shows
        # the related pair a3/b1 (0.75) as a false merge or not, honestly.
        self.assertGreater(e["threshold"], 0.6)
        self.assertLessEqual(e["threshold"], 0.8)
        self.assertEqual(e["test"]["by_lang_pair"]["fr-en"]["n"], 2)
        self.assertIn("clusters", e["test"])

    def test_report_and_json_never_carry_publisher_text(self):
        items = {c["id"]: c for c in ITEMS}
        gold = run_eval.load_gold_from_doc(gold_doc())
        scorers = run_eval.build_scorers(["baseline", "graded", "graded-guard", "shipped"], [])
        res = run_eval.run(gold, items, scorers, log=lambda *a: None)
        res["component_split"] = run_eval.run_component_split(gold, items, log=lambda *a: None)
        block = run_eval.render(res, "0" * 64)
        blob = block + json.dumps(res, ensure_ascii=False)
        for marker in ("Zorblax", "zorblax", "Quillimet", "quillimet", "entrepôt", "warehouse", "Limoilou"):
            self.assertNotIn(marker, blob)
        self.assertTrue(block.startswith(run_eval.BEGIN) and block.endswith(run_eval.END))

    def test_shipped_scorer_uses_the_frozen_constants(self):
        import event_match

        items = {c["id"]: c for c in ITEMS}
        gold = run_eval.load_gold_from_doc(gold_doc())
        res = run_eval.run(gold, items, run_eval.build_scorers(["shipped"], []), log=lambda *a: None)
        e = res["scorers"]["shipped"]
        self.assertEqual(e["tiers"], event_match.THRESHOLDS)
        self.assertTrue(e["tiers_fixed"])
        self.assertEqual(e["weights"], event_match.WEIGHTS)
        self.assertEqual(e["threshold"], event_match.THRESHOLDS["probable"])
        self.assertIn("certain", e["test"]["tiers"]["all"]["bands"])
        self.assertIn("blocking", e)

    def test_provenance_travels_with_every_report(self):
        items = {c["id"]: c for c in ITEMS}
        doc = gold_doc()
        doc["labelers_provenance"] = "invented test labeller, three passes"
        res = run_eval.run(run_eval.load_gold_from_doc(doc), items, [FixedScorer({})], do_clusters=False,
                           log=lambda *a: None)
        self.assertEqual(res["gold"]["provenance"], "invented test labeller, three passes")
        self.assertIn("invented test labeller, three passes", run_eval.render(res, "0" * 64))
        bare = run_eval.run(run_eval.load_gold_from_doc(gold_doc()), items, [FixedScorer({})], do_clusters=False,
                            log=lambda *a: None)
        self.assertIn("NOT RECORDED", run_eval.render(bare, "0" * 64))

    def test_cluster_table_carries_the_all_singletons_reference(self):
        items = {c["id"]: c for c in ITEMS}
        gold = run_eval.load_gold_from_doc(gold_doc())
        res = run_eval.run(gold, items, [FixedScorer({("a1", "a2"): 0.9})], log=lambda *a: None)
        c = res["scorers"]["fixed"]["test"]["clusters"]
        ids = c["items"]
        self.assertEqual(c["bcubed_all_singletons"]["precision"], 1.0)
        self.assertLessEqual(c["gold_singleton_items"], ids)
        block = run_eval.render(res, "0" * 64)
        self.assertIn("all-singletons (reference)", block)
        self.assertIn("Induced pairwise F1 is the cluster-level figure", block)

    def test_fill_report_replaces_only_the_marked_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "REPORT.md"
            path.write_text("# Title\n\nintro\n\n" + run_eval.BEGIN + "\nold\n" + run_eval.END + "\n\noutro\n",
                            encoding="utf-8")
            run_eval.fill_report(path, run_eval.BEGIN + "\nnew\n" + run_eval.END)
            text = path.read_text(encoding="utf-8")
            self.assertIn("intro", text)
            self.assertIn("outro", text)
            self.assertIn("new", text)
            self.assertNotIn("old", text)

    def test_committed_report_template_has_markers(self):
        text = (EVAL / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn(run_eval.BEGIN, text)
        self.assertIn(run_eval.END, text)


class GracefulSkip(unittest.TestCase):
    def _main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = run_eval.main(list(argv))
        return code, out.getvalue()

    def test_main_prints_the_labeller_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            doc = gold_doc()
            doc["labelers_provenance"] = "invented: one model, three passes"
            gold = tmp / "gold.json"
            gold.write_text(json.dumps(doc), encoding="utf-8")
            store = tmp / "normalized"
            store.mkdir()
            (store / "20260920T000000Z_candidates.json").write_text(json.dumps({"candidates": ITEMS}), encoding="utf-8")
            code, out = self._main("--gold", str(gold), "--data-dir", str(store), "--scorers", "baseline",
                                   "--no-component-split", "--no-clusters")
        self.assertEqual(code, 0)
        self.assertIn("gold labels by: invented: one model, three passes", out)
        self.assertIn("model-labelled and relative", out)

    def test_missing_gold_exits_zero_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main("--gold", str(Path(tmp) / "absent.json"), "--data-dir", tmp)
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)

    def test_missing_store_exits_zero_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            gold = Path(tmp) / "gold.json"
            gold.write_text(json.dumps(gold_doc()), encoding="utf-8")
            code, out = self._main("--gold", str(gold), "--data-dir", str(Path(tmp) / "nope"))
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)

    def test_end_to_end_on_a_synthetic_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            gold = tmp / "gold.json"
            gold.write_text(json.dumps(gold_doc()), encoding="utf-8")
            store = tmp / "normalized"
            store.mkdir()
            (store / "20260920T000000Z_candidates.json").write_text(
                json.dumps({"candidates": ITEMS}), encoding="utf-8")
            report = tmp / "REPORT.md"
            report.write_text("x\n" + run_eval.BEGIN + "\n" + run_eval.END + "\n", encoding="utf-8")
            out_json = tmp / "res.json"
            code, _ = self._main("--gold", str(gold), "--data-dir", str(store), "--report", str(report),
                                 "--json", str(out_json), "--scorers", "baseline,graded-prior")
            self.assertEqual(code, 0)
            first = report.read_text(encoding="utf-8")
            self.assertIn("| baseline |", first)
            self.assertIn("| graded-prior |", first)
            # Deterministic: a second run produces the same bytes.
            self._main("--gold", str(gold), "--data-dir", str(store), "--report", str(report),
                       "--scorers", "baseline,graded-prior")
            self.assertEqual(first, report.read_text(encoding="utf-8"))

    def test_error_dumps_refuse_public_paths(self):
        self.assertFalse(run_eval._private_path_ok(ROOT / "eval" / "dump"))
        self.assertTrue(run_eval._private_path_ok(ROOT / "data" / "eval"))
        code, _ = self._main("--dump-errors", str(ROOT / "eval" / "dump"))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
