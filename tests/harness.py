"""Import lookout scripts as modules. Stdlib only — no package install.

unittest discover starts in tests/, so this module is `import harness`.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Hermetic robots.txt: article-page and image fetches read the host's
# robots.txt first, and no test may touch the network for it. By default every
# host answers 404 (absent = allowed), so fetch tests see only their own mocked
# opener. Tests of the robots law swap ROBOTS_FETCHER and clear the cache.
import ingest_rss  # noqa: E402


def robots_absent(url: str) -> bytes:
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


ingest_rss.ROBOTS_FETCHER = robots_absent
