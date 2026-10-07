"""surfaces — the three-position switch of the event surfaces.

    EVENTS_SURFACES = "off" | "preview" | "live"

One constant, flipped by a reviewed commit (docs/AUTONOMY.md: switching the
front door is an act of the founder, never an editorial choice made per
edition). Every stage that touches the event surfaces asks `mode()` and
nothing else, so the render (`rank_display`), the machine layer
(`substrate`), the method pages (`method_site`) and the release gate
(`stage_public`) can never disagree about what ships.

off      nothing new is emitted, staged, linked, sitemapped or validated: the
         release is byte-identical to the one the code produced before the
         event surfaces were wired, for the same inputs. delta-v2 is not
         written (a stale one is removed) and llms.txt lists no event page, so
         the event layer's shadow stores (events.py runs in every full edition)
         reach nobody.
preview  the event surfaces are rendered and staged (/evenements.html,
         /en/evenements.html, /evenements/<id>.html, /en/evenements/<id>.html,
         /evenements/latest.json, /qualite.json, /delta/v2/latest.json) and
         reachable by URL, but every page carries `noindex`, the sitemap omits
         them, llms.txt does not list them and the front door at / is the
         brief, untouched.
live     / is the French events front door and /en/ the English one; the
         former brief is served at /le-point.html (its canonical says so) and
         the front door links it as the full river; event pages are indexable;
         the sitemap lists the index pages with their hreflang alternates, and
         a release whose hreflang target is missing is refused.

How to flip: change EVENTS_SURFACES below in a reviewed commit, run
`python -X utf8 scripts/verify.py`, push, then run the refresh workflow. To
undo, set it back to "off": the next release is the brief again.

The environment variable VIGIE_EVENTS_SURFACES can only LOWER the committed
constant (live -> preview -> off), never raise it, in CI and locally alike: a
stray variable can never make a run publish more than what was committed, and
setting it to "off" is a kill switch. Unset or empty, the constant holds; any
other value than the three modes means "off" and is printed as a diagnosis,
never guessed (no case folding, no trimming: "Live" is not "live").

Stdlib only, pure, no clock.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

MODES = ("off", "preview", "live")

# The switch. Founder act: flip by commit (see the module docstring).
EVENTS_SURFACES = "live"

ENV_VAR = "VIGIE_EVENTS_SURFACES"
SITE_URL = "https://vigieqc.com"

# Robots meta of the event pages per mode ("off" renders none).
ROBOTS = {"preview": "noindex, nofollow", "live": "index, follow"}

# Where the former brief lives in each mode (live moves it off the root).
BRIEF_NAME = {"off": "index.html", "preview": "index.html", "live": "le-point.html"}
RIVER_PATH = "/le-point.html"

# The event-surface files under public/ (relative POSIX paths). `off` stages
# none of them; `preview` stages all but the live-only ones; `live` all.
EVENT_FILES = ("evenements.html", "qualite.json")
EVENT_DIRS = ("evenements/", "en/", "delta/v2/")
LIVE_ONLY = ("le-point.html", "en/index.html")

# The front-door HTML budget (bytes), checked at staging in preview and live.
FRONT_DOOR_BUDGET = 120 * 1024

# The vercel.json header rules that exist only for the event surfaces. With
# the switch off they are left out of the staged config, so the release
# (config included) is byte-identical to the one before the wiring.
EVENT_HEADER_SOURCES = ("/le-point.html", "/evenements.html", "/evenements/(.*).html", "/en", "/en/(.*)",
                        "/evenements/latest.json", "/qualite.json", "/delta/v2/(.*)")

_SAID: set[str] = set()


def _say(message: str) -> None:
    """Print a diagnosis once per process (the switch is read by several stages)."""
    if message not in _SAID:
        _SAID.add(message)
        print(f"surfaces: {message}", flush=True)


def validate(value: object, origin: str) -> str:
    """`value` when it is one of MODES, else "off" with a printed diagnosis."""
    if isinstance(value, str) and value in MODES:
        return value
    _say(f"{origin} = {str(value)[:40]!r} is not one of {', '.join(MODES)}: the event surfaces stay off")
    return "off"


_ORDER = {"off": 0, "preview": 1, "live": 2}


def mode(environ: Mapping[str, str] | None = None) -> str:
    """The effective mode: the committed constant, which the environment
    variable VIGIE_EVENTS_SURFACES can only LOWER (live -> preview -> off),
    never raise. So no variable, local or in CI, can make a run publish more
    than what was committed; it is also a kill switch (set it to off). Every
    value is validated; an invalid one means off, diagnosed."""
    env = os.environ if environ is None else environ
    base = validate(EVENTS_SURFACES, "surfaces.EVENTS_SURFACES")
    raw = env.get(ENV_VAR)
    if raw is None or not str(raw).strip():
        return base   # unset (or set to nothing): the constant
    asked = validate(raw, ENV_VAR)
    if _ORDER[asked] > _ORDER[base]:
        _say(f"{ENV_VAR}={asked} would raise the committed mode ({base}): the variable can only lower it; staying {base}")
        return base
    return asked


def brief_path(public_dir: Path, current: str) -> Path:
    """Where the brief (resident_brief) is written in `current` mode."""
    return Path(public_dir) / BRIEF_NAME.get(current, "index.html")


_CANONICAL = f'<link rel="canonical" href="{SITE_URL}/">'
_OG_URL = f'<meta property="og:url" content="{SITE_URL}/">'


def relocate_brief(html: str) -> tuple[str, list[str]]:
    """The brief as served at /le-point.html (live): its canonical and its
    og:url name that address instead of the root. Returns (html, diagnoses);
    a head that does not carry exactly one of each is left as found and
    diagnosed (the release gate then refuses a wrong canonical)."""
    problems: list[str] = []
    out = html
    for old, new in ((_CANONICAL, f'<link rel="canonical" href="{SITE_URL}{RIVER_PATH}">'),
                     (_OG_URL, f'<meta property="og:url" content="{SITE_URL}{RIVER_PATH}">')):
        n = out.count(old)
        if n == 1:
            out = out.replace(old, new)
        else:
            problems.append(f"brief head carries {n} copies of {old!r}: not relocated")
    return out, problems


def staged(rel: str, current: str) -> bool:
    """Whether the public/ file at `rel` (POSIX, relative) belongs to the
    release in `current` mode. Files outside the event surfaces always do."""
    rel = rel.lstrip("/")
    event = rel in EVENT_FILES or any(rel.startswith(d) for d in EVENT_DIRS) or rel in LIVE_ONLY
    if not event:
        return True
    if current == "live":
        return True
    if current == "preview":
        return rel not in LIVE_ONLY
    return False


def path_of_url(url: str) -> str | None:
    """The staged file a vigieqc.com URL is served from ("/" -> index.html,
    "/en/" -> en/index.html), or None for another host."""
    m = re.match(r"^https://vigieqc\.com(/[^?#]*)?(?:[?#].*)?$", str(url or ""))
    if not m:
        return None
    path = m.group(1) or "/"
    if path.endswith("/"):
        path += "index.html"
    return path.lstrip("/")
