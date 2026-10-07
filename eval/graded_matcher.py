"""Graded same-event matcher, as an evaluation scorer over the PRODUCTION module.

Phase 0 prototyped the matcher here; Phase 1 moved it to
scripts/event_match.py (and the lexicon to scripts/event_lexicon.py). This
file is now a thin adapter: every feature, score, tier, guard and cluster
comes from the module that ships, so eval/run_eval.py measures what ships.
The only thing that lives here is the logistic refit on DEV pairs, which is
evaluation-only (production reads the frozen event_match.WEIGHTS).

Scorer variants built by eval/run_eval.py:

  graded-prior  hand-set PRIOR_WEIGHTS, no FR/EN guard, thresholds chosen on dev
  graded        weights refit on dev, no guard, thresholds chosen on dev
  graded-guard  refit on dev + FR/EN guard + tiers chosen on dev: the
                procedure that produced the shipped constants
  shipped       frozen event_match.WEIGHTS + THRESHOLDS + guard: what ships
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import event_match as em  # noqa: E402
from event_match import (  # noqa: E402,F401  (re-exported for older imports and tests)
    FEATURES, PRIOR_WEIGHTS, ItemFeatures, Idf, canon_token, char_grams, dates_of, language_of,
    names_of, numbers_of, published_when, stem,
)

METHOD = em.METHOD


def fit_weights(rows: list[tuple[dict, int]], prior: dict, l2: float = 0.002, epochs: int = 4000,
                rate: float = 0.5) -> dict:
    """Logistic regression on dev component rows, L2-pulled toward the prior.

    Plain batch gradient descent with a fixed schedule over rows in a fixed
    order (dev pairs sorted by pair id) and features in FEATURES order:
    deterministic, no set or dict-hash order anywhere.
    """
    keys = ("bias",) + FEATURES
    w = {k: prior.get(k, 0.0) for k in keys}
    n = float(len(rows))
    for _ in range(epochs):
        grad = dict.fromkeys(keys, 0.0)
        for x, y in rows:
            z = w["bias"] + sum(w[k] * x[k] for k in FEATURES)
            err = em._sigmoid(z) - y
            grad["bias"] += err
            for k in FEATURES:
                grad[k] += err * x[k]
        for k in keys:
            penalty = 0.0 if k == "bias" else l2 * (w[k] - prior.get(k, 0.0))
            w[k] -= rate * (grad[k] / n + penalty)
    return {k: round(v, 4) for k, v in w.items()}


class GradedMatcher:
    """Pluggable pair scorer for eval/run_eval.py, backed by scripts/event_match.py."""

    binary = False

    def __init__(self, weights: dict | None = None, fit: bool = True, name: str | None = None,
                 l2: float = 0.002, epochs: int = 4000, rate: float = 0.5, guard: bool = False,
                 thresholds: dict | None = None):
        # l2 chosen by 2-fold cross-validation INSIDE the dev split in Phase 0
        # (0.2 / 0.05 / 0.01 / 0.002); the test split played no part.
        self.weights = dict(PRIOR_WEIGHTS if weights is None else weights)
        self.prior = dict(self.weights)
        self.do_fit = fit
        self.name = name or ("graded" if fit else "graded-prior")
        self.l2, self.epochs, self.rate = l2, epochs, rate
        self.guarded = guard
        # Frozen thresholds (the shipped scorer) are never re-chosen on dev.
        self.fixed_thresholds = dict(thresholds) if thresholds else None
        self.thresholds = dict(thresholds or em.THRESHOLDS)
        self.ctx = em.MatchContext((), weights=self.weights)
        self.fitted = False

    # -- corpus ------------------------------------------------------------
    def prepare(self, corpus: list[dict]) -> None:
        """IDF tables from unlabelled items (the production MatchContext)."""
        self.ctx = em.MatchContext(corpus, weights=self.weights, thresholds=self.thresholds)

    def components(self, a: dict, b: dict) -> tuple[dict, dict]:
        return em.components(a, b, self.ctx)

    def score(self, a: dict, b: dict) -> float:
        return em.probability(a, b, self.ctx)

    def guard_ok(self, a: dict, b: dict) -> bool:
        """FR/EN guard (always True when this variant does not use it)."""
        return em.fr_en_guard(a, b, self.ctx).ok if self.guarded else True

    def set_thresholds(self, thresholds: dict) -> None:
        self.thresholds = dict(thresholds)
        self.ctx.thresholds = dict(thresholds)

    def tier(self, a: dict, b: dict) -> str | None:
        return em.tier(self.score(a, b), self.guard_ok(a, b), self.thresholds)

    # -- fitting -------------------------------------------------------------------
    def fit(self, triples: list[tuple[dict, dict, int]]) -> None:
        if not self.do_fit or not triples:
            return
        rows = [(em.components(a, b, self.ctx)[0], int(y)) for a, b, y in triples]
        self.weights = fit_weights(rows, self.prior, self.l2, self.epochs, self.rate)
        self.ctx.weights = dict(self.weights)
        self.fitted = True

    # -- explanations ----------------------------------------------------------------
    def contributions(self, a: dict, b: dict) -> list[tuple[str, float]]:
        x, _ = em.components(a, b, self.ctx)
        out = [(k, self.weights.get(k, 0.0) * x[k]) for k in FEATURES if x[k]]
        out.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
        return out

    def explain(self, a: dict, b: dict, top: int = 4, lang: str = "en") -> list[str]:
        """Human-readable reasons, strongest first (the production reasons)."""
        _, reasons = em.score(a, b, self.ctx)
        out = [r["sign"] + " " + r[lang] for r in reasons if r["code"] != "base"]
        return out[:top]

    # -- clusters ----------------------------------------------------------------------
    def cluster(self, items: list[dict], threshold: float) -> list[list[str]]:
        """The production sticky clusterer (event_match.attach) on one batch.

        `threshold` is the strict operating point chosen by the harness; it
        becomes the probable (merge) threshold unless this scorer is frozen."""
        saved = dict(self.ctx.thresholds)
        if not self.fixed_thresholds:
            t = dict(self.thresholds)
            t["probable"] = threshold
            t["certain"] = max(t.get("certain", threshold), threshold)
            t["possible"] = min(t.get("possible", threshold), threshold)
            self.ctx.thresholds = t
        guard_state = self.ctx.use_guard
        self.ctx.use_guard = self.guarded
        try:
            return em.cluster(items, self.ctx, edition="eval")
        finally:
            self.ctx.thresholds = saved
            self.ctx.use_guard = guard_state
