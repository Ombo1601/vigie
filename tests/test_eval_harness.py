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
        scorers = run_eval.build_scorers(["baseline", "graded"], [])
        res = run_eval.run(gold, items, scorers, log=lambda *a: None)
        block = run_eval.render(res, "0" * 64)
        blob = block + json.dumps(res, ensure_ascii=False)
        for marker in ("Zorblax", "zorblax", "Quillimet", "quillimet", "entrepôt", "warehouse"):
            self.assertNotIn(marker, blob)
        self.assertTrue(block.startswith(run_eval.BEGIN) and block.endswith(run_eval.END))

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
