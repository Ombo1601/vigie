"""Event-grouping evaluation harness (Phase 0, additive, stdlib only).

python -X utf8 eval/run_eval.py                          # all scorers, fill nothing
python -X utf8 eval/run_eval.py --report eval/REPORT.md  # fill the report's results block
python -X utf8 eval/run_eval.py --scorers baseline,graded --json data/eval/results.json
python -X utf8 eval/run_eval.py --scorer-spec mymodule:MyScorer   # plug in a scorer

Reads gold pair ids from --gold (default data/eval/gold_v0.json; labels
same_event / related / different, split dev / test, ids only) and the item
texts from the private stores (data/normalized/*_candidates.json and
latest_enriched.json). Both live under the gitignored data/ tree: the public
repository never carries publisher text, and this harness never writes any
into the report or the JSON results. When the gold set or the stores are
absent (CI, a fresh checkout) it prints why and exits 0.

Protocol: a scorer may be fitted and its threshold chosen on DEV only; the
figures that count are on TEST. The strict metric treats same_event as the
only positive class (related counts as a negative); how often each scorer
merges related or different pairs is reported separately, as is a lenient
metric that leaves related pairs out.

A scorer is any object with .name and .score(a, b) -> float in [0, 1];
optional .binary (fixed 0.5 threshold), .prepare(corpus), .fit(triples),
.explain(a, b) and .cluster(items, threshold).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DEFAULT_GOLD = ROOT / "data" / "eval" / "gold_v0.json"
DEFAULT_DATA = ROOT / "data" / "normalized"
LABELS = ("same_event", "related", "different")
LANG_PAIRS = ("fr-fr", "fr-en", "en-en")
BEGIN = "<!-- eval:results:begin -->"
END = "<!-- eval:results:end -->"
BOOTSTRAP_ROUNDS = 1000
BOOTSTRAP_SEED = 20261006
CLUSTER_WINDOW_HOURS = 7 * 24


class Skip(Exception):
    """Data is absent: the harness explains and exits 0."""


# ---------------------------------------------------------------- loading
def load_gold(path: Path) -> dict:
    if not path.exists():
        raise Skip(f"no gold set at {path} (it lives under the private data/ tree)")
    try:
        gold = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Skip(f"unreadable gold set {path}: {exc}")
    return load_gold_from_doc(gold, str(path))


def load_gold_from_doc(gold: dict, where: str = "gold") -> dict:
    """Validate a gold document: keep well-formed pairs, sorted by pair id."""
    pairs = gold.get("pairs") if isinstance(gold, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise Skip(f"gold set {where} has no pairs")
    clean = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        if p.get("label") not in LABELS or p.get("split") not in ("dev", "test"):
            continue
        if not p.get("a_id") or not p.get("b_id"):
            continue
        clean.append(p)
    gold = dict(gold)
    gold["pairs"] = sorted(clean, key=lambda p: str(p.get("pair_id") or (p["a_id"], p["b_id"])))
    return gold


def store_files(data_dir: Path) -> list[Path]:
    files = sorted(data_dir.glob("*_candidates.json"))
    latest = data_dir / "latest_enriched.json"
    if latest.exists():
        files.append(latest)
    return files


def load_items(data_dir: Path) -> dict[str, dict]:
    """Every unique item in the private stores, keyed by id (first seen wins)."""
    if not data_dir.is_dir():
        raise Skip(f"no item store at {data_dir} (gitignored; unpack the private state)")
    items: dict[str, dict] = {}
    for path in store_files(data_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cands = payload.get("candidates") if isinstance(payload, dict) else payload
        for c in cands if isinstance(cands, list) else []:
            if isinstance(c, dict) and c.get("id") and str(c["id"]) not in items:
                items[str(c["id"])] = c
    if not items:
        raise Skip(f"no candidate items under {data_dir}")
    return items


def lang_of(item: dict) -> str:
    lang = str(item.get("language") or "").lower()
    return "en" if lang.startswith("en") else "fr" if lang.startswith("fr") else "xx"


def lang_pair(a: dict, b: dict) -> str:
    return "-".join(sorted((lang_of(a), lang_of(b)), key=lambda s: (s != "fr", s)))


# ---------------------------------------------------------------- metrics
def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def confusion(rows: list[tuple[float, str]], threshold: float, drop_related: bool = False) -> dict:
    tp = fp = fn = tn = 0
    for score, label in rows:
        if drop_related and label == "related":
            continue
        pred = score >= threshold
        pos = label == "same_event"
        tp += pred and pos
        fp += pred and not pos
        fn += (not pred) and pos
        tn += (not pred) and not pos
    out = prf(tp, fp, fn)
    out["tn"] = tn
    out["n"] = tp + fp + fn + tn
    return out


PLATEAU = 0.01


def tune_threshold(rows: list[tuple[float, str]]) -> float:
    """Threshold for strict F1 on the given (dev) rows.

    The single arg-max is noisy on a few hundred pairs, so take the median of
    every candidate threshold whose F1 is within PLATEAU of the best one.
    """
    cands = sorted({s for s, _ in rows})
    if not cands:
        return 0.5
    f1 = [(t, confusion(rows, t)["f1"]) for t in cands]
    best = max(f for _, f in f1)
    near = [t for t, f in f1 if f >= best - PLATEAU]
    return near[(len(near) - 1) // 2]


def pr_curve(rows: list[tuple[float, str]], points: int = 12) -> tuple[list[dict], float]:
    """PR points at distinct thresholds (downsampled) and average precision."""
    ranked = sorted(rows, key=lambda r: -r[0])
    total_pos = sum(1 for _, lab in rows if lab == "same_event")
    if not ranked or not total_pos:
        return [], 0.0
    curve, tp, fp, ap, last_recall = [], 0, 0, 0.0, 0.0
    i = 0
    while i < len(ranked):
        t = ranked[i][0]
        while i < len(ranked) and ranked[i][0] == t:
            tp += ranked[i][1] == "same_event"
            fp += ranked[i][1] != "same_event"
            i += 1
        precision, recall = tp / (tp + fp), tp / total_pos
        ap += precision * (recall - last_recall)
        last_recall = recall
        curve.append({"threshold": t, "precision": precision, "recall": recall})
    if len(curve) > points:
        step = (len(curve) - 1) / (points - 1)
        curve = [curve[round(k * step)] for k in range(points)]
    return curve, ap


def bootstrap_f1(rows: list[tuple[float, str]], threshold: float) -> tuple[float, float]:
    """95% percentile interval of strict F1 over pair resamples (fixed seed)."""
    if not rows:
        return 0.0, 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(rows)
    values = sorted(
        confusion([rows[rng.randrange(n)] for _ in range(n)], threshold)["f1"]
        for _ in range(BOOTSTRAP_ROUNDS)
    )
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


class Union:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, k):
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> list[list[str]]:
        out: dict[str, list[str]] = {}
        for k in sorted(self.parent):
            out.setdefault(self.find(k), []).append(k)
        return sorted(out.values())


def gold_clusters(pairs: list[dict], ids: list[str]) -> list[list[str]]:
    u = Union(ids)
    for p in pairs:
        if p["label"] == "same_event":
            u.union(p["a_id"], p["b_id"])
    return u.groups()


def bcubed(pred: list[list[str]], gold: list[list[str]]) -> dict:
    pc = {i: frozenset(g) for g in pred for i in g}
    gc = {i: frozenset(g) for g in gold for i in g}
    keys = sorted(set(pc) & set(gc))
    if not keys:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "items": 0}
    p = sum(len(pc[i] & gc[i]) / len(pc[i]) for i in keys) / len(keys)
    r = sum(len(pc[i] & gc[i]) / len(gc[i]) for i in keys) / len(keys)
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "items": len(keys)}


def average_link(items: list[dict], scorer, threshold: float) -> list[list[str]]:
    """Generic clusterer: greedy average-link over pairs scored >= threshold.

    Pairs more than 7 days apart are not scored (treated as 0), the same
    bounded window the live pipeline uses for publication.
    """
    from graded_matcher import published_when  # local: keeps imports light

    ids = sorted(str(c.get("id")) for c in items)
    by_id = {str(c.get("id")): c for c in items}
    when = {i: published_when(by_id[i]) for i in ids}
    scores: dict[tuple[str, str], float] = {}
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            ta, tb = when[a], when[b]
            if ta and tb and abs((ta - tb).total_seconds()) > CLUSTER_WINDOW_HOURS * 3600:
                continue
            scores[(a, b)] = scorer.score(by_id[a], by_id[b])
    members = {i: [i] for i in ids}
    owner = {i: i for i in ids}
    for (a, b), s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        if s < threshold:
            break
        ca, cb = owner[a], owner[b]
        if ca == cb:
            continue
        cross = [scores.get((min(i, j), max(i, j)), 0.0) for i in members[ca] for j in members[cb]]
        if sum(cross) / len(cross) < threshold:
            continue
        keep, gone = min(ca, cb), max(ca, cb)
        for i in members[gone]:
            owner[i] = keep
        members[keep] = sorted(members[keep] + members.pop(gone))
    return sorted(members.values())


# ---------------------------------------------------------------- evaluation
def evaluate_split(scorer, rows: list[dict], threshold: float, items: dict[str, dict], do_clusters: bool) -> dict:
    scored = [(r["score"], r["label"]) for r in rows]
    out = {
        "n": len(rows),
        "label_counts": {lab: sum(1 for r in rows if r["label"] == lab) for lab in LABELS},
        "threshold": threshold,
        "strict": confusion(scored, threshold),
        "lenient": confusion(scored, threshold, drop_related=True),
        "merge_rate": {},
        "by_lang_pair": {},
    }
    for lab in LABELS:
        sub = [s for s, l in scored if l == lab]
        out["merge_rate"][lab] = (sum(1 for s in sub if s >= threshold) / len(sub)) if sub else None
    for lp in LANG_PAIRS:
        sub = [(r["score"], r["label"]) for r in rows if r["lang_pair"] == lp]
        m = confusion(sub, threshold)
        m["positives"] = sum(1 for _, l in sub if l == "same_event")
        m["related_merged"] = sum(1 for s, l in sub if l == "related" and s >= threshold)
        out["by_lang_pair"][lp] = m
    curve, ap = pr_curve(scored)
    out["pr_curve"] = curve
    out["average_precision"] = ap
    out["f1_ci95"] = bootstrap_f1(scored, threshold)
    errors = []
    for r in rows:
        pred = r["score"] >= threshold
        if pred != (r["label"] == "same_event"):
            errors.append({
                "pair_id": r["pair_id"], "kind": "false_merge" if pred else "missed",
                "label": r["label"], "lang_pair": r["lang_pair"], "score": r["score"],
                "components": r.get("components", []),
            })
    errors.sort(key=lambda e: (e["kind"], -abs(e["score"] - threshold), e["pair_id"]))
    out["errors"] = errors
    if do_clusters:
        ids = sorted({r["a_id"] for r in rows} | {r["b_id"] for r in rows})
        split_items = [items[i] for i in ids]
        if hasattr(scorer, "cluster"):
            pred = scorer.cluster(split_items, threshold)
        else:
            pred = average_link(split_items, scorer, threshold)
        gold = gold_clusters(rows, ids)
        owner = {i: k for k, g in enumerate(pred) for i in g}
        induced = [(1.0 if owner.get(r["a_id"]) == owner.get(r["b_id"]) else 0.0, r["label"]) for r in rows]
        gowner = {i: k for k, g in enumerate(gold) for i in g}
        out["clusters"] = {
            "items": len(ids),
            "predicted_groups": sum(1 for g in pred if len(g) > 1),
            "predicted_largest": max((len(g) for g in pred), default=0),
            "gold_groups": sum(1 for g in gold if len(g) > 1),
            "gold_largest": max((len(g) for g in gold), default=0),
            "bcubed": bcubed(pred, gold),
            "induced_pairwise": confusion(induced, 0.5),
            "gold_closure_conflicts": sum(
                1 for r in rows if r["label"] != "same_event" and gowner[r["a_id"]] == gowner[r["b_id"]]),
        }
    return out


def build_scorers(names: list[str], specs: list[str]):
    import baseline_current
    import graded_matcher

    built = []
    for name in names:
        if name == "baseline":
            built.append(baseline_current.BaselineCurrent(scars=True))
        elif name == "baseline-features":
            built.append(baseline_current.BaselineCurrent(scars=False))
        elif name == "graded":
            built.append(graded_matcher.GradedMatcher(fit=True))
        elif name == "graded-prior":
            built.append(graded_matcher.GradedMatcher(fit=False))
        else:
            raise SystemExit(f"unknown scorer {name!r} (baseline, baseline-features, graded-prior, graded)")
    for spec in specs:
        module, _, attr = spec.partition(":")
        obj = getattr(importlib.import_module(module), attr or "Scorer")
        built.append(obj() if isinstance(obj, type) else obj)
    return built


def run(gold: dict, items: dict[str, dict], scorers, do_clusters: bool = True, log=print) -> dict:
    resolved, missing = [], 0
    for p in gold["pairs"]:
        a, b = items.get(str(p["a_id"])), items.get(str(p["b_id"]))
        if a is None or b is None:
            missing += 1
            continue
        resolved.append((p, a, b))
    if not resolved:
        raise Skip("none of the gold pair ids resolve in the item store")
    corpus = [items[k] for k in sorted(items)]
    results = {
        "gold": {
            "version": gold.get("version"),
            "pairs": len(gold["pairs"]),
            "resolved": len(resolved),
            "missing": missing,
            "kappa": gold.get("kappa"),
            "agreement": gold.get("agreement"),
            "split_method": (gold.get("split") or {}).get("method") if isinstance(gold.get("split"), dict) else None,
        },
        "corpus_items": len(corpus),
        "scorers": {},
    }
    for scorer in scorers:
        log(f"eval: scoring with {scorer.name}")
        if hasattr(scorer, "prepare"):
            scorer.prepare(corpus)
        dev = [(p, a, b) for p, a, b in resolved if p["split"] == "dev"]
        if hasattr(scorer, "fit"):
            scorer.fit([(a, b, 1 if p["label"] == "same_event" else 0) for p, a, b in dev])

        def rows_for(split: str) -> list[dict]:
            out = []
            for p, a, b in resolved:
                if p["split"] != split:
                    continue
                row = {
                    "pair_id": str(p.get("pair_id") or ""), "a_id": str(p["a_id"]), "b_id": str(p["b_id"]),
                    "label": p["label"], "lang_pair": lang_pair(a, b), "score": float(scorer.score(a, b)),
                }
                if hasattr(scorer, "contributions"):
                    row["components"] = [k for k, _ in scorer.contributions(a, b)[:3]]
                out.append(row)
            return out

        dev_rows, test_rows = rows_for("dev"), rows_for("test")
        binary = bool(getattr(scorer, "binary", False))
        threshold = 0.5 if binary else tune_threshold([(r["score"], r["label"]) for r in dev_rows])
        entry = {
            "binary": binary,
            "threshold": threshold,
            "weights": dict(getattr(scorer, "weights", {}) or {}),
            "dev": evaluate_split(scorer, dev_rows, threshold, items, do_clusters),
            "test": evaluate_split(scorer, test_rows, threshold, items, do_clusters),
        }
        results["scorers"][scorer.name] = entry
        s = entry["test"]["strict"]
        log(f"eval: {scorer.name} test strict P={s['precision']:.3f} R={s['recall']:.3f} F1={s['f1']:.3f} (threshold {threshold:.3f})")
    return results


# ---------------------------------------------------------------- report
def _f(x) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def render(results: dict, gold_sha: str) -> str:
    g = results["gold"]
    names = list(results["scorers"])
    lines = [
        BEGIN,
        "",
        f"Filled by `eval/run_eval.py` from gold `{g['version']}` (sha256 `{gold_sha[:16]}`): "
        f"{g['resolved']} of {g['pairs']} pairs resolved in the private store ({g['missing']} missing); "
        f"labeller kappa {g['kappa']}, raw agreement {g['agreement']}. "
        f"IDF corpus: {results['corpus_items']} unlabelled items. No publisher text below: ids, counts and measures only.",
        "",
        "### Headline: TEST split, strict (same_event vs related + different)",
        "",
        "| scorer | threshold (dev) | precision | recall | F1 | F1 95% CI | avg precision | related merged | different merged |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n in names:
        e = results["scorers"][n]
        t = e["test"]
        lo, hi = t["f1_ci95"]
        lines.append(
            f"| {n} | {e['threshold']:.3f} | {_f(t['strict']['precision'])} | {_f(t['strict']['recall'])} | "
            f"{_f(t['strict']['f1'])} | {lo:.3f} to {hi:.3f} | {'binary' if e['binary'] else _f(t['average_precision'])} | "
            f"{_f(t['merge_rate']['related'])} | {_f(t['merge_rate']['different'])} |")
    lines += ["", "Test label counts: " + ", ".join(
        f"{k} {v}" for k, v in results["scorers"][names[0]]["test"]["label_counts"].items()) if names else "", ""]
    lines += [
        "### Lenient (related pairs left out) and DEV for reference",
        "",
        "| scorer | test lenient P | test lenient R | test lenient F1 | dev strict P | dev strict R | dev strict F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for n in names:
        e = results["scorers"][n]
        tl, ds = e["test"]["lenient"], e["dev"]["strict"]
        lines.append(f"| {n} | {_f(tl['precision'])} | {_f(tl['recall'])} | {_f(tl['f1'])} | "
                     f"{_f(ds['precision'])} | {_f(ds['recall'])} | {_f(ds['f1'])} |")
    lines += ["", "### TEST by language pair (strict)", "",
              "| scorer | pair | n | same_event | TP | FP | FN | related merged | precision | recall | F1 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n in names:
        for lp in LANG_PAIRS:
            m = results["scorers"][n]["test"]["by_lang_pair"][lp]
            if not m["n"]:
                lines.append(f"| {n} | {lp} | 0 | 0 | - | - | - | - | n/a | n/a | n/a |")
                continue
            lines.append(f"| {n} | {lp} | {m['n']} | {m['positives']} | {m['tp']} | {m['fp']} | {m['fn']} | "
                         f"{m['related_merged']} | {_f(m['precision'])} | {_f(m['recall'])} | {_f(m['f1'])} |")
    lines += ["", "### TEST clusters", "",
              "B-cubed compares predicted groups with the transitive closure of the gold same_event pairs over the "
              "items of the split. The gold set labels sampled pairs, not every pair, so a predicted group that "
              "joins two unlabelled items is counted against the scorer: read B-cubed precision as a lower bound. "
              "Induced pairwise scores the labelled pairs by whether the clusterer put both items in one group.",
              "",
              "| scorer | items | predicted groups (largest) | gold groups (largest) | B3 precision | B3 recall | B3 F1 | induced P | induced R | induced F1 | closure conflicts |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n in names:
        c = results["scorers"][n]["test"].get("clusters")
        if not c:
            continue
        b, ip = c["bcubed"], c["induced_pairwise"]
        lines.append(f"| {n} | {c['items']} | {c['predicted_groups']} ({c['predicted_largest']}) | "
                     f"{c['gold_groups']} ({c['gold_largest']}) | {_f(b['precision'])} | {_f(b['recall'])} | {_f(b['f1'])} | "
                     f"{_f(ip['precision'])} | {_f(ip['recall'])} | {_f(ip['f1'])} | {c['gold_closure_conflicts']} |")
    lines += ["", "### TEST precision-recall points", ""]
    for n in names:
        e = results["scorers"][n]
        pts = e["test"]["pr_curve"]
        lines.append(f"- {n}: " + ("; ".join(
            f"t={p['threshold']:.3f} P={p['precision']:.3f} R={p['recall']:.3f}" for p in pts) or "none"))
    lines += ["", "### Fitted weights (log-odds per unit of evidence)", ""]
    for n in names:
        w = results["scorers"][n]["weights"]
        if w:
            lines.append(f"- {n}: " + ", ".join(f"{k} {v:+.2f}" for k, v in w.items()))
    lines += ["", "### TEST errors (pair ids only; strongest components)", ""]
    for n in names:
        errs = results["scorers"][n]["test"]["errors"]
        fm = [e for e in errs if e["kind"] == "false_merge"]
        ms = [e for e in errs if e["kind"] == "missed"]
        by = lambda lst, key: ", ".join(  # noqa: E731
            f"{k} {v}" for k, v in sorted(_count(lst, key).items())) or "none"
        lines.append(f"- {n}: {len(fm)} false merges (by label: {by(fm, 'label')}; by language pair: "
                     f"{by(fm, 'lang_pair')}), {len(ms)} missed (by language pair: {by(ms, 'lang_pair')})")
        for e in (fm[:5] + ms[:5]):
            comp = ("; " + ", ".join(e["components"])) if e.get("components") else ""
            lines.append(f"  - `{e['pair_id']}` {e['kind']} gold={e['label']} {e['lang_pair']} score={e['score']:.3f}{comp}")
    lines += ["", END]
    return "\n".join(lines)


def _count(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def fill_report(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        text = head + block + tail
    else:
        text = (text.rstrip("\n") + "\n\n" if text else "") + block + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _private_path_ok(path: Path) -> bool:
    """Error dumps carry publisher headlines: only under data/ or outside the repo."""
    path = path.resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        return True
    try:
        path.relative_to(ROOT / "data")
        return True
    except ValueError:
        return False


def dump_errors(directory: Path, results: dict, scorers, items: dict[str, dict], gold: dict) -> None:
    """Private review aid: test errors with both headlines and the reasons."""
    pairs = {str(p.get("pair_id") or ""): (str(p["a_id"]), str(p["b_id"])) for p in gold["pairs"]}
    directory.mkdir(parents=True, exist_ok=True)
    for scorer in scorers:
        out = []
        for e in results["scorers"][scorer.name]["test"]["errors"]:
            a_id, b_id = pairs[e["pair_id"]]
            a, b = items[a_id], items[b_id]
            out.append({**e, "a": {k: a.get(k) for k in ("id", "source_id", "language", "published_at", "title")},
                        "b": {k: b.get(k) for k in ("id", "source_id", "language", "published_at", "title")},
                        "why": scorer.explain(a, b) if hasattr(scorer, "explain") else []})
        target = directory / f"errors_test_{scorer.name}.json"
        target.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--scorers", default="baseline,baseline-features,graded-prior,graded")
    ap.add_argument("--scorer-spec", action="append", default=[], help="module:Class of an extra scorer")
    ap.add_argument("--report", type=Path, help="markdown file whose results block is filled (e.g. eval/REPORT.md)")
    ap.add_argument("--json", type=Path, help="write the measured results (no publisher text) as JSON")
    ap.add_argument("--dump-errors", type=Path, help="private dir (under data/) for test errors WITH headlines")
    ap.add_argument("--no-clusters", action="store_true")
    args = ap.parse_args(argv)
    if args.dump_errors and not _private_path_ok(args.dump_errors):
        print("eval: --dump-errors must point under data/ (gitignored) or outside the repository", file=sys.stderr)
        return 2
    try:
        gold = load_gold(args.gold)
        items = load_items(args.data_dir)
        names = [n.strip() for n in args.scorers.split(",") if n.strip()]
        scorers = build_scorers(names, args.scorer_spec)
        results = run(gold, items, scorers, do_clusters=not args.no_clusters)
    except Skip as why:
        print(f"eval: skipped: {why}. Nothing measured; exit 0.")
        return 0
    gold_sha = hashlib.sha256(args.gold.read_bytes()).hexdigest()
    results["gold"]["sha256"] = gold_sha
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")
    block = render(results, gold_sha)
    if args.report:
        fill_report(args.report, block)
        print(f"eval: report filled -> {args.report}")
    if args.dump_errors:
        dump_errors(args.dump_errors, results, scorers, items, gold)
        print(f"eval: private error dumps -> {args.dump_errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
