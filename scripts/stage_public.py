"""Build a complete, validated static snapshot in deploy/public/ (no upload).

A failed build keeps the previous snapshot; a successful build cannot retain
obsolete assets. Files are copied from their authoritative sources.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deploy" / "public"
METHODS = ("VISION.md", "ranking.md", "sources.yaml", "RENT.md", "FRICTION.md", "FACETS.md", "DESIGN.md", "edge.md", "anomalies.md", "legal.md", "REGISTRE.md")
REQUIRED_ASSETS = ("index.html", "morning.html", "explorer.html", "favicon.svg")
# Pages emitted by the record layer (registre / affiche): listed in the sitemap
# when present, never required, so a fixture tree or a partial render still
# stages the front door.
OPTIONAL_PAGES = ("registre.html", "affiche.html", "dossiers.html", "partir.html", "memoire.html")
# Method pages rendered by scripts/method_site.py; listed in the sitemap when
# present. The raw .md/.yaml sources remain staged (machine twins) but humans
# are never pointed at them.
METHOD_PAGES = (
    "vision", "classement", "sources", "financement", "frictions",
    "facettes", "design", "rues", "anomalies", "legal", "registre",
)
ASSET_EXTENSIONS = {
    ".html", ".css", ".js", ".mjs", ".svg", ".png", ".jpg", ".jpeg",
    ".webp", ".avif", ".gif", ".ico", ".woff", ".woff2", ".txt",
    ".webmanifest", ".xml", ".json",
    # Markdown twins for agents (llms.txt v2: rel="alternate" type="text/markdown").
    ".md",
}
# Locally served publisher preview images (scripts/fetch_brief_media.py).
MEDIA_NAME = re.compile(r"[a-f0-9]{20}\.(?:jpg|jpeg|png|webp|avif|gif)")
MEDIA_MAX_BYTES = 900_000

# Discoverability: every staged release carries a permissive robots.txt (this is
# public-interest aggregation; nothing here is private) and a sitemap listing the
# front door and the published method files. Only permanent URLs belong in it: the
# English explorer / morning pages are off the French resident path, and the
# per-dossier record pages exist for one edition only, so neither is advertised.
SITE_URL = "https://vigieqc.com"
ROBOTS_TEXT = f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"
SITEMAP_PATHS = ("/",)


# Live (scripts/surfaces.py): the index pages listed with their hreflang
# alternates (docs/I18N.md section 1). Each language version carries the full
# set, x-default pointing at the French page.
LIVE_ALTERNATES = (("fr-CA", "/"), ("en-CA", "/en/"), ("x-default", "/"))
LIVE_SITEMAP_PATHS = ("/", "/en/", "/le-point.html")


def _surfaces_mode(mode: str | None) -> str:
    import surfaces  # noqa: PLC0415 - lazy: staging stays importable alone

    return surfaces.mode() if mode is None else surfaces.validate(mode, "stage_public mode")


def sitemap_xml(directory: Path, mode: str = "off") -> str:
    """A small sitemap with the edition's build date as lastmod.

    Off and preview: the front door, the record pages and the method pages,
    byte-identical in both (a preview is never sitemapped). Live: the French
    and English front doors with their xhtml:link hreflang alternates, and the
    full brief at /le-point.html."""
    try:
        lastmod = datetime.fromtimestamp(
            (directory / "index.html").stat().st_mtime, tz=timezone.utc
        ).date().isoformat()
    except OSError:
        lastmod = None
    live = mode == "live"
    optional = tuple(f"/{name}" for name in OPTIONAL_PAGES if (directory / name).is_file())
    methode_pages = (
        tuple(f"/methode/{slug}.html" for slug in METHOD_PAGES
              if (directory / "methode" / f"{slug}.html").is_file())
    )
    paths = ((*LIVE_SITEMAP_PATHS,) if live else SITEMAP_PATHS) + (*optional, *methode_pages)
    parts = []
    for path in paths:
        loc = f"{SITE_URL}/" if path == "/" else f"{SITE_URL}{path}"
        stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        links = ""
        if live and path in ("/", "/en/"):
            links = "".join(f'<xhtml:link rel="alternate" hreflang="{lang}" href="{SITE_URL}{href}"/>'
                            for lang, href in LIVE_ALTERNATES)
        parts.append(f"<url><loc>{loc}</loc>{stamp}{links}</url>")
    xmlns = ('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml"'
             if live else 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset {xmlns}>'
        + "".join(parts)
        + "</urlset>\n"
    )


class PageLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.duplicates: set[str] = set()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        values = dict(attrs)
        ident = values.get("id")
        if ident:
            if ident in self.ids:
                self.duplicates.add(ident)
            self.ids.add(ident)
        for attr in ("href", "src", "poster"):
            if values.get(attr):
                if tag == "link" and values.get("rel") in {"preconnect", "dns-prefetch"}:
                    continue
                self.links.append((attr, values[attr]))


class PageHead(HTMLParser):
    """What the release gate reads in a page head: robots, canonical,
    hreflang alternates, the event-surface marker."""

    def __init__(self) -> None:
        super().__init__()
        self.robots: list[str] = []
        self.canonical: list[str] = []
        self.alternates: list[tuple[str, str]] = []
        self.event_view = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        values = {k: (v or "") for k, v in attrs}
        if tag == "meta" and values.get("name", "").lower() == "robots":
            self.robots.append(values.get("content", "").lower())
        if tag == "meta" and values.get("name") == "vigie-view":
            self.event_view = values.get("content", "")
        if tag == "link" and values.get("rel", "").lower() == "canonical":
            self.canonical.append(values.get("href", ""))
        if tag == "link" and values.get("rel", "").lower() == "alternate" and values.get("hreflang"):
            self.alternates.append((values["hreflang"], values.get("href", "")))


# Event surfaces (scripts/surfaces.py): what each mode must ship.
SURFACE_REQUIRED = {
    "preview": ("evenements.html", "en/evenements.html", "evenements/latest.json", "qualite.json"),
    "live": ("index.html", "en/index.html", "evenements.html", "en/evenements.html",
             "evenements/latest.json", "qualite.json", "le-point.html"),
}
FRONT_DOORS = {"preview": ("evenements.html", "en/evenements.html"), "live": ("index.html", "en/index.html")}


def _head(path: Path) -> PageHead:
    head = PageHead()
    head.feed(path.read_text(encoding="utf-8"))
    head.close()
    return head


def surface_errors(directory: Path, mode: str) -> list[str]:
    """The release gate of the event surfaces in `mode` ("preview" | "live";
    "off" checks nothing new). Every problem is a diagnosed refusal:

      required   the artefacts of the mode exist (a fault of the event emitter
                 blocks the release, like a missing registre page);
      hreflang   every hreflang alternate of every page names a staged file;
      robots     preview: every event page says noindex, and / is still the
                 brief; live: no event page says noindex, / is the events front
                 door, it links the full river, whose canonical is its own URL;
      budget     each front door is at most surfaces.FRONT_DOOR_BUDGET bytes;
      sitemap    live: every <loc> and alternate of sitemap.xml is staged."""
    import surfaces  # noqa: PLC0415

    if mode not in SURFACE_REQUIRED:
        return []
    base = Path(directory)
    errors: list[str] = []
    for rel in SURFACE_REQUIRED[mode]:
        if not (base / rel).is_file():
            errors.append(f"{rel}: required event-surface artefact missing (surfaces {mode})")
    for path in sorted(base.rglob("*.html")):
        rel = path.relative_to(base).as_posix()
        head = _head(path)
        for lang, href in head.alternates:
            target = surfaces.path_of_url(href)
            if target is None:
                errors.append(f"{rel}: hreflang {lang} points outside the site: {href}")
            elif not (base / target).is_file():
                errors.append(f"{rel}: hreflang {lang} target missing: {href}")
        is_event = bool(head.event_view) or (rel in surfaces.EVENT_FILES
                                             or any(rel.startswith(d) for d in surfaces.EVENT_DIRS))
        if not is_event:
            continue
        noindex = any("noindex" in r for r in head.robots)
        if mode == "preview" and not noindex:
            errors.append(f"{rel}: a preview event page must carry noindex")
        if mode == "live" and noindex:
            errors.append(f"{rel}: a live event page must not carry noindex")
    for rel in FRONT_DOORS[mode]:
        path = base / rel
        if path.is_file() and path.stat().st_size > surfaces.FRONT_DOOR_BUDGET:
            errors.append(f"{rel}: front door is {path.stat().st_size} bytes, over the "
                          f"{surfaces.FRONT_DOOR_BUDGET} byte budget")
    index = base / "index.html"
    if mode == "preview" and index.is_file() and _head(index).event_view:
        errors.append("index.html: in preview the front door at / stays the brief")
    if mode == "live":
        if index.is_file():
            if _head(index).event_view != "current":
                errors.append("index.html: in live the front door at / must be the events front door")
            if f'href="{surfaces.RIVER_PATH}"' not in index.read_text(encoding="utf-8"):
                errors.append(f"index.html: the front door must link the full river {surfaces.RIVER_PATH}")
        point = base / "le-point.html"
        if point.is_file():
            want = f"{SITE_URL}{surfaces.RIVER_PATH}"
            if _head(point).canonical != [want]:
                errors.append(f"le-point.html: canonical must be {want} (the brief moved off /)")
        sitemap = base / "sitemap.xml"
        if sitemap.is_file():
            text = sitemap.read_text(encoding="utf-8")
            for url in sorted(set(re.findall(r"<loc>([^<]+)</loc>", text)) | set(re.findall(r'href="([^"]+)"', text))):
                target = surfaces.path_of_url(html.unescape(url))
                if target is None or not (base / target).is_file():
                    errors.append(f"sitemap.xml: {url} is not staged")
    return errors


def validate_site(directory: Path, mode: str = "off") -> list[str]:
    """Check local navigation and anchors without making network requests;
    with an event-surface `mode` other than "off", also `surface_errors`."""
    base = directory.resolve()
    pages: dict[Path, PageLinks] = {}
    errors: list[str] = []
    for path in sorted(base.rglob("*.html")):
        page = PageLinks()
        page.feed(path.read_text(encoding="utf-8"))
        pages[path] = page
        for ident in sorted(page.duplicates):
            errors.append(f"{path.relative_to(base)}: duplicate id {ident}")
    for path, page in pages.items():
        for attr, raw in page.links:
            link = urlsplit(raw)
            if link.scheme or link.netloc:
                if link.scheme.lower() not in {"https", "http", "mailto", "tel", ""}:
                    errors.append(f"{path.relative_to(base)}: unsafe {attr} scheme")
                continue
            decoded = unquote(link.path)
            if "\\" in decoded or "\x00" in decoded:
                errors.append(f"{path.relative_to(base)}: invalid local URL {raw}")
                continue
            target = (base / decoded.lstrip("/")) if decoded.startswith("/") else (path.parent / decoded)
            if not decoded:
                target = path
            target = target.resolve()
            if not target.is_relative_to(base):
                errors.append(f"{path.relative_to(base)}: URL escapes publish root {raw}")
                continue
            if target.is_dir():
                target /= "index.html"
            if not target.is_file():
                errors.append(f"{path.relative_to(base)}: missing local target {raw}")
            elif link.fragment and target in pages and unquote(link.fragment) not in pages[target].ids:
                errors.append(f"{path.relative_to(base)}: missing anchor {raw}")
    if mode != "off":
        errors.extend(surface_errors(base, mode))
    return sorted(set(errors))


_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>`)\]\\]+")
_TEXT_SUFFIXES = {".html", ".md", ".json", ".txt", ".xml", ".yaml"}


def takedown_violations(directory: Path, root: Path = ROOT) -> list[str]:
    """R10 release gate: what in a staged tree still matches an active takedown.

    Refuses an unreadable or invalid takedowns.yaml (an entry nobody can
    interpret is a request nobody enforces), any media file matching an image
    takedown by sha256, file name or manifest URL, and any page that still
    links or embeds a withdrawn article or image, or an article of a withdrawn
    source still present in the stores. Domain takedowns apply to every page
    except the method pages, which name sources by their homepage.
    Missing or empty takedowns.yaml: nothing to check.
    """
    import takedown  # noqa: PLC0415 - lazy: staging stays importable alone

    entries, errors = takedown.load(Path(root) / "takedowns.yaml")
    problems = [f"takedowns.yaml invalid: {err}" for err in errors]
    rules = takedown.Rules(entries)
    if not rules:
        return problems
    base = Path(directory)
    # A withdrawn source's articles, as the stores behind these pages know them.
    stores = tuple(Path(root) / "data" / name for name in takedown.STORE_NAMES)
    source_urls: dict[str, dict] = {}
    for sid, entry in sorted(rules.sources.items()):
        for url in takedown.source_article_urls(sid, stores):
            source_urls[takedown._canon(url) or url] = entry
    manifest: dict = {}
    try:
        doc = json.loads((Path(root) / "data" / "media" / "brief_manifest.json").read_text(encoding="utf-8"))
        media = doc.get("media") if isinstance(doc, dict) else None
        for entry in (media or {}).values() if isinstance(media, dict) else []:
            if isinstance(entry, dict) and isinstance(entry.get("file"), str):
                manifest[entry["file"]] = entry
    except (OSError, ValueError):
        manifest = {}
    media_dir = base / "media"
    if media_dir.is_dir():
        for path in sorted(media_dir.iterdir()):
            if not path.is_file():
                continue
            entry = manifest.get(path.name, {})
            hit = (rules.match_image(sha=hashlib.sha256(path.read_bytes()).hexdigest(), file=path.name)
                   or rules.match_image(entry.get("image_url"))
                   or rules.match_url(entry.get("article_url"))
                   or source_urls.get(takedown._canon(entry.get("article_url")) or entry.get("article_url")))
            if hit:
                problems.append(f"media/{path.name}: withdrawn on request (takedown {hit['id']})")
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base).as_posix()
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES or rel == "build-manifest.json":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".html":
            text = html.unescape(text)
        check_hosts = not (rel.startswith("methode/") or rel in METHODS)
        for raw in sorted(set(_URL_IN_TEXT.findall(text))):
            url = raw.rstrip(".,;:!?")
            canon = takedown._canon(url) or url
            hit = (rules.urls.get(canon) or rules.image_urls.get(canon) or source_urls.get(canon)
                   or (rules.match_host(url) if check_hosts else None))
            if hit:
                problems.append(f"{rel}: still references a withdrawn {hit['kind']} (takedown {hit['id']})")
                break
    return problems


def staged_vercel_config(data: bytes, mode: str) -> bytes:
    """The deploy config (public/vercel.json, kept in canonical JSON: indent 2,
    LF, a test holds it) as staged in `mode`. Off: the event-surface rules
    (surfaces.EVENT_HEADER_SOURCES) are left out, so the staged config is the
    one from before the wiring, byte for byte; no other rule is touched, so no
    existing header is weakened. Preview and live: as committed."""
    import surfaces  # noqa: PLC0415

    if mode != "off":
        return data
    doc = json.loads(data.decode("utf-8"))
    doc["headers"] = [rule for rule in doc.get("headers") or []
                      if not (isinstance(rule, dict) and rule.get("source") in surfaces.EVENT_HEADER_SOURCES)]
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _safe_remove(path: Path, parent: Path) -> None:
    # Check final resolved paths before any recursive removal on Windows.
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise ValueError(f"Unsafe temporary path: {path}")
    if path.exists():
        shutil.rmtree(path)


STALE_STAGE_SECONDS = 24 * 3600
RENAME_ATTEMPTS = 8
RENAME_BASE_DELAY = 0.2
# Windows codes for "file in use": ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION,
# ERROR_LOCK_VIOLATION. Only those (and their POSIX errno cousins) are retried;
# a genuine filesystem failure must surface immediately.
TRANSIENT_RENAME_WINERR = frozenset({5, 32, 33})
TRANSIENT_RENAME_ERRNO = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY})
STAGE_LOCK_NAME = ".vigie-stage.lock"
STAGE_LOCK_STALE_SECONDS = 10 * 60
STAGE_LOCK_WAIT_SECONDS = 60.0


def _is_transient_rename_error(exc: OSError) -> bool:
    if getattr(exc, "winerror", None) in TRANSIENT_RENAME_WINERR:
        return True
    return exc.errno in TRANSIENT_RENAME_ERRNO


def _rename_retry(source: Path, target: Path) -> None:
    """Rename, tolerating the transient Windows sharing violation (WinError 32).

    Antivirus, the search indexer or a just-finished reader can hold a handle
    on a file inside the tree for a moment, making Path.rename fail with
    "used by another process". Retry only those transient errors with a short
    backoff; anything else propagates at once so a broken swap never publishes.
    """
    delay = RENAME_BASE_DELAY
    for attempt in range(RENAME_ATTEMPTS):
        try:
            source.rename(target)
            return
        except FileNotFoundError:
            # A concurrent stager moved the source between our check and the
            # rename; the caller decides what that means.
            raise
        except OSError as exc:
            if attempt == RENAME_ATTEMPTS - 1 or not _is_transient_rename_error(exc):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


@contextlib.contextmanager
def _stage_lock(parent: Path):
    """Serialize the release swap between concurrent stagers.

    The six-hour scheduled refresh, a manual `verify.py` and a manual
    `pipeline.py --stage` can overlap; without this, two processes rename the
    same `deploy/public` at once and one fails (or publishes a half-race). A
    lock older than ten minutes is treated as a crashed stager. If the
    filesystem refuses locking at all, staging proceeds unsynchronized rather
    than block a deploy.
    """
    parent.mkdir(parents=True, exist_ok=True)
    lock = parent / STAGE_LOCK_NAME
    token = str(os.getpid())
    acquired = False
    waited = 0.0
    delay = 0.1
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > STAGE_LOCK_STALE_SECONDS:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if waited >= STAGE_LOCK_WAIT_SECONDS:
                raise RuntimeError(
                    "Another staging run is active; refusing to race the release swap")
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 1.0)
        except OSError:
            break  # cannot lock here: proceed without serialization
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
            acquired = True
            break
    try:
        yield
    finally:
        if acquired:
            try:
                if lock.read_text(encoding="utf-8", errors="replace").strip() == token:
                    lock.unlink()
            except OSError:
                pass




def _sweep_stale_siblings(parent: Path, output_name: str) -> None:
    """Remove .vigie-stage-*/.vigie-previous-* leftovers older than a day.

    A hard crash between the rename and the cleanup would otherwise park the
    previous release and accumulate orphans forever; normal exceptions are
    already handled by the restore path. Fail-soft: a sweep problem never
    blocks staging.
    """
    try:
        now = time.time()
        for entry in parent.iterdir():
            if entry.name == output_name:
                continue
            if not entry.name.startswith((".vigie-stage-", ".vigie-previous-")):
                continue
            try:
                if entry.is_symlink() or not entry.is_dir():
                    continue
                if now - entry.stat().st_mtime < STALE_STAGE_SECONDS:
                    continue
                _safe_remove(entry, parent)
            except OSError:
                continue
    except OSError:
        return


def stage(root: Path = ROOT, output: Path = OUT, *, mode: str | None = None) -> dict:
    """Stage and validate a release. `mode` is the switch of the event
    surfaces (scripts/surfaces.py; None reads it): "off" leaves every event
    surface file out of the release, so a stale preview page in public/ can
    never ship; "preview" leaves out the live-only files (le-point.html,
    en/index.html); "live" stages everything."""
    import surfaces  # noqa: PLC0415

    current = _surfaces_mode(mode)
    root = root.resolve()
    public = root / "public"
    if public.is_symlink():
        raise ValueError("The public source directory cannot be a symlink")
    output = output.absolute()
    resolved_output = output.resolve()
    inside_sources = resolved_output.is_relative_to(root) and not resolved_output.is_relative_to(root / "deploy")
    if output.is_symlink() or resolved_output == root or root.is_relative_to(resolved_output) or inside_sources:
        raise ValueError("The publish destination must be separate from source files")
    for name in REQUIRED_ASSETS:
        if not (public / name).is_file():
            raise ValueError(f"Missing public/{name}; rebuild the site first")
    for name in METHODS:
        source = root / name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing or unsafe method file: {name}")
    assets: list[Path] = []
    left_out = 0
    for source in sorted(public.rglob("*")):
        relative = source.relative_to(public)
        if source.is_symlink() or not source.resolve().is_relative_to(public.resolve()):
            raise ValueError(f"Public symlinks are not publishable: {relative}")
        if relative.as_posix() == "build-manifest.json":
            raise ValueError("build-manifest.json is generated during staging, not a source asset")
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            raise ValueError(f"Private file in public tree: {relative}")
        if source.is_file():
            if source.suffix.lower() not in ASSET_EXTENSIONS:
                raise ValueError(f"Unsupported public asset: {relative}")
            if not surfaces.staged(relative.as_posix(), current):
                left_out += 1
                continue
            assets.append(source)
    if left_out:
        print(f"stage: {left_out} event-surface file(s) left out of the release (surfaces {current})")
    output.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_siblings(output.parent, output.name)
    temporary = Path(tempfile.mkdtemp(prefix=".vigie-stage-", dir=output.parent))
    backup: Path | None = None
    try:
        for source in assets:
            target = temporary / source.relative_to(public)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.relative_to(public).as_posix() == "vercel.json" and current == "off":
                target.write_bytes(staged_vercel_config(source.read_bytes(), current))
                continue
            shutil.copy2(source, target)
        for name in METHODS:
            shutil.copy2(root / name, temporary / name)
        media_dir = root / "data" / "media" / "brief"
        if media_dir.is_symlink():
            raise ValueError("The media source directory cannot be a symlink")
        if media_dir.is_dir():
            for source in sorted(media_dir.iterdir()):
                if source.name.startswith("."):
                    continue  # a crashed fetch may leave a .part behind; pruning owns it
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"Unsafe media asset: {source.name}")
                if not MEDIA_NAME.fullmatch(source.name):
                    raise ValueError(f"Unsupported media asset: {source.name}")
                if source.stat().st_size > MEDIA_MAX_BYTES:
                    raise ValueError(f"Oversized media asset: {source.name}")
                target = temporary / "media" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (temporary / "robots.txt").write_text(ROBOTS_TEXT, encoding="utf-8")
        (temporary / "sitemap.xml").write_text(sitemap_xml(temporary, current), encoding="utf-8")
        errors = validate_site(temporary, current)
        if errors:
            raise ValueError("Invalid static site:\n" + "\n".join(errors))
        withdrawn = takedown_violations(temporary, root)
        if withdrawn:
            raise ValueError("Takedown not enforced (R10); release refused:\n" + "\n".join(withdrawn))
        files = {
            p.relative_to(temporary).as_posix(): {
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in sorted(temporary.rglob("*")) if p.is_file()
        }
        manifest = {"schema_version": 1, "files": files}
        (temporary / "build-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with _stage_lock(output.parent):
            if output.exists():
                if output.resolve().parent != output.parent.resolve() or not output.is_dir():
                    raise ValueError("Unsafe publish destination")
                backup = Path(tempfile.mkdtemp(prefix=".vigie-previous-", dir=output.parent))
                backup.rmdir()
                # Rename only checked direct children of the chosen destination parent.
                try:
                    _rename_retry(output, backup)
                except FileNotFoundError:
                    # Another staging process moved the previous release between
                    # our existence check and the rename: nothing left to back up.
                    backup = None
            try:
                _rename_retry(temporary, output)
            except BaseException:
                # A concurrent stager may already have published its own release;
                # never clobber a complete release that is not ours to restore.
                if backup is not None and not output.exists():
                    _rename_retry(backup, output)
                raise
            if backup is not None:
                _safe_remove(backup, output.parent)
        return manifest
    finally:
        _safe_remove(temporary, output.parent)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    try:
        manifest = stage(output=args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Staging failed: {exc}\n")
    print(f"Validated {len(manifest['files'])} files -> {args.output}")
    print("No files have been uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
