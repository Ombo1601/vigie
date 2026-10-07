"""La Méthode — the method files as first-class pages.

The brief used to point at raw .md/.yaml files. A resident does not read
Markdown; a resident reads pages. This emitter renders every served method
file into a polished HTML page under /methode/ with the house chrome (zero
JavaScript, print-first, same palette), and rewrites internal cross-links
(legal.md -> ranking.md becomes /methode/classement.html). The .md files stay
published as the machine-readable source of truth (llms.txt, the Markdown
twin and tests read them); humans never land on them again.

Sources of truth are the root files themselves — no second brain. Pages are
deterministic (same files, same bytes) and fail-soft: a missing or unreadable
file skips that page and the rest still renders.

Usage:
  python scripts/method_site.py   # emit public/methode/*.html
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ingest_rss  # noqa: E402
import resident_brief as brief  # noqa: E402
import store_io  # noqa: E402
import takedown  # noqa: E402

# Same sentence as legal.md ("Retrait et contact"); tests pin the equality.
TAKEDOWN_PHRASE_FR = (
    "Dès réception d'une demande, le retrait est appliqué le jour même par une édition déclenchée à la main ; à défaut, au prochain passage planifié (toutes les 6 h environ, parfois jusqu'à une dizaine d'heures). Une mise en ligne qui contiendrait encore l'élément retiré est refusée."
)

METHOD = "methode-v1"
OUT_DIR = ROOT / "public" / "methode"
SITE_URL = "https://vigieqc.com"

# slug -> (source file, French title, eyebrow, one-line intro). Order = index order.
PAGES: tuple[tuple[str, str, str, str, str], ...] = (
    ("classement", "ranking.md", "Le classement", "LA LOI PUBLIQUE",
     "Comment les articles sont ordonnés — et ce qui ne pèse jamais dans l'ordre."),
    ("sources", "sources.yaml", "Les sources", "LA CHANCELLERIE",
     "La liste finie des flux suivis, les coupes consignées et leurs raisons."),
    ("financement", "RENT.md", "Qui finance Vigie", "LE LOYER",
     "Gratuit ne veut pas dire sans coût. Qui paie la machine — et ce qui n'est jamais à vendre."),
    ("vision", "VISION.md", "La vision", "LE SERMENT",
     "Ce que Vigie est, et refuse d'être. Le serment avant la machine."),
    ("registre", "REGISTRE.md", "La méthode du registre", "SCELLÉ ET VÉRIFIABLE",
     "Ce qu'un sceau contient, ce qu'il prouve, et ce qu'il ne prouve pas."),
    ("legal", "legal.md", "Mentions légales", "ATTRIBUTION ET RETRAIT",
     "Le fondement juridique du point local, et le retrait le jour même."),
    ("design", "DESIGN.md", "La loi du design", "LA BEAUTÉ SANS BROUILLARD",
     "Pourquoi le point ressemble à ceci : chaque choix de forme est une loi écrite."),
    ("facettes", "FACETS.md", "Les facettes", "VOS LUNETTES, OPT-IN",
     "Les lentilles de lecture : elles réordonnent l'arrivée, jamais le classement public."),
    ("rues", "edge.md", "L'atlas des rues", "RAPPROCHEMENTS LITTÉRAUX",
     "Un nom de rue partagé entre un dossier et une entrave officielle — jamais une preuve géographique."),
    ("anomalies", "anomalies.md", "Les anomalies", "LECTURE STRUCTURELLE",
     "Les règles à seuils fixes qui lisent la collecte officielle — des faits mesurés, pas des prédictions."),
    ("frictions", "FRICTION.md", "Les frictions", "LE CARNET DE BORD",
     "Les douleurs de l'arrivée, nommées une à une. La méthode s'améliore en public."),
)

# Internal cross-links inside the method files -> their page.
_LINK_MAP = {
    "ranking.md": "classement",
    "sources.yaml": "sources",
    "RENT.md": "financement",
    "VISION.md": "vision",
    "REGISTRE.md": "registre",
    "legal.md": "legal",
    "DESIGN.md": "design",
    "FACETS.md": "facettes",
    "edge.md": "rues",
    "anomalies.md": "anomalies",
    "FRICTION.md": "frictions",
}

_WORDMARK_SVG = (
    '<svg width="28" height="32" viewBox="0 0 28 32" aria-hidden="true">'
    '<path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/>'
    "</svg>"
)


# --------------------------------------------------------------------------- #
# Markdown subset -> HTML (deterministic, safe)
# --------------------------------------------------------------------------- #
def _slugify(text: str) -> str:
    folded = brief.folded(text)
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:64]


def _map_link(url: str) -> str:
    base = url.split("#", 1)[0]
    name = base.rstrip("/").rsplit("/", 1)[-1]
    if name in _LINK_MAP:
        anchor = url.split("#", 1)[1] if "#" in url else ""
        return f"/methode/{_LINK_MAP[name]}.html" + (f"#{anchor}" if anchor else "")
    return url


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)

    def link_sub(m: re.Match) -> str:
        # The whole line was already escaped for text, so the URL arrives with
        # entities (`?a=1&amp;b=2`): unescape before escaping it as an attribute
        # or a query `&` becomes `&amp;amp;`.
        label, url = m.group(1), html.unescape(m.group(2))
        if url.startswith(("http://", "https://")):
            return f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{label}</a>'
        href = _map_link(url)
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link_sub, text)
    return text


def _table(lines: list[str]) -> str:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    if not rows:
        return ""
    head, *body = rows
    if body and all(re.fullmatch(r":?-{2,}:?", cell) for cell in body[0]):
        body = body[1:]
    thead = "<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>"
    tbody = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body)
    return f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"


def md_to_html(text: str) -> str:
    """The subset of Markdown the house method files actually use."""
    lines = text.splitlines()
    out: list[str] = []
    seen_ids: dict[str, int] = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            i += 1
            buf: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(html.escape(lines[i], quote=False))
                i += 1
            i += 1
            out.append(f"<pre><code>{chr(10).join(buf)}</code></pre>")
            continue
        if stripped.startswith("|") and i + 1 < n and lines[i + 1].strip().startswith("|"):
            buf = []
            while i < n and lines[i].strip().startswith("|"):
                buf.append(lines[i])
                i += 1
            out.append(_table(buf))
            continue
        heading = re.match(r"^(#{1,5})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 5)
            text_content = heading.group(2)
            anchor = _slugify(text_content) or "section"
            count = seen_ids.get(anchor, 0)
            seen_ids[anchor] = count + 1
            if count:
                anchor = f"{anchor}-{count + 1}"
            out.append(f'<h{level} id="{anchor}">{_inline(text_content)}</h{level}>')
            i += 1
            continue
        if re.fullmatch(r"-{3,}", stripped):
            out.append("<hr>")
            i += 1
            continue
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(_inline(lines[i].strip()[1:].strip()))
                i += 1
            out.append(f"<blockquote>{' '.join(buf)}</blockquote>")
            continue
        list_match = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if list_match:
            ordered = bool(re.match(r"\d+\.", list_match.group(2)))
            tag = "ol" if ordered else "ul"
            items = []
            while i < n:
                lm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if not lm or not lines[i].strip():
                    break
                items.append(f"<li>{_inline(lm.group(3))}</li>")
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
            r"^(#{1,5}\s|\||```|>|\s*[-*]\s|\s*\d+\.\s|-{3,})", lines[i]
        ):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(part.strip() for part in buf))}</p>")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Sources page (data, not raw YAML)
# --------------------------------------------------------------------------- #
def _deferred_sources() -> list[dict]:
    text = (ROOT / "sources.yaml").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^deferred:\n(.*?)(?=^rules:|\Z)", text)
    if not m:
        return []
    chunks = re.split(r"\n  - id:", "\n" + m.group(1))
    out: list[dict] = []
    for chunk in chunks:
        chunk = chunk.strip("\n")
        if not chunk.strip() or chunk.strip().startswith("#"):
            continue
        if not chunk.lstrip().startswith("id:"):
            chunk = "id:" + chunk
        rec: dict = {}
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            if line.startswith("- "):
                line = line[2:]
            key, _, val = line.partition(":")
            rec[key.strip()] = brief.plain(val)
        if rec.get("id"):
            out.append(rec)
    return out


def _rules_scalars() -> dict:
    """The one-line scalars of the `rules:` block (keys indented two spaces).

    Folded (`>`) values and their deeper-indented continuation lines are
    skipped: only single-line facts such as the RSS ceiling are rendered."""
    text = (ROOT / "sources.yaml").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^rules:\n(.*?)\Z", text)
    out: dict = {}
    if not m:
        return out
    for raw_line in m.group(1).splitlines():
        if not raw_line.startswith("  ") or raw_line.startswith("   ") or ":" not in raw_line:
            continue
        key, _, val = raw_line.strip().partition(":")
        val = val.strip()
        if not key or key.startswith("#") or val in ("", ">", "|"):
            continue
        out[key] = brief.plain(val)
    return out


# Ownership as FACTUAL STRUCTURE (who owns, in what legal form), as each row's
# public reference (sources.yaml `ownership_ref`, read on `ownership_asof`)
# states it. Never a judgement of the source: the class code "independent" is
# not a structure and is never printed. The words come from ONE table,
# scripts/ownership.py (`STRUCTURE`, `words`), which the event pages print on
# their ownership chips too: the same owner reads the same on both.
def ownership_structure(src: dict) -> str:
    """The structure words of one sources.yaml record: the label the event
    pages print, then the table's detail in parentheses when it gives one."""
    import ownership  # noqa: PLC0415 - lazy: the method pages render without it when off

    label, detail = ownership.source_words(src, "fr")
    return f"{label} ({detail})" if detail else label


def _ownership_cell(src: dict) -> str:
    structure = brief.esc(ownership_structure(src))
    ref = brief.safe_url(src.get("ownership_ref"))
    asof = brief.plain(src.get("ownership_asof")) if src.get("ownership_asof") else ""
    when = f" (lue le {brief.esc(asof)})" if asof else ""
    if ref:
        return f'{structure} — <a href="{brief.esc(ref)}" rel="noopener noreferrer">référence publique</a>{when}'
    return f"{structure} — référence publique non déclarée"


def _sources_html(ownership: bool = False) -> str:
    """The sources page. `ownership` (the event surfaces are not off,
    scripts/surfaces.py) adds the ownership column: the factual structure of
    each source's owner, with its public reference and the date it was read."""
    sources = (
        ingest_rss.load_enabled_by_type(ROOT / "sources.yaml", "rss")
        + ingest_rss.load_enabled_by_type(ROOT / "sources.yaml", "wzdx")
        + ingest_rss.load_enabled_by_type(ROOT / "sources.yaml", "civic-html")
    )
    deferred = _deferred_sources()
    rules = _rules_scalars()
    nest_label = {"primary": "Québec et environs", "province": "Au Québec", "linked": "Ailleurs"}
    rows = []
    for src in sources:
        kind = "officiel" if str(src.get("source_kind") or "") == "official" else "média"
        nest = nest_label.get(str(src.get("nest_role") or ""), str(src.get("nest_role") or "—"))
        lang = "EN" if str(src.get("language") or "").startswith("en") else "FR"
        homepage = brief.safe_url(src.get("homepage"))
        name = brief.esc(str(src.get("name") or src.get("id") or ""))
        title = (
            f'<a href="{brief.esc(homepage)}" rel="noopener noreferrer">{name}</a>'
            if homepage else name
        )
        chips = (
            f'<span class="methode-chip official">officiel</span>' if kind == "officiel"
            else '<span class="methode-chip">média</span>'
        )
        owner = f"<td>{_ownership_cell(src)}</td>" if ownership else ""
        rows.append(
            "<tr>"
            f"<td>{title}{chips}</td>"
            f"<td>{brief.esc(nest)}</td>"
            f"<td>{brief.esc(str(src.get('institution_name') or '—'))}</td>"
            f"{owner}"
            f"<td>{lang}</td>"
            f"<td>{brief.esc(str(src.get('license_note') or '—'))}</td>"
            "</tr>"
        )
    owner_head = "<th>Propriété</th>" if ownership else ""
    table = (
        f"<table><thead><tr><th>Flux</th><th>Échelle</th><th>Institution</th>{owner_head}<th>Langue</th>"
        "<th>Note de licence</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    if ownership:
        table = (
            '<p id="propriete">La colonne <strong>Propriété</strong> décrit la structure de propriété '
            "de chaque source telle que sa référence publique l’énonce (forme juridique, actionnaire), "
            "avec la date où Vigie l’a lue : un fait vérifiable, jamais un jugement sur la source. "
            "Les pages des événements décrivent chaque propriétaire avec les mêmes mots (ceux qui "
            "précèdent la parenthèse). Les articles de sources qui partagent un même propriétaire "
            "comptent pour une seule origine dans les événements.</p>" + table
        )
    cut_rows = []
    for src in deferred:
        cut_rows.append(
            f'<li><strong>{brief.esc(str(src.get("name") or src.get("id")))}</strong> — '
            f'{brief.esc(str(src.get("reason") or "coupé, raison consignée"))}</li>'
        )
    # A source cut in the registry itself (enabled: false + cut_reason) or
    # withdrawn on a publisher's request stays named here, never vanishes.
    requests = takedown.load()[0]
    withdrawn = {
        str(rec.get("id")): rec
        for rec in takedown.withdrawn_sources(takedown.Rules(requests), sources_path=ROOT / "sources.yaml")
    }
    for src in ingest_rss.load_sources(ROOT / "sources.yaml"):
        sid = str(src.get("id") or "")
        name = brief.esc(str(src.get("name") or sid))
        if sid in withdrawn:
            req = withdrawn[sid].get("takedown") or {}
            cut_rows.append(
                f'<li><strong>{name}</strong> — {brief.esc(takedown.WITHDRAWN_LABEL_FR)}'
                f' (demande du {brief.esc(str(req.get("requested_at") or "—"))})</li>'
            )
        elif src.get("enabled") is not True:
            reason = str(src.get("cut_reason") or "coupé, raison consignée")
            cut_at = brief.plain(src.get("cut_at")) if src.get("cut_at") else ""
            when = f" (coupé le {brief.esc(cut_at)})" if cut_at else ""
            cut_rows.append(f'<li><strong>{name}</strong> — {brief.esc(brief.plain(reason))}{when}</li>')
    cuts = (
        f'<h3 id="coupes">Coupées ou reportées</h3><ul class="methode-cuts">{"".join(cut_rows)}</ul>'
        if cut_rows else ""
    )
    removals = takedown.public_rows(requests)
    removal_rows = "".join(
        "<tr>"
        f"<td>{brief.esc(r['by'])}</td><td>{brief.esc(r['kind_label'])}</td>"
        f"<td>{brief.esc(r['requested_at'])}</td><td>{brief.esc(r['status_label'])}</td>"
        "</tr>"
        for r in removals
    )
    retraits = (
        '<h3 id="retraits">Retraits à la demande des éditeurs</h3>'
        + (
            "<table><thead><tr><th>Éditeur ou ayant droit</th><th>Portée</th><th>Demande</th>"
            f"<th>État</th></tr></thead><tbody>{removal_rows}</tbody></table>"
            if removal_rows else '<p class="no-data">Aucune demande de retrait à ce jour.</p>'
        )
        + '<p class="fine">' + TAKEDOWN_PHRASE_FR + " "
        "Seuls l’éditeur, la portée et la date sont publiés — jamais le "
        "contenu retiré. <a href=\"/methode/legal.html#retrait-et-contact\">Demander un retrait</a>.</p>"
    )
    fine = []
    if rules.get("max_enabled_rss_v0"):
        rss_now = len([src for src in sources if src.get("type") == "rss"])
        fine.append(f"Plafond : au plus {brief.esc(rules['max_enabled_rss_v0'])} flux RSS actifs "
                    f"({rss_now} aujourd’hui).")
    # rules.coverage_note is an English working note of the registry (served as
    # /sources.yaml); this French page states the ceiling only.
    return (
        f'<p>Vigie suit une liste <strong>finie et publiée</strong> de sources : '
        f"<strong>{len(sources)}</strong> actives, <strong>{len(cut_rows)}</strong> coupées ou "
        "reportées. Chaque coupe est consignée avec sa raison, jamais effacée en silence. "
        "Toute institution est nommée ; aucune ne possède le point.</p>"
        f"{table}{cuts}{retraits}"
        + (f'<p class="fine">{" ".join(fine)}</p>' if fine else "")
    )


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def _chrome(title: str, desc: str, canonical: str, body: str) -> str:
    nav = (
        '<header class="masthead">'
        f'<a class="wordmark" href="/" aria-label="Vigie, accueil">{_WORDMARK_SVG}vigie'
        '<span class="wordmark-dot">.</span></a><span class="edition">LA MÉTHODE</span>'
        '<nav aria-label="Navigation principale"><a href="/">Le point</a>'
        '<a href="/methode/index.html">La méthode</a>'
        '<a href="/registre.html">Le registre</a>'
        '<a href="/methode/legal.html">Mentions légales</a></nav></header>'
    )
    return f"""<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title} — Vigie</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}"><meta name="robots" content="index, follow">
<link rel="describedby" href="/llms.txt">
<meta property="og:type" content="website"><meta property="og:site_name" content="Vigie"><meta property="og:locale" content="fr_CA">
<meta property="og:title" content="{title} — Vigie"><meta property="og:description" content="{desc}"><meta property="og:url" content="{canonical}">
<meta property="og:image" content="{brief.SITE_OG_IMAGE}">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{title} — Vigie"><meta name="twitter:description" content="{desc}">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"></head>
<body class="methode-body"><a class="skip-link" href="#methode">Aller à la méthode</a>
{nav}
<main id="methode">
{body}
</main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>La méthode est publique.</span><a class="legal-link" href="/methode/legal.html">Mentions légales, attribution et retrait</a></footer></body></html>
"""


def _body(eyebrow: str, title: str, intro: str, content: str, *, link_index: bool = True) -> str:
    index_link = (
        '<p class="methode-index-link"><a href="/methode/index.html">← Toutes les pages méthode</a></p>'
        if link_index else ""
    )
    return (
        '<header class="methode-head"><p class="eyebrow">' + brief.esc(eyebrow) + "</p>"
        f'<h1 class="recit-title">{brief.esc(title)}</h1>'
        f'<p class="recit-attrib">{brief.esc(intro)}</p></header>'
        + index_link
        + f'<div class="methode-doc">{content}</div>'
    )


def render_page(slug: str, file: str, title: str, eyebrow: str, intro: str, *, ownership: bool = False) -> str:
    path = ROOT / file
    # utf-8-sig: a BOM must never turn the file's first `#` heading into a
    # paragraph (ranking.md / RENT.md shipped with one; the BOM is stripped
    # here even if it reappears).
    text = path.read_text(encoding="utf-8-sig")
    if file == "sources.yaml":
        content = _sources_html(ownership=ownership)
    else:
        content = md_to_html(text)
    return _chrome(
        title, intro, f"{SITE_URL}/methode/{slug}.html",
        _body(eyebrow, title, intro, content),
    )


def render_index(rendered: set[str] | None = None) -> str:
    """The index lists only pages that were actually rendered (no dead cards)."""
    cards = []
    for slug, _file, title, eyebrow, intro in PAGES:
        if rendered is not None and slug not in rendered:
            continue
        cards.append(
            f'<a class="method-card" href="/methode/{slug}.html">'
            f'<span class="method-card-kicker">{brief.esc(eyebrow)}</span>'
            f'<span class="method-card-title">{brief.esc(title)}</span>'
            f'<span class="method-card-line">{brief.esc(intro)}</span>'
            '<span class="method-card-go">Lire la page ↗</span></a>'
        )
    body = (
        '<header class="methode-head"><p class="eyebrow">TOUT EST PUBLIÉ</p>'
        '<h1 class="recit-title">La méthode, page par page.</h1>'
        '<p class="recit-attrib">Chaque règle de Vigie est écrite, versionnée et servie '
        "comme une page normale. Les fichiers Markdown restent publiés pour les "
        "machines ; les humains lisent des pages.</p></header>"
        f'<div class="method-grid methode-grid">{"".join(cards)}</div>'
    )
    return _chrome(
        "La méthode", "Toutes les pages méthode de Vigie : le classement, les sources, le financement, le registre, les mentions légales.",
        f"{SITE_URL}/methode/index.html",
        body,
    )


def emit(out_dir: Path = OUT_DIR, *, mode: str | None = None) -> dict:
    """Render every method page + the index. Fail-soft per file.

    `mode` is the switch of the event surfaces (scripts/surfaces.py): the
    sources page prints ownership only when it is "preview" or "live" (the
    event pages count origins by owner); "off" or None renders the pages as
    they were before the event layer."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ownership = mode in ("preview", "live")
    rendered: list[str] = []
    for slug, file, title, eyebrow, intro in PAGES:
        try:
            page = render_page(slug, file, title, eyebrow, intro, ownership=ownership)
        except (OSError, ValueError, SystemExit) as exc:
            # SystemExit included: a malformed sources.yaml must skip one page,
            # never terminate the render (BaseException would escape every
            # other fail-soft layer).
            print(f"methode: skip {slug} ({type(exc).__name__}: {exc})")
            continue
        store_io.write_text_atomic(out_dir / f"{slug}.html", page)
        rendered.append(slug)
    rendered_set = set(rendered)
    store_io.write_text_atomic(out_dir / "index.html", render_index(rendered_set))
    # A removed/renamed PAGES slug must not keep being served as a stale page.
    for stale in sorted(out_dir.glob("*.html")):
        stem = stale.stem
        if stale.name == "index.html" or stem in rendered_set:
            continue
        if stem in {p[0] for p in PAGES}:
            continue
        try:
            stale.unlink()
        except OSError:
            pass
    print(f"methode: {len(rendered)} pages -> {out_dir}")
    return {"method": METHOD, "pages": len(rendered), "slugs": sorted(rendered)}


def main() -> int:
    emit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
