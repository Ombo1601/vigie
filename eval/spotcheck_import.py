"""Read the founder's spot-check labels and compare them with the gold's model labels.

python -X utf8 eval/spotcheck_import.py --labels spotcheck_labels.json
python -X utf8 eval/spotcheck_import.py --labels a.json --labels b.json --json data/eval/spotcheck_results.json

Input: the JSON exported by the page that eval/spotcheck.py generates
({"kind": "vigie-spotcheck-labels", "labels": [{"pair_id", "label"}]}, label in
same_event / related / different / uncertain) and the private gold. Output,
COUNTS AND MEASURES ONLY (no headline, no item id, no pair id):

  - agreement and Cohen's kappa between the human and the model labels, on the
    pairs the human could decide (3 classes; also same_event versus the rest),
    the confusion matrix (model rows, human columns, uncertain apart), and the
    same agreement per stratum (gold-uncertain pairs, fr-en same_event pairs);
  - what the human labels do to the matcher's evaluation: with the shipped
    weights fixed, the tier thresholds that eval/run_eval.py would choose on
    dev, and the precision / recall of the `certain` tier and of the grouped
    tiers (certain or probable), under the shipped thresholds and under the
    re-chosen ones, with model labels versus human labels replacing them for the
    pairs the human answered. Pairs answered "uncertain" leave the evaluation.

This replaces nothing: a person's labels are a measurement of the model's
labels, not a new gold. When the human labels are what you trust, update the
gold's labelers_provenance and version, then re-run eval/run_eval.py.

Fail-soft: an absent or malformed file prints why and exits 0.
Python 3.12+ standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (HERE, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import event_match as em  # noqa: E402
import run_eval  # noqa: E402
import spotcheck  # noqa: E402

CLASSES = ("same_event", "related", "different")
HUMAN = CLASSES + ("uncertain",)
CERTAIN_PRECISION = run_eval.CERTAIN_PRECISION


# ---------------------------------------------------------------- statistics
def cohen_kappa(model: list[str], human: list[str], classes: tuple[str, ...] = CLASSES) -> dict:
    """Cohen's kappa between two raters over paired labels (same length).

    po = observed agreement; pe = sum over classes of the product of the raters'
    marginal shares. When pe == 1 (both raters always say the same single class)
    kappa is 1.0 if they agree everywhere and 0.0 otherwise, by convention."""
    n = len(model)
    if n == 0 or n != len(human):
        return {"n": n, "agreement": None, "kappa": None}
    agree = sum(1 for m, h in zip(model, human) if m == h)
    po = agree / n
    pe = sum((sum(1 for m in model if m == c) / n) * (sum(1 for h in human if h == c) / n) for c in classes)
    kappa = (po - pe) / (1.0 - pe) if pe < 1.0 else (1.0 if po == 1.0 else 0.0)
    return {"n": n, "agree": agree, "agreement": round(po, 4), "pe": round(pe, 4), "kappa": round(kappa, 4)}


def confusion_matrix(model: list[str], human: list[str]) -> dict:
    """{model_label: {human_label: n}}, model rows, human columns (uncertain included)."""
    out = {m: {h: 0 for h in HUMAN} for m in CLASSES}
    for m, h in zip(model, human):
        if m in out and h in out[m]:
            out[m][h] += 1
    return out


def _binary(labels: list[str]) -> list[str]:
    return ["same_event" if x == "same_event" else "other" for x in labels]


# ---------------------------------------------------------------- loading
def load_labels(paths: list[Path]) -> tuple[dict[str, str], dict]:
    """{pair_id: label} from one or more exports (the last file wins on a repeat)."""
    merged: dict[str, str] = {}
    info = {"files": 0, "rows": 0, "ignored": 0, "overwritten": 0}
    for path in paths:
        if not path.is_file():
            raise run_eval.Skip(f"no labels file at {path}")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise run_eval.Skip(f"unreadable labels file {path}: {exc}")
        rows = doc.get("labels") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            raise run_eval.Skip(f"{path} is not a spot-check labels export (no 'labels' list)")
        info["files"] += 1
        for r in rows:
            info["rows"] += 1
            if not isinstance(r, dict) or not r.get("pair_id") or r.get("label") not in HUMAN:
                info["ignored"] += 1
                continue
            pid = str(r["pair_id"])
            if pid in merged and merged[pid] != r["label"]:
                info["overwritten"] += 1
            merged[pid] = r["label"]
    if not merged:
        raise run_eval.Skip("the labels file holds no usable answer")
    return merged, info


# ---------------------------------------------------------------- tiers under replaced labels
def score_pairs(pairs: list[dict], items: dict[str, dict]) -> list[dict]:
    """Shipped-matcher rows for every gold pair that resolves (labels not yet applied)."""
    ctx = em.MatchContext([items[k] for k in sorted(items)])
    rows = []
    for p in pairs:
        a, b = items.get(str(p["a_id"])), items.get(str(p["b_id"]))
        if a is None or b is None:
            continue
        raw = em.probability(a, b, ctx)
        ok = em.fr_en_guard(a, b, ctx).ok
        rows.append({"pair_id": run_eval.pair_key(p), "a_id": str(p["a_id"]), "b_id": str(p["b_id"]),
                     "split": p["split"], "label": p["label"], "raw": raw, "guard_ok": ok,
                     "score": raw if ok else 0.0, "lang_pair": run_eval.lang_pair(a, b)})
    return rows


def _band(rows: list[dict], thresholds: dict, members: tuple[str, ...]) -> dict:
    pred = [r for r in rows if run_eval.row_tier(r, thresholds) in members]
    positives = sum(1 for r in rows if r["label"] == "same_event")
    tp = sum(1 for r in pred if r["label"] == "same_event")
    lo, hi = run_eval.wilson(tp, len(pred))
    return {"predicted": len(pred), "tp": tp, "positives": positives,
            "precision": round(tp / len(pred), 4) if pred else None,
            "recall": round(tp / positives, 4) if positives else None,
            "precision_ci95": [round(lo, 4), round(hi, 4)] if pred else None}


def tier_effect(rows: list[dict]) -> dict:
    """Thresholds re-chosen on dev and tier precision, for one labelling of the rows."""
    dev = [r for r in rows if r["split"] == "dev"]
    chosen = (run_eval.choose_tiers([(r["score"], r["raw"], r["label"]) for r in dev])
              if dev else dict(em.THRESHOLDS))
    out = {"pairs": len(rows), "dev": len(dev), "test": len(rows) - len(dev),
           "positives": sum(1 for r in rows if r["label"] == "same_event"),
           "rechosen_thresholds": {k: round(v, 4) for k, v in chosen.items()}}
    for name, thr in (("shipped_thresholds", dict(em.THRESHOLDS)), ("rechosen_thresholds_applied", chosen)):
        block = {}
        for split in ("test", "all"):
            sub = [r for r in rows if split == "all" or r["split"] == split]
            block[split] = {"certain": _band(sub, thr, ("certain",)),
                            "grouped": _band(sub, thr, ("certain", "probable"))}
        out[name] = block
    return out


def threshold_report(scored: list[dict], human: dict[str, str]) -> dict:
    """Model labels versus human labels (replacing the model's where answered;
    "uncertain" pairs leave the evaluation)."""
    replaced = []
    for r in scored:
        h = human.get(r["pair_id"])
        if h == "uncertain":
            continue
        replaced.append(dict(r, label=h) if h in CLASSES else r)
    answered = {r["pair_id"] for r in scored if human.get(r["pair_id"]) in CLASSES}
    model = tier_effect(scored)
    hum = tier_effect(replaced)
    return {
        "pairs_replaced": len(answered),
        "pairs_dropped_uncertain": sum(1 for r in scored if human.get(r["pair_id"]) == "uncertain"),
        "pairs_whose_label_changed": sum(1 for r in scored if human.get(r["pair_id"]) in CLASSES
                                         and human[r["pair_id"]] != r["label"]),
        "model_labels": model,
        "human_labels_replace": hum,
        "certain_threshold_shift": round(hum["rechosen_thresholds"]["certain"] - model["rechosen_thresholds"]["certain"], 4),
        "probable_threshold_shift": round(hum["rechosen_thresholds"]["probable"] - model["rechosen_thresholds"]["probable"], 4),
        "certain_test_precision_shipped": {
            "model": model["shipped_thresholds"]["test"]["certain"]["precision"],
            "human": hum["shipped_thresholds"]["test"]["certain"]["precision"]},
    }


# ---------------------------------------------------------------- analysis
def agreement_report(gold_pairs: list[dict], human: dict[str, str], items: dict[str, dict] | None) -> dict:
    by_key = {run_eval.pair_key(p): p for p in gold_pairs}
    matched = {k: v for k, v in human.items() if k in by_key}
    decided = sorted(k for k, v in matched.items() if v in CLASSES)
    model = [by_key[k]["label"] for k in decided]
    hum = [matched[k] for k in decided]
    out = {
        "labels_read": len(human), "matched_gold_pairs": len(matched), "unknown_pair_ids": len(human) - len(matched),
        "decided": len(decided), "uncertain_answers": sum(1 for v in matched.values() if v == "uncertain"),
        "three_class": cohen_kappa(model, hum),
        "same_event_vs_rest": cohen_kappa(_binary(model), _binary(hum), ("same_event", "other")),
        "confusion_model_rows_human_cols": confusion_matrix(
            [by_key[k]["label"] for k in sorted(matched)], [matched[k] for k in sorted(matched)]),
    }
    strata: dict[str, list[str]] = {"gold_uncertain": [k for k in decided if by_key[k].get("uncertain")]}
    if items is not None:
        strata["fr_en_same_event"] = [
            k for k in decided if by_key[k]["label"] == "same_event"
            and str(by_key[k]["a_id"]) in items and str(by_key[k]["b_id"]) in items
            and run_eval.lang_pair(items[str(by_key[k]["a_id"])], items[str(by_key[k]["b_id"])]) == "fr-en"]
    out["by_stratum"] = {}
    for name, keys in sorted(strata.items()):
        agree = sum(1 for k in keys if by_key[k]["label"] == matched[k])
        out["by_stratum"][name] = {"decided": len(keys), "agree": agree,
                                   "agreement": round(agree / len(keys), 4) if keys else None}
        if name == "gold_uncertain":
            out["by_stratum"][name]["uncertain_answers"] = sum(
                1 for k, v in matched.items() if v == "uncertain" and by_key[k].get("uncertain"))
    return out


def analyse(gold: dict, human: dict[str, str], items: dict[str, dict] | None) -> dict:
    out = {"schema": 1, "kind": "vigie-spotcheck-import", "gold_version": gold.get("version"),
           "gold_provenance": run_eval.provenance_of(gold),
           "agreement": agreement_report(gold["pairs"], human, items)}
    if items is None:
        out["thresholds"] = {"skipped": "no item store: the matcher cannot be re-scored"}
        return out
    scored = score_pairs(gold["pairs"], items)
    if not scored:
        out["thresholds"] = {"skipped": "no gold pair resolves in the item store"}
        return out
    out["thresholds"] = threshold_report(scored, human)
    return out


# ---------------------------------------------------------------- printing
def _f(x) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def summary_lines(res: dict) -> list[str]:
    a = res["agreement"]
    k3, kb = a["three_class"], a["same_event_vs_rest"]
    lines = [
        f"spotcheck_import: gold labels by: {res['gold_provenance']}",
        f"spotcheck_import: {a['labels_read']} answers read, {a['matched_gold_pairs']} match the gold "
        f"({a['unknown_pair_ids']} unknown ids), {a['decided']} decided, {a['uncertain_answers']} answered uncertain",
        f"spotcheck_import: 3-class agreement {_f(k3['agreement'])} kappa {_f(k3['kappa'])} (n={k3['n']}); "
        f"same_event vs rest agreement {_f(kb['agreement'])} kappa {_f(kb['kappa'])}",
        "spotcheck_import: confusion (model rows -> human same_event/related/different/uncertain)",
    ]
    for m, row in a["confusion_model_rows_human_cols"].items():
        lines.append(f"  {m:<11} " + " ".join(f"{row[h]:>3}" for h in HUMAN))
    for name, s in a["by_stratum"].items():
        lines.append(f"spotcheck_import: stratum {name}: {s['agree']} of {s['decided']} agree ({_f(s['agreement'])})")
    t = res["thresholds"]
    if "skipped" in t:
        lines.append(f"spotcheck_import: thresholds skipped: {t['skipped']}")
        return lines
    m, h = t["model_labels"], t["human_labels_replace"]
    lines += [
        f"spotcheck_import: {t['pairs_replaced']} pairs carry a human label ({t['pairs_whose_label_changed']} "
        f"differ from the model), {t['pairs_dropped_uncertain']} dropped as uncertain",
        f"spotcheck_import: thresholds re-chosen on dev (model labels): {m['rechosen_thresholds']}",
        f"spotcheck_import: thresholds re-chosen on dev (human labels replace): {h['rechosen_thresholds']}",
    ]
    for name, e in (("model labels", m), ("human labels", h)):
        c = e["shipped_thresholds"]["test"]["certain"]
        g = e["shipped_thresholds"]["test"]["grouped"]
        lines.append(f"spotcheck_import: shipped thresholds, TEST, {name}: certain {c['tp']} of {c['predicted']} "
                     f"(P {_f(c['precision'])}, R {_f(c['recall'])}); grouped {g['tp']} of {g['predicted']} "
                     f"(P {_f(g['precision'])}, R {_f(g['recall'])})")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, action="append", required=True,
                    help="labels export from the spot-check page (repeatable)")
    ap.add_argument("--gold", type=Path, default=run_eval.DEFAULT_GOLD)
    ap.add_argument("--items", type=Path, help="items JSON (a list); default: the stores under --data-dir")
    ap.add_argument("--data-dir", type=Path, default=run_eval.DEFAULT_DATA)
    ap.add_argument("--json", type=Path, help="write the counts and measures as JSON")
    args = ap.parse_args(argv)
    try:
        human, info = load_labels(args.labels)
        gold = run_eval.load_gold(args.gold)
        try:
            items = spotcheck.load_items_any(args.items, args.data_dir)
        except run_eval.Skip as why:
            print(f"spotcheck_import: no items ({why}): the agreement is computed, the thresholds are not")
            items = None
        res = analyse(gold, human, items)
    except run_eval.Skip as why:
        print(f"spotcheck_import: skipped: {why}. Nothing measured; exit 0.")
        return 0
    res["labels_files"] = info
    for line in summary_lines(res):
        print(line)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")
        print(f"spotcheck_import: results -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
