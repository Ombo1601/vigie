"""eval/baseline_current.py wraps the live rule, it does not re-implement it."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import harness  # noqa: F401  (hermetic ingest_rss policy path)
import cluster_issues

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import baseline_current  # noqa: E402


def item(i, title, when="2026-09-20T08:00:00+00:00", lang="fr", summary=""):
    return {"id": i, "title": title, "summary": summary, "published_at": when, "language": lang}


# Invented headlines.
A = item("a", "Collision majeure boulevard Hamel entre un camion et trois voitures")
B = item("b", "Collision majeure boulevard Hamel: un camion percute trois voitures", "2026-09-20T09:00:00+00:00")
C = item("c", "Collision majeure boulevard Charest entre un camion et trois voitures")
SCAR_1 = item("m1", "Bruno Marchand présente ses priorités pour Limoilou")
SCAR_2 = item("m2", "Le maire Marchand dévoile sa liste de priorités", "2026-09-20T10:00:00+00:00")
SCAR_IMM = item("m3", "Marchand et l'immigration: nouvelle demande à Québec")
ALL = [A, B, C, SCAR_1, SCAR_2, SCAR_IMM]


class BaselineWrapsLiveRule(unittest.TestCase):
    def test_features_only_equals_same_event(self):
        scorer = baseline_current.BaselineCurrent(scars=False)
        for x in ALL:
            for y in ALL:
                if x is y:
                    continue
                self.assertEqual(scorer.score(x, y), 1.0 if cluster_issues.same_event(x, y) else 0.0)

    def test_pipeline_rule_respects_scars(self):
        scorer = baseline_current.BaselineCurrent(scars=True)
        self.assertEqual(cluster_issues.event_scar(SCAR_1), cluster_issues.event_scar(SCAR_2))
        self.assertEqual(scorer.score(SCAR_1, SCAR_2), 1.0)
        self.assertEqual(scorer.score(SCAR_1, SCAR_IMM), 0.0, "different scars never merge")
        self.assertEqual(scorer.score(SCAR_1, A), 0.0, "a scarred item never joins an automatic group")
        self.assertEqual(scorer.score(A, C), 0.0, "different roads never merge")
        self.assertTrue(scorer.binary)

    def test_cluster_runs_event_buckets_and_keeps_singletons(self):
        scorer = baseline_current.BaselineCurrent(scars=True)
        groups = scorer.cluster(ALL)
        flat = sorted(i for g in groups for i in g)
        self.assertEqual(flat, sorted(x["id"] for x in ALL))
        owner = {i: n for n, g in enumerate(groups) for i in g}
        self.assertEqual(owner["m1"] == owner["m2"], True)
        self.assertNotEqual(owner["m1"], owner["m3"])
        self.assertEqual(owner["a"] == owner["b"], scorer.score(A, B) == 1.0)

    def test_features_only_cluster_restores_the_module(self):
        original = cluster_issues.event_scar
        baseline_current.BaselineCurrent(scars=False).cluster(ALL)
        self.assertIs(cluster_issues.event_scar, original)


if __name__ == "__main__":
    unittest.main()
