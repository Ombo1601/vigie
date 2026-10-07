"""Honest labels: an event whose type the evidence does not establish is never
named by a type that says nothing ("Événement non classé").

docs/AUTONOMY.md: a reader-visible choice is a published rule computed from
evidence. The rule under test (ranking.md, 2026-10-07): the unclassified type
is never printed as a chip, heading or title on a card or an event page; a card
shows the place chip only (nothing when the place is not established); the
CURRENT page of such an event is named by its lead voice's verbatim headline
(own lang, attributed), the PERMANENT page (no publisher text, ever) by its
place alone with a small neutral "type not established" line; the machine files
keep the codes and labels.

Every headline below is INVENTED; nothing here reads data/.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import harness  # noqa: F401  (puts scripts/ on sys.path)
import composants as ck
import evenements
import events
import i18n
import normalize
import vocabulaire as V

import test_evenements as te   # invented sources, registry and store helpers (read only)

LANGS = ("fr", "en")
# What no emitted page may print for an unclassified event.
FORBIDDEN = sorted({V.type_label(V.UNCLASSIFIED, lang).casefold() for lang in LANGS}
                   | {"non classé", "non classe", "unclassified"})
UNKNOWN_PLACE = {lang: V.place_label(V.FALLBACK_PLACE, lang) for lang in LANGS}
CITY = {lang: V.place_label("quebec-city", lang) for lang in LANGS}

# A neutral line and its words, in the real strings (accents correct).
TYPE_UNKNOWN = {"fr": "Type non établi", "en": "Type not established"}


def forbidden_in(text: str) -> list[str]:
    plain = html.unescape(text).casefold()
    return [w for w in FORBIDDEN if w in plain]


def unclassified(ev: dict, *, known: bool = True) -> dict:
    """The kit's view of an unclassified event, as evenements.build_view makes
    it: the code and its labels stay on the view (machine files read them); the
    kit must never print them."""
    out = copy.deepcopy(ev)
    place = ["quebec-city"] if known else [V.FALLBACK_PLACE]
    out.update({
        "type": V.UNCLASSIFIED,
        "places": place,
        "type_label": {lang: V.type_label(V.UNCLASSIFIED, lang) for lang in LANGS},
        "place_label": CITY if known else UNKNOWN_PLACE,
        "place_known": known,
    })
    out["label"] = {lang: V.label(V.UNCLASSIFIED, "quebec-city" if known else None, lang) for lang in LANGS}
    return out


def title_of(page: str) -> str:
    return html.unescape(re.search(r"<title>(.*?)</title>", page, re.S).group(1))


def meta_of(page: str, key: str, attr: str = "name") -> str:
    m = re.search(r'<meta %s="%s" content="(.*?)">' % (attr, re.escape(key)), page, re.S)
    return html.unescape(m.group(1)) if m else ""


def h1_of(page: str) -> tuple[str, str]:
    m = re.search(r"<h1([^>]*)>(.*?)</h1>", page, re.S)
    lang = re.search(r'lang="(\w+)"', m.group(1))
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(2))), (lang.group(1) if lang else "")


def chips_of(fragment: str) -> list[tuple[str, str]]:
    """(css classes, visible text) of every chip of a fragment."""
    return [(m.group(1), html.unescape(re.sub(r"<[^>]+>", "", m.group(2))))
            for m in re.finditer(r'<span class="(chip[^"]*)"[^>]*>(.*?)</span>(?=<span class="chip|</div>)', fragment, re.S)]


VIEWS = ck.demo_views()
BASE = {e["event_id"]: e for e in VIEWS["events"]}
BYLAW = BASE["ev-1a2b3c4d5e6f7081"]      # three voices, French
EN_ONLY = BASE["ev-3c4d5e6f70819203"]    # one English voice
EDITION = VIEWS["edition"]


# --------------------------------------------------------------------------- #
# The kit
# --------------------------------------------------------------------------- #
class KitNeverPrintsTheUnclassifiedType(unittest.TestCase):
    def test_no_card_or_page_prints_it_in_any_language_view_or_place_state(self):
        n = 0
        for base in VIEWS["events"]:
            for known in (True, False):
                for perm in (False, True):
                    ev = dict(unclassified(base, known=known), permanent=perm)
                    for lang in LANGS:
                        for name, text in (("card", ck.event_card(ev, lang)),
                                           ("page", ck.event_page(ev, lang, edition=EDITION, followed=9))):
                            n += 1
                            self.assertEqual(forbidden_in(text), [], f"{name} {lang} perm={perm} known={known}")
        self.assertEqual(n, 5 * 2 * 2 * 2 * 2)

    def test_the_front_door_and_its_roster_print_it_nowhere(self):
        ed = dict(EDITION, events=[unclassified(e, known=i % 2 == 0) for i, e in enumerate(VIEWS["events"])])
        for lang in LANGS:
            page = ck.edition_page(ed, lang, roadworks=VIEWS["roadworks"])
            self.assertEqual(forbidden_in(page), [], lang)
            self.assertIn('class="roster"', page)   # the roster is there, and says nothing of types

    def test_a_view_that_only_carries_the_unclassified_words_is_read_the_same_way(self):
        ev = unclassified(BYLAW)
        del ev["type"]
        for lang in LANGS:
            self.assertEqual(forbidden_in(ck.event_card(ev, lang)), [], lang)
            self.assertEqual(forbidden_in(ck.event_page(ev, lang)), [], lang)

    def test_a_classified_event_is_unchanged(self):
        for lang in LANGS:
            card = ck.event_card(BYLAW, lang)
            kind = ck.loc(BYLAW["type_label"], lang)
            self.assertIn(("chip kind", kind), chips_of(card.split("</div>", 1)[0] + "</div>"))
            page = ck.event_page(BYLAW, lang, edition=EDITION)
            self.assertEqual(h1_of(page)[0], ck.loc(BYLAW["label"], lang))
            self.assertIn(ck.loc(BYLAW["label"], lang), title_of(page))


class Cards(unittest.TestCase):
    def top(self, ev: dict, lang: str) -> list[tuple[str, str]]:
        card = ck.event_card(ev, lang)
        return chips_of(re.search(r'<div class="ev-top">(.*?)</div>', card, re.S).group(0))

    def test_a_card_shows_the_place_chip_only_and_no_kind_chip(self):
        for lang in LANGS:
            chips = self.top(unclassified(BYLAW), lang)
            self.assertFalse([c for c in chips if "kind" in c[0].split()], chips)
            self.assertIn(("chip", CITY[lang]), chips)

    def test_nothing_is_printed_when_the_place_is_not_established(self):
        for lang in LANGS:
            for base in (BYLAW, EN_ONLY):
                chips = self.top(unclassified(base, known=False), lang)
                texts = [t for _c, t in chips]
                self.assertNotIn(UNKNOWN_PLACE[lang], texts)
                self.assertFalse([c for c in chips if "kind" in c[0].split()], chips)
                self.assertFalse([t for c, t in chips if c == "chip"], f"a bare chip is a place: {chips}")

    def test_the_headline_is_the_lead_voices_verbatim_title_and_the_why_row_says_no_type(self):
        card = ck.event_card(unclassified(BYLAW), "fr")
        lead = BYLAW["members"][0]
        self.assertIn(html.escape(lead["title"], quote=True), card)
        self.assertIn(i18n.t("why.label.none", "fr"), html.unescape(card))
        self.assertNotIn(i18n.t("why.label.text", "fr", k=""), html.unescape(card))

    def test_the_screen_reader_context_names_the_headline_in_its_own_language(self):
        card = ck.event_card(unclassified(EN_ONLY), "fr")
        self.assertRegex(card, r'<span class="sr" lang="en"> \(Cyclist hurt in collision')

    def test_a_permanent_card_is_named_by_its_place_with_the_neutral_line(self):
        for lang in LANGS:
            card = ck.event_card(dict(unclassified(BYLAW), permanent=True), lang)
            self.assertIn(f'<a href="{ck.path_for(lang, ck.event_path(BYLAW["event_id"]))}">{CITY[lang]}</a>', card)
            self.assertIn(TYPE_UNKNOWN[lang], card)
            unknown = ck.event_card(dict(unclassified(BYLAW, known=False), permanent=True), lang)
            self.assertIn(f">{UNKNOWN_PLACE[lang]}</a>", unknown)
            self.assertIn(TYPE_UNKNOWN[lang], unknown)


class EventPages(unittest.TestCase):
    def test_a_current_page_is_named_by_the_lead_headline(self):
        for lang in LANGS:
            ev = unclassified(BYLAW)
            page = ck.event_page(ev, lang, edition=EDITION)
            lead = ck._lead(ck._sorted_members(ev), lang)
            h1, h1_lang = h1_of(page)
            self.assertEqual(h1, lead["title"])
            self.assertEqual(h1_lang, lead["language"], "the headline keeps its own lang")
            self.assertTrue(title_of(page).startswith(lead["title"]))
            self.assertIn(lead["title"], meta_of(page, "og:title", "property"))
            self.assertIn(lead["title"], meta_of(page, "description"))
            # attributed under the heading: the outlet and the declared time
            head = re.search(r'<header class="evp-head">.*?</header>', page, re.S).group(0)
            self.assertIn(html.escape(ck._inst(lead, lang)), head)
            self.assertIn(i18n.fmt_time(ck._shown_instant(lead), lang), head)

    def test_the_place_is_a_chip_on_the_current_page_and_absent_when_unknown(self):
        for lang in LANGS:
            head = re.search(r'<header class="evp-head">.*?</header>',
                             ck.event_page(unclassified(BYLAW), lang), re.S).group(0)
            self.assertIn(("chip", CITY[lang]), chips_of(head))
            head = re.search(r'<header class="evp-head">.*?</header>',
                             ck.event_page(unclassified(BYLAW, known=False), lang), re.S).group(0)
            self.assertNotIn(UNKNOWN_PLACE[lang], [t for _c, t in chips_of(head)])

    def test_a_permanent_page_is_the_place_alone_and_a_neutral_line_and_no_publisher_text(self):
        for lang in LANGS:
            ev = dict(unclassified(BYLAW), permanent=True)
            page = ck.event_page(ev, lang, edition=EDITION)
            self.assertEqual(h1_of(page), (CITY[lang], ""))
            self.assertIn(TYPE_UNKNOWN[lang], page)
            self.assertTrue(title_of(page).startswith(CITY[lang]))
            self.assertEqual(meta_of(page, "vigie-view"), "permanent")
            for member in BYLAW["members"]:
                self.assertNotIn(html.escape(member["title"], quote=True), page)
            self.assertEqual(forbidden_in(page), [])

    def test_a_permanent_page_whose_place_is_unknown_says_so_and_still_names_no_type(self):
        for lang in LANGS:
            page = ck.event_page(dict(unclassified(BYLAW, known=False), permanent=True), lang, edition=EDITION)
            self.assertEqual(h1_of(page)[0], UNKNOWN_PLACE[lang])
            self.assertIn(TYPE_UNKNOWN[lang], page)
            self.assertEqual(forbidden_in(page), [])

    def test_titles_tell_permanent_records_apart_by_the_day_they_were_opened(self):
        a = dict(unclassified(BYLAW), permanent=True, born_edition="2026-10-05T14:00:00+00:00")
        b = dict(unclassified(BYLAW), permanent=True, born_edition="2026-10-06T14:00:00+00:00")
        self.assertNotEqual(title_of(ck.event_page(a, "fr")), title_of(ck.event_page(b, "fr")))

    def test_a_current_event_whose_lead_lost_its_text_falls_back_to_the_place_and_names_no_type(self):
        ev = unclassified(BYLAW)
        ev["members"] = [dict(m, title="", text_gone=True) for m in ev["members"]]
        for lang in LANGS:
            page = ck.event_page(ev, lang, edition=EDITION)
            self.assertEqual(h1_of(page)[0], CITY[lang])
            self.assertIn(TYPE_UNKNOWN[lang], page)
            self.assertEqual(forbidden_in(page), [])

    def test_the_archive_panel_keeps_the_place_as_its_label_and_never_the_type(self):
        for lang in LANGS:
            page = ck.event_page(dict(unclassified(BYLAW), permanent=True), lang)
            self.assertRegex(html.unescape(page), r"«\s*%s\s*»" % re.escape(CITY[lang]))
            gone = ck.event_page(dict(unclassified(BYLAW, known=False), permanent=True), lang)
            self.assertNotIn(i18n.t("k.label", lang) + i18n.t("colon", lang), html.unescape(gone))

    def test_a_hostile_headline_is_escaped_as_the_heading(self):
        ev = unclassified(BYLAW)
        ev["members"] = [dict(m, title=te.HOSTILE) for m in ev["members"]]
        page = ck.event_page(ev, "fr")
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<script>alert", page.split('<script type="application/json"')[0])


class Catalogue(unittest.TestCase):
    def test_the_new_keys_exist_in_both_languages_with_the_same_placeholders(self):
        self.assertEqual(i18n.validate(), [])
        for key in ("ev.type_unknown", "why.label.none"):
            for lang in LANGS:
                self.assertTrue(i18n.t(key, lang), (key, lang))
        self.assertEqual(i18n.t("ev.type_unknown", "fr"), TYPE_UNKNOWN["fr"])
        self.assertEqual(i18n.t("ev.type_unknown", "en"), TYPE_UNKNOWN["en"])

    def test_no_catalogue_string_the_pages_print_carries_the_unclassified_label(self):
        for lang in LANGS:
            for key, value in i18n.catalogue(lang).items():
                if key.startswith("js."):
                    continue
                self.assertEqual(forbidden_in(value), [], (lang, key))


# --------------------------------------------------------------------------- #
# End to end: invented items -> the real builder -> the real emitter
# --------------------------------------------------------------------------- #
def item(name: str, source: str, title: str, published: str, summary: str) -> dict:
    url = f"https://{source}.example.org/nouvelles/2026/{name}-lx"
    iid = normalize.stable_id(url, source, title, None)
    rec = te.BY_ID[source]
    return {"id": iid, "title": title, "summary": summary, "published_at": published, "source_id": source,
            "url": url, "author": None, "photo_credit": None, "language": rec["language"],
            "institution": rec["institution"], "institution_name": rec["institution_name"],
            "source_kind": rec["source_kind"], "geo": rec["geo"]}


iso = te.iso
# Edition 1 (becomes permanent): a placed pair and an unplaced pair, both unclassified.
A1 = item("a1", "alpha-qc", "Le dirigeable Blinoux survole Limoilou ce matin", iso(20, 8),
          "Le dirigeable Blinoux fait le tour de Limoilou.")
A2 = item("a2", "delta", "Limoilou : le dirigeable Blinoux fait le tour du quartier", iso(20, 9),
          "Le dirigeable Blinoux est visible depuis Limoilou.")
B1 = item("b1", "beta-qc", "Le professeur Zonzon dévoile ses boulgi-boulga", iso(20, 10),
          "Zonzon présente ses boulgi-boulga.")
B2 = item("b2", "gamma-fr", "Boulgi-boulga : le professeur Zonzon s'explique", iso(20, 11),
          "Zonzon s'explique sur ses boulgi-boulga.")
# Edition 2 (current): the same shapes, other words, and one classified event.
C1 = item("c1", "alpha-qc", "La montgolfière Quilbo se pose à Vanier", iso(22, 8), "La montgolfière Quilbo s'est posée à Vanier.")
C2 = item("c2", "gamma-fr", "Vanier : la montgolfière Quilbo atterrit près du parc", iso(22, 9), "Quilbo atterrit à Vanier.")
D1 = item("d1", "beta-qc", "L'inventeur Plimbus montre son chronomètre à treize cadrans", iso(22, 10), "Plimbus montre son chronomètre.")
D2 = item("d2", "delta", "Chronomètre à treize cadrans : Plimbus répond aux curieux", iso(22, 11), "Plimbus répond aux curieux sur son chronomètre.")
G = item("g1", "ville-x", "Avis de collecte des encombrants dans le quartier Vanier-Plumetis", iso(22, 7),
         "La collecte des encombrants aura lieu mardi dans le secteur Vanier-Plumetis.")
INVENTED = (A1, A2, B1, B2, C1, C2, D1, D2, G)


def eid(first: dict) -> str:
    return "ev-" + hashlib.sha256(("event-v1|" + first["id"]).encode("utf-8")).hexdigest()[:16]


PLACED_PAST, UNPLACED_PAST = eid(A1), eid(B1)
PLACED_NOW, UNPLACED_NOW, CLASSIFIED_NOW = eid(C1), eid(D1), eid(G)

FX_DIR: Path | None = None
PUBLIC: Path | None = None
FILES: dict[str, str] = {}
DATA: Path | None = None


def build(root: Path) -> tuple[Path, Path]:
    data = root / "data"
    (root / "sources.yaml").write_text(te.sources_yaml(te.SOURCES), encoding="utf-8")
    (root / "takedowns.yaml").write_text(te.takedowns_yaml(), encoding="utf-8")
    for name, doc in (("roadworks/latest_roadworks.json", te.roadworks("2026-09-22T13:30:00+00:00")),
                      ("registre/registre.json", te.registre_state())):
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    ok = ("alpha-qc", "beta-qc", "gamma-fr", "delta", "ville-x")
    enriched = data / "normalized" / "latest_enriched.json"
    enriched.parent.mkdir(parents=True, exist_ok=True)
    for clock, rows in ((te.E1_CLOCK, [A1, A2, B1, B2]), (te.E2_CLOCK, [C1, C2, D1, D2, G])):
        enriched.write_text(json.dumps(te.payload(clock, rows, te.status_of(*ok)), ensure_ascii=False), encoding="utf-8")
        events.run(enriched, data, root / "sources.yaml", root / "takedowns.yaml")
    return data, root


def emit(root: Path, data: Path, out: str = "public", **kw) -> tuple[Path, dict]:
    public = root / out
    result = evenements.emit({"ranking_module": False}, public_dir=public, data_dir=data,
                             sources_path=root / "sources.yaml", takedowns_path=root / "takedowns.yaml", **kw)
    return public, result


def files_of(public: Path) -> dict[str, str]:
    return {p.relative_to(public).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(public.rglob("*")) if p.is_file()}


def setUpModule():  # noqa: N802
    global FX_DIR, PUBLIC, FILES, DATA
    FX_DIR = Path(tempfile.mkdtemp(prefix="vigie-honest-labels-"))
    DATA, _ = build(FX_DIR)
    PUBLIC, result = emit(FX_DIR, DATA)
    assert result["status"] == "ok", result
    FILES = files_of(PUBLIC)


def tearDownModule():  # noqa: N802
    if FX_DIR is not None:
        shutil.rmtree(FX_DIR, ignore_errors=True)


class TheFixtureIsWhatTheTestsAssume(unittest.TestCase):
    def test_the_builder_made_unclassified_events_current_and_permanent(self):
        store = json.loads((DATA / "events" / "store.json").read_text(encoding="utf-8"))
        by_id = {e["event_id"]: e for e in store["events"]}
        for event_id in (PLACED_PAST, UNPLACED_PAST, PLACED_NOW, UNPLACED_NOW):
            self.assertEqual(by_id[event_id]["type"], V.UNCLASSIFIED, event_id)
            self.assertEqual(len(by_id[event_id]["members"]), 2, event_id)
        self.assertNotEqual(by_id[CLASSIFIED_NOW]["type"], V.UNCLASSIFIED)
        self.assertEqual(by_id[UNPLACED_NOW]["place_basis"], "fallback")
        self.assertEqual(by_id[PLACED_NOW]["places"][0], "vanier")
        views = {k: re.search(r'<meta name="vigie-view" content="(\w+)">', v).group(1)
                 for k, v in FILES.items() if k.startswith("evenements/ev-")}
        self.assertEqual(views[f"evenements/{PLACED_PAST}.html"], "permanent")
        self.assertEqual(views[f"evenements/{PLACED_NOW}.html"], "current")


class NoEmittedPageNamesTheUnclassifiedType(unittest.TestCase):
    def test_not_one_html_page_prints_it(self):
        pages = {k: v for k, v in FILES.items() if k.endswith(".html")}
        self.assertGreaterEqual(len(pages), 2 + 2 * 5)
        for name, page in pages.items():
            self.assertEqual(forbidden_in(page), [], name)

    def test_the_front_door_cards_show_the_place_chip_only(self):
        for lang, path in (("fr", "evenements.html"), ("en", "en/evenements.html")):
            page = FILES[path]
            card = re.search(r'<article class="card ev" id="c-%s".*?</article>' % PLACED_NOW, page, re.S).group(0)
            top = re.search(r'<div class="ev-top">.*?</div>', card, re.S).group(0)
            self.assertIn(("chip", V.place_label("vanier", lang)), chips_of(top))
            self.assertNotIn("kind", top)
            lost = re.search(r'<article class="card ev" id="c-%s".*?</article>' % UNPLACED_NOW, page, re.S).group(0)
            lost_top = re.search(r'<div class="ev-top">.*?</div>', lost, re.S).group(0)
            self.assertNotIn(UNKNOWN_PLACE[lang], lost_top)
            self.assertNotIn("kind", lost_top)
            # the classified event keeps its type chip
            kept = re.search(r'<article class="card ev" id="c-%s".*?</article>' % CLASSIFIED_NOW, page, re.S).group(0)
            self.assertIn('<span class="chip kind">', kept)

    def test_current_pages_are_named_by_the_lead_headline_in_its_own_lang(self):
        for lang, base in (("fr", ""), ("en", "en/")):
            for event_id, first in ((PLACED_NOW, C1), (UNPLACED_NOW, D1)):
                page = FILES[f"{base}evenements/{event_id}.html"]
                self.assertEqual(h1_of(page), (first["title"], "fr"), (lang, event_id))
                self.assertTrue(title_of(page).startswith(first["title"]), (lang, event_id))
                self.assertEqual(meta_of(page, "vigie-view"), "current")

    def test_permanent_pages_are_the_place_alone_and_carry_no_publisher_text(self):
        for lang, base in (("fr", ""), ("en", "en/")):
            placed = FILES[f"{base}evenements/{PLACED_PAST}.html"]
            self.assertEqual(h1_of(placed), (V.place_label("limoilou", lang), ""))
            unplaced = FILES[f"{base}evenements/{UNPLACED_PAST}.html"]
            self.assertEqual(h1_of(unplaced), (UNKNOWN_PLACE[lang], ""))
            for page in (placed, unplaced):
                self.assertIn(TYPE_UNKNOWN[lang], page)
                self.assertEqual(meta_of(page, "vigie-view"), "permanent")
                for it in (A1, A2, B1, B2):
                    self.assertNotIn(html.escape(it["title"], quote=True), page)
                    self.assertNotIn(it["title"], page)

    def test_machine_files_keep_the_codes_and_both_labels(self):
        doc = json.loads(FILES["evenements/latest.json"])
        by_id = {e["event_id"]: e for e in doc["events"]}
        for event_id in (PLACED_NOW, UNPLACED_NOW):
            e = by_id[event_id]
            self.assertEqual(e["type"], V.UNCLASSIFIED)
            self.assertEqual(e["type_label"], {lang: V.type_label(V.UNCLASSIFIED, lang) for lang in LANGS})
            self.assertEqual(sorted(e["label"]), ["en", "fr"])
            self.assertIn(V.type_label(V.UNCLASSIFIED, "fr"), e["label"]["fr"])
            self.assertNotIn("place_known", e, "the machine file keeps its shape")
        self.assertEqual(by_id[UNPLACED_NOW]["place_label"], UNKNOWN_PLACE)

    def test_a_view_without_stored_labels_is_still_named_without_the_type(self):
        root = FX_DIR / "copy"
        shutil.copytree(DATA, root / "data")
        shutil.copy(FX_DIR / "sources.yaml", root / "sources.yaml")
        shutil.copy(FX_DIR / "takedowns.yaml", root / "takedowns.yaml")
        try:
            path = root / "data" / "events" / "latest_events.json"
            view = json.loads(path.read_text(encoding="utf-8"))
            for e in view["events"]:
                e.pop("label", None)
            path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
            out, result = emit(root, root / "data")
            self.assertEqual(result["status"], "ok")
            for name, page in files_of(out).items():
                if name.endswith(".html"):
                    self.assertEqual(forbidden_in(page), [], name)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class Merges(unittest.TestCase):
    def test_a_merged_unclassified_record_is_named_by_its_place_not_its_type(self):
        survivor = {"event_id": PLACED_NOW, "type": V.UNCLASSIFIED, "place_known": True,
                    "label": {lang: V.label(V.UNCLASSIFIED, "vanier", lang) for lang in LANGS},
                    "place_label": {lang: V.place_label("vanier", lang) for lang in LANGS}}
        merged = {"event_id": PLACED_PAST, "type": V.UNCLASSIFIED, "places": ["limoilou"], "place_basis": "named",
                  "label": {lang: V.label(V.UNCLASSIFIED, "limoilou", lang) for lang in LANGS},
                  "lineage": {"merged_into": PLACED_NOW, "merged_at": te.E2_CLOCK}}
        render = type("R", (), {"robots": "index, follow", "mode": ""})()
        for lang in LANGS:
            page = evenements._merged_page(render, merged, survivor, lang, "")
            self.assertEqual(forbidden_in(page), [], lang)
            self.assertEqual(h1_of(page)[0], V.place_label("limoilou", lang))
            self.assertIn(TYPE_UNKNOWN[lang], page)
            self.assertIn(V.place_label("vanier", lang), html.unescape(page))


class Determinism(unittest.TestCase):
    def test_identical_inputs_give_identical_bytes(self):
        out, _ = emit(FX_DIR, DATA, "public-again")
        try:
            self.assertEqual(files_of(out), FILES)
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
