"""scripts/evenements.py: the event surfaces, end to end on INVENTED stores.

Every source, outlet, headline, excerpt, byline, photo credit, street and URL
below is invented for the test. Nothing here reads data/. The stores are
built by the real event builder (scripts/events.py) from invented editions,
then rendered into a temporary public directory.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

import harness  # noqa: F401  (puts scripts/ on sys.path; hermetic robots policy)
import composants as ck
import evenements
import events
import i18n
import normalize
import registre

ROOT = harness.ROOT
HOSTILE = '"><img src=x onerror=alert(1)><script>alert(1)</script>&amp;\' onmouseover="x'
NO_MODULE = False   # ctx ranking_module=False forces the documented fallback


# --------------------------------------------------------------------------- #
# Invented registry
# --------------------------------------------------------------------------- #
def src(sid, inst, name, cls, group, lang="fr", kind="media", geo="quebec-city", nest="primary", enabled=True,
        typ="rss"):
    return {"id": sid, "name": name, "institution": inst, "institution_name": name,
            "ownership_class": cls, "owner_group": group,
            "ownership_ref": "https://ownership.example.org/registre", "ownership_asof": "2026-09-01",
            "language": lang, "geo": geo, "nest_role": nest, "source_kind": kind, "type": typ,
            "url": f"https://feeds.example.org/{sid}.xml", "homepage": f"https://{sid}.example.org/",
            "enabled": enabled}


SOURCES = [
    src("alpha-qc", "alpha", "Alpha Québec", "quebecor", "quebecor"),
    src("beta-qc", "beta", "Beta Matin", "quebecor", "quebecor"),
    src("gamma-fr", "gamma", "Gamma Radio", "public_broadcaster", "cbc-radio-canada"),
    src("gamma-en", "gamma-en", "Gamma English", "public_broadcaster", "cbc-radio-canada",
        lang="en", geo="linked", nest="linked"),
    src("delta", "delta", "Delta Quotidien", "independent", "delta"),
    src("ville-x", "ville-x", "Ville Xénon", "government", "ville-x", kind="official"),
    src("wzdx-x", "ville-x", "Ville Xénon", "government", "ville-x", kind="official", typ="wzdx"),
]


def sources_yaml(records=SOURCES) -> str:
    lines = ["version: 1", "sources:"]
    for rec in records:
        first = True
        for key, value in rec.items():
            text = ("true" if value else "false") if isinstance(value, bool) else str(value)
            lines.append(("  - " if first else "    ") + f"{key}: {text}")
            first = False
    return "\n".join(lines) + "\n"


def takedowns_yaml(entries=()) -> str:
    lines = ["version: 1"]
    if not entries:
        return "\n".join(lines + ["takedowns: []"]) + "\n"
    lines.append("takedowns:")
    for n, (kind, value) in enumerate(entries):
        lines += [f"  - id: td-2026-09-30-{n}", f"    kind: {kind}", f"    value: {value}",
                  "    requested_at: 2026-09-30", "    by: Éditeur Inventé", "    status: active"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Invented coverage
# --------------------------------------------------------------------------- #
BY_ID = {s["id"]: s for s in SOURCES}
TEXTS: list[str] = []   # every publisher-like string the fixtures carry
URLS: dict[str, str] = {}


def item(name, source, title, published, summary="", author=None, credit=None):
    url = f"https://{source}.example.org/nouvelles/2026/{name}-qx"
    iid = normalize.stable_id(url, source, title, None)
    URLS[name] = url
    for text in (title, summary, author, credit):
        if text:
            TEXTS.append(text)
    rec = BY_ID[source]
    return {"id": iid, "title": title, "summary": summary, "published_at": published, "source_id": source,
            "url": url, "author": author, "photo_credit": credit, "language": rec["language"],
            "institution": rec["institution"], "institution_name": rec["institution_name"],
            "source_kind": rec["source_kind"], "geo": rec["geo"]}


def iso(day, hour, minute=0):
    return f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:00+00:00"


F1 = item("f1", "alpha-qc", "Incendie majeur dans l'entrepôt Quillabert de Limoilou : 48 000 litres de mazout menacés",
          iso(20, 8), "Les pompiers municipaux combattent les flammes à l'entrepôt Quillabert depuis l'aube.",
          author="Odile Vernisseau-Plume", credit="Photo Zéphyrine Cadran")
F2 = item("f2", "delta", "Limoilou : l'incendie de l'entrepôt Quillabert menace 48 000 litres de mazout",
          iso(20, 9), "Les pompiers de Québec combattent toujours les flammes à l'entrepôt Quillabert.",
          author="Barnabé Quillon-Ruisseau")
F4 = item("f4", "gamma-en", "Fire at Limoilou's Quillabert warehouse threatens 48,000 litres of fuel oil",
          iso(20, 9, 30), "Quebec City firefighters battle the blaze at the Quillabert warehouse.",
          author="Wilhelmina Ostrander-Pike")
S1 = item("s1", "beta-qc", "Grève des chauffeurs d'autobus du réseau Zénobe à Charlesbourg", iso(20, 10),
          "Le syndicat des chauffeurs du réseau Zénobe déclenche une grève de trois jours à Charlesbourg.")
G1 = item("g1", "ville-x", "Travaux de réfection de la rue Quirion-Tabarnouche à Beauport", iso(20, 7),
          "La Ville annonce des travaux de réfection de la chaussée pendant douze jours.")
X1 = item("x1", "delta", "Le Festival des Lanternes zorblaxiennes ouvre ses portes", iso(20, 11),
          "Des milliers de lanternes zorblaxiennes illuminent le site dès ce soir.")
# Edition 2, two days later: the strike develops; a new official release; a hostile item.
S2 = item("s2", "alpha-qc", "Charlesbourg : la grève des chauffeurs d'autobus du réseau Zénobe se poursuit", iso(22, 9),
          "Deuxième journée de grève des chauffeurs d'autobus du réseau Zénobe à Charlesbourg.",
          author="Odile Vernisseau-Plume")
S3 = item("s3", "gamma-fr", "Réseau Zénobe : troisième jour de grève des chauffeurs d'autobus à Charlesbourg", iso(22, 10),
          "Les chauffeurs d'autobus du réseau Zénobe poursuivent leur grève à Charlesbourg.")
G2 = item("g2", "ville-x", "Avis de collecte des encombrants dans le quartier Vanier-Plumetis", iso(22, 7),
          "La collecte des encombrants aura lieu mardi dans le secteur Vanier-Plumetis.")
H1 = item("h1", "beta-qc", "Titre piégé " + HOSTILE, iso(22, 11), "Résumé piégé " + HOSTILE, author="Auteur " + HOSTILE)

E1_CLOCK = "2026-09-20T12:00:00+00:00"
MID_CLOCK = "2026-09-22T09:30:00+00:00"   # the strike develops while its first article is still collected
E2_CLOCK = "2026-09-22T12:00:00+00:00"    # the current edition: the first strike article has left the feeds


def status_of(*ok_sources, failed=()) -> dict:
    out = {}
    for s in SOURCES:
        if s["type"] != "rss":
            continue
        if s["id"] in failed:
            out[s["id"]] = {"status": "error", "error": "timeout", "candidate_count": 0}
        else:
            out[s["id"]] = {"status": "ok", "candidate_count": 3 if s["id"] in ok_sources else 0}
    return out


def payload(clock, items, status):
    return {"normalized_at": clock, "candidates": [dict(i) for i in items], "source_status": status}


E1 = payload(E1_CLOCK, [F1, F2, F4, S1, G1, X1], status_of("alpha-qc", "beta-qc", "gamma-en", "delta", "ville-x"))
MID = payload(MID_CLOCK, [S1, S2], status_of("alpha-qc", "beta-qc"))
E2 = payload(E2_CLOCK, [S2, S3, G2, H1], status_of("alpha-qc", "beta-qc", "gamma-fr", "ville-x", failed=("delta",)))
EDITIONS = (E1, MID, E2)


def roadworks(fetched, street="Rue Quirion-Tabarnouche", n=3):
    rows = []
    for k in range(n):
        rows.append({"event_id": f"RW-{k:03d}-{street[:4]}", "road_names": [street if k == 0 else f"Avenue Zéphir-{k}"],
                     "description": f"{street} entre la rue Alpha et la rue Bêta, travaux d'aqueduc.",
                     "direction": "both-directions", "event_type": "road-work", "event_status": "active",
                     "start_date": "2026-09-18T00:00:00Z", "end_date": "2026-10-30T00:00:00Z",
                     "start_date_accuracy": "verified", "end_date_accuracy": "estimated",
                     "vehicle_impact": "all-lanes-closed" if k == 0 else "some-lanes-closed",
                     "update_date": f"2026-09-21T0{k}:00:00Z"})
    return {"fetched_at": fetched, "institution_name": "Ville Xénon", "events": rows,
            "dataset_url": "https://donnees.example.org/entraves"}


def registre_state() -> dict:
    seals, prev = [], ""
    for seq, clock in enumerate((E1_CLOCK, MID_CLOCK, E2_CLOCK), start=1):
        record = {"edition": clock, "method": registre.METHOD}
        leaf = registre.leaf_of(record)
        root = registre.chain_hash(prev, leaf)
        seals.append({"seq": seq, "edition": clock, "prev": prev, "leaf": leaf, "root": root, "record": record})
        prev = root
    return {"method": registre.METHOD, "origin": registre.ORIGIN, "seals": seals, "voice": [],
            "names": {"ville-x": {"name": "Ville Xénon", "kind": "official"}}}


class Fixture:
    """A temporary site: invented sources, two editions built by events.py."""

    def __init__(self, takedowns=()):
        self.dir = Path(tempfile.mkdtemp(prefix="vigie-evenements-"))
        self.data = self.dir / "data"
        self.sources = self.dir / "sources.yaml"
        self.takedowns = self.dir / "takedowns.yaml"
        self.sources.write_text(sources_yaml(), encoding="utf-8")
        self.takedowns.write_text(takedowns_yaml(), encoding="utf-8")
        for name, doc in (("roadworks/latest_roadworks.json", roadworks("2026-09-22T13:30:00+00:00")),
                          ("registre/registre.json", registre_state())):
            path = self.data / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        enriched = self.data / "normalized" / "latest_enriched.json"
        enriched.parent.mkdir(parents=True, exist_ok=True)
        for edition in EDITIONS:
            enriched.write_text(json.dumps(edition, ensure_ascii=False), encoding="utf-8")
            events.run(enriched, self.data, self.sources, self.takedowns)
        if takedowns:
            self.takedowns.write_text(takedowns_yaml(takedowns), encoding="utf-8")

    def emit(self, out: str = "public", ctx: dict | None = None, **kw) -> tuple[Path, dict]:
        public = self.dir / out
        ctx = {"ranking_module": NO_MODULE, **(ctx or {})}
        result = evenements.emit(ctx, public_dir=public, data_dir=self.data, sources_path=self.sources,
                                 takedowns_path=self.takedowns, **kw)
        return public, result

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def files_of(public: Path) -> dict[str, str]:
    return {p.relative_to(public).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(public.rglob("*")) if p.is_file()}


def html_pages(files: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in files.items() if k.endswith(".html")}


def view_of(page: str) -> str:
    m = re.search(r'<meta name="vigie-view" content="(\w+)">', page)
    return m.group(1) if m else ""


def event_id_of(item_: dict) -> str:
    return "ev-" + hashlib.sha256(("event-v1|" + item_["id"]).encode("utf-8")).hexdigest()[:16]


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: (v or "") for k, v in attrs}))

    handle_startendtag = handle_starttag


def tags_of(page: str) -> list[tuple[str, dict]]:
    p = Tags()
    p.feed(page)
    p.close()
    return p.tags


# --------------------------------------------------------------------------- #
# The shared fixture
# --------------------------------------------------------------------------- #
FX: Fixture | None = None
PUBLIC: Path | None = None
RESULT: dict = {}
FILES: dict[str, str] = {}


def setUpModule():  # noqa: N802
    global FX, PUBLIC, RESULT, FILES
    FX = Fixture()
    PUBLIC, RESULT = FX.emit()
    FILES = files_of(PUBLIC)


def tearDownModule():  # noqa: N802
    if FX is not None:
        FX.close()


FIRE = event_id_of(F1)
STRIKE = event_id_of(S1)
WORKS = event_id_of(G1)
FESTIVAL = event_id_of(X1)
NOTICE = event_id_of(G2)
TRAP = event_id_of(H1)


class TheFixtureIsWhatTheTestsAssume(unittest.TestCase):
    def test_the_builder_grouped_the_invented_stories(self):
        store = json.loads((FX.data / "events" / "store.json").read_text(encoding="utf-8"))
        by_id = {e["event_id"]: e for e in store["events"]}
        self.assertEqual(len(by_id[FIRE]["members"]), 3, "the three fire articles are one event")
        self.assertEqual({m["institution"] for m in by_id[FIRE]["members"]}, {"alpha", "delta", "gamma-en"})
        strike = [e for e in store["events"] if any(m["item_id"] == S2["id"] for m in e["members"])]
        self.assertEqual([e["event_id"] for e in strike], [STRIKE], "the strike developed in edition 2")
        self.assertEqual(RESULT["status"], "ok", RESULT)


class FileSet(unittest.TestCase):
    def test_full_emit_produces_the_expected_files(self):
        pages = sorted(k for k in FILES if k.startswith("evenements/ev-"))
        expected_pages = sorted(f"evenements/{e}.html" for e in (FIRE, STRIKE, WORKS, NOTICE, TRAP))
        self.assertEqual(pages, expected_pages)
        self.assertEqual(sorted(k for k in FILES if k.startswith("en/evenements/ev-")), ["en/" + p for p in expected_pages])
        for name in ("evenements.html", "en/evenements.html", "evenements/latest.json", "qualite.json"):
            self.assertIn(name, FILES)
        self.assertEqual(len(FILES), 2 * 5 + 4)
        self.assertNotIn(f"evenements/{FESTIVAL}.html", FILES, "a past single-institution, unanchored event has no page")

    def test_views_current_and_permanent(self):
        self.assertEqual(view_of(FILES["evenements.html"]), "current")
        for eid in (STRIKE, NOTICE, TRAP):
            self.assertEqual(view_of(FILES[f"evenements/{eid}.html"]), "current", eid)
        for eid in (FIRE, WORKS):
            self.assertEqual(view_of(FILES[f"evenements/{eid}.html"]), "permanent", eid)
        latest = json.loads(FILES["evenements/latest.json"])
        self.assertEqual(latest["pages"], {FIRE: "permanent", WORKS: "permanent", STRIKE: "current",
                                           NOTICE: "current", TRAP: "current"})

    def test_result_counts(self):
        self.assertEqual(RESULT["pages"], 5)
        self.assertEqual(RESULT["pages_current"], 3)
        self.assertEqual(RESULT["pages_permanent"], 2)
        self.assertEqual(RESULT["ranking"], evenements.FALLBACK_RANKING)


class PublisherTextLaw(unittest.TestCase):
    def test_permanent_pages_carry_no_fixture_publisher_text(self):
        perm = {k: v for k, v in html_pages(FILES).items() if view_of(v) == "permanent"}
        self.assertEqual(len(perm), 4)
        for name, page in perm.items():
            text = re.sub(r"\s+", " ", __import__("html").unescape(page))
            for s in TEXTS:
                self.assertNotIn(s, text, f"{name} carries publisher text")
                self.assertNotIn(s[:28], text, f"{name} carries the start of a publisher text")
            self.assertNotIn("data-hl", page, name)

    def test_machine_files_carry_no_publisher_text(self):
        for name in ("evenements/latest.json", "qualite.json"):
            text = FILES[name]
            for s in TEXTS:
                self.assertNotIn(s[:28], text, name)
                self.assertNotIn(json.dumps(s, ensure_ascii=False)[1:29], text, name)

    def test_current_page_shows_attributed_verbatim_text_with_its_lang(self):
        page = FILES[f"evenements/{STRIKE}.html"]
        self.assertIn(S2["title"].replace("'", "&#x27;"), page)
        self.assertIn('Par <span lang="fr">Odile Vernisseau-Plume</span>', page)
        self.assertIn(f'href="{URLS["s2"]}" target="_blank" rel="noopener noreferrer"', page)
        self.assertRegex(page, r'<h3 lang="fr" data-hl>Charlesbourg')
        # S1's text left the collection: its row stays, its text does not
        self.assertNotIn("déclenche une grève", page)
        self.assertIn(i18n.t("voice.text_gone", "fr").replace("’", "’"), page)

    def test_excerpts_are_cut_never_reworded(self):
        long = dict(S2, summary="mot " * 200)
        enriched = json.loads((FX.data / "normalized" / "latest_enriched.json").read_text(encoding="utf-8"))
        enriched["candidates"] = [long if c["id"] == S2["id"] else c for c in enriched["candidates"]]
        public, _ = FX.emit("cut", {"enriched": enriched})
        page = (public / "evenements" / f"{STRIKE}.html").read_text(encoding="utf-8")
        m = re.search(r'<p lang="fr" data-hl>(mot[^<]*)</p>', page)
        self.assertTrue(m)
        self.assertLessEqual(len(m.group(1)), ck.EXCERPT_MAX + 1)
        self.assertTrue(m.group(1).endswith("…"))

    def test_a_bare_email_is_never_published(self):
        self.assertEqual(evenements.plain("Écrivez à redaction@exemple.org pour tout"), "Écrivez à…")
        self.assertEqual(evenements.plain("joe@exemple.org"), "")
        self.assertEqual(evenements.plain("<b>Gras</b> &amp; net"), "Gras & net")


class Takedowns(unittest.TestCase):
    """R10 at render time: the hourly lane re-renders from stored files."""

    def test_an_article_takedown_removes_the_voice_from_every_page(self):
        fx = Fixture(takedowns=[("url", URLS["f2"])])
        try:
            public, result = fx.emit()
            files = files_of(public)
            self.assertEqual(result["status"], "ok")
            for name, text in files.items():
                self.assertNotIn(URLS["f2"], text, name)
                self.assertNotIn(F2["id"], text, name)
            fire = files[f"evenements/{FIRE}.html"]
            self.assertIn("2 articles", fire)
            self.assertNotIn("3 articles", fire)
            self.assertNotIn("Delta Quotidien", fire, "a withdrawn voice is never credited")
        finally:
            fx.close()

    def test_a_source_takedown_removes_the_institution_everywhere_the_same_run(self):
        fx = Fixture(takedowns=[("source", "delta")])
        try:
            public, _ = fx.emit()
            files = files_of(public)
            for name, text in files.items():
                self.assertNotIn("Delta Quotidien", text, name)
                self.assertNotIn("delta.example.org", text, name)
                self.assertNotIn('"delta"', text, name)
            self.assertIn("2 articles", files[f"evenements/{FIRE}.html"])
        finally:
            fx.close()

    def test_a_takedown_of_the_only_voice_removes_the_page_and_prunes_it(self):
        fx = Fixture()
        try:
            public, _ = fx.emit()
            self.assertTrue((public / "evenements" / f"{NOTICE}.html").exists())
            fx.takedowns.write_text(takedowns_yaml([("url", URLS["g2"])]), encoding="utf-8")
            public, result = fx.emit()
            self.assertFalse((public / "evenements" / f"{NOTICE}.html").exists())
            self.assertFalse((public / "en" / "evenements" / f"{NOTICE}.html").exists())
            self.assertGreaterEqual(result["removed"], 2)
            for text in files_of(public).values():
                self.assertNotIn(G2["title"][:28], text)
        finally:
            fx.close()

    def test_an_unreadable_register_fails_closed(self):
        fx = Fixture()
        try:
            fx.takedowns.write_text("version: 1\ntakedowns:\n  - id: td-x\n    kind: banana\n    value: x\n", encoding="utf-8")
            public, result = fx.emit()
            self.assertEqual(result["status"], "ok")
            self.assertTrue(any("takedowns.yaml has errors" in d for d in result["diagnosis"]))
            for name, text in files_of(public).items():
                for s in TEXTS:
                    self.assertNotIn(s[:28], text.replace("&#x27;", "'"), name)
                self.assertNotIn(".example.org/nouvelles/", text, f"{name}: an article link")
        finally:
            fx.close()

    def test_the_events_helper_is_applied_when_it_exists(self):
        calls = []

        def apply_takedowns(doc, reg):
            calls.append(type(reg).__name__)
            return doc

        with mock.patch.object(events, "apply_takedowns", apply_takedowns, create=True):
            _, result = FX.emit("helper")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(calls, ["Registry"])


class English(unittest.TestCase):
    def test_every_french_page_has_its_english_twin_with_hreflang(self):
        pages = html_pages(FILES)
        fr = [k for k in pages if not k.startswith("en/")]
        self.assertTrue(fr)
        for name in fr:
            twin = "en/" + name
            self.assertIn(twin, pages, name)
            path = "/" + name
            for page in (pages[name], pages[twin]):
                self.assertIn(f'<link rel="alternate" hreflang="fr-CA" href="https://vigieqc.com{path}">', page)
                self.assertIn(f'<link rel="alternate" hreflang="en-CA" href="https://vigieqc.com/en{path}">', page)
                self.assertIn(f'<link rel="alternate" hreflang="x-default" href="https://vigieqc.com{path}">', page)
            self.assertIn('<html lang="en-CA">', pages[twin])
            self.assertIn('<html lang="fr-CA">', pages[name])
            self.assertEqual(view_of(pages[name]), view_of(pages[twin]))

    def test_english_never_carries_more_than_french(self):
        pages = html_pages(FILES)
        for name in [k for k in pages if not k.startswith("en/")]:
            fr_links = sorted(set(re.findall(r'href="(https?://[^"]+)"', pages[name])))
            en_links = sorted(set(re.findall(r'href="(https?://[^"]+)"', pages["en/" + name])))
            fr_links = [u for u in fr_links if not u.startswith("https://vigieqc.com")]
            en_links = [u for u in en_links if not u.startswith("https://vigieqc.com")]
            self.assertEqual(fr_links, en_links, name)
            self.assertEqual(sorted(re.findall(r'id="c-(ev-[0-9a-f]+)"', pages[name])),
                             sorted(re.findall(r'id="c-(ev-[0-9a-f]+)"', pages["en/" + name])), name)

    def test_english_brand_points_at_an_existing_surface(self):
        en = FILES["en/evenements.html"]
        self.assertIn('class="brand" href="/en/evenements.html"', en)
        self.assertIn('class="brand" href="/"', FILES["evenements.html"])


class MarkupLaw(unittest.TestCase):
    def test_no_inline_style_or_script_anywhere(self):
        for name, page in html_pages(FILES).items():
            self.assertNotRegex(page, r"(?i)<style", name)
            tags = tags_of(page)
            self.assertEqual(sum(1 for t, a in tags if t == "script" and a.get("src")), 1, name)
            for tag, attrs in tags:
                self.assertNotIn("style", attrs, f"{name}: {tag}")
                self.assertFalse([k for k in attrs if k.startswith("on")], f"{name}: {tag} {attrs}")
                if tag == "script":
                    self.assertTrue(attrs.get("src") == "/assets/evenements.js" or attrs.get("type") == "application/json",
                                    f"{name}: inline script {attrs}")
                for value in attrs.values():
                    self.assertFalse(value.strip().lower().startswith(("javascript:", "data:", "vbscript:")), name)
            sheets = [a["href"] for t, a in tags if t == "link" and a.get("rel") == "stylesheet"]
            self.assertEqual(sheets, ["/assets/fonts.css", "/assets/evenements.css"], name)

    def test_one_h1_landmarks_skip_link_language_link(self):
        for name, page in html_pages(FILES).items():
            tags = tags_of(page)
            self.assertEqual(sum(1 for t, _a in tags if t == "h1"), 1, name)
            self.assertEqual(sum(1 for t, _a in tags if t == "main"), 1, name)
            first = next(a for t, a in tags if t in ("a", "button", "input"))
            self.assertEqual(first.get("href"), "#main", name)
            self.assertTrue(any(t == "a" and "seg-link" in a.get("class", "") for t, a in tags), name)
            ids = [a["id"] for _t, a in tags if "id" in a]
            self.assertEqual(len(ids), len(set(ids)), f"{name}: duplicate ids")
            self.assertIn('<link rel="canonical"', page)
            self.assertIn('<meta property="og:title"', page)

    def test_hostile_strings_are_escaped(self):
        page = FILES[f"evenements/{TRAP}.html"]
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x", page)
        # markup in publisher text is stripped to words (as the brief does), the rest escaped
        self.assertIn("Titre piégé &quot;&gt; alert(1) &amp;&#x27; onmouseover=&quot;x", page)
        self.assertIn('<span lang="fr">Auteur &quot;&gt; alert(1)', page)
        for name, text in html_pages(FILES).items():
            self.assertNotIn("<script>alert", text, name)
            self.assertNotRegex(text, r"<img[^>]*onerror", name)

    def test_every_internal_link_targets_a_written_file_or_a_known_surface(self):
        known = ("/", "/methode/", "/registre.html", "/memoire.html", "/methode/legal.html", "/partir.html")
        for name, page in html_pages(FILES).items():
            for _t, a in tags_of(page):
                href = a.get("href", "")
                if not href.startswith("/") or href.startswith("//"):
                    continue
                path = href.split("#", 1)[0]
                if path in known or re.fullmatch(r"/memoire/\d+\.html", path) or path.startswith("/assets/") \
                        or path == "/favicon.svg":
                    continue
                self.assertIn(path.lstrip("/"), FILES, f"{name}: {href}")

    def test_every_class_is_styled_or_a_known_hook(self):
        css = (ROOT / "public" / "assets" / "evenements.css").read_text(encoding="utf-8")
        styled = set(re.findall(r"\.([A-Za-z_][\w-]*)", css))
        hooks = {"js-only", "mine", "words-panel", "roads", "disc", "rules-d", "why", "wrap", "end-p", "tl-a", "kind", "bt",
                 "lang", "origin", "own", "act", "certain", "probable", "possible", "origin-wire", "origin-own_reporting",
                 "origin-unknown", "origin-press_release", "origin-official", "act-developed", "act-new", "note", "impact",
                 "ledger", "act-quiet", "mt-xs", "age-time"}
        used = set()
        for page in html_pages(FILES).values():
            for _t, a in tags_of(page):
                used.update(a.get("class", "").split())
        self.assertEqual(sorted(c for c in used if c not in styled and c not in hooks), [])


class FrontDoor(unittest.TestCase):
    def test_cards_rules_chez_moi_roads_official_end_and_seal(self):
        page = FILES["evenements.html"]
        self.assertIn("Nos trois règles", page)
        self.assertIn('data-mine', page)
        self.assertIn("<noscript>", page)
        self.assertIn('<div class="js-only" hidden>', page)
        self.assertIn("data-roadworks", page)
        self.assertIn("Déclaré par les autorités", page)
        self.assertIn("Vous êtes à jour.", page)
        seal = registre_state()["seals"][2]
        self.assertIn(f'href="/memoire/3.html">{i18n.t("seal.n", "fr", seq=3)}</a> · <code>{seal["root"][:12]}…</code>', page)
        cards = re.findall(r'id="c-(ev-[0-9a-f]+)"', page)
        # the fallback order: newest first (H1 11:00, S3 10:00, G2 7:00)
        self.assertEqual(cards, [TRAP, STRIKE, NOTICE])
        self.assertIn(i18n.t("rank.fallback", "fr", t=i18n.fmt_datetime(H1["published_at"], "fr", short=True)).replace("’", "’"),
                      page.replace("&#x27;", "'"))

    def test_the_notice_in_a_card_is_not_repeated_in_the_official_block(self):
        page = FILES["evenements.html"]
        block = page.split('aria-labelledby="off-h"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn(G2["title"][:20], block)
        self.assertIn(i18n.t("off.empty", "fr").replace("’", "’"), block)

    def test_roster_is_measured_and_never_names_a_withdrawn_institution(self):
        page = FILES["evenements.html"]
        roster = page.split('class="roster"', 1)[1]
        self.assertIn("Delta Quotidien</span><span>notre collecte a échoué", roster)
        self.assertIn("Gamma English</span><span>aucun article collecté", roster)
        # the strike (its first article from Beta, text gone, membership kept) and the trap item
        self.assertIn("Beta Matin</span><span>dans 2 événements", roster)
        self.assertIn("Ville Xénon</span><span>dans 1 événement", roster)
        fx = Fixture(takedowns=[("source", "delta")])
        try:
            public, _ = fx.emit()
            self.assertNotIn("Delta", (public / "evenements.html").read_text(encoding="utf-8"))
        finally:
            fx.close()

    def test_chip_never_says_certain_without_measured_quality(self):
        for name, page in html_pages(FILES).items():
            self.assertNotIn("Regroupement certain", page, name)
            self.assertNotIn("Certain grouping", page, name)
        self.assertIn("Regroupé automatiquement", FILES[f"evenements/{FIRE}.html"])
        self.assertIn('href="/qualite.json"', FILES[f"evenements/{FIRE}.html"])
        quality = json.loads(FILES["qualite.json"])
        self.assertEqual(quality["status"], "not_established")

    def test_counts_never_understate_the_collection(self):
        self.assertIn("3 événements", FILES["evenements.html"])

    def test_page_budget_for_twelve_realistic_cards(self):
        long_title = ("Le conseil de la Ville Xénon adopte un règlement sur les abribus chauffants du quartier "
                      "Vanier-Plumetis après un long débat ")
        long_summary = ("Les élus ont voté le règlement encadrant les abribus chauffants, dont le coût reste à préciser, "
                        "selon la responsable xénonienne du dossier, qui a répondu aux questions des citoyens présents. ") * 6
        sources = ["alpha-qc", "delta", "gamma-fr"]
        cands, evs = [], []
        for k in range(14):
            members = []
            for j, sid in enumerate(sources):
                rec = BY_ID[sid]
                url = f"https://{sid}.example.org/budget/{k}-{j}-abribus-chauffants-et-reglement-municipal"
                iid = hashlib.sha256(url.encode()).hexdigest()[:24]
                when = f"2026-09-22T{(k % 10) + 1:02d}:{j * 7:02d}:00+00:00"
                cands.append({"id": iid, "title": f"{long_title}{k}-{j}", "summary": long_summary, "url": url,
                              "author": "Prénom Nom-Composé de l'Exemple", "photo_credit": "Photo Agence Inventée",
                              "published_at": when, "source_id": sid, "language": "fr",
                              "institution_name": rec["institution_name"], "source_kind": "media"})
                members.append({"item_id": iid, "institution": rec["institution"], "source_id": sid, "language": "fr",
                                "published_at": when, "first_seen": E2_CLOCK, "origin_class": "own_reporting",
                                "origin_rule": "own.byline", "ownership_class": rec["ownership_class"],
                                "owner_group": rec["owner_group"], "url": url, "date_suspect": False})
            evs.append({"event_id": f"ev-{k:016x}", "type": "municipal-bylaw", "places": ["vanier"], "place_basis": "named",
                        "label": {"fr": "Règlement municipal · Vanier", "en": "Municipal bylaw · Vanier"},
                        "born_edition": E2_CLOCK, "last_edition": E2_CLOCK, "window_state": "in_window",
                        "activity": "new", "members": members, "institutions": sorted(BY_ID[s]["institution"] for s in sources),
                        "languages": ["fr"], "independence": {"groups": [[m["item_id"]] for m in members], "count": 3},
                        "facts": [], "anchors": [], "language_pairs": [], "lineage": {"merged_into": "", "absorbed": [], "detached": []},
                        "seals": [], "tier": "probable", "copies": [], "neighbours": [], "member_count": 3,
                        "in_edition": [m["item_id"] for m in members], "silence": [
                            {"institution": "beta", "institution_name": "Beta Matin", "state": "no_linked_item"}]})
        view = {"format": "events-latest-v1", "edition": E2_CLOCK, "status": "ok", "events": evs}
        enriched = {"normalized_at": E2_CLOCK, "candidates": cands, "source_status": E2["source_status"]}
        public, result = FX.emit("budget", {"events_view": view, "enriched": enriched, "events_store": None})
        self.assertEqual(result["cards"], 12)
        for name in ("evenements.html", "en/evenements.html"):
            size = (public / name).stat().st_size
            self.assertLess(size, 120 * 1024, f"{name}: {size} bytes")
            self.assertGreater(size, 30 * 1024, "the fixture is realistic, not empty")


class RoadsLane(unittest.TestCase):
    def test_a_newer_roadworks_store_changes_the_roadworks_block_and_nothing_else(self):
        before = files_of(FX.emit("roads-a")[0])
        newer = roadworks("2026-09-22T17:45:00+00:00", street="Boulevard Pélican-Zinzolin", n=5)
        after = files_of(FX.emit("roads-b", {"roadworks": newer})[0])
        self.assertEqual(sorted(before), sorted(after))
        changed = sorted(k for k in before if before[k] != after[k])
        self.assertEqual(changed, ["en/evenements.html", "evenements.html"])
        block = re.compile(r'<section class="card panel roads".*?</section>', re.S)
        for name in changed:
            self.assertEqual(block.sub("", before[name]), block.sub("", after[name]), name)
            self.assertIn("Pélican-Zinzolin", after[name])
            self.assertNotIn("Pélican-Zinzolin", before[name])
        self.assertIn('datetime="2026-09-22T17:45:00Z"', after["evenements.html"])


class FailSoft(unittest.TestCase):
    def test_absent_inputs_render_an_honest_front_door(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = evenements.emit({"ranking_module": NO_MODULE}, public_dir=Path(tmp) / "p",
                                     data_dir=Path(tmp) / "nothing", sources_path=Path(tmp) / "none.yaml",
                                     takedowns_path=Path(tmp) / "none-td.yaml")
            self.assertEqual(result["status"], "ok")
            page = (Path(tmp) / "p" / "evenements.html").read_text(encoding="utf-8")
            self.assertIn(i18n.t("ed.notbuilt", "fr").replace("’", "’")[:40], page.replace("&#x27;", "'"))
            self.assertIn(i18n.t("roads.time_unknown", "fr"), page)
            self.assertNotIn(i18n.t("ed.empty", "fr")[:30], page, "not built is never 'no event'")

    def test_corrupt_inputs_never_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            for name in ("events/latest_events.json", "events/store.json", "normalized/latest_enriched.json",
                         "roadworks/latest_roadworks.json", "registre/registre.json", "civic/latest_consultations.json",
                         "ops/events_quality.json"):
                p = data / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('{"events": [1, {"event_id": "ev-zz"}], "candidates": "x", "seals": 5', encoding="utf-8")
            (Path(tmp) / "td.yaml").write_text("takedowns: [oops", encoding="utf-8")
            result = evenements.emit({"ranking_module": NO_MODULE}, public_dir=Path(tmp) / "p", data_dir=data,
                                     sources_path=FX.sources, takedowns_path=Path(tmp) / "td.yaml")
            self.assertEqual(result["status"], "ok")
            self.assertTrue((Path(tmp) / "p" / "evenements.html").exists())

    def test_a_fault_is_diagnosed_and_returns(self):
        with mock.patch.object(ck, "edition_page", side_effect=RuntimeError("boom")):
            _, result = FX.emit("fault")
        self.assertEqual(result["status"], "failed")

    def test_a_damaged_store_gives_no_permanent_record_but_keeps_the_edition(self):
        store = json.loads((FX.data / "events" / "store.json").read_text(encoding="utf-8"))
        store["events"][0]["members"] = "damaged"
        public, result = FX.emit("damaged", {"events_store": store})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["pages_permanent"], 0)
        self.assertEqual(result["pages_current"], 3)
        self.assertTrue(any("event store unusable" in d for d in result["diagnosis"]))

    def test_a_view_from_another_edition_is_not_shown_as_this_one(self):
        view = json.loads((FX.data / "events" / "latest_events.json").read_text(encoding="utf-8"))
        view["edition"] = E1_CLOCK
        public, result = FX.emit("stale", {"events_view": view})
        self.assertEqual(result["cards"], 0)
        page = (public / "evenements.html").read_text(encoding="utf-8")
        self.assertIn("ne sont pas" if False else "n’ont pas pu être établis", page)

    def test_cli_always_exits_zero(self):
        proc = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "evenements.py"), "--bogus"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0)


class Determinism(unittest.TestCase):
    def test_identical_inputs_give_identical_bytes(self):
        a = files_of(FX.emit("det-a")[0])
        b = files_of(FX.emit("det-b")[0])
        self.assertEqual(a, b)
        self.assertEqual(a, FILES)

    def test_independent_of_the_hash_seed(self):
        digests = []
        for seed in ("0", "4242"):
            out = FX.dir / f"seed-{seed}"
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "evenements.py"),
                                   "--data-dir", str(FX.data), "--public-dir", str(out),
                                   "--sources", str(FX.sources), "--takedowns", str(FX.takedowns)],
                                  capture_output=True, text=True, timeout=300, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            files = files_of(out)
            self.assertTrue(files)
            digests.append(hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest())
        self.assertEqual(digests[0], digests[1])

    def test_no_wall_clock(self):
        source = (ROOT / "scripts" / "evenements.py").read_text(encoding="utf-8")
        for banned in ("datetime.now", "datetime.utcnow", "time.time(", "date.today", "random"):
            self.assertNotIn(banned, source)


class PageBound(unittest.TestCase):
    def test_permanent_page_count_is_bounded_cards_first_and_stale_pages_pruned(self):
        fx = Fixture()
        try:
            public, _ = fx.emit()
            self.assertEqual(len(list((public / "evenements").glob("ev-*.html"))), 5)
            with mock.patch.object(evenements, "PAGES_MAX", 3):
                public, result = fx.emit()
            pages = sorted(p.stem for p in (public / "evenements").glob("ev-*.html"))
            self.assertEqual(len(pages), 3)
            self.assertEqual(len(list((public / "en" / "evenements").glob("ev-*.html"))), 3)
            front = (public / "evenements.html").read_text(encoding="utf-8")
            for eid in re.findall(r'id="c-(ev-[0-9a-f]+)"', front):
                self.assertIn(eid, pages, "every card has its page")
            self.assertEqual(result["removed"], 4)
            self.assertNotIn(FIRE, pages, "past records go first when the bound bites")
        finally:
            fx.close()

    def test_the_rule_is_published_on_every_page(self):
        rule = i18n.t("rule.pages", "fr", n=i18n.fmt_int(evenements.PAGES_MAX, "fr")).replace("’", "’")
        for name, page in html_pages(FILES).items():
            if not name.startswith("en/"):
                self.assertIn(rule.replace("'", "&#x27;"), page, name)
        latest = json.loads(FILES["evenements/latest.json"])
        self.assertEqual(latest["rules"]["pages_max"], 500)


class Merges(unittest.TestCase):
    def test_a_merged_record_keeps_a_page_that_links_to_its_survivor(self):
        store = json.loads((FX.data / "events" / "store.json").read_text(encoding="utf-8"))
        fire = next(e for e in store["events"] if e["event_id"] == FIRE)
        young = copy.deepcopy(fire)
        young["event_id"] = "ev-00000000000000aa"
        row = dict(fire["members"][0], item_id="ab" * 12, url="https://alpha-qc.example.org/nouvelles/2026/f1b-qx")
        young["members"] = [row]
        young["born_edition"] = young["last_edition"] = E1_CLOCK
        young["lineage"] = {"merged_into": FIRE, "merged_at": E1_CLOCK, "absorbed": [], "detached": []}
        fire["lineage"] = dict(fire["lineage"], absorbed=[young["event_id"]])
        store["events"].append(young)
        public, result = FX.emit("merged", {"events_store": store})
        self.assertEqual(result["pages_merged"], 1)
        stub = (public / "evenements" / "ev-00000000000000aa.html").read_text(encoding="utf-8")
        self.assertIn(f'href="/evenements/{FIRE}.html"', stub)
        self.assertEqual(view_of(stub), "permanent")
        en_stub = (public / "en" / "evenements" / "ev-00000000000000aa.html").read_text(encoding="utf-8")
        self.assertIn(f'href="/en/evenements/{FIRE}.html"', en_stub)
        survivor_en = (public / "en" / "evenements" / f"{FIRE}.html").read_text(encoding="utf-8")
        self.assertIn('href="/en/evenements/ev-00000000000000aa.html"', survivor_en, "journal links stay in English")
        self.assertIn("4 articles", (public / "evenements" / f"{FIRE}.html").read_text(encoding="utf-8"),
                      "the survivor counts its absorbed members")


class RankingContract(unittest.TestCase):
    """Contract R: a stub of scripts/ranking_events.py (built in parallel)."""

    def stub(self, *, fail=False):
        mod = types.ModuleType("ranking_events_stub")
        seen = {}

        def rank_events(evs, ctx):
            if fail:
                raise RuntimeError("stub failure")
            seen["ctx"] = ctx
            seen["ids"] = [e["event_id"] for e in evs]
            seen["events"] = json.dumps(evs, sort_keys=True, ensure_ascii=False)
            ordered = sorted(evs, key=lambda e: e["event_id"], reverse=True)
            return [{"event_id": e["event_id"], "position": n, "shown": n <= 2,
                     "criteria": {"origins": e["independence"]["count"]},
                     "explain": [{"key": "why.rank.raw", "values": {"k": "origines", "v": e["independence"]["count"]}},
                                 {"key": "rank.unknown-key", "values": {"origins": 2}}]}
                    for n, e in enumerate(ordered, start=1)]

        def tier_chip(tier, quality):
            if tier is None:
                return {"kind": "none", "key": "", "values": {}}
            return {"kind": "certain", "key": "tier.certain", "values": {}}

        def quality_public(quality):
            return {"pairs_labelled": 73, "precision_lower_bp": 9500}

        mod.rank_events, mod.tier_chip, mod.quality_public = rank_events, tier_chip, quality_public
        return mod, seen

    def test_the_stub_ranks_explains_and_words_the_chip(self):
        mod, seen = self.stub()
        public, result = FX.emit("stub", {"ranking_module": mod})
        self.assertEqual(result["ranking"], "ranking_events")
        front = (public / "evenements.html").read_text(encoding="utf-8")
        cards = re.findall(r'id="c-(ev-[0-9a-f]+)"', front)
        self.assertEqual(cards, sorted([STRIKE, NOTICE, TRAP], reverse=True)[:2])
        self.assertIn("origines : 1", front)
        self.assertIn("rank.unknown-key : origins = 2", front, "an unknown key is printed as its code and values")
        self.assertEqual(seen["ctx"]["edition_clock"], E2_CLOCK)
        self.assertEqual(seen["ctx"]["now"], "2026-09-22T13:30:00+00:00", "the later of edition and roadworks clocks")
        self.assertIn("collected_at", seen["ctx"]["roadworks_view"])
        fire = (public / "evenements" / f"{FIRE}.html").read_text(encoding="utf-8")
        self.assertIn("Regroupement certain", fire, "the chip follows what the quality says")
        quality = json.loads((public / "qualite.json").read_text(encoding="utf-8"))
        self.assertEqual(quality["quality"], {"pairs_labelled": 73, "precision_lower_bp": 9500})
        latest = json.loads((public / "evenements" / "latest.json").read_text(encoding="utf-8"))
        shown = [e["event_id"] for e in latest["events"] if e["shown"]]
        self.assertEqual(sorted(shown), sorted(cards))

    def test_a_failing_ranking_falls_back_and_says_so(self):
        mod, _ = self.stub(fail=True)
        _, result = FX.emit("stub-fail", {"ranking_module": mod})
        self.assertEqual(result["ranking"], evenements.FALLBACK_RANKING)
        self.assertTrue(any("rank_events faulted" in d for d in result["diagnosis"]))

    def test_withdrawn_voices_never_reach_the_ranking(self):
        fx = Fixture(takedowns=[("url", URLS["s2"])])
        try:
            mod, seen = self.stub()
            fx.emit(ctx={"ranking_module": mod})
            self.assertIn(STRIKE, seen["ids"])
            self.assertNotIn(S2["id"], seen["events"], "a withdrawn article is never counted by the ranking")
            self.assertIn(S3["id"], seen["events"])
        finally:
            fx.close()

    def test_fallback_order_is_newest_first_then_id(self):
        evs = [{"event_id": "ev-b", "members": [{"published_at": "2026-09-22T10:00:00Z"}]},
               {"event_id": "ev-a", "members": [{"published_at": "2026-09-22T10:00:00Z"}]},
               {"event_id": "ev-c", "members": [{"published_at": None, "first_seen": "2026-09-22T12:00:00Z"}]}]
        rows = evenements.fallback_rank(evs)
        self.assertEqual([r["event_id"] for r in rows], ["ev-c", "ev-a", "ev-b"])
        self.assertTrue(all(r["shown"] for r in rows))


if __name__ == "__main__":
    unittest.main()
