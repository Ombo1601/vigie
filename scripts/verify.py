"""Offline verification: tests, syntax, complete staging, and real HTTP delivery.

python scripts/verify.py --rebuild   # rebuild from cached raw snapshots first
python scripts/verify.py            # verify and stage current generated pages
python scripts/verify.py --code-only  # tests/syntax on a checkout without data

Python 3.11+ and Node.js are required; neither needs installed packages.
"""
from __future__ import annotations

import argparse
import ast
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import serve
import stage_public

ROOT = Path(__file__).resolve().parents[1]
MAX_FETCH_AGE_HOURS = 48  # mirrors normalize.MAX_FETCH_AGE_HOURS


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.inline: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs = dict(attrs)
            self.active = not attrs.get("src") and attrs.get("type", "text/javascript") in {"text/javascript", "application/javascript", "module"}
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.inline.append("".join(self.parts))
            self.active = False


def run(args: list[str]) -> None:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def syntax_checks(include_pages: bool = True) -> None:
    paths = [*sorted((ROOT / "scripts").glob("*.py")), *sorted((ROOT / "tests").glob("*.py"))]
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required to validate browser JavaScript; install it and rerun")
    count = 0
    for path in sorted(p for p in (ROOT / "public").rglob("*") if p.suffix in {".js", ".mjs"}):
        run([node, "--check", str(path)])
        count += 1
    if include_pages:
        with tempfile.TemporaryDirectory(prefix="vigie-js-") as temp:
            for page in sorted((ROOT / "public").rglob("*.html")):
                scripts = Scripts()
                scripts.feed(page.read_text(encoding="utf-8"))
                for i, script in enumerate(scripts.inline):
                    target = Path(temp) / f"{page.stem}-{i}.js"
                    target.write_text(script, encoding="utf-8")
                    run([node, "--check", str(target)])
                    count += 1
    print(f"Syntax: {len(paths)} Python files, {count} JavaScript files/blocks", flush=True)


class QuietHandler(serve.VigieHandler):
    def log_message(self, *args):
        pass


def smoke_site(directory: Path, manifest: dict) -> None:
    handler = functools.partial(QuietHandler, directory=directory, methods={})
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for name, meta in manifest["files"].items():
                with urlopen(f"{base}/{name}", timeout=5) as response:
                    body = response.read()
                    if len(body) != meta["bytes"] or hashlib.sha256(body).hexdigest() != meta["sha256"]:
                        raise RuntimeError(f"HTTP content mismatch: {name}")
                    if response.headers.get("X-Content-Type-Options") != "nosniff":
                        raise RuntimeError(f"Missing safe content-type header: {name}")
                with urlopen(Request(f"{base}/{name}", method="HEAD"), timeout=5) as response:
                    # A missing/odd Content-Length is a failed verification, not
                    # a KeyError traceback.
                    declared = response.headers.get("Content-Length")
                    if not (declared and declared.isdigit()) or int(declared) != meta["bytes"] or response.read():
                        raise RuntimeError(f"Incorrect HEAD response: {name}")
            with urlopen(base + "/", timeout=5) as response:
                if hashlib.sha256(response.read()).hexdigest() != manifest["files"]["index.html"]["sha256"]:
                    raise RuntimeError("Homepage route differs from staged index.html")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print(f"HTTP: {len(manifest['files'])} files match manifest; GET, HEAD, and homepage pass", flush=True)


def _newest_raw_run() -> datetime | None:
    raw = ROOT / "data" / "raw"
    newest: datetime | None = None
    if not raw.is_dir():
        return None
    for path in raw.glob("_run_*.json"):
        try:
            stamp = datetime.strptime(path.name[5:21], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if newest is None or stamp > newest:
            newest = stamp
    return newest


def _candidate_count() -> int:
    try:
        doc = json.loads((ROOT / "data" / "normalized" / "latest_candidates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return int(doc.get("candidate_count") or 0) if isinstance(doc, dict) else 0


def guard_offline_rebuild() -> None:
    """--rebuild writes the live stores, so refuse the two ways it can silently
    blank the site: snapshots too old to normalize (every source goes stale ->
    zero candidates) or a rebuild that produced nothing to stage."""
    newest = _newest_raw_run()
    if newest is None:
        raise RuntimeError("--rebuild needs a raw snapshot; run the online pipeline first")
    age_h = (datetime.now(timezone.utc) - newest).total_seconds() / 3600
    if age_h > MAX_FETCH_AGE_HOURS:
        raise RuntimeError(
            f"--rebuild refused: newest raw snapshot is {age_h:.1f}h old; offline "
            f"normalize marks sources stale after {MAX_FETCH_AGE_HOURS}h and would "
            "stage an empty edition")


def guard_rebuilt_edition() -> None:
    """After the offline rebuild ran: refuse to stage what it produced when it
    produced nothing. Split from guard_offline_rebuild so the pre-run check
    never blames the current store for an edition the rebuild has not run yet.
    """
    if _candidate_count() == 0:
        raise RuntimeError("--rebuild produced an empty edition; refusing to stage")


def guard_nonempty_edition() -> None:
    """Refuse to stage an empty edition on the online path too.

    `guard_offline_rebuild` protects `--rebuild`; without this, one surviving
    source that yields zero items (all others failed/stale) would stage an
    empty brief over a good production edition. Keeping the previous site is
    the honest outcome."""
    if _candidate_count() == 0:
        raise RuntimeError(
            "refusing to stage an empty edition (0 candidates); the previous "
            "production site stays up")


def claims_gate() -> None:
    """Extraction provenance is a release gate, not a dev script: an excerpt
    that is not contained in its declared source field, or a claim mutated
    between enrich and cluster, must stop the deploy."""
    enriched = ROOT / "data" / "normalized" / "latest_enriched.json"
    issues = ROOT / "data" / "issues" / "latest_issues.json"
    if not (enriched.exists() and issues.exists()):
        return
    import check_claims
    try:
        enriched_doc = json.loads(enriched.read_text(encoding="utf-8"))
        issues_doc = json.loads(issues.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"claim gate could not read the stores: {exc}") from exc
    report = check_claims.audit_claims(enriched_doc, issues_doc)
    if not report.get("ok"):
        first = (report.get("errors") or [{}])[0]
        raise RuntimeError(
            f"claim provenance failed: {len(report.get('errors') or [])} error(s); first={first}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--rebuild", action="store_true")
    mode.add_argument("--code-only", action="store_true")
    args = parser.parse_args()
    try:
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
        if args.rebuild:
            guard_offline_rebuild()
            run([sys.executable, str(ROOT / "scripts" / "pipeline.py"), "--offline"])
            guard_rebuilt_edition()
        claims_gate()
        syntax_checks(include_pages=not args.code_only)
        if not args.code_only:
            guard_nonempty_edition()
            manifest = stage_public.stage()
            smoke_site(stage_public.OUT, manifest)
    except (OSError, ValueError, SyntaxError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    print("Verification passed" + (" (code only; no generated release checked)" if args.code_only else "; static release ready in deploy/public (not uploaded)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
