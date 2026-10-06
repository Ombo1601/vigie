"""scripts/events_eval.py: the four gates, counts only, fail-soft, deterministic.

Every title below is invented ("Zorblax", "Quuxland"): no publisher text
exists in this file, and none may reach the output."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import harness  # noqa: F401  (puts scripts/ on sys.path)

import events_eval as ev  # noqa: E402
import registre  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "main_chain.json"


def item(i, title, when, lang="fr", institution="radio-canada", summary=""):
    return {"id": i, "title": title, "summary": summary, "published_at": when, "language": lang,
            "institution": institution, "institution_name": institution.replace("-", " ").title(),
            "source_id": institution}


FAMILIES = [("Zorblax", "Limoilou"), ("Quuxland", "Beauport"), ("Wibblefrost", "Charlesbourg"),
            ("Snorklewick", "Sillery"), ("Plimbarton", "Lebourgneuf"), ("Drazzmatic", "Saint-Roch")]


def family(n):
    """A French item and its English twin about one invented happening."""
    word, place = FAMILIES[n]
    day = 10 + n
    fr = item(f"f{n}a", f"Incendie {word} dans un entrepôt de {place}: {60 + n} 000 litres menacés",
              f"2026-09-{day}T08:00:00+00:00")
    en = item(f"f{n}b", f"{word} warehouse fire in {place} threatens {60 + n},000 litres",
              f"2026-09-{day}T09:00:00+00:00", "en", "cbc")
    return fr, en


def gold_for(n_fam):
    pairs = []
    for n in range(n_fam):
        pairs.append({"pair_id": f"p{n}s", "a_id": f"f{n}a", "b_id": f"f{n}b", "label": "same_event",
                      "split": "dev" if n % 2 == 0 else "test", "uncertain": False})
        m = (n + 1) % n_fam
        pairs.append({"pair_id": f"p{n}d", "a_id": f"f{n}a", "b_id": f"f{m}a", "label": "different",
                      "split": "test" if n % 2 == 0 else "dev", "uncertain": False})
    return {"version": "gold_test", "kappa": 0.5, "labelers": ["x"], "pairs": pairs}


def write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def write_store(root: Path, items: list[dict], edition="2026-09-20T16:00:00+00:00") -> Path:
    d = root / "data" / "normalized"
    write_json(d / "latest_candidates.json", {"normalized_at": edition, "candidates": items})
    return d


class Maths(unittest.TestCase):
    def test_wilson_is_honest_at_the_edges(self):
        lo, hi = ev.wilson(0, 0)
        self.assertEqual((lo, hi), (0.0, 1.0))
        lo, hi = ev.wilson(10, 10)
        self.assertLess(lo, 1.0)
        self.assertAlmostEqual(hi, 1.0)
        self.assertAlmostEqual(ev.wilson(33, 34)[0], 0.851, places=2)

    def test_zero_error_pairs_needed_matches_the_spec(self):
        self.assertEqual(ev.zero_error_pairs_needed(), 73)

    def test_band_counts(self):
        rows = [{"label": "same_event", "tier": "certain"}, {"label": "related", "tier": "certain"},
                {"label": "same_event", "tier": None}, {"label": "different", "tier": "probable"}]
        c = ev.band_counts(rows, ("certain",))
        self.assertEqual((c["predicted"], c["tp"], c["related"], c["positives"]), (2, 1, 1, 2))
        self.assertEqual(c["precision"], 0.5)
        self.assertEqual(c["recall"], 0.5)
        g = ev.band_counts(rows, ("certain", "probable"))
        self.assertEqual((g["predicted"], g["different"]), (3, 1))


class Gate1(unittest.TestCase):
    def setUp(self):
        self.items = {}
        for n in range(len(FAMILIES)):
            for it in family(n):
                self.items[it["id"]] = it

    def test_counts_only_and_never_open_on_a_model_gold(self):
        out = ev.gate_tiers(gold_for(6), self.items)
        self.assertEqual(out["status"], "measured")
        self.assertEqual(out["resolved"], 12)
        self.assertFalse(out["human_verified"])
        self.assertEqual(out["verdict"], "not_met")
        self.assertIn("not human-verified", out["reason"])
        self.assertIn("NOT RECORDED", out["provenance"])
        text = json.dumps(out)
        for word in ("Zorblax", "Limoilou", "f0a"):
            self.assertNotIn(word, text)
        test = out["splits"]["test"]
        self.assertEqual(test["n"], sum(test["label_counts"].values()))
        self.assertEqual(out["certain_test"]["zero_error_pairs_needed"], 73)

    def test_a_human_gold_still_needs_the_lower_bound(self):
        gold = gold_for(6)
        gold["human_verified"] = True
        gold["labelers_provenance"] = "two people, invented"
        out = ev.gate_tiers(gold, self.items)
        self.assertTrue(out["human_verified"])
        self.assertEqual(out["provenance"], "two people, invented")
        # a handful of pairs can never put the Wilson lower bound at 0.95
        self.assertEqual(out["verdict"], "not_met")
        self.assertIn("lower 95% bound", out["reason"])

    def test_unresolved_pairs_are_counted_and_all_missing_skips(self):
        gold = gold_for(6)
        gold["pairs"].append({"pair_id": "pz", "a_id": "nope", "b_id": "f0a", "label": "related",
                              "split": "dev"})
        out = ev.gate_tiers(gold, self.items)
        self.assertEqual(out["missing"], 1)
        with self.assertRaises(ev.Skip):
            ev.gate_tiers(gold_for(6), {"other": item("other", "x", "2026-09-10T08:00:00+00:00")})

    def test_load_gold_skips_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ev.Skip):
                ev.load_gold(Path(tmp) / "absent.json")
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ev.Skip):
                ev.load_gold(bad)
            write_json(Path(tmp) / "empty.json", {"pairs": [{"label": "weird"}]})
            with self.assertRaises(ev.Skip):
                ev.load_gold(Path(tmp) / "empty.json")


class Gate2(unittest.TestCase):
    TITLE = "Incendie Zorblax majeur dans un entrepôt désaffecté de Limoilou"
    EXCERPT = "Les pompiers combattent depuis minuit un brasier Wibblefrost qui menace trois bâtiments voisins."

    def make(self, tmp):
        root = Path(tmp)
        write_store(root, [item("a1", self.TITLE, "2026-09-20T08:00:00+00:00", summary=self.EXCERPT,
                                institution="le-soleil")])
        write_json(root / "data" / "registre" / "registre.json", {
            "method": registre.METHOD, "seals": [], "voice": [], "names": {"le-soleil": {"name": "Le Soleil"}}})
        return root

    def run_gate(self, root):
        return ev.gate_leak(root, root / "data" / "normalized")

    def test_clean_files_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make(tmp)
            (root / "public").mkdir()
            (root / "public" / "memoire.html").write_text(
                "<html><body><p>Vigie scelle des identifiants et des comptes, jamais un titre.</p></body></html>",
                encoding="utf-8")
            out = self.run_gate(root)
        self.assertEqual(out["verdict"], "met")
        self.assertGreaterEqual(out["files_scanned"], 2)
        self.assertEqual(out["grams_matched"], 0)

    def test_a_title_in_a_permanent_page_is_a_leak_even_through_markup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make(tmp)
            (root / "public" / "memoire").mkdir(parents=True)
            (root / "public" / "memoire" / "1.html").write_text(
                "<li>Incendie <b>Zorblax</b> majeur dans&nbsp;un entrepôt   désaffecté</li>", encoding="utf-8")
            out = self.run_gate(root)
        self.assertEqual(out["verdict"], "not_met")
        cat = out["categories"]["permanent_pages"]
        self.assertEqual((cat["files_with_hits"], cat["items_matched"], cat["items_matched_current"]), (1, 1, 1))
        self.assertGreater(cat["grams_matched"], 0)
        self.assertNotIn("Zorblax", json.dumps(out))

    def test_an_excerpt_in_a_sealed_record_is_a_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make(tmp)
            state = json.loads((root / "data" / "registre" / "registre.json").read_text(encoding="utf-8"))
            state["voice"] = [{"edition": "e", "note": self.EXCERPT.upper()}]
            write_json(root / "data" / "registre" / "registre.json", state)
            out = self.run_gate(root)
        self.assertEqual(out["categories"]["sealed_records"]["files_with_hits"], 1)
        self.assertEqual(out["verdict"], "not_met")

    def test_urls_are_not_scanned_and_names_are_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make(tmp)
            write_json(root / "data" / "events" / "latest_events.json", {"events": [{
                "event_id": "ev-0123456789abcdef",
                "members": [{"url": "https://exemple.invalid/incendie-zorblax-majeur-dans-un-entrepot-desaffecte-de-limoilou"}]}]})
            out = self.run_gate(root)
            self.assertEqual(out["categories"]["event_store"]["files_scanned"], 1)
            self.assertEqual(out["categories"]["event_store"]["grams_matched"], 0)
            # a gram that straddles an institution name is blanked by the mask
            it = item("a2", "Le Soleil publie un rapport inattendu sur Zorblax", "2026-09-20T09:00:00+00:00")
            write_store(root, [it])
            (root / "public").mkdir(exist_ok=True)
            (root / "public" / "registre.html").write_text("Radio Canada Le Soleil publie un rapport", encoding="utf-8")
            self.assertEqual(self.run_gate(root)["grams_matched"], 0)

    def test_short_generic_phrases_are_not_distinctive(self):
        self.assertFalse(ev.distinctive("de la ville de"))
        self.assertFalse(ev.distinctive("l agrandissement de l"))
        self.assertTrue(ev.distinctive("incendie zorblax majeur dans"))

    def test_nothing_to_scan_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_store(root, [item("a1", self.TITLE, "2026-09-20T08:00:00+00:00")])
            out = self.run_gate(root)
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["verdict"], "not_measurable")


class Gate3(unittest.TestCase):
    ED1 = [item("a1", "Incendie Zorblax dans un entrepôt de Limoilou: 60 000 litres menacés", "2026-09-20T08:00:00+00:00"),
           item("a2", "Zorblax warehouse fire in Limoilou threatens 60,000 litres", "2026-09-20T09:00:00+00:00", "en", "cbc"),
           item("b1", "Grève des chauffeurs d'autobus du RTC à Charlesbourg", "2026-09-20T10:00:00+00:00")]
    ED2 = ED1 + [item("a3", "Limoilou: l'incendie Zorblax de l'entrepôt est maîtrisé, 60 000 litres sauvés",
                      "2026-09-20T15:00:00+00:00", institution="le-soleil")]

    def store(self, tmp):
        d = Path(tmp) / "data" / "normalized"
        write_json(d / "20260920T120000Z_candidates.json", {"normalized_at": "2026-09-20T12:00:00+00:00", "candidates": self.ED1})
        write_json(d / "20260920T180000Z_candidates.json", {"normalized_at": "2026-09-20T18:00:00+00:00", "candidates": self.ED2})
        return d

    def test_replay_loses_no_id_and_moves_no_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = ev.gate_stability(self.store(tmp), Path(tmp))
        self.assertEqual(out["verdict"], "met")
        self.assertEqual((out["editions"], out["unique_items"]), (2, 4))
        self.assertEqual((out["id_changes"], out["memberships_moved"], out["event_ids_lost"]), (0, 0, 0))
        self.assertEqual(out["minted"] + out["attached"], 4)
        self.assertNotIn("Zorblax", json.dumps(out))

    def test_replay_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.store(tmp)
            a, b = ev.gate_stability(d), ev.gate_stability(d)
        self.assertEqual(a, b)
        self.assertEqual(len(a["final_digest"]), 64)

    def test_live_store_ids_are_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.store(tmp)
            import event_match as em
            write_json(Path(tmp) / "data" / "events" / "store.json", {"events": [
                {"event_id": em.event_id_for("a1")}, {"event_id": "ev-ffffffffffffffff"}]})
            out = ev.gate_stability(d, Path(tmp))
        self.assertEqual(out["live_store"]["events"], 2)
        self.assertEqual(out["live_store"]["ids_reproduced_by_replay"], 1)

    def test_no_stamped_snapshot_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "n"
            write_json(d / "latest_candidates.json", {"normalized_at": "2026-09-20T12:00:00+00:00", "candidates": self.ED1})
            with self.assertRaises(ev.Skip):
                ev.gate_stability(d)


class Gate4(unittest.TestCase):
    def test_the_frozen_golden_is_reproduced_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = ev.gate_main_chain(Path(tmp), GOLDEN)
        self.assertEqual(out["verdict"], "met")
        self.assertEqual((out["golden_editions"], out["golden_seals_identical"]), (3, 3))
        self.assertEqual(out["live_state"], {"present": False})

    def test_a_changed_golden_fails(self):
        doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
        doc["expected"][1]["root"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            g = Path(tmp) / "g.json"
            write_json(g, doc)
            out = ev.gate_main_chain(Path(tmp), g)
        self.assertEqual(out["verdict"], "not_met")
        self.assertEqual(out["golden_seals_identical"], 2)

    def test_the_golden_carries_no_new_field_and_no_publisher_text(self):
        doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
        state = registre.empty_state()
        for ed in doc["editions"]:
            registre.seal_edition(state, registre.edition_record(ed["payload"], ed["edition"], ed.get("collection")))
        for seal in state["seals"]:
            self.assertEqual(set(seal["record"]) - {"record_schema", "collection"},
                             {"method", "edition", "followed", "dossiers", "ledger"})
        self.assertNotIn("jamais être scellé", json.dumps([s["record"] for s in state["seals"]], ensure_ascii=False))

    def test_live_state_is_reverified_and_checked_against_the_anchor(self):
        doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
        state = registre.empty_state()
        for ed in doc["editions"]:
            registre.seal_edition(state, registre.edition_record(ed["payload"], ed["edition"], ed.get("collection")))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "data" / "registre" / "registre.json", state)
            (root / "anchors").mkdir()
            tip = state["seals"][-1]
            (root / "anchors" / "checkpoint.txt").write_text(
                f"{registre.ORIGIN}\n{tip['seq']}\n{tip['root']}\n\nedition {tip['edition']}\n", encoding="utf-8")
            out = ev.gate_main_chain(root, GOLDEN)
            self.assertEqual(out["verdict"], "met")
            self.assertTrue(out["live_state"]["chain_recomputes_byte_identical"])
            self.assertTrue(out["live_state"]["anchor_root_present_in_state"])
            # another root at the anchored seq is a fork
            (root / "anchors" / "checkpoint.txt").write_text(
                f"{registre.ORIGIN}\n{tip['seq']}\n{'f' * 64}\n\nedition x\n", encoding="utf-8")
            self.assertEqual(ev.gate_main_chain(root, GOLDEN)["verdict"], "not_met")
            # a state that stops before the anchor is an older copy, not a fork
            (root / "anchors" / "checkpoint.txt").write_text(
                f"{registre.ORIGIN}\n99\n{'f' * 64}\n\nedition x\n", encoding="utf-8")
            out = ev.gate_main_chain(root, GOLDEN)
            self.assertEqual(out["verdict"], "met")
            self.assertFalse(out["live_state"]["anchor_comparable"])
            # tamper with a sealed record: the chain no longer recomputes
            state["seals"][0]["record"]["followed"] = ["tampered"]
            write_json(root / "data" / "registre" / "registre.json", state)
            self.assertEqual(ev.gate_main_chain(root, GOLDEN)["verdict"], "not_met")

    def test_missing_golden_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ev.Skip):
                ev.gate_main_chain(Path(tmp), Path(tmp) / "none.json")


class Driver(unittest.TestCase):
    def main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ev.main(list(argv))
        return code, out.getvalue()

    def test_absent_inputs_skip_every_private_gate_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text = self.main("--root", tmp, "--golden", str(GOLDEN))
            result = json.loads((Path(tmp) / "data" / "ops" / "events_quality.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        g = result["gates"]
        for name in ("gate1_tier_precision", "gate2_publisher_text_leak", "gate3_id_stability"):
            self.assertEqual(g[name]["status"], "skipped", name)
            self.assertTrue(g[name]["reason"])
        self.assertEqual(g["gate4_main_chain_identity"]["verdict"], "met")
        self.assertFalse(result["all_gates_met"])

    def test_a_fault_inside_a_gate_is_a_diagnosed_skip(self):
        real = ev.gate_main_chain
        try:
            ev.gate_main_chain = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            with tempfile.TemporaryDirectory() as tmp:
                result = ev.run(Path(tmp), gates="4", log=lambda *_: None)
        finally:
            ev.gate_main_chain = real
        self.assertEqual(result["gates"]["gate4_main_chain_identity"]["status"], "skipped")
        self.assertIn("RuntimeError", result["gates"]["gate4_main_chain_identity"]["reason"])

    def test_full_run_writes_counts_only_and_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = {}
            for n in range(len(FAMILIES)):
                for it in family(n):
                    items[it["id"]] = it
            write_store(root, list(items.values()))
            d = root / "data" / "normalized"
            write_json(d / "20260920T120000Z_candidates.json",
                       {"normalized_at": "2026-09-20T12:00:00+00:00", "candidates": list(items.values())})
            write_json(root / "data" / "eval" / "gold_v0.json", gold_for(6))
            (root / "public").mkdir()
            (root / "public" / "registre.html").write_text("<p>Aucun texte d'éditeur ici.</p>", encoding="utf-8")
            out = root / "q.json"
            self.main("--root", tmp, "--golden", str(GOLDEN), "--out", str(out))
            first = out.read_bytes()
            self.main("--root", tmp, "--golden", str(GOLDEN), "--out", str(out))
            self.assertEqual(first, out.read_bytes())
            text = first.decode("utf-8")
            result = json.loads(text)
        for word in ("Zorblax", "Quuxland", "Limoilou", "warehouse"):
            self.assertNotIn(word, text)
        self.assertEqual(result["gates"]["gate1_tier_precision"]["status"], "measured")
        self.assertEqual(result["gates"]["gate2_publisher_text_leak"]["verdict"], "met")
        self.assertEqual(result["gates"]["gate3_id_stability"]["verdict"], "met")
        self.assertNotIn("seconds", text)  # no timing, no clock: only counts

    def test_output_is_independent_of_hash_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = [it for n in range(3) for it in family(n)]
            d = root / "data" / "normalized"
            write_json(d / "20260920T120000Z_candidates.json",
                       {"normalized_at": "2026-09-20T12:00:00+00:00", "candidates": items})
            write_json(root / "data" / "eval" / "gold_v0.json", gold_for(3))
            outs = []
            for seed in ("0", "1", "777"):
                target = root / f"out{seed}.json"
                env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONUTF8="1")
                subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "events_eval.py"),
                                "--root", tmp, "--golden", str(GOLDEN), "--out", str(target)],
                               check=True, capture_output=True, env=env)
                outs.append(target.read_bytes())
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[1], outs[2])


if __name__ == "__main__":
    unittest.main()
