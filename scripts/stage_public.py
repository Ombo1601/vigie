"""Build a complete, validated static snapshot in deploy/public/ (no upload).

A failed build keeps the previous snapshot; a successful build cannot retain
obsolete assets. Files are copied from their authoritative sources.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
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
OPTIONAL_PAGES = ("registre.html", "affiche.html")
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
# front door and the published method files.
SITE_URL = "https://vigieqc.com"
ROBOTS_TEXT = f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"
SITEMAP_PATHS = ("/", "/explorer.html", "/morning.html")


def sitemap_xml(directory: Path) -> str:
    """A small sitemap with the edition's build date as lastmod."""
    try:
        lastmod = datetime.fromtimestamp(
            (directory / "index.html").stat().st_mtime, tz=timezone.utc
        ).date().isoformat()
    except OSError:
        lastmod = None
    optional = tuple(f"/{name}" for name in OPTIONAL_PAGES if (directory / name).is_file())
    paths = (*SITEMAP_PATHS, *optional, *(f"/{name}" for name in METHODS))
    parts = []
    for path in paths:
        loc = f"{SITE_URL}/" if path == "/" else f"{SITE_URL}{path}"
        stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        parts.append(f"<url><loc>{loc}</loc>{stamp}</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
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


def validate_site(directory: Path) -> list[str]:
    """Check local navigation and anchors without making network requests."""
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
    return sorted(set(errors))


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


def stage(root: Path = ROOT, output: Path = OUT) -> dict:
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
            assets.append(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_siblings(output.parent, output.name)
    temporary = Path(tempfile.mkdtemp(prefix=".vigie-stage-", dir=output.parent))
    backup: Path | None = None
    try:
        for source in assets:
            target = temporary / source.relative_to(public)
            target.parent.mkdir(parents=True, exist_ok=True)
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
        (temporary / "sitemap.xml").write_text(sitemap_xml(temporary), encoding="utf-8")
        errors = validate_site(temporary)
        if errors:
            raise ValueError("Invalid static site:\n" + "\n".join(errors))
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
