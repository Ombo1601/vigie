"""Serve the local Vigie preview, or a validated static release directory."""
from __future__ import annotations

import argparse
import functools
import mimetypes
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from stage_public import METHODS

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
METHOD_FILES = {f"/{name}": ROOT / name for name in METHODS}
MEDIA_DIR = ROOT / "data" / "media" / "brief"
MEDIA_NAME = re.compile(r"[a-f0-9]{20}\.(?:jpg|jpeg|png|webp|avif|gif)")
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".avif": "image/avif", ".gif": "image/gif"}


class VigieHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, methods=None, media=None, **kwargs):
        self.public_root = Path(directory or PUBLIC).resolve()
        self.methods = METHOD_FILES if methods is None and directory is None else (methods or {})
        if media is not None:
            self.media_root = Path(media)
        elif directory is None:
            self.media_root = MEDIA_DIR  # dev preview; staged releases carry media/ themselves
        else:
            self.media_root = None
        super().__init__(*args, directory=str(self.public_root), **kwargs)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        # Match the production header exactly, so local validation reflects it.
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404, "Not found")

    def _open_sized(self, target: Path, content_type: str):
        """Open a file and announce its size; a race or permission error is a
        404, never an unhandled traceback in the handler thread."""
        try:
            file = target.open("rb")
            size = target.stat().st_size
        except OSError:
            self.send_error(404, "Not found")
            return None
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        return file

    def send_head(self):
        """GET and HEAD share the same route and filesystem checks."""
        path = unquote(urlsplit(self.path).path)
        if "\x00" in path or "\\" in path or any(part.startswith(".") for part in path.split("/") if part):
            self.send_error(404, "Not found")
            return None
        if path in self.methods:
            target = Path(self.methods[path])
            if target.is_symlink() or not target.is_file() or target.resolve().parent != ROOT.resolve():
                self.send_error(404, "Method file missing")
                return None
            return self._open_sized(target, "text/plain; charset=utf-8")
        if self.media_root is not None and path.startswith("/media/"):
            name = path[len("/media/"):]
            target = self.media_root / name
            if (not MEDIA_NAME.fullmatch(name) or target.is_symlink()
                    or not target.is_file()
                    or target.resolve().parent != self.media_root.resolve()):
                self.send_error(404, "Not found")
                return None
            return self._open_sized(
                target, MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream"))
        target = Path(self.translate_path(self.path))
        if not target.resolve().is_relative_to(self.public_root):
            self.send_error(404, "Not found")
            return None
        cursor = self.public_root
        for part in target.relative_to(self.public_root).parts:
            cursor /= part
            if cursor.is_symlink():
                self.send_error(404, "Not found")
                return None
        if target.is_dir():
            for index in ("index.html", "index.htm"):
                if (target / index).is_symlink():
                    self.send_error(404, "Not found")
                    return None
        return super().send_head()

    def guess_type(self, path):
        if Path(path).suffix.lower() in {".md", ".yaml", ".yml"}:
            return "text/plain; charset=utf-8"
        ctype, _ = mimetypes.guess_type(path)
        return ctype or "application/octet-stream"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def build_handler(directory: Path | None = None):
    """Dev preview (None): repo public/ + method files + the local media store.

    A staged directory is self-contained - it carries its own media/ and needs
    no method routes - so it must not be wired to the dev media store.
    """
    if directory is None:
        return functools.partial(VigieHandler, media=MEDIA_DIR)
    return functools.partial(VigieHandler, directory=directory, methods={})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", type=Path, help="Serve an existing staged static directory")
    args = parser.parse_args()
    staged = args.directory.resolve() if args.directory else None
    directory = staged or PUBLIC
    if not (directory / "index.html").is_file():
        parser.exit(1, f"Missing {directory / 'index.html'}. Rebuild the site first.\n")
    handler = build_handler(staged)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"Vigie: http://127.0.0.1:{httpd.server_port}/", flush=True)
        print(f"Serving {directory}. Ctrl+C to stop.", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
