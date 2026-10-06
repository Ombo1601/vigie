"""eval/spotcheck.py and eval/spotcheck_import.py on invented fixtures.

Every headline is invented ("Zorblax" and friends). The generated page must
not carry the model's label, split, stratum or reason; the importer must
compute Cohen's kappa correctly and publish counts only."""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import harness  # noqa: F401  (puts scripts/ on sys.path)

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import run_eval  # noqa: E402
import spotcheck  # noqa: E402
import spotcheck_import as imp  # noqa: E402

WORDS = ["Zorblax", "Quuxland", "Wibblefrost", "Snorklewick", "Plimbarton", "Drazzmatic",
         "Frobnitz", "Gloopendale", "Hexaglyph", "Jibberwock", "Kvetchmire", "Lumpernickel"]
PLACES = ["Limoilou", "Beauport", "Charlesbourg", "Sillery", "Lebourgneuf", "Saint-Roch"]
REASON = "SECRETREASON model says these are alike"


def make_items(n=12):
    items = {}
    for i in range(n):
        word, place = WORDS[i], PLACES[i % len(PLACES)]
        day = 1 + i
        items[f"fr{i}"] = {"id": f"fr{i}", "title": f"Incendie {word} dans un entrepôt de {place}: {60 + i} 000 litres",
                           "summary": f"Les pompiers de {place} combattent le brasier {word}.",
                           "published_at": f"2026-09-{day:02d}T08:00:00+00:00", "language": "fr",
                           "institution": "radio-canada", "source_id": "radio-canada-quebec",
                           "institution_name": "Radio-Canada"}
        items[f"en{i}"] = {"id": f"en{i}", "title": f"{word} warehouse fire in {place} threatens {60 + i},000 litres",
                           "summary": "", "published_at": f"2026-09-{day:02d}T09:00:00+00:00", "language": "en",
                           "institution": "cbc", "source_id": "cbc-montreal", "source_name": "CBC Montréal"}
    return items


def make_gold(n=12):
    pairs = []
    for i in range(n):  # fr-en same_event
        pairs.append({"pair_id": f"s{i:02d}", "a_id": f"fr{i}", "b_id": f"en{i}", "label": "same_event",
                      "split": "dev" if i % 2 else "test", "uncertain": i == 0, "adjudicated": i == 1,
                      "reason": REASON})
    for i in range(n):
        j = (i + 1) % n
        pairs.append({"pair_id": f"r{i:02d}", "a_id": f"fr{i}", "b_id": f"fr{j}", "label": "related",
                      "split": "dev" if i % 2 else "test", "uncertain": False, "reason": REASON})
        pairs.append({"pair_id": f"d{i:02d}", "a_id": f"en{i}", "b_id": f"fr{j}", "label": "different",
                      "split": "test" if i % 2 else "dev", "uncertain": i == 3, "reason": REASON})
        pairs.append({"pair_id": f"e{i:02d}", "a_id": f"fr{i}", "b_id": f"fr{(i + 2) % n}", "label": "same_event",
                      "split": "dev" if i % 3 else "test", "uncertain": False, "reason": REASON})
    return {"version": "gold_test", "labelers": ["m"], "pairs": pairs}


class Sampling(unittest.TestCase):
    def setUp(self):
        self.items, self.gold = make_items(), make_gold()

    def sample(self, **kw):
        return spotcheck.sample_pairs(self.gold["pairs"], self.items, **kw)

    def test_strata(self):
        pairs, counts = self.sample(seed=5, n_random=2)
        ids = {p["pair_id"] for p in pairs}
        uncertain = {p["pair_id"] for p in self.gold["pairs"] if p["uncertain"]}
        fr_en = {p["pair_id"] for p in self.gold["pairs"] if p["pair_id"].startswith("s")}
        self.assertTrue(uncertain <= ids, "every uncertain pair is in")
        self.assertTrue(fr_en <= ids, "every fr-en same_event pair is in")
        self.assertEqual(counts["total"], len(pairs))
        self.assertEqual(len(ids), len(pairs), "no pair twice")
        self.assertEqual(counts["random_related"], 2)
        self.assertEqual(counts["random_same_event"], 2)
        self.assertEqual(counts["uncertain"], 2)
        self.assertEqual(counts["fr_en_same_event"], 11, "s00 is already in the uncertain stratum")

    def test_seed_is_deterministic_and_matters(self):
        a, _ = self.sample(seed=1, n_random=3)
        b, _ = self.sample(seed=1, n_random=3)
        c, _ = self.sample(seed=2, n_random=3)
        self.assertEqual([p["pair_id"] for p in a], [p["pair_id"] for p in b])
        self.assertNotEqual([p["pair_id"] for p in a], [p["pair_id"] for p in c])

    def test_cap_on_fr_en(self):
        pairs, counts = self.sample(seed=1, n_random=0, max_fr_en=3)
        self.assertLessEqual(counts["fr_en_same_event"], 3)

    def test_unresolved_pairs_are_dropped_and_counted(self):
        gold = make_gold()
        gold["pairs"].append({"pair_id": "zz", "a_id": "ghost", "b_id": "fr0", "label": "related", "split": "dev",
                              "uncertain": True})
        pairs, counts = spotcheck.sample_pairs(gold["pairs"], self.items, seed=1)
        self.assertEqual(counts["unresolved"], 1)
        self.assertNotIn("zz", {p["pair_id"] for p in pairs})


class Page(unittest.TestCase):
    def setUp(self):
        self.items, self.gold = make_items(), make_gold()
        self.page, self.counts = spotcheck.build(self.gold, self.items, seed=11, n_random=2)

    def data(self, page=None):
        m = re.search(r'<script type="application/json" id="pairs">(.*?)</script>', page or self.page, re.S)
        return json.loads(m.group(1))

    def test_generation_is_byte_deterministic(self):
        again, _ = spotcheck.build(make_gold(), make_items(), seed=11, n_random=2)
        self.assertEqual(again, self.page)
        other, _ = spotcheck.build(make_gold(), make_items(), seed=12, n_random=2)
        self.assertNotEqual(other, self.page)
        self.assertNotIn("\r", self.page)

    def test_the_page_carries_no_model_label_split_stratum_or_reason(self):
        data = self.data()
        self.assertEqual(set(data), {"schema", "pairs", "run"})
        for row in data["pairs"]:
            self.assertEqual(set(row), {"id", "a", "b", "gap"})
            for side in (row["a"], row["b"]):
                self.assertEqual(set(side), {"outlet", "lang", "when", "title", "excerpt"})
        blob = json.dumps(data)
        for secret in (REASON, "SECRETREASON", "adjudicated", "uncertain", "split", "same_event", "related",
                       "stratum", "label"):
            self.assertNotIn(secret, blob, secret)
        self.assertNotIn(REASON, self.page)

    def test_order_is_shuffled_not_sorted_by_label(self):
        ids = [r["id"] for r in self.data()["pairs"]]
        self.assertNotEqual(ids, sorted(ids))
        letters = "".join(i[0] for i in ids)
        self.assertNotEqual(letters, "".join(sorted(letters)), "labels are not grouped")

    def test_two_headlines_with_outlet_and_time(self):
        row = self.data()["pairs"][0]
        for side in (row["a"], row["b"]):
            self.assertTrue(side["title"] and side["outlet"])
            self.assertRegex(side["when"], r"^\d{4}-\d\d-\d\d \d\d:\d\d UTC$")
        self.assertIn("écart de publication", row["gap"])

    def test_self_contained_french_light_dark_mobile_no_network(self):
        p = self.page
        self.assertIn('<html lang="fr">', p)
        self.assertIn("prefers-color-scheme:dark", p)
        self.assertIn("max-width:640px", p)
        self.assertIn('name="viewport"', p)
        self.assertIn("default-src 'none'", p)
        self.assertNotRegex(p, r"https?://")
        self.assertNotRegex(p, r'(?:src|href)="(?!#)')
        self.assertNotIn("@import", p)
        self.assertNotIn("fonts.googleapis", p)
        for text in ("M\\u00eame \\u00e9v\\u00e9nement", "Li\\u00e9", "Diff\\u00e9rent", "Incertain",
                     "Exporter mes étiquettes", "localStorage"):
            self.assertIn(text, p, text)
        for key in ('"1"', '"4"', "ArrowRight", "ArrowLeft"):
            self.assertIn(key, p)
        self.assertIn("try {", p, "localStorage access is guarded")

    def test_publisher_text_cannot_break_out_of_the_script(self):
        items = make_items()
        items["fr0"]["title"] = "</script><img src=x onerror=alert(1)> & <!-- \u2028"
        page, _ = spotcheck.build(make_gold(), items, seed=11, n_random=2)
        self.assertEqual(page.count("</script>"), 2, "only the two real closing tags")
        self.assertNotIn("<img", page)
        self.assertEqual(self.data(page)["run"], self.data()["run"])
        titles = [s["title"] for r in self.data(page)["pairs"] for s in (r["a"], r["b"])]
        self.assertTrue(any(t.startswith("</script>") for t in titles), "round-trips verbatim")


class Cli(unittest.TestCase):
    def run_cli(self, mod, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out):
            code = mod.main(list(argv))
        return code, out.getvalue()

    def write(self, tmp):
        d = Path(tmp)
        (d / "gold.json").write_text(json.dumps(make_gold()), encoding="utf-8")
        (d / "items.json").write_text(json.dumps(list(make_items().values())), encoding="utf-8")
        return d

    def test_writes_one_file_and_prints_counts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.write(tmp)
            code, out = self.run_cli(spotcheck, "--out", str(d / "out" / "s.html"), "--gold", str(d / "gold.json"),
                                     "--items", str(d / "items.json"), "--random", "2")
            self.assertEqual(code, 0)
            self.assertTrue((d / "out" / "s.html").is_file())
            self.assertNotIn("Zorblax", out)
            self.assertIn("pairs", out)

    def test_refuses_a_path_inside_the_repository_except_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.write(tmp)
            target = ROOT / "eval" / "_spotcheck_must_not_exist.html"
            code, _ = self.run_cli(spotcheck, "--out", str(target), "--gold", str(d / "gold.json"),
                                   "--items", str(d / "items.json"))
            self.assertEqual(code, 2)
            self.assertFalse(target.exists())
        self.assertFalse(run_eval._private_path_ok(ROOT / "public" / "spot.html"))
        self.assertTrue(run_eval._private_path_ok(ROOT / "data" / "eval" / "spot.html"))
        self.assertTrue(run_eval._private_path_ok(Path(tempfile.gettempdir()) / "spot.html"))

    def test_absent_inputs_skip_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_cli(spotcheck, "--out", str(Path(tmp) / "s.html"),
                                     "--gold", str(Path(tmp) / "nope.json"))
            self.assertEqual(code, 0)
            self.assertIn("skipped", out)
            self.assertFalse((Path(tmp) / "s.html").exists())


class Kappa(unittest.TestCase):
    def test_perfect_agreement(self):
        k = imp.cohen_kappa(["same_event", "related", "different"], ["same_event", "related", "different"])
        self.assertEqual(k["kappa"], 1.0)
        self.assertEqual(k["agreement"], 1.0)

    def test_known_value(self):
        # model a a b b, human a b b b: po 0.75, pe 0.5*0.25 + 0.5*0.75 = 0.5, kappa 0.5
        k = imp.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "b", "b"], ("a", "b"))
        self.assertEqual((k["agreement"], k["pe"], k["kappa"]), (0.75, 0.5, 0.5))

    def test_known_value_three_classes(self):
        model = ["same_event"] * 5 + ["related"] * 3 + ["different"] * 2
        human = ["same_event"] * 4 + ["related"] + ["related"] * 3 + ["different"] * 2
        # po 9/10; pe = .5*.4 + .3*.4 + .2*.2 = .36; kappa = (.9-.36)/.64 = .84375
        k = imp.cohen_kappa(model, human)
        self.assertEqual(k["agree"], 9)
        self.assertAlmostEqual(k["kappa"], 0.8438, places=4)

    def test_chance_and_disagreement(self):
        self.assertEqual(imp.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"], ("a", "b"))["kappa"], 0.0)
        self.assertEqual(imp.cohen_kappa(["a", "a", "b", "b"], ["b", "b", "a", "a"], ("a", "b"))["kappa"], -1.0)

    def test_degenerate_raters(self):
        self.assertEqual(imp.cohen_kappa(["a", "a"], ["a", "a"], ("a", "b"))["kappa"], 1.0)
        self.assertEqual(imp.cohen_kappa([], [])["kappa"], None)
        self.assertEqual(imp.cohen_kappa(["a"], ["a", "b"])["kappa"], None)

    def test_confusion_matrix_keeps_uncertain_apart(self):
        m = imp.confusion_matrix(["same_event", "same_event", "related", "different"],
                                 ["same_event", "uncertain", "different", "different"])
        self.assertEqual(m["same_event"], {"same_event": 1, "related": 0, "different": 0, "uncertain": 1})
        self.assertEqual(m["related"]["different"], 1)
        self.assertEqual(m["different"]["different"], 1)


class Import(unittest.TestCase):
    def labels_doc(self, gold, flip=()):
        rows = []
        for p in gold["pairs"][:40]:
            lab = p["label"]
            if p["pair_id"] in flip:
                lab = "uncertain" if lab == "related" else "related" if lab == "same_event" else "same_event"
            rows.append({"pair_id": p["pair_id"], "label": lab})
        return {"schema": 1, "kind": "vigie-spotcheck-labels", "labels": rows}

    def test_load_labels_validates_and_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
            a.write_text(json.dumps({"labels": [{"pair_id": "x", "label": "related"}, {"pair_id": "y", "label": "bogus"},
                                                {"label": "related"}, "junk"]}), encoding="utf-8")
            b.write_text(json.dumps({"labels": [{"pair_id": "x", "label": "different"}]}), encoding="utf-8")
            labels, info = imp.load_labels([a, b])
            self.assertEqual(labels, {"x": "different"})
            self.assertEqual((info["files"], info["rows"], info["ignored"], info["overwritten"]), (2, 5, 3, 1))
            with self.assertRaises(run_eval.Skip):
                imp.load_labels([Path(tmp) / "absent.json"])
            c = Path(tmp) / "c.json"
            c.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(run_eval.Skip):
                imp.load_labels([c])
            d = Path(tmp) / "d.json"
            d.write_text(json.dumps({"labels": []}), encoding="utf-8")
            with self.assertRaises(run_eval.Skip):
                imp.load_labels([d])

    def test_agreement_report_counts(self):
        gold = run_eval.load_gold_from_doc(make_gold())
        doc = self.labels_doc(gold, flip={"s01", "r03"})
        human = {r["pair_id"]: r["label"] for r in doc["labels"]}
        rep = imp.agreement_report(gold["pairs"], human, make_items())
        self.assertEqual(rep["matched_gold_pairs"], 40)
        self.assertEqual(rep["unknown_pair_ids"], 0)
        self.assertEqual(rep["uncertain_answers"], 1)
        self.assertEqual(rep["decided"], 39)
        self.assertEqual(rep["three_class"]["agree"], 38)
        cm = rep["confusion_model_rows_human_cols"]
        self.assertEqual(cm["same_event"]["related"], 1)
        self.assertEqual(cm["related"]["uncertain"], 1)
        self.assertIn("fr_en_same_event", rep["by_stratum"])

    def test_threshold_report_replaces_labels_and_drops_uncertain(self):
        gold = run_eval.load_gold_from_doc(make_gold())
        items = make_items()
        scored = imp.score_pairs(gold["pairs"], items)
        self.assertEqual(len(scored), len(gold["pairs"]))
        human = {r["pair_id"]: r["label"] for r in self.labels_doc(gold, flip={"s01", "r03"})["labels"]}
        rep = imp.threshold_report(scored, human)
        self.assertEqual(rep["pairs_replaced"], 39)
        self.assertEqual(rep["pairs_dropped_uncertain"], 1)
        self.assertEqual(rep["pairs_whose_label_changed"], 1)
        self.assertEqual(rep["model_labels"]["pairs"], len(gold["pairs"]))
        self.assertEqual(rep["human_labels_replace"]["pairs"], len(gold["pairs"]) - 1)
        for k in ("certain", "probable", "possible"):
            self.assertIn(k, rep["model_labels"]["rechosen_thresholds"])
        # identical labels change nothing
        same = imp.threshold_report(scored, {p["pair_id"]: p["label"] for p in gold["pairs"][:40]})
        self.assertEqual(same["certain_threshold_shift"], 0.0)
        self.assertEqual(same["model_labels"], same["human_labels_replace"])

    def test_cli_prints_and_writes_counts_only(self):
        gold = make_gold()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "gold.json").write_text(json.dumps(gold), encoding="utf-8")
            (d / "items.json").write_text(json.dumps(list(make_items().values())), encoding="utf-8")
            (d / "labels.json").write_text(json.dumps(self.labels_doc(run_eval.load_gold_from_doc(gold), {"s01"})),
                                           encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = imp.main(["--labels", str(d / "labels.json"), "--gold", str(d / "gold.json"),
                                 "--items", str(d / "items.json"), "--json", str(d / "res.json")])
            text = (d / "res.json").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        res = json.loads(text)
        self.assertIn("kappa", res["agreement"]["three_class"])
        self.assertIn("rechosen_thresholds", res["thresholds"]["model_labels"])
        everything = out.getvalue() + text
        for secret in ("Zorblax", "Limoilou", "warehouse", "fr0", "s01", REASON):
            self.assertNotIn(secret, everything, secret)
        self.assertIn("kappa", out.getvalue())

    def test_cli_without_items_still_reports_agreement(self):
        gold = make_gold()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "gold.json").write_text(json.dumps(gold), encoding="utf-8")
            (d / "labels.json").write_text(json.dumps(self.labels_doc(run_eval.load_gold_from_doc(gold))), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = imp.main(["--labels", str(d / "labels.json"), "--gold", str(d / "gold.json"),
                                 "--data-dir", str(d / "nostore")])
        self.assertEqual(code, 0)
        self.assertIn("thresholds skipped", out.getvalue())
        self.assertIn("kappa", out.getvalue())

    def test_cli_absent_labels_skips(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = imp.main(["--labels", str(Path(tempfile.gettempdir()) / "definitely-absent-labels.json")])
        self.assertEqual(code, 0)
        self.assertIn("skipped", out.getvalue())


if __name__ == "__main__":
    unittest.main()
