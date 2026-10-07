"""The event surfaces wired into the release behind a three-position switch.

scripts/surfaces.py decides; scripts/rank_display.py renders (the record
layer's `evenements` emitter, the brief's address in live mode);
scripts/substrate.py and scripts/method_site.py follow the switch;
scripts/stage_public.py stages and refuses per mode; vercel.json carries the
headers. Every source, outlet, headline, street and URL below is invented
(the stores are those of tests/test_evenements.py, built by the real event
builder from invented editions). Nothing here reads data/.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

import harness  # noqa: F401  (puts scripts/ on sys.path)
import evenements
import events
import method_site
import rank_display
import stage_public
import store_io
import substrate
import surfaces
import test_evenements as te

ROOT = harness.ROOT
CLOCK = "2026-09-22T12:30:00+00:00"   # the render clock of the fixture edition (after E2)
FIXED_MTIME = 1_790_000_000           # sitemap lastmod comes from index.html's mtime
EVENT_PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                  "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
MACHINE_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# The vercel.json header rules before the wiring (phase1/base), frozen: the
# staged config of the "off" release must be exactly these, and no later
# change may weaken one of them (their canonical JSON is hashed below).
BASE_SOURCES = ("/(.*)", "/", "/index.html", "/explorer.html", "/morning.html", "/registre.html",
                "/affiche.html", "/partir.html", "/memoire.html", "/dossiers.html", "/dossiers/(.*)",
                "/methode/(.*)", "/registre/(.*)", "/delta/(.*)", "/llms.txt", "/(.*).md", "/sources.yaml",
                "/media/(.*)")
# sha256 of json.dumps(<those rules>, sort_keys=True), measured on phase1/base
BASE_RULES_DIGEST = "3ebf359d4cb9791c0d20b95d77aef176a4fdbdb9934d7100001c71a7496032e4"
_ORIG_LOAD = substrate.load_events_input

BRIEF = ("<!doctype html>\n<html lang=\"fr-CA\"><head><meta charset=\"utf-8\"><title>Le point</title>"
         "<link rel=\"canonical\" href=\"https://vigieqc.com/\"><meta property=\"og:url\" content=\"https://vigieqc.com/\">"
         "</head><body><h1 id=\"essentiel\">Le point inventé</h1><section id=\"travaux\">Entraves</section>"
         "<a href=\"/registre.html\">Le registre</a> <a href=\"/partir.html\">Avant de partir</a></body></html>\n")
STUB = "<!doctype html>\n<html lang=\"fr-CA\"><head><meta charset=\"utf-8\"><title>{t}</title></head><body><h1>{t}</h1>{x}</body></html>\n"


# --------------------------------------------------------------------------- #
# A whole site on invented stores
# --------------------------------------------------------------------------- #
class Site:
    """A temporary repository root: the event stores of tests/test_evenements
    (data/), invented brief and record pages (public/), the published method
    files, and a release directory (deploy/public)."""

    def __init__(self, takedowns=(), events_data: bool = True):
        self.fx = te.Fixture(takedowns=takedowns)
        self.root = self.fx.dir
        self.public = self.root / "public"
        self.out = self.root / "deploy" / "public"
        if not events_data:
            shutil.rmtree(self.root / "data" / "events")
        self.public.mkdir(parents=True, exist_ok=True)
        for name in stage_public.METHODS:
            if not (self.root / name).exists():   # sources.yaml is the fixture's registry
                (self.root / name).write_text(f"Méthode publiée : {name}\n", encoding="utf-8")
        assets = self.public / "assets"
        assets.mkdir(exist_ok=True)
        for name in ("fonts.css", "evenements.css", "evenements.js", "brief.css"):
            shutil.copy2(ROOT / "public" / "assets" / name, assets / name)
        shutil.copy2(ROOT / "public" / "favicon.svg", self.public / "favicon.svg")
        shutil.copy2(ROOT / "public" / "vercel.json", self.public / "vercel.json")
        pages = {"morning.html": "Le matin", "explorer.html": "Atelier", "registre.html": "Le registre",
                 "memoire.html": "La mémoire", "partir.html": "Avant de partir"}
        for seq in (1, 2, 3):
            pages[f"memoire/{seq}.html"] = f"Édition {seq}"
        for rel, title in pages.items():
            path = self.public / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(STUB.format(t=title, x='<a href="/">Accueil</a>'), encoding="utf-8")

    def load_events_input(self):
        """substrate's own loader, pointed at this site's stores (never data/)."""
        return _ORIG_LOAD(self.root / "data" / "events" / "latest_events.json",
                          self.root / "data" / "normalized" / "latest_enriched.json",
                          reg=events.Registry.load(self.fx.sources, self.fx.takedowns))

    def machine(self, mode: str | None, **kw) -> None:
        """substrate.emit as the render calls it (`mode` None: no switch)."""
        if mode is not None:
            kw["events_mode"] = mode
        with mock.patch.object(substrate, "load_events_input", self.load_events_input):
            substrate.emit([], [], {}, {}, te.registre_state(), CLOCK, run={},
                           out_llms=self.public / "llms.txt", out_md=self.public / "index.html.md",
                           out_delta=self.public / "delta" / "latest.json",
                           out_delta_v2=self.public / "delta" / "v2" / "latest.json", **kw)

    # the record layer of rank_display.main, as far as the switch reaches it
    def render(self, mode: str, *, legacy: bool = False) -> None:
        with redirect_stdout(io.StringIO()):
            if legacy:
                # the code path without the event surfaces: the brief at /,
                # no event layer for the machine files, no switch anywhere
                store_io.write_text_atomic(self.public / "index.html", BRIEF)
                self.machine(None, events_input=None)
            else:
                rank_display.write_brief(BRIEF, mode, self.public)
                self.machine(mode)
            if legacy:
                method_site.emit(self.public / "methode")
            else:
                method_site.emit(self.public / "methode", mode=mode)
            if not legacy and mode != "off":
                self.result = rank_display.emit_event_surfaces(
                    mode, public_dir=self.public, data_dir=self.root / "data",
                    sources_path=self.fx.sources, takedowns_path=self.fx.takedowns)

    def stage(self, mode: str) -> dict[str, bytes]:
        index = self.public / "index.html"
        if index.exists():
            os.utime(index, (FIXED_MTIME, FIXED_MTIME))
        with redirect_stdout(io.StringIO()):
            stage_public.stage(self.root, self.out, mode=mode)
        return tree(self.out)

    def close(self) -> None:
        self.fx.close()


def tree(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in sorted(Path(directory).rglob("*")) if p.is_file()}


def text(files: dict[str, bytes], name: str) -> str:
    return files[name].decode("utf-8")


class Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: (v or "") for k, v in attrs}))

    handle_startendtag = handle_starttag


def tags_of(page: str) -> list[tuple[str, dict]]:
    p = Head()
    p.feed(page)
    p.close()
    return p.tags


def robots_of(page: str) -> str:
    return next((a.get("content", "") for t, a in tags_of(page) if t == "meta" and a.get("name") == "robots"), "")


EVENT_PATHS = re.compile(r"^(evenements\.html|evenements/.*|en/.*|qualite\.json|delta/v2/.*|le-point\.html)$")


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #
class Switch(unittest.TestCase):
    def test_the_committed_constant_is_a_known_mode(self):
        # The founder flips by commit (2026-10-06: live). Whatever is committed
        # must be a known mode, and an unset environment follows it.
        self.assertIn(surfaces.EVENTS_SURFACES, surfaces.MODES)
        self.assertEqual(surfaces.mode({}), surfaces.EVENTS_SURFACES)
        self.assertEqual(surfaces.MODES, ("off", "preview", "live"))

    def test_the_environment_can_only_lower_the_committed_mode(self):
        for committed, allowed in (("off", {"off"}), ("preview", {"off", "preview"}), ("live", {"off", "preview", "live"})):
            for asked in ("off", "preview", "live"):
                with mock.patch.object(surfaces, "EVENTS_SURFACES", committed), redirect_stdout(io.StringIO()):
                    surfaces._SAID.clear()
                    got = surfaces.mode({surfaces.ENV_VAR: asked})
                    self.assertEqual(got, asked if asked in allowed else committed,
                                     f"committed={committed} asked={asked}")
                    self.assertEqual(surfaces.mode({surfaces.ENV_VAR: asked, "GITHUB_ACTIONS": "true"}), got,
                                     "same rule under CI: a variable never raises, it may lower")

    def test_anything_else_is_off_and_diagnosed(self):
        for value in ("Live", "on", "1", "true", "live ", "preview\n", "évènements"):
            out = io.StringIO()
            with redirect_stdout(out):
                surfaces._SAID.clear()
                self.assertEqual(surfaces.mode({surfaces.ENV_VAR: value}), "off", repr(value))
            self.assertIn("stay off", out.getvalue(), repr(value))
        for empty in ("", "   "):
            self.assertEqual(surfaces.mode({surfaces.ENV_VAR: empty}), surfaces.EVENTS_SURFACES, "unset is the constant")
        with mock.patch.object(surfaces, "EVENTS_SURFACES", "banana"), redirect_stdout(io.StringIO()):
            self.assertEqual(surfaces.mode({}), "off", "a typo in the constant is off, never a guess")

    def test_the_harness_runs_the_suite_under_the_committed_constant(self):
        self.assertEqual(os.environ.get(surfaces.ENV_VAR), "off",
                         "the legacy suite is pinned to off through the variable (it can only lower the mode)")

    def test_what_each_mode_stages(self):
        cases = {
            "index.html": (True, True, True), "registre.html": (True, True, True),
            "delta/latest.json": (True, True, True), "methode/sources.html": (True, True, True),
            "evenements.html": (False, True, True), "evenements/latest.json": (False, True, True),
            "evenements/ev-0123456789abcdef.html": (False, True, True), "qualite.json": (False, True, True),
            "en/evenements.html": (False, True, True), "delta/v2/latest.json": (False, True, True),
            "en/index.html": (False, False, True), "le-point.html": (False, False, True),
        }
        for rel, expected in cases.items():
            got = tuple(surfaces.staged(rel, m) for m in surfaces.MODES)
            self.assertEqual(got, expected, rel)

    def test_the_brief_moves_with_its_canonical(self):
        out, problems = surfaces.relocate_brief(BRIEF)
        self.assertEqual(problems, [])
        self.assertIn('<link rel="canonical" href="https://vigieqc.com/le-point.html">', out)
        self.assertIn('<meta property="og:url" content="https://vigieqc.com/le-point.html">', out)
        self.assertNotIn('href="https://vigieqc.com/"', out)
        same, problems = surfaces.relocate_brief("<html></html>")
        self.assertEqual(same, "<html></html>")
        self.assertEqual(len(problems), 2, "diagnosed, never guessed")

    def test_the_real_brief_head_relocates(self):
        import resident_brief

        page = resident_brief.render_brief([], CLOCK, [], run={}, media={})
        out, problems = surfaces.relocate_brief(page)
        self.assertEqual(problems, [])
        self.assertEqual(out.count("https://vigieqc.com/le-point.html"), 2)

    def test_urls_map_to_staged_files(self):
        self.assertEqual(surfaces.path_of_url("https://vigieqc.com/"), "index.html")
        self.assertEqual(surfaces.path_of_url("https://vigieqc.com/en/"), "en/index.html")
        self.assertEqual(surfaces.path_of_url("https://vigieqc.com/evenements/ev-0a.html"), "evenements/ev-0a.html")
        self.assertIsNone(surfaces.path_of_url("https://example.org/"))
        self.assertIsNone(surfaces.path_of_url("http://vigieqc.com/"))


# --------------------------------------------------------------------------- #
# Off: nothing new, byte for byte
# --------------------------------------------------------------------------- #
class OffIsByteIdentical(unittest.TestCase):
    def test_wired_but_off_equals_the_code_path_without_it(self):
        """Release A: the switch wired but off, with built event stores and a
        stale preview left in public/ (the worst case). Release B: the code
        path without the event layer (no event stores, legacy calls). Same
        fixtures otherwise: the two releases are identical, byte for byte,
        and A is identical to itself when built twice."""
        a = Site()
        b = Site(events_data=False)
        try:
            # a stale preview in A's public/: every event file a preview writes
            stale = Site()
            try:
                stale.render("preview")
                for rel in [p for p in tree(stale.public) if EVENT_PATHS.match(p)]:
                    target = a.public / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(stale.public / rel, target)
            finally:
                stale.close()
            self.assertTrue((a.public / "evenements.html").exists() and (a.public / "en").is_dir())
            a.render("off")
            first = a.stage("off")
            a.render("off")
            second = a.stage("off")
            b.render("off", legacy=True)
            legacy = b.stage("off")
            self.assertEqual(sorted(first), sorted(legacy))
            for name in sorted(legacy):
                self.assertEqual(first[name], legacy[name], name)
            self.assertEqual(first, second, "deterministic")
            self.assertFalse([n for n in first if EVENT_PATHS.match(n)], "no event surface ships")
            self.assertNotIn(b"xhtml", first["sitemap.xml"])
            self.assertNotIn(b"/evenements", first["llms.txt"])
            self.assertNotIn(b"deprecation", first["delta/latest.json"])
            self.assertNotIn(b"Propri", first["methode/sources.html"], "no ownership column while off")
        finally:
            a.close()
            b.close()

    def test_the_switch_is_what_withholds_delta_v2(self):
        # control: the same stores without the switch publish delta-v2, which
        # is why "off" must gate it (events.py runs in every full edition)
        site = Site()
        v2 = site.public / "delta" / "v2" / "latest.json"
        try:
            with redirect_stdout(io.StringIO()):
                site.machine(None)
                self.assertTrue(v2.exists(), "control: without the switch, built stores publish delta-v2")
                self.assertIn(b"/evenements", (site.public / "llms.txt").read_bytes())
                calls = []
                with mock.patch.object(site, "load_events_input", side_effect=lambda: calls.append(1)):
                    site.machine("off")
                self.assertFalse(v2.exists(), "off removes it")
                self.assertEqual(calls, [], "off never even reads the event view")
                self.assertNotIn(b"/evenements", (site.public / "llms.txt").read_bytes())
                self.assertNotIn(b"deprecation", (site.public / "delta" / "latest.json").read_bytes())
                site.machine("preview")
                self.assertTrue(v2.exists(), "preview: reachable")
                self.assertNotIn(b"/evenements", (site.public / "llms.txt").read_bytes(), "not listed")
                self.assertNotIn(b"deprecation", (site.public / "delta" / "latest.json").read_bytes(), "not announced")
                site.machine("live")
                self.assertTrue(v2.exists())
                llms = (site.public / "llms.txt").read_text(encoding="utf-8")
                self.assertIn("https://vigieqc.com/evenements.html", llms)
                self.assertIn("https://vigieqc.com/le-point.html", llms)
                self.assertIn(b"deprecation", (site.public / "delta" / "latest.json").read_bytes())
        finally:
            site.close()

    def test_the_staged_config_while_off_is_the_one_before_the_wiring(self):
        data = (ROOT / "public" / "vercel.json").read_bytes()
        doc = json.loads(data)
        self.assertEqual((json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), data,
                         "public/vercel.json stays canonical JSON, so the off release strips it byte-exactly")
        off = json.loads(stage_public.staged_vercel_config(data, "off"))
        self.assertEqual(tuple(r["source"] for r in off["headers"]), BASE_SOURCES)
        self.assertEqual(stage_public.staged_vercel_config(data, "preview"), data)
        self.assertEqual(stage_public.staged_vercel_config(data, "live"), data)
        base_rules = [r for r in doc["headers"] if r["source"] in BASE_SOURCES]
        self.assertEqual(base_rules, off["headers"], "no existing rule is changed, only rules are added")
        digest = hashlib.sha256(json.dumps(base_rules, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(digest, BASE_RULES_DIGEST, "an existing header changed: review it, never weaken one")


# --------------------------------------------------------------------------- #
# Preview and live: file sets, markup law, robots, hreflang, sitemap
# --------------------------------------------------------------------------- #
class Modes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sites: dict[str, Site] = {}
        cls.releases: dict[str, dict[str, bytes]] = {}
        for mode in ("off", "preview", "live"):
            site = Site()
            site.render(mode)
            cls.sites[mode] = site
            cls.releases[mode] = site.stage(mode)

    @classmethod
    def tearDownClass(cls):
        for site in cls.sites.values():
            site.close()

    def html_pages(self, mode: str) -> dict[str, str]:
        return {k: v.decode("utf-8") for k, v in self.releases[mode].items() if k.endswith(".html")}

    def event_pages(self, mode: str) -> dict[str, str]:
        return {k: v for k, v in self.html_pages(mode).items()
                if '<meta name="vigie-view"' in v}

    def test_file_sets_per_mode(self):
        off, preview, live = (set(self.releases[m]) for m in ("off", "preview", "live"))
        new_preview = sorted(preview - off)
        self.assertIn("evenements.html", new_preview)
        self.assertIn("en/evenements.html", new_preview)
        self.assertIn("evenements/latest.json", new_preview)
        self.assertIn("qualite.json", new_preview)
        self.assertIn("delta/v2/latest.json", new_preview)
        self.assertTrue(any(re.fullmatch(r"evenements/ev-[0-9a-f]{16}\.html", n) for n in new_preview))
        self.assertTrue(any(re.fullmatch(r"en/evenements/ev-[0-9a-f]{16}\.html", n) for n in new_preview))
        self.assertTrue(all(EVENT_PATHS.match(n) for n in new_preview), new_preview)
        self.assertEqual(off - preview, set())
        self.assertEqual(sorted(live - preview), ["en/index.html", "le-point.html"])

    def test_preview_leaves_the_front_door_and_the_sitemap_untouched(self):
        off, preview = self.releases["off"], self.releases["preview"]
        self.assertEqual(preview["index.html"], off["index.html"], "the brief keeps /")
        self.assertEqual(preview["sitemap.xml"], off["sitemap.xml"], "a preview is never sitemapped")
        self.assertEqual(preview["robots.txt"], off["robots.txt"])
        self.assertEqual(preview["llms.txt"], off["llms.txt"], "nor listed for agents")
        self.assertEqual(preview["delta/latest.json"], off["delta/latest.json"], "nor announced as v1's successor")
        self.assertNotIn(b"/evenements", off["index.html"])

    def test_preview_pages_carry_noindex_and_live_pages_are_indexable(self):
        for name, page in self.event_pages("preview").items():
            self.assertEqual(robots_of(page), "noindex, nofollow", name)
        live = self.event_pages("live")
        self.assertIn("index.html", live)
        self.assertIn("en/index.html", live)
        for name, page in live.items():
            self.assertEqual(robots_of(page), "index, follow", name)

    def test_live_front_door_english_door_and_the_river(self):
        live = self.html_pages("live")
        fr, en, point = live["index.html"], live["en/index.html"], live["le-point.html"]
        self.assertIn('<html lang="fr-CA">', fr)
        self.assertIn('<html lang="en-CA">', en)
        for page in (fr, en):
            self.assertIn('href="/le-point.html"', page, "the full river is linked from the front door")
            self.assertIn('id="travaux"', page, "the other surfaces' /#travaux links land on the roadworks")
            self.assertIn('<link rel="alternate" hreflang="fr-CA" href="https://vigieqc.com/">', page)
            self.assertIn('<link rel="alternate" hreflang="en-CA" href="https://vigieqc.com/en/">', page)
            self.assertIn('<link rel="alternate" hreflang="x-default" href="https://vigieqc.com/">', page)
        self.assertIn('<link rel="canonical" href="https://vigieqc.com/">', fr)
        self.assertIn('<link rel="canonical" href="https://vigieqc.com/en/">', en)
        self.assertIn('<link rel="canonical" href="https://vigieqc.com/le-point.html">', point)
        self.assertIn("Le point inventé", point, "the former brief, moved")
        self.assertEqual(live["evenements.html"], fr, "the old address serves the same page, canonical /")
        self.assertEqual(live["en/evenements.html"], en)

    def test_live_sitemap_lists_the_index_pages_with_alternates(self):
        sitemap = self.releases["live"]["sitemap.xml"].decode("utf-8")
        self.assertIn('xmlns:xhtml="http://www.w3.org/1999/xhtml"', sitemap)
        for loc in ("https://vigieqc.com/", "https://vigieqc.com/en/", "https://vigieqc.com/le-point.html"):
            self.assertIn(f"<loc>{loc}</loc>", sitemap)
        for lang, href in (("fr-CA", "https://vigieqc.com/"), ("en-CA", "https://vigieqc.com/en/"),
                           ("x-default", "https://vigieqc.com/")):
            self.assertEqual(sitemap.count(f'<xhtml:link rel="alternate" hreflang="{lang}" href="{href}"/>'), 2)
        self.assertNotIn("/evenements/ev-", sitemap, "event pages are crawled, not sitemapped (they rotate)")

    def test_every_hreflang_target_exists(self):
        for mode in ("preview", "live"):
            release = self.releases[mode]
            for name, page in self.html_pages(mode).items():
                for _t, a in tags_of(page):
                    if a.get("rel") == "alternate" and a.get("hreflang"):
                        target = surfaces.path_of_url(a["href"])
                        self.assertIn(target, release, f"{mode} {name}: {a['href']}")

    def test_no_inline_style_or_script_in_any_event_page(self):
        for mode in ("preview", "live"):
            for name, page in self.event_pages(mode).items():
                self.assertNotRegex(page, r"(?i)<style", name)
                for tag, attrs in tags_of(page):
                    self.assertNotIn("style", attrs, f"{mode} {name}: {tag}")
                    self.assertFalse([k for k in attrs if k.startswith("on")], f"{mode} {name}: {tag}")
                    if tag == "script":
                        self.assertTrue(attrs.get("src") == "/assets/evenements.js"
                                        or attrs.get("type") == "application/json", f"{mode} {name}: inline script")

    def test_front_doors_fit_the_budget(self):
        for mode, names in (("preview", ("evenements.html", "en/evenements.html")),
                            ("live", ("index.html", "en/index.html"))):
            for name in names:
                self.assertLessEqual(len(self.releases[mode][name]), surfaces.FRONT_DOOR_BUDGET, f"{mode} {name}")

    def test_ownership_is_printed_as_structure_only_when_not_off(self):
        off = self.releases["off"]["methode/sources.html"].decode("utf-8")
        self.assertNotIn("Propriété", off)
        for mode in ("preview", "live"):
            page = self.releases[mode]["methode/sources.html"].decode("utf-8")
            self.assertIn("<th>Propriété</th>", page)
            self.assertIn("Coopérative (CN2i)", page)
            self.assertIn("Société d’État fédérale", page)
            self.assertIn("actionnaire unique : le gouvernement du Québec", page)
            self.assertIn("lue le 2026-10-06", page)
            self.assertIn('rel="noopener noreferrer">référence publique</a>', page)
            words = html.unescape(re.sub(r"<[^>]+>", " ", page)).lower()
            self.assertNotRegex(words, r"\bind[ée]pendant", "a structure, never the word independent")


    def test_the_event_pages_word_each_owner_as_the_sources_page_does(self):
        # ranking.md « Propriété des sources »: one table of structure words
        # (scripts/ownership.py) for every surface, never the class word.
        import composants
        import i18n

        chip_re = re.compile(r'<span class="chip own[^"]*">(?:<span class="sw" aria-hidden="true"></span>)?([^<]*)</span>')
        fallbacks = {i18n.t(f"own.{c}", "fr") for c in ("public_broadcaster", "cooperative", "quebecor", "government")}
        for mode in ("preview", "live"):
            sources_page = self.releases[mode]["methode/sources.html"].decode("utf-8")
            self.assertIn('id="propriete"', sources_page)
            sources_text = html.unescape(re.sub(r"<[^>]+>", " ", sources_page))
            chips_fr: set[str] = set()
            for name, page in self.event_pages(mode).items():
                for words in map(html.unescape, chip_re.findall(page)):
                    self.assertNotRegex(composants.fold(words), r"independan|independen", f"{mode} {name}: {words}")
                    if not name.startswith("en/"):
                        chips_fr.add(words)
                # every ownership claim is one click from its public reference and date
                if '<ul class="orig">' in page:
                    self.assertIn('href="/methode/sources.html#propriete"', page, f"{mode} {name}")
            self.assertIn("Société d’État fédérale", chips_fr, "the invented cbc-radio-canada group is worded by the table")
            self.assertIn(i18n.t("own.unworded", "fr"), chips_fr, "the invented `independent` owner reads 'see the sources'")
            for words in sorted(chips_fr - fallbacks - {i18n.t("own.unworded", "fr")}):
                # an owner of the real registry: the sources page of the same release words it the same
                self.assertIn(words, sources_text, f"{mode}: the chip {words!r} as /methode/sources.html words it")
            latest = json.loads(self.releases[mode]["evenements/latest.json"])
            for inst, row in latest["institutions"].items():
                self.assertEqual(set(row["ownership_words"]), {"fr", "en"}, inst)
                for words in row["ownership_words"].values():
                    self.assertNotRegex(composants.fold(words), r"independan|independen", inst)


class OwnershipWords(unittest.TestCase):
    """The same owner reads the same on /methode/sources.html and on every
    event page: one table (scripts/ownership.py), never a class word."""

    def test_every_enabled_source_is_worded_by_the_table_without_the_word_independent(self):
        import i18n
        import ingest_rss
        import ownership

        for src in ingest_rss.load_sources(ROOT / "sources.yaml"):
            words = method_site.ownership_structure(src)
            self.assertNotRegex(words.lower(), r"ind[ée]pendan", src.get("id"))
            if src.get("enabled") is True:
                self.assertNotIn(words, (i18n.t("own.unworded", "fr"), i18n.t("own.unverified", "fr")),
                                 f"{src.get('id')}: worded by the table")
        self.assertEqual(method_site.ownership_structure({"ownership_class": "independent", "owner_group": "zz",
                                                          "ownership_ref": "https://example.org/x",
                                                          "ownership_asof": "2026-10-06"}),
                         i18n.t("own.unworded", "fr"))
        self.assertEqual(method_site.ownership_structure({"ownership_class": "independent"}),
                         i18n.t("own.unverified", "fr"), "an unsourced declaration is not established, never guessed")
        self.assertIn(("etat-quebec", "hydro-quebec"), ownership.STRUCTURE)

    def test_the_chip_of_every_real_source_is_the_sources_page_label(self):
        # Before this fix the chip printed the class word ("Indépendant" on
        # Le Devoir and La Presse) while the sources page printed a structure.
        import composants
        import ingest_rss
        import ownership

        page = method_site._sources_html(ownership=True)
        rows = re.findall(r"<tr>(.*?)</tr>", page, re.S)
        checked = 0
        for src in ingest_rss.load_sources(ROOT / "sources.yaml"):
            if src.get("enabled") is not True or src.get("type") != "rss":
                continue
            decl = ownership.declaration(src["id"], ingest_rss.load_sources(ROOT / "sources.yaml"))
            member = {"ownership_class": decl["ownership_class"], "owner_group": decl["owner_group"] or "",
                      "institution": src.get("institution")}
            name = html.escape(str(src.get("name")), quote=True)
            row = next(r for r in rows if f">{name}</a>" in r or f"<td>{name}<" in r)
            cell = html.unescape(re.findall(r"<td>(.*?)</td>", row, re.S)[3])
            for lang in ("fr", "en"):
                chip = composants.owner_chip(member, lang)
                words = html.unescape(re.sub(r"<[^>]+>", "", chip))
                self.assertTrue(words, src["id"])
                self.assertNotRegex(composants.fold(words), r"independan|independen", src["id"])
                if lang == "fr":
                    self.assertTrue(cell.startswith(words + " (") or cell.startswith(words + " — "),
                                    f"{src['id']}: chip {words!r}, sources page {cell[:80]!r}")
            checked += 1
        self.assertGreaterEqual(checked, 8)


# --------------------------------------------------------------------------- #
# The release gate refuses, diagnosed
# --------------------------------------------------------------------------- #
class ReleaseGate(unittest.TestCase):
    def setUp(self):
        self.site = Site()

    def tearDown(self):
        self.site.close()

    def refused(self, mode: str, pattern: str) -> None:
        with self.assertRaisesRegex(ValueError, pattern):
            self.site.stage(mode)

    def test_a_missing_hreflang_target_blocks_the_release(self):
        self.site.render("live")
        self.site.stage("live")   # baseline: it ships
        page = next(p for p in sorted((self.site.public / "en" / "evenements").glob("ev-*.html")))
        page.unlink()
        self.refused("live", r"hreflang en-CA target missing: https://vigieqc\.com/en/evenements/ev-")
        self.assertTrue((self.site.out / "index.html").exists(), "the previous release stays up")

    def test_a_missing_english_front_door_blocks_live(self):
        self.site.render("live")
        (self.site.public / "en" / "index.html").unlink()
        self.refused("live", r"en/index\.html: required event-surface artefact missing")

    def test_a_faulting_event_emitter_blocks_live_never_serves_a_stale_door(self):
        self.site.render("live")
        self.site.stage("live")
        with mock.patch.object(evenements.ck, "edition_page", side_effect=RuntimeError("boom")):
            self.site.render("live")
        self.assertEqual(self.site.result["status"], "failed")
        self.assertFalse((self.site.public / "index.html").exists(), "no stale front door is left at /")
        with self.assertRaises(ValueError):
            self.site.stage("live")

    # The emitter is fail-soft: with no usable event view of THIS collection it
    # still renders a door, with no card ("0 événement"). Before this gate
    # that door shipped: at / in live, over the full brief. Each case below
    # is refused in preview and in live, and the previous release stays up.
    def _refused_after_a_good_release(self, damage, pattern: str) -> None:
        for mode in ("preview", "live"):
            with self.subTest(mode=mode):
                site = Site()
                try:
                    site.render(mode)
                    good = site.stage(mode)   # a good release ships first
                    damage(site)
                    site.render(mode)
                    self.assertEqual(site.result["status"], "ok", "the emitter itself never raises")
                    with self.assertRaisesRegex(ValueError, pattern):
                        site.stage(mode)
                    self.assertEqual(tree(site.out), good, "the previous release stays up, byte for byte")
                finally:
                    site.close()

    def test_an_absent_event_view_blocks_the_release_never_an_empty_door(self):
        def damage(site):
            shutil.rmtree(site.root / "data" / "events")
        self._refused_after_a_good_release(damage, r"evenements/latest\.json: status 'not_built', not 'ok'")

    def test_an_unreadable_event_view_blocks_the_release(self):
        def damage(site):
            for name in ("latest_events.json", "store.json"):
                (site.root / "data" / "events" / name).write_text("{garbage", encoding="utf-8")
        self._refused_after_a_good_release(damage, r"evenements/latest\.json: status 'not_built', not 'ok'")

    def test_a_stale_event_view_blocks_the_release_never_last_editions_door(self):
        # the builder failed on a new collection (pipeline.py kept going while
        # events.py was a shadow stage): the collection moved on, the view did not
        def damage(site):
            path = site.root / "data" / "normalized" / "latest_enriched.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["normalized_at"] = "2026-09-22T18:00:00+00:00"
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        self._refused_after_a_good_release(damage, r"evenements/latest\.json: status 'not_built', not 'ok'")

    def test_an_empty_view_while_the_collection_has_candidates_blocks_the_release(self):
        def damage(site):
            path = site.root / "data" / "events" / "latest_events.json"
            view = json.loads(path.read_text(encoding="utf-8"))
            view.update({"events": [], "event_count": 0})
            path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
        self._refused_after_a_good_release(damage, r"0 card while the collection holds \d+ candidate")

    def test_the_gate_reads_the_collection_clock_itself(self):
        # independent of the emitter's own check: a door whose machine view
        # names another edition is refused even when it says "ok"
        for mode in ("preview", "live"):
            with self.subTest(mode=mode):
                site = Site()
                try:
                    site.render(mode)
                    path = site.public / "evenements" / "latest.json"
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(doc["status"], "ok")
                    doc["edition"] = te.E1_CLOCK
                    path.write_text(json.dumps(doc), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, r"edition '2026-09-20T12:00:00\+00:00' is not this collection"):
                        site.stage(mode)
                    (site.root / "data" / "normalized" / "latest_enriched.json").unlink()
                    with self.assertRaisesRegex(ValueError, r"latest_enriched\.json: unreadable"):
                        site.stage(mode)
                finally:
                    site.close()

    def test_the_door_gate_is_silent_when_off(self):
        self.site.render("preview")
        shutil.rmtree(self.site.root / "data" / "events")
        self.site.render("off")
        self.assertNotIn("evenements.html", self.site.stage("off"))
        self.assertEqual(stage_public.door_errors(self.site.out, self.site.root / "data", "off"), [])

    def test_a_preview_page_without_noindex_blocks_preview(self):
        self.site.render("preview")
        path = self.site.public / "evenements.html"
        path.write_text(path.read_text(encoding="utf-8").replace("noindex, nofollow", "index, follow"), encoding="utf-8")
        self.refused("preview", r"evenements\.html: a preview event page must carry noindex")

    def test_preview_never_moves_the_brief(self):
        self.site.render("live")
        self.site.render("preview")   # / is the brief again; the live-only files are left out
        release = self.site.stage("preview")
        self.assertNotIn("le-point.html", release)
        self.assertNotIn("en/index.html", release)
        self.assertNotIn(b"vigie-view", release["index.html"])

    def test_an_events_page_at_the_root_blocks_preview(self):
        self.site.render("preview")
        shutil.copy2(self.site.public / "evenements.html", self.site.public / "index.html")
        self.refused("preview", r"index\.html: in preview the front door at / stays the brief")

    def test_the_brief_canonical_must_move_in_live(self):
        self.site.render("live")
        (self.site.public / "le-point.html").write_text(BRIEF, encoding="utf-8")
        self.refused("live", r"le-point\.html: canonical must be https://vigieqc\.com/le-point\.html")

    def test_the_front_door_budget(self):
        self.site.render("preview")
        with mock.patch.object(surfaces, "FRONT_DOOR_BUDGET", 1000):
            self.refused("preview", r"front door is \d+ bytes, over the 1000 byte budget")

    def test_off_validates_nothing_new(self):
        self.site.render("preview")
        (self.site.public / "en" / "evenements.html").unlink()   # a broken preview, left in public/
        release = self.site.stage("off")                      # ships: off ignores the event surfaces
        self.assertNotIn("evenements.html", release)


# --------------------------------------------------------------------------- #
# The hourly roads-only lane
# --------------------------------------------------------------------------- #
ROADS_BLOCK = re.compile(r'<section class="card panel roads".*?</section>', re.S)
WHY_LISTS = re.compile(r'<details class="why"><summary>.*?</details>', re.S)
DOORS = {"preview": ["en/evenements.html", "evenements.html"],
         "live": ["en/evenements.html", "en/index.html", "evenements.html", "index.html"]}


class RoadsLane(unittest.TestCase):
    """The hourly roads-only lane re-renders from the stored files: a newer
    roadworks store must refresh the roadworks block of the front doors and
    leave the edition alone (no event page, no record, no other file)."""

    def roads_rerender(self, mode: str, *, fallback: bool) -> tuple[dict[str, bytes], dict[str, bytes]]:
        site = Site()
        try:
            with mock.patch.object(evenements, "ranking_module", return_value=None) if fallback else nullcontext():
                site.render(mode)
                before = tree(site.public)
                newer = te.roadworks("2026-09-22T17:45:00+00:00", street="Boulevard Pélican-Zinzolin", n=5)
                (site.root / "data" / "roadworks" / "latest_roadworks.json").write_text(
                    json.dumps(newer, ensure_ascii=False), encoding="utf-8")
                with redirect_stdout(io.StringIO()):
                    rank_display.emit_event_surfaces(mode, public_dir=site.public, data_dir=site.root / "data",
                                                     sources_path=site.fx.sources, takedowns_path=site.fx.takedowns)
                return before, tree(site.public)
        finally:
            site.close()

    def test_the_re_render_updates_only_the_roadworks_block(self):
        # the documented fallback order reads no roadworks: strictly the block
        for mode, doors in DOORS.items():
            before, after = self.roads_rerender(mode, fallback=True)
            self.assertEqual(sorted(before), sorted(after), mode)
            self.assertEqual(sorted(k for k in before if before[k] != after[k]), doors, mode)
            for name in doors:
                a, b = before[name].decode("utf-8"), after[name].decode("utf-8")
                self.assertEqual(ROADS_BLOCK.sub("", a), ROADS_BLOCK.sub("", b), f"{mode} {name}")
                self.assertIn("Pélican-Zinzolin", b)
                self.assertNotIn("Pélican-Zinzolin", a)

    def test_with_the_published_ranking_only_what_it_derives_from_the_roadworks_moves(self):
        # The published rule reads the roadworks and their clock (criterion 1,
        # "what affects you now", at the later of the edition and roadworks
        # clocks: ranking.md): the roads lane refreshes that, and nothing else.
        for mode, doors in DOORS.items():
            before, after = self.roads_rerender(mode, fallback=False)
            changed = sorted(k for k in before if before[k] != after[k])
            self.assertEqual(changed, sorted(doors + ["evenements/latest.json"]), mode)
            for name in doors:
                a, b = before[name].decode("utf-8"), after[name].decode("utf-8")
                self.assertEqual(re.findall(r'id="c-(ev-[0-9a-f]+)"', a), re.findall(r'id="c-(ev-[0-9a-f]+)"', b),
                                 f"{mode} {name}: no anchored obstruction changed, so the order holds")
                strip = lambda s: WHY_LISTS.sub("", ROADS_BLOCK.sub("", s))  # noqa: E731
                self.assertEqual(strip(a), strip(b), f"{mode} {name}")
            la, lb = (json.loads(x["evenements/latest.json"]) for x in (before, after))
            for e in la["events"] + lb["events"]:
                e.pop("rank_criteria", None)
            self.assertEqual(la, lb, f"{mode}: latest.json moves only in the ranking's own criteria")

    def test_the_lane_never_runs_the_event_builder(self):
        site = Site()
        try:
            boom = RuntimeError("the render must never run events.py")
            with mock.patch.object(events, "run", side_effect=boom) as run, \
                    mock.patch.object(events, "main", side_effect=boom) as main, redirect_stdout(io.StringIO()):
                result = rank_display.emit_event_surfaces("live", public_dir=site.public, data_dir=site.root / "data",
                                                          sources_path=site.fx.sources,
                                                          takedowns_path=site.fx.takedowns)
            self.assertEqual(result["status"], "ok", result)
            run.assert_not_called()
            main.assert_not_called()
            source = (ROOT / "scripts" / "evenements.py").read_text(encoding="utf-8")
            self.assertNotRegex(source, r"events\.(run|main)\(")
        finally:
            site.close()

    def test_the_render_step_calls_the_emitter_only_when_not_off(self):
        source = (ROOT / "scripts" / "rank_display.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'if mode != "off":\s+_emit\("evenements", lambda: emit_event_surfaces\(mode\)\)')
        self.assertLess(source.index('_emit("affiche"'), source.index('_emit("evenements"'),
                        "after depart and affiche, so this edition's seal and pages exist")
        pipeline = (ROOT / "scripts" / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("rank_display.py", pipeline)


# --------------------------------------------------------------------------- #
# R10 in every mode, in every emitted and staged file
# --------------------------------------------------------------------------- #
class Takedowns(unittest.TestCase):
    def check(self, takedowns, needles: list[str]) -> None:
        for mode in ("off", "preview", "live"):
            site = Site(takedowns=takedowns)
            try:
                site.render(mode)
                release = site.stage(mode)   # the R10 gate of the release passes too
                public = tree(site.public)
                for files, where in ((release, "release"), (public, "public")):
                    for name, data in files.items():
                        # the registry itself (a published method file) keeps
                        # naming a withdrawn source, as the sources page does
                        if name.endswith((".png", ".woff2", ".svg")) or name in stage_public.METHODS:
                            continue
                        words = html.unescape(data.decode("utf-8", errors="replace"))
                        for needle in needles:
                            self.assertNotIn(needle, words, f"{mode} {where} {name}: {needle}")
            finally:
                site.close()

    def test_a_source_takedown_removes_the_voice_from_every_file_in_every_mode(self):
        self.check([("source", "delta")], ["Delta Quotidien", "delta.example.org", te.F2["id"], te.X1["title"][:30]])

    def test_an_article_takedown_removes_it_from_every_file_in_every_mode(self):
        self.check([("url", te.URLS["s2"])], [te.URLS["s2"], te.S2["id"], te.S2["title"][:30]])


# --------------------------------------------------------------------------- #
# vercel.json: strict CSP for the new paths, JSON content types
# --------------------------------------------------------------------------- #
def _source_regex(source: str) -> re.Pattern:
    """Vercel's `source` (path-to-regexp; this config uses literal paths and
    `(.*)` groups only) as a full-match regex."""
    parts = re.split(r"(\(\.\*\))", source)
    return re.compile("".join(".*" if p == "(.*)" else re.escape(p) for p in parts) + r"\Z")


class Headers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        cls.rules = [(r["source"], {h["key"].lower(): h["value"] for h in r["headers"]}) for r in cls.cfg["headers"]]

    def matching(self, path: str, key: str) -> list[str]:
        return [h[key] for s, h in self.rules if _source_regex(s).match(path) and key in h]

    def test_root_and_staged_configs_agree(self):
        staged = json.loads((ROOT / "public" / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(staged["headers"], self.cfg["headers"])

    def test_every_event_page_path_has_exactly_one_strict_csp(self):
        for path in ("/", "/index.html", "/en", "/en/", "/en/index.html", "/evenements.html", "/en/evenements.html",
                     "/evenements/ev-0123456789abcdef.html", "/en/evenements/ev-0123456789abcdef.html",
                     "/le-point.html"):
            csps = self.matching(path, "content-security-policy")
            self.assertEqual(len(csps), 1, f"{path}: {csps}")
            self.assertEqual(csps[0], EVENT_PAGE_CSP, path)
            self.assertNotIn("unsafe-inline", csps[0])

    def test_machine_files_are_json_with_a_strict_csp(self):
        for path in ("/evenements/latest.json", "/qualite.json", "/delta/v2/latest.json"):
            csps = self.matching(path, "content-security-policy")
            self.assertEqual(csps, [MACHINE_CSP], path)
            self.assertEqual(self.matching(path, "content-type"), ["application/json; charset=utf-8"], path)
            self.assertEqual(self.matching(path, "x-robots-tag"), ["noindex"], path)
            self.assertEqual(self.matching(path, "access-control-allow-origin"), ["*"], path)

    def test_the_catch_all_hardening_still_reaches_the_new_paths(self):
        for path in ("/en/", "/evenements/latest.json", "/le-point.html"):
            self.assertEqual(self.matching(path, "x-content-type-options"), ["nosniff"], path)
            self.assertEqual(self.matching(path, "x-frame-options"), ["DENY"], path)


if __name__ == "__main__":
    unittest.main()
