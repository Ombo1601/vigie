"""The CURRENT live grouping rule, wrapped as an evaluation scorer.

Imports scripts/cluster_issues.py read-only and calls its own functions, so
the baseline is exactly what the pipeline runs today, not a re-implementation:

  baseline           the pipeline rule for a pair: two items that carry a
                     named scar (event_scar) are grouped iff the scar is the
                     same; an item with a scar never joins an automatic
                     cluster; two unscarred items are grouped iff
                     features_match (headline overlap, FR/EN guard), and
                     event_buckets skips items with fewer than 3 headline
                     tokens or no publication date.
  baseline-features  features_match / same_event alone (no scars).

Both are binary (score 0 or 1). cluster() runs the real event_buckets
(complete-link) over the given items. Dossier gates that are not about event
identity (two institutions, the city anchor, the 7-day window) are left out.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cluster_issues  # noqa: E402

METHOD = cluster_issues.METHOD


class BaselineCurrent:
    binary = True

    def __init__(self, scars: bool = True):
        self.scars = scars
        self.name = "baseline" if scars else "baseline-features"
        self._cache: dict[str, tuple] = {}

    def _feat(self, item: dict) -> tuple:
        key = str(item.get("id") or "") or repr(sorted(item.items()))
        hit = self._cache.get(key)
        if hit is None:
            hit = (cluster_issues.event_scar(item) if self.scars else None, cluster_issues.event_features(item))
            self._cache[key] = hit
        return hit

    def score(self, a: dict, b: dict) -> float:
        scar_a, fa = self._feat(a)
        scar_b, fb = self._feat(b)
        if self.scars:
            if scar_a or scar_b:
                return 1.0 if scar_a == scar_b else 0.0
            # event_buckets never seeds or joins an item without a date or
            # with fewer than 3 headline tokens.
            if fa[0] is None or fb[0] is None or len(fa[1]) < 3 or len(fb[1]) < 3:
                return 0.0
        return 1.0 if cluster_issues.features_match(fa, fb) else 0.0

    def explain(self, a: dict, b: dict, top: int = 4) -> list[str]:
        scar_a, _ = self._feat(a)
        scar_b, _ = self._feat(b)
        if self.scars and (scar_a or scar_b):
            return ["named scar %s / %s" % (scar_a or "-", scar_b or "-")]
        return ["headline overlap rule " + ("matched" if self.score(a, b) else "not matched")]

    def cluster(self, items: list[dict], threshold: float = 0.5) -> list[list[str]]:
        """Run the live clusterer; returns groups of item ids (singletons included)."""
        if self.scars:
            buckets = cluster_issues.event_buckets(list(items))
            groups = [sorted(str(c.get("id")) for c in members) for members in buckets.values()]
        else:
            # features-only: the same complete-link walk, scars disabled.
            saved = cluster_issues.event_scar
            try:
                cluster_issues.event_scar = lambda c: None
                buckets = cluster_issues.event_buckets(list(items))
            finally:
                cluster_issues.event_scar = saved
            groups = [sorted(str(c.get("id")) for c in members) for members in buckets.values()]
        placed = {i for g in groups for i in g}
        groups += [[str(c.get("id"))] for c in items if str(c.get("id")) not in placed]
        return sorted(groups)
