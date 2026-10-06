"""Founder spot-check: ONE self-contained, offline HTML file of ~60 gold pairs.

python -X utf8 eval/spotcheck.py --out PATH.html                 # gold + data/normalized
python -X utf8 eval/spotcheck.py --out PATH.html --gold G.json --items items.json --seed 7

The founder labels each pair (même événement / lié / différent / incertain)
WITHOUT seeing the model's label, then exports a JSON labels file that
eval/spotcheck_import.py compares with the gold: agreement, kappa, confusion
matrix, and what the human labels do to the tier thresholds and precision.

Why: gold_v0 was labelled by one language model (see eval/README.md); no human
has checked a label, and EVENTS.md gate 1 cannot be passed on it.

PRIVATE. The page carries publisher headlines (and short excerpts), so it is
never committed and never published: --out is refused when it points inside
the repository, except under data/ (gitignored). The page holds no model label,
no split, no stratum and no gold reason: only the pair id, outlet, language,
time, headline and excerpt of each side, in a seeded shuffled order, with the
side order of each pair also seeded.

Sample (strata, counts printed on stdout; seeded, deterministic):
  - every pair the gold marks uncertain
  - every French/English pair labelled same_event
  - N random same_event (French/French or English/English), N related, N different
Self-contained: one HTML file, inline CSS and JS, system fonts, no network
(CSP default-src 'none'), light and dark, phone width. Answers are autosaved in
the browser's localStorage (try/catch guarded) and exported with a button.

Python 3.12+ standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (HERE, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_eval  # noqa: E402  (loaders, privacy check; the dependency runs eval -> scripts, never the reverse)

DEFAULT_SEED = 20261006
DEFAULT_RANDOM = 4          # per random stratum
EXCERPT_CHARS = 400
SCHEMA = 1
HUMAN_LABELS = ("same_event", "related", "different", "uncertain")


# ---------------------------------------------------------------- items
def load_items_any(path: Path | None, data_dir: Path) -> dict[str, dict]:
    """Items from --items (a JSON list, {"candidates": [...]}, or an id-keyed object)
    or, failing that, from the private stores under data_dir."""
    if path is not None:
        if not path.is_file():
            raise run_eval.Skip(f"no items file at {path}")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise run_eval.Skip(f"unreadable items file {path}: {exc}")
        rows = doc.get("candidates") if isinstance(doc, dict) and "candidates" in doc else doc
        if isinstance(rows, dict):
            rows = [dict(v, id=v.get("id") or k) for k, v in rows.items() if isinstance(v, dict)]
        items = {str(c["id"]): c for c in rows if isinstance(c, dict) and c.get("id")} if isinstance(rows, list) else {}
        if not items:
            raise run_eval.Skip(f"no items in {path}")
        return items
    return run_eval.load_items(data_dir)


# ---------------------------------------------------------------- sampling
def sample_pairs(pairs: list[dict], items: dict[str, dict], seed: int = DEFAULT_SEED,
                 n_random: int = DEFAULT_RANDOM, max_fr_en: int | None = None) -> tuple[list[dict], dict]:
    """Strata sample of gold pairs whose two items resolve. Returns (pairs, counts)."""
    rng = random.Random(seed)
    ok = [p for p in sorted(pairs, key=run_eval.pair_key)
          if str(p["a_id"]) in items and str(p["b_id"]) in items]
    chosen: dict[str, dict] = {}
    counts = {"unresolved": len(pairs) - len(ok)}

    def take(stratum: str, rows: list[dict]) -> None:
        taken = 0
        for p in rows:
            if run_eval.pair_key(p) not in chosen:
                chosen[run_eval.pair_key(p)] = p
                taken += 1
        counts[stratum] = taken

    take("uncertain", [p for p in ok if p.get("uncertain")])
    fr_en = [p for p in ok if p["label"] == "same_event"
             and run_eval.lang_pair(items[str(p["a_id"])], items[str(p["b_id"])]) == "fr-en"]
    if max_fr_en is not None and len(fr_en) > max_fr_en:
        fr_en = sorted(rng.sample(fr_en, max_fr_en), key=run_eval.pair_key)
    take("fr_en_same_event", fr_en)
    for label in ("same_event", "related", "different"):
        pool = [p for p in ok if p["label"] == label and run_eval.pair_key(p) not in chosen
                and not (label == "same_event"
                         and run_eval.lang_pair(items[str(p["a_id"])], items[str(p["b_id"])]) == "fr-en")]
        take("random_" + label, rng.sample(pool, min(n_random, len(pool))))
    out = [chosen[k] for k in sorted(chosen)]
    rng.shuffle(out)
    counts["total"] = len(out)
    return out, counts


# ---------------------------------------------------------------- page data
def _when(item: dict) -> tuple[str, datetime | None]:
    raw = str(item.get("published_at") or "").strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return "date inconnue", None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if not 1990 <= dt.year <= 2100:
        return "date inconnue", None
    return dt.strftime("%Y-%m-%d %H:%M UTC"), dt


def _gap(a: datetime | None, b: datetime | None) -> str:
    if a is None or b is None:
        return "écart de publication inconnu"
    minutes = int(abs((a - b).total_seconds()) // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = ([f"{days} j"] if days else []) + ([f"{hours} h"] if hours else []) + ([f"{mins} min"] if mins and not days else [])
    return "écart de publication : " + (" ".join(parts) if parts else "moins d'une minute")


def _side(item: dict) -> dict:
    when, _ = _when(item)
    lang = str(item.get("language") or "").lower()
    outlet = (item.get("institution_name") or item.get("source_name") or item.get("institution")
              or item.get("source_id") or "source inconnue")
    return {
        "outlet": str(outlet).strip()[:120],
        "lang": "EN" if lang.startswith("en") else "FR" if lang.startswith("fr") else "?",
        "when": when,
        "title": " ".join(str(item.get("title") or "").split())[:300] or "(sans titre)",
        "excerpt": " ".join(str(item.get("summary") or "").split())[:EXCERPT_CHARS],
    }


def page_data(pairs: list[dict], items: dict[str, dict], seed: int) -> dict:
    """What the page embeds: no label, split, stratum, uncertainty or gold reason."""
    rng = random.Random(seed ^ 0x5EED)
    rows = []
    for p in pairs:
        a, b = items[str(p["a_id"])], items[str(p["b_id"])]
        if rng.random() < 0.5:
            a, b = b, a
        rows.append({"id": run_eval.pair_key(p), "a": _side(a), "b": _side(b),
                     "gap": _gap(_when(a)[1], _when(b)[1])})
    run = hashlib.sha256("|".join(sorted(r["id"] for r in rows)).encode("utf-8")).hexdigest()[:16]
    return {"schema": SCHEMA, "run": run, "pairs": rows}


def _json_for_script(doc: object) -> str:
    """JSON safe inside <script type=application/json>: ASCII only, no '<'."""
    return json.dumps(doc, ensure_ascii=True, separators=(",", ":"), sort_keys=True).replace("<", "\\u003c")


# ---------------------------------------------------------------- the page
CSS = """
:root{color-scheme:light dark;--bg:#f6f5f1;--fg:#1b1b19;--mute:#5d5c57;--card:#fff;--line:#d9d6cc;--accent:#0b5cad;--accent-fg:#fff;--ok:#1d6b3a;--warn:#8a5a00;--bad:#a12a2a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15151a;--fg:#ecebe6;--mute:#a4a29a;--card:#1f1f26;--line:#34343e;--accent:#6aa9ee;--accent-fg:#101014;--ok:#6fcf93;--warn:#e6b45a;--bad:#ef8a8a}}
:root[data-theme=dark]{--bg:#15151a;--fg:#ecebe6;--mute:#a4a29a;--card:#1f1f26;--line:#34343e;--accent:#6aa9ee;--accent-fg:#101014;--ok:#6fcf93;--warn:#e6b45a;--bad:#ef8a8a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:62rem;margin:0 auto;padding:1rem 16px 3rem}
h1{font-size:1.25rem;margin:.2rem 0}
.private{background:var(--card);border:1px solid var(--bad);border-radius:8px;padding:.5rem .75rem;color:var(--bad);font-size:.9rem;margin:.5rem 0}
.mute{color:var(--mute);font-size:.9rem}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:.5rem 0}
.bar>i{display:block;height:100%;background:var(--accent);width:0}
.dots{display:flex;flex-wrap:wrap;gap:4px;margin:.5rem 0 1rem}
.dots button{width:30px;height:30px;padding:0;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);font-size:.75rem;cursor:pointer}
.dots button.done{background:var(--ok);color:var(--accent-fg);border-color:var(--ok)}
.dots button.cur{outline:3px solid var(--accent);outline-offset:1px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.side{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.8rem .9rem;min-width:0}
.meta{display:flex;flex-wrap:wrap;gap:.4rem .75rem;align-items:baseline;font-size:.85rem;color:var(--mute)}
.meta b{color:var(--fg)}
.lang{border:1px solid var(--line);border-radius:4px;padding:0 .35rem;font-size:.75rem}
.title{font-size:1.1rem;font-weight:600;margin:.5rem 0;overflow-wrap:anywhere}
details{margin-top:.4rem}summary{cursor:pointer;color:var(--mute);font-size:.85rem}
.excerpt{margin:.4rem 0 0;font-size:.9rem;color:var(--mute);overflow-wrap:anywhere}
.gap{text-align:center;margin:.6rem 0;color:var(--mute);font-size:.9rem}
.choices{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:.8rem 0}
.choices button{min-height:56px;border:2px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);font:inherit;font-weight:600;cursor:pointer;padding:.4rem}
.choices button small{display:block;font-weight:400;color:var(--mute);font-size:.75rem}
.choices button[aria-pressed=true]{border-color:var(--accent);background:var(--accent);color:var(--accent-fg)}
.choices button[aria-pressed=true] small{color:inherit}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:.5rem 0}
.nav button,.export button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:.5rem .9rem;font:inherit;cursor:pointer;min-height:44px}
.export button.primary{background:var(--accent);color:var(--accent-fg);border-color:var(--accent);font-weight:600}
button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.defs{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.5rem .8rem;margin:.6rem 0;font-size:.9rem}
.defs dt{font-weight:600;margin-top:.4rem}.defs dd{margin:0;color:var(--mute)}
textarea{width:100%;min-height:7rem;font:.8rem/1.4 ui-monospace,Consolas,monospace;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:.5rem}
.keys{font-size:.8rem;color:var(--mute)}
kbd{border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 .3rem;font-size:.8rem;background:var(--card)}
@media (max-width:640px){.pair{grid-template-columns:1fr}.choices{grid-template-columns:1fr 1fr}}
@media print{.choices,.nav,.export,.dots{display:none}}
"""

JS = """
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("pairs").textContent);
  var P = D.pairs, N = P.length;
  var KEY = "vigie-spotcheck-" + D.run;
  var CHOICES = [["same_event", "1"], ["related", "2"], ["different", "3"], ["uncertain", "4"]];
  var answers = {}, idx = 0, stored = true;
  function $(id) { return document.getElementById(id); }
  function load() {
    try {
      var raw = window.localStorage.getItem(KEY);
      if (raw) { var o = JSON.parse(raw); answers = o.answers || {}; idx = Math.max(0, Math.min(N - 1, o.idx || 0)); }
    } catch (e) { stored = false; }
  }
  function save() {
    try { window.localStorage.setItem(KEY, JSON.stringify({ answers: answers, idx: idx })); stored = true; }
    catch (e) { stored = false; }
  }
  function done() { var n = 0; for (var i = 0; i < N; i++) { if (answers[P[i].id]) n++; } return n; }
  function side(prefix, s) {
    $(prefix + "-outlet").textContent = s.outlet;
    $(prefix + "-lang").textContent = s.lang;
    $(prefix + "-when").textContent = s.when;
    $(prefix + "-title").textContent = s.title;
    $(prefix + "-excerpt").textContent = s.excerpt || "(aucun extrait)";
  }
  function exportDoc() {
    var labels = [];
    for (var i = 0; i < N; i++) { if (answers[P[i].id]) labels.push({ pair_id: P[i].id, label: answers[P[i].id] }); }
    labels.sort(function (a, b) { return a.pair_id < b.pair_id ? -1 : a.pair_id > b.pair_id ? 1 : 0; });
    return { schema: 1, kind: "vigie-spotcheck-labels", run: D.run, n_total: N, n_answered: labels.length, labels: labels };
  }
  function render() {
    var p = P[idx];
    side("a", p.a); side("b", p.b);
    $("gap").textContent = p.gap;
    $("pos").textContent = "Paire " + (idx + 1) + " sur " + N + " \\u00b7 " + done() + " r\\u00e9pondue" + (done() > 1 ? "s" : "");
    $("fill").style.width = (100 * done() / N) + "%";
    var btns = document.querySelectorAll(".choices button");
    for (var i = 0; i < btns.length; i++) { btns[i].setAttribute("aria-pressed", answers[p.id] === btns[i].getAttribute("data-v") ? "true" : "false"); }
    var dots = document.querySelectorAll(".dots button");
    for (var j = 0; j < dots.length; j++) { dots[j].className = (answers[P[j].id] ? "done" : "") + (j === idx ? " cur" : ""); }
    $("save").textContent = stored ? "Sauvegarde automatique dans ce navigateur." : "Sauvegarde automatique indisponible : exportez avant de fermer la page.";
    $("export-text").value = JSON.stringify(exportDoc(), null, 1);
    $("count").textContent = done() + " / " + N;
  }
  function go(i) { idx = Math.max(0, Math.min(N - 1, i)); save(); render(); window.scrollTo(0, 0); }
  function answer(v) { answers[P[idx].id] = v; save(); if (idx < N - 1) { go(idx + 1); } else { render(); } }
  function clearOne() { delete answers[P[idx].id]; save(); render(); }
  function download() {
    var text = JSON.stringify(exportDoc(), null, 1) + "\\n";
    try {
      var url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      var a = document.createElement("a");
      a.href = url; a.download = "spotcheck_labels.json";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    } catch (e) { $("export-details").open = true; }
  }
  var box = $("choices");
  var labels = { same_event: ["M\\u00eame \\u00e9v\\u00e9nement", "le m\\u00eame fait r\\u00e9el"], related: ["Li\\u00e9", "m\\u00eame dossier, autre fait"], different: ["Diff\\u00e9rent", "aucun lien"], uncertain: ["Incertain", "je ne peux pas trancher"] };
  CHOICES.forEach(function (c) {
    var b = document.createElement("button");
    b.type = "button"; b.setAttribute("data-v", c[0]); b.setAttribute("aria-pressed", "false");
    b.appendChild(document.createTextNode(labels[c[0]][0] + " (" + c[1] + ")"));
    var s = document.createElement("small"); s.textContent = labels[c[0]][1]; b.appendChild(s);
    b.addEventListener("click", function () { answer(c[0]); });
    box.appendChild(b);
  });
  var dots = $("dots");
  P.forEach(function (_, i) {
    var b = document.createElement("button");
    b.type = "button"; b.textContent = String(i + 1); b.setAttribute("aria-label", "Aller \\u00e0 la paire " + (i + 1));
    b.addEventListener("click", function () { go(i); });
    dots.appendChild(b);
  });
  $("prev").addEventListener("click", function () { go(idx - 1); });
  $("next").addEventListener("click", function () { go(idx + 1); });
  $("clear").addEventListener("click", clearOne);
  $("download").addEventListener("click", download);
  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    var t = e.target && e.target.tagName;
    if (t === "TEXTAREA" || t === "INPUT") { return; }
    if (e.key >= "1" && e.key <= "4") { e.preventDefault(); answer(CHOICES[Number(e.key) - 1][0]); }
    else if (e.key === "ArrowRight") { go(idx + 1); }
    else if (e.key === "ArrowLeft") { go(idx - 1); }
    else if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); clearOne(); }
  });
  load();
  render();
})();
"""

BODY = """
<main>
  <h1>Vérification des paires</h1>
  <p class="private"><strong>Fichier privé.</strong> Il contient des titres et extraits d'éditeurs : ne pas le publier, ne pas le déposer dans le dépôt. Aucune donnée ne quitte cette page.</p>
  <p class="mute">Pour chaque paire, dites si les deux articles rapportent <strong>le même événement</strong>. Vous ne voyez pas l'étiquette du modèle.</p>
  <details class="defs"><summary>Définitions des quatre réponses</summary>
    <dl>
      <dt>Même événement</dt><dd>Le même fait réel, y compris l'annonce et le résultat d'un même événement planifié, le même discours, la même publication de sondage, la même livraison de travaux.</dd>
      <dt>Lié</dt><dd>Le même fil d'actualité, mais un fait distinct ; un article-synthèse de plusieurs sujets face à un article de son fil.</dd>
      <dt>Différent</dt><dd>Aucun fil commun ; un acteur partagé ne suffit pas.</dd>
      <dt>Incertain</dt><dd>Vous ne pouvez pas trancher avec ce que la page montre. Préférez-le à une devinette.</dd>
    </dl>
  </details>
  <div class="bar" role="progressbar" aria-label="Progression" aria-valuemin="0" aria-valuemax="100"><i id="fill"></i></div>
  <p class="mute" id="pos" aria-live="polite"></p>
  <div class="dots" id="dots"></div>
  <section class="pair" aria-label="Paire d'articles">
    <article class="side"><div class="meta"><b id="a-outlet"></b><span class="lang" id="a-lang"></span><span id="a-when"></span></div>
      <p class="title" id="a-title"></p><details><summary>Extrait</summary><p class="excerpt" id="a-excerpt"></p></details></article>
    <article class="side"><div class="meta"><b id="b-outlet"></b><span class="lang" id="b-lang"></span><span id="b-when"></span></div>
      <p class="title" id="b-title"></p><details><summary>Extrait</summary><p class="excerpt" id="b-excerpt"></p></details></article>
  </section>
  <p class="gap" id="gap"></p>
  <div class="choices" id="choices" role="group" aria-label="Votre réponse"></div>
  <div class="nav"><button type="button" id="prev">&larr; Précédente</button><button type="button" id="next">Suivante &rarr;</button><button type="button" id="clear">Effacer la réponse</button></div>
  <p class="keys">Clavier : <kbd>1</kbd> même événement · <kbd>2</kbd> lié · <kbd>3</kbd> différent · <kbd>4</kbd> incertain · <kbd>&larr;</kbd> <kbd>&rarr;</kbd> naviguer · <kbd>Retour arrière</kbd> effacer.</p>
  <hr>
  <div class="export">
    <p><strong id="count"></strong> <span class="mute" id="save"></span></p>
    <button type="button" class="primary" id="download">Exporter mes étiquettes (JSON)</button>
    <details id="export-details"><summary>Si le téléchargement ne démarre pas, copiez ce texte</summary><textarea id="export-text" readonly></textarea></details>
    <p class="mute">Renvoyez le fichier <code>spotcheck_labels.json</code> : il ne contient que des identifiants de paires et vos réponses.</p>
  </div>
</main>
"""


def render_page(data: dict) -> str:
    csp = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
           "img-src data:; base-uri 'none'; form-action 'none'")
    return (
        "<!doctype html>\n<html lang=\"fr\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"robots\" content=\"noindex, nofollow\">\n"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{csp}\">\n"
        "<title>Vérification des paires</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n{BODY}\n"
        f"<script type=\"application/json\" id=\"pairs\">{_json_for_script(data)}</script>\n"
        f"<script>{JS}</script>\n</body>\n</html>\n"
    )


# ---------------------------------------------------------------- cli
def build(gold: dict, items: dict[str, dict], seed: int = DEFAULT_SEED, n_random: int = DEFAULT_RANDOM,
          max_fr_en: int | None = None) -> tuple[str, dict]:
    pairs, counts = sample_pairs(gold["pairs"], items, seed, n_random, max_fr_en)
    if not pairs:
        raise run_eval.Skip("no gold pair resolves in the item store")
    return render_page(page_data(pairs, items, seed)), counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="the private HTML file to write (never inside the repo, except data/)")
    ap.add_argument("--gold", type=Path, default=run_eval.DEFAULT_GOLD)
    ap.add_argument("--items", type=Path, help="items JSON (a list); default: the stores under --data-dir")
    ap.add_argument("--data-dir", type=Path, default=run_eval.DEFAULT_DATA)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--random", type=int, default=DEFAULT_RANDOM, dest="n_random",
                    help="random pairs per label stratum (same_event, related, different)")
    ap.add_argument("--max-fr-en", type=int, default=None, help="cap the fr-en same_event stratum (default: all)")
    args = ap.parse_args(argv)
    if not run_eval._private_path_ok(args.out):
        print("spotcheck: --out must point under data/ (gitignored) or outside the repository: "
              "the page carries publisher headlines", file=sys.stderr)
        return 2
    try:
        gold = run_eval.load_gold(args.gold)
        items = load_items_any(args.items, args.data_dir)
        page, counts = build(gold, items, args.seed, args.n_random, args.max_fr_en)
    except run_eval.Skip as why:
        print(f"spotcheck: skipped: {why}. Nothing written; exit 0.")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8", newline="\n")
    print(f"spotcheck: {counts['total']} pairs -> {args.out} (private: never commit or publish)")
    print("spotcheck: strata " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()) if k != "total"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
