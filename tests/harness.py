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

# The event-surface switch (scripts/surfaces.py) follows its committed
# constant in the suite: a developer's local VIGIE_EVENTS_SURFACES must never
# change what the existing tests stage. Tests of other modes pass them
# explicitly.
import os  # noqa: E402

os.environ["VIGIE_EVENTS_SURFACES"] = "off"

# Hermetic robots.txt: article-page and image fetches read the host's
# robots.txt first, and no test may touch the network for it. By default every
# host answers 404 (absent = allowed), so fetch tests see only their own mocked
# opener. Tests of the robots law swap ROBOTS_FETCHER and clear the cache.
import ingest_rss  # noqa: E402


def robots_absent(url: str) -> bytes:
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


ingest_rss.ROBOTS_FETCHER = robots_absent


# --------------------------------------------------------------------------- #
# The registry as fixture
# --------------------------------------------------------------------------- #
# The clustering tests were written against the chancellery of 2026-10-05,
# when the two CBC desks were still followed. Their subject is a mechanism
# (sister feeds share one seat; an English desk can open a bilingual dossier),
# not which feeds are live today, so they run against that registry as a
# fixture: the real sources.yaml with the named cut feeds re-enabled. The live
# registry is checked by its own tests (ceiling from its rules, cuts logged).
CBC_DESKS = ("cbc-montreal", "cbc-politics")
SOURCES = ROOT / "sources.yaml"


def rss_ceiling(path: Path = SOURCES) -> int:
    """The documented RSS ceiling (`rules: max_enabled_rss_v0`) of a registry."""
    import re

    m = re.search(r"(?m)^  max_enabled_rss_v0:\s*(\d+)\s*$", path.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError(f"no max_enabled_rss_v0 rule in {path}")
    return int(m.group(1))


def sources_with_reenabled(directory: Path, *source_ids: str) -> Path:
    """A copy of sources.yaml in `directory` with the named cut feeds enabled again."""
    text = SOURCES.read_text(encoding="utf-8")
    for sid in source_ids:
        start = text.index(f"\n  - id: {sid}\n")
        end = text.find("\n  - id:", start + 1)
        end = len(text) if end == -1 else end
        block = text[start:end]
        if "\n    enabled: false\n" not in block:
            raise AssertionError(f"{sid} is not cut in {SOURCES}; the fixture is stale")
        text = text[:start] + block.replace("\n    enabled: false\n", "\n    enabled: true\n", 1) + text[end:]
    out = Path(directory) / "sources.yaml"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def use_cbc_chancellery(case) -> Path:
    """Run `case` (a TestCase, from setUp) against the registry with both CBC
    desks followed: patches cluster_issues.SOURCES_PATH for the test."""
    import tempfile
    from unittest import mock

    import cluster_issues

    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    path = sources_with_reenabled(Path(tmp.name), *CBC_DESKS)
    patcher = mock.patch.object(cluster_issues, "SOURCES_PATH", path)
    patcher.start()
    case.addCleanup(patcher.stop)
    return path
