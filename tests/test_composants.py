"""The bilingual UI kit (scripts/composants.py + evenements.css/js), invented data only."""
from __future__ import annotations

import copy
import hashlib
import html
import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import composants as ck
import i18n

ROOT = harness.ROOT
CSS = (ROOT / "public" / "assets" / "evenements.css").read_text(encoding="utf-8")
JS = (ROOT / "public" / "assets" / "evenements.js").read_text(encoding="utf-8")
VOID = {"meta", "link", "input", "br", "hr", "img", "area", "base", "col", "embed", "source", "track", "wbr"}
HOSTILE = '"><img src=x onerror=alert(1)><script>alert(1)</script>&amp;\' onmouseover="x'


class Dom(HTMLParser):
    """Just enough structure: tags, attributes, ids, scripts, text, chip text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict]] = []
        self.ids: list[str] = []
        self.text: list[str] = []
        self.chips: list[str] = []
        self._chip_stack: list[tuple[str, list[str]]] = []
        self._depth: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        self.tags.append((tag, a))
        if "id" in a:
            self.ids.append(a["id"])
        if tag in VOID:
            return
        self._depth.append(tag)
        if tag == "span" and "chip" in a.get("class", "").split():
            self._chip_stack.append((tag, []))
        elif self._chip_stack:
            self._chip_stack.append((tag, self._chip_stack[-1][1]))

    def handle_startendtag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        self.tags.append((tag, a))
        if "id" in a:
            self.ids.append(a["id"])

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self._depth and self._depth.pop() != tag:
            pass
        if self._chip_stack:
            _t, buf = self._chip_stack.pop()
            if not self._chip_stack or self._chip_stack[-1][1] is not buf:
                self.chips.append("".join(buf))

    def handle_data(self, data):
        self.text.append(data)
        for _t, buf in self._chip_stack:
            buf.append(data)
            break

    def count(self, tag: str) -> int:
        return sum(1 for t, _a in self.tags if t == tag)

    def find(self, tag: str, **attrs) -> list[dict]:
        return [a for t, a in self.tags if t == tag and all(a.get(k) == v for k, v in attrs.items())]

    @property
    def visible_text(self) -> str:
        return " ".join("".join(self.text).split())


def parse(page: str) -> Dom:
    dom = Dom()
    dom.feed(page)
    dom.close()
    return dom


def stringify(node, *, keep=()):
    """Replace every string leaf with the hostile payload (a total fuzz)."""
    if isinstance(node, dict):
        return {k: (v if k in keep else stringify(v, keep=keep)) for k, v in node.items()}
    if isinstance(node, list):
        return [stringify(x, keep=keep) for x in node]
    if isinstance(node, str):
        return HOSTILE
    return node


PAGES = ck.demo_pages()
VIEWS = ck.demo_views()
EV = {e["event_id"]: e for e in VIEWS["events"]}
FIRE = EV["ev-2b3c4d5e6f708192"]
BYLAW = EV["ev-1a2b3c4d5e6f7081"]
EN_ONLY = EV["ev-3c4d5e6f70819203"]


class EveryPageIsWellFormed(unittest.TestCase):
    def test_exactly_one_h1_and_the_landmarks(self):
        self.assertGreaterEqual(len(PAGES), 20)
        for name, page in PAGES.items():
            dom = parse(page)
            self.assertEqual(dom.count("h1"), 1, name)
            self.assertEqual(dom.count("main"), 1, name)
            self.assertEqual(len(dom.find("header", **{"class": "top"})), 1, name)
            self.assertEqual(len(dom.find("footer", **{"class": "site"})), 1, name)
            self.assertEqual(dom.count("nav"), 1, name)
            self.assertIn({"id": "main", "tabindex": "-1"}, [{k: a[k] for k in ("id", "tabindex") if k in a} for t, a in dom.tags if t == "main"], name)
            self.assertEqual(len(dom.ids), len(set(dom.ids)), f"{name}: duplicate ids")

    def test_skip_link_is_the_first_focusable_thing(self):
        for name, page in PAGES.items():
            dom = parse(page)
            first = next(a for t, a in dom.tags if t in ("a", "button", "input"))
            self.assertEqual(first.get("href"), "#main", name)
            self.assertIn("#main", [a.get("href") for t, a in dom.tags if t == "a"])

    def test_html_lang_and_doctype(self):
        for name, page in PAGES.items():
            self.assertTrue(page.startswith("<!doctype html>\n"), name)
            want = "en-CA" if "/en/" in "/" + name or name.startswith("en/") or "permanent/en/" in name else "fr-CA"
            self.assertIn(f'<html lang="{want}">', page, name)

    def test_no_inline_style_script_or_handler(self):
        for name, page in PAGES.items():
            dom = parse(page)
            self.assertEqual(dom.count("style"), 0, name)
            self.assertNotRegex(page, r"(?i)\sstyle\s*=", name)
            self.assertNotRegex(page, r"(?i)\son[a-z]+\s*=", name)
            for tag, a in dom.tags:
                self.assertFalse([v for v in a.values() if v.strip().lower().startswith(("javascript:", "data:", "vbscript:"))], f"{name}: {tag} {a}")
                if tag == "script":
                    self.assertTrue(a.get("src") or a.get("type") == "application/json", f"{name}: inline script {a}")
                self.assertFalse([k for k in a if k.startswith("on")], f"{name}: {tag} {a}")
            srcs = [a.get("src") for t, a in dom.tags if t == "script" and a.get("src")]
            self.assertEqual(srcs, ["/assets/evenements.js"], name)
            sheets = [a["href"] for t, a in dom.tags if t == "link" and a.get("rel") == "stylesheet"]
            self.assertEqual(sheets, ["/assets/fonts.css", "/assets/evenements.css"], name)

    def test_every_link_and_asset_is_self_hosted_or_an_outbound_noopener(self):
        for name, page in PAGES.items():
            dom = parse(page)
            for tag, a in dom.tags:
                href = a.get("href", "")
                if tag == "a" and re.match(r"https?://", href):
                    self.assertEqual(a.get("target"), "_blank", name)
                    self.assertEqual(a.get("rel"), "noopener noreferrer", f"{name}: {href}")
                if tag == "link" and a.get("rel") in ("stylesheet", "icon"):
                    self.assertTrue(href.startswith("/"), f"{name}: external resource {href}")

    def test_chips_never_colour_only(self):
        seen = 0
        for name, page in PAGES.items():
            dom = parse(page)
            for chip in dom.chips:
                seen += 1
                self.assertTrue(chip.strip(), f"{name}: a chip without text")
        self.assertGreater(seen, 100)
        self.assertNotIn('<span class="chip"></span>', "".join(PAGES.values()))

    def test_chips_carry_the_meaning_in_words(self):
        fr = PAGES["evenements/ev-2b3c4d5e6f708192.html"]
        for words in ("Regroupement probable", "Diffuseur public", "Reportage propre", "Dépêche d’agence",
                      "Même propriétaire", "Nouveau", "article en français", "article en anglais"):
            self.assertIn(words, fr)
        en = PAGES["en/evenements/ev-2b3c4d5e6f708192.html"]
        for words in ("Probable grouping", "Public broadcaster", "Own reporting", "Wire copy", "Same owner",
                      "article in French", "article in English"):
            self.assertIn(words, en)

    def test_only_vigie_labels_and_verbatim_text_no_verdicts(self):
        for name, page in PAGES.items():
            text = parse(page).visible_text.lower()
            for banned in ("confirmé", "confirmée", "confirmed", "debunk", "démenti", "fact-check", "verdict",
                           "vérifié", "neutre", "impartial", "unbiased"):
                self.assertNotIn(banned, text, f"{name}: {banned}")


class HeadAndLanguageLinks(unittest.TestCase):
    def test_hreflang_canonical_and_alternates(self):
        for fr_page, en_page, path in (
            ("evenements.html", "en/evenements.html", "/evenements.html"),
            ("index.html", "en/index.html", "/"),
            ("evenements/ev-1a2b3c4d5e6f7081.html", "en/evenements/ev-1a2b3c4d5e6f7081.html", "/evenements/ev-1a2b3c4d5e6f7081.html"),
        ):
            fr_url = "https://vigieqc.com" + path
            en_url = "https://vigieqc.com/en" + path
            for name, canon in ((fr_page, fr_url), (en_page, en_url)):
                page = PAGES[name]
                self.assertIn(f'<link rel="canonical" href="{canon}">', page, name)
                self.assertIn(f'<link rel="alternate" hreflang="fr-CA" href="{fr_url}">', page, name)
                self.assertIn(f'<link rel="alternate" hreflang="en-CA" href="{en_url}">', page, name)
                self.assertIn(f'<link rel="alternate" hreflang="x-default" href="{fr_url}">', page, name)
                self.assertIn(f'<meta property="og:url" content="{canon}">', page, name)
                self.assertIn('<meta name="robots" content="noindex, nofollow">', page)  # the demo is never indexed

    def test_a_page_without_counterpart_emits_no_alternate_and_no_language_link(self):
        head = ck.head_meta(lang="en", fr_path="/registre.html", title="Registre", description="d")
        self.assertNotIn("hreflang", head)
        self.assertIn('<link rel="canonical" href="https://vigieqc.com/registre.html">', head)
        self.assertEqual(ck.lang_link("en", "/registre.html"), "")
        head = ck.head_meta(lang="fr", fr_path="/evenements.html", title="x", description="d", counterpart=False)
        self.assertNotIn("hreflang", head)
        self.assertNotIn("og:locale:alternate", head)

    def test_robots_default_is_permissive(self):
        head = ck.head_meta(lang="fr", fr_path="/evenements.html", title="x", description="d")
        self.assertIn('<meta name="robots" content="index, follow">', head)

    def test_open_graph_and_locale(self):
        head = ck.head_meta(lang="en", fr_path="/evenements.html", title="Title", description="Desc")
        for needle in ('og:type" content="website"', 'og:locale" content="en_CA"', 'og:locale:alternate" content="fr_CA"',
                       'og:title" content="Title — Vigie"', 'og:description" content="Desc"',
                       'og:image" content="https://vigieqc.com/assets/og-default.png"'):
            self.assertIn(needle, head)

    def test_language_link_is_one_real_link_to_the_counterpart(self):
        fr = PAGES["evenements.html"]
        self.assertIn('<a class="seg-link" lang="en" hreflang="en-CA" href="/en/evenements.html">English</a>', fr)
        self.assertIn('<span class="seg-cur" aria-current="true" lang="fr">FR</span>', fr)
        en = PAGES["en/evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn('<a class="seg-link" lang="fr" hreflang="fr-CA" href="/evenements/ev-1a2b3c4d5e6f7081.html">Français</a>', en)
        self.assertEqual(len(re.findall(r"seg-link", en)), 1)

    def test_english_pages_flag_french_only_surfaces(self):
        en = PAGES["en/evenements.html"]
        self.assertIn('href="/registre.html" lang="fr" hreflang="fr-CA"', en)
        self.assertIn('href="/en/evenements.html"', PAGES["en/evenements/ev-1a2b3c4d5e6f7081.html"])  # events index is mirrored
        fr = PAGES["evenements.html"]
        self.assertNotIn('lang="fr" hreflang', fr)

    def test_paths(self):
        self.assertEqual(ck.en_path("/evenements.html"), "/en/evenements.html")
        self.assertEqual(ck.path_for("en", "/"), "/en/")
        self.assertEqual(ck.path_for("en", "/evenements/ev-x.html"), "/en/evenements/ev-x.html")
        self.assertEqual(ck.path_for("en", "/registre.html"), "/registre.html")
        self.assertEqual(ck.path_for("fr", "/evenements.html"), "/evenements.html")
        self.assertEqual(ck.event_path("ev-ab/../x"), "/evenements/ev-abx.html")


class PublisherTextIsAttributedAndMarked(unittest.TestCase):
    def test_foreign_language_text_carries_its_own_lang(self):
        fr = PAGES["evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn('<h3 lang="en" data-hl>Warehouse fire in Limoilou leaves two people treated</h3>', fr)
        self.assertIn('<h3 lang="fr" data-hl>Incendie dans un entrepôt de Limoilou', fr)
        en = PAGES["en/evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn('<h3 lang="fr" data-hl>Incendie dans un entrepôt de Limoilou', en)
        self.assertIn('<h3 lang="en" data-hl>Warehouse fire in Limoilou', en)
        card_en = PAGES["en/evenements.html"]
        self.assertRegex(card_en, r'<a href="/en/evenements/ev-1a2b3c4d5e6f7081.html" lang="fr" data-hl>Abribus chauffants')

    def test_every_publisher_text_element_has_a_lang(self):
        for name, page in PAGES.items():
            if name.startswith("permanent/"):
                continue
            for tag in re.findall(r"<[^>]*data-hl[^>]*>", page):
                self.assertRegex(tag, r'lang="(fr|en)"', f"{name}: {tag}")

    def test_author_credit_and_outbound_link(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn('Par <span lang="fr">P. Démo</span> · Photo : <span lang="fr">Photo Démo</span>', page)
        self.assertIn('<a href="https://courrier-cap.example/conseil/abribus" target="_blank" rel="noopener noreferrer">Lire chez Le Courrier du Cap ↗</a>', page)
        en = PAGES["en/evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn('By <span lang="en">S. Example</span> · Photo: <span lang="en">Demo Photo</span>', en)

    def test_excerpt_is_cut_never_reworded(self):
        long_text = "mot " * 100
        ev = copy.deepcopy(BYLAW)
        ev["members"][0]["excerpt"] = long_text
        card = ck.event_card(ev, "fr")
        m = re.search(r'<p class="ev-sum"[^>]*>(.*?)</p>', card)
        self.assertTrue(m)
        self.assertLessEqual(len(m.group(1)), ck.EXCERPT_MAX + 1)
        self.assertTrue(m.group(1).endswith("…"))
        self.assertTrue(long_text.startswith(m.group(1).rstrip("…")))

    def test_english_only_coverage_is_labelled(self):
        self.assertIn("Couverture anglophone seulement", PAGES["evenements.html"])
        self.assertIn("English coverage only", PAGES["en/evenements.html"])
        self.assertIn("English coverage only", PAGES["en/evenements/ev-3c4d5e6f70819203.html"])

    def test_single_voice_is_a_fact_not_a_gap(self):
        page = PAGES["evenements/ev-3c4d5e6f70819203.html"]
        self.assertIn("Une seule voix", page)
        self.assertIn("parmi les 9 institutions suivies", page)
        self.assertIn("pas un trou", page)
        plain = ck.event_page(EN_ONLY, "fr")
        self.assertIn("dans cette collecte", plain)

    def test_dates_and_times_are_quebec_wall_clock(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn(">9 h 12<", page)  # 13:12Z is 9:12 EDT
        en = PAGES["en/evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn(">9:12 a.m.<", en)
        self.assertIn("Tuesday, October 6, 2026 edition", en)
        self.assertIn("mardi 6 octobre 2026", page)


class Panels(unittest.TestCase):
    def test_words_panel_is_server_rendered_with_presence_pips(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn('data-forms="abribus"', page)
        self.assertIn("3/3", page)
        self.assertIn("2/3", page)
        self.assertIn('<span class="pip c-public"></span>', page)
        self.assertIn('<span class="pip c-indep off"></span>', page)  # the third voice lacks "règlement"
        self.assertNotIn("<button", ck.words_panel(BYLAW, "fr"))  # buttons only appear when the script runs

    def test_word_matching_is_accent_case_and_boundary_aware(self):
        self.assertEqual(ck.fold("ENTREPÔT œuvre l’été"), "entrepot oeuvre l'ete")
        self.assertTrue(ck._word_present(["entrepôt"], ck.fold("Un ENTREPOT en feu")))
        self.assertFalse(ck._word_present(["règle"], ck.fold("le règlement")))
        self.assertTrue(ck._word_present(["feu vert"], ck.fold("un feu vert, enfin")))
        self.assertFalse(ck._word_present([""], ck.fold("x")))

    def test_origins_panel_counts_articles_to_origins_and_notes_same_owner(self):
        page = PAGES["evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn("3 articles → 2 origines", page)
        self.assertIn("Radio Fleuve et Fleuve News appartiennent au même propriétaire", page)
        en = PAGES["en/evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn("3 articles → 2 origins", en)
        self.assertIn("Radio Fleuve and Fleuve News have the same owner", en)
        solo = PAGES["evenements/ev-3c4d5e6f70819203.html"]
        self.assertIn("1 article → 1 origine", solo)

    def test_owners_are_worded_by_structure_never_by_class_or_code(self):
        # ranking.md « Propriété des sources »: the chip, the origins panel and
        # the same-owner note use scripts/ownership.py's words, linked to the
        # sources page; never "Indépendant", never an owner_group code.
        def member(iid, inst, name, cls, group, lang):
            return {"item_id": iid, "institution": inst, "institution_name": name, "ownership_class": cls,
                    "owner_group": group, "language": lang, "published_at": "2026-10-06T13:00:00Z",
                    "origin_class": "own_reporting"}
        ev = {"event_id": "ev-0123456789abcdef", "label": {"fr": "Essai", "en": "Test"},
              "members": [member("a", "inst-a", "Quotidien A", "independent", "le-devoir", "fr"),
                          member("b", "inst-b", "Quotidien B", "independent", "zz-unworded", "fr"),
                          member("c", "inst-c", "Radio C", "public_broadcaster", "cbc-radio-canada", "fr"),
                          member("d", "inst-d", "Radio D", "public_broadcaster", "cbc-radio-canada", "en"),
                          member("e", "inst-e", "Feuille E", "unverified", "", "fr")],
              "independence": {"groups": [["a"], ["b"], ["c", "d"], ["e"]], "count": 4},
              "language_pairs": [{"fr": "c", "en": "d", "rule": "x", "same_owner": True}]}
        fr, en = ck.event_page(ev, "fr"), ck.event_page(ev, "en")
        for page, lang in ((fr, "fr"), (en, "en")):
            visible = ck.fold(html.unescape(re.sub(r"<[^>]+>", " ", page)))
            self.assertNotRegex(visible, r"\bindependant\b|\bindependent\b(?! reporting)", lang)
            self.assertNotIn("cbc-radio-canada", visible, lang)
            self.assertIn('href="/methode/sources.html#propriete"', page, lang)
        for words in ("Contrôlé par une fiducie", "Propriété : voir les sources", "Société d’État fédérale",
                      "Propriété non établie", "appartiennent au même propriétaire (Société d’État fédérale)"):
            self.assertIn(words, html.unescape(fr).replace(" ", " "))
        for words in ("Controlled by a trust", "Ownership: see the sources", "Federal Crown corporation",
                      "Ownership not established", "have the same owner (Federal Crown corporation)"):
            self.assertIn(words, html.unescape(en))
        self.assertIn('lang="fr" hreflang="fr-CA"', en.split('href="/methode/sources.html#propriete"', 1)[1][:40],
                      "the English page says the sources page is in French")
        self.assertIn('<span class="chip own">Propriété non établie</span>', fr, "no class colour without a class")

    def test_numbers_are_side_by_side_never_reconciled(self):
        page = PAGES["evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn("<code>2</code>", page)
        self.assertIn("<code>3</code>", page)
        self.assertIn("Valeurs différentes", page)
        self.assertIn("Personnes blessées", page)
        self.assertNotIn("désaccord ", page.replace("conclut pas à un désaccord", ""))
        slot = {"kind": "amount", "unit": "total"}
        self.assertEqual(ck._fact_value(slot, 123450, "fr"), "1 234,50 $")
        self.assertEqual(ck._fact_value({"kind": "amount", "unit": "percent_bp"}, 350, "en"), "3.5%")
        self.assertEqual(ck._fact_value({"kind": "date", "unit": "x"}, "2026-10-01", "fr"), "1er octobre 2026")

    def test_why_panel_explains_the_grouping(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        for needle in ("Pourquoi ces articles sont regroupés", "Ancres communes", "abribus", "Écart", "2 h 28 (limite : 72 h)",
                       "3 (minimum : 2)", "<code>complete-link</code>", "Certain — règle fixe, vérifiable à la main."):
            self.assertIn(needle, page)
        fire = PAGES["evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn("incendie ≙ fire (alias)", fire)
        self.assertIn("Une règle automatique peut se tromper", fire)
        self.assertIn("Aucun regroupement : un seul article.", PAGES["evenements/ev-3c4d5e6f70819203.html"])

    def test_silence_is_measured_absence(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn("aucun article lié", page)
        self.assertIn("notre collecte a échoué : absence non établie", page)
        self.assertIn("Sans article lié", page)

    def test_official_anchors_are_pointers_not_confirmations(self):
        page = PAGES["evenements/ev-2b3c4d5e6f708192.html"]
        self.assertIn("Un lien vers un document officiel n’est pas une confirmation.", page)
        self.assertIn("Fermeture temporaire de la rue Démo", page)
        self.assertIn("<code>same-road-overlapping-dates</code>", page)
        self.assertIn('href="/memoire/57.html"', page)
        none = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn("Aucune retrouvée parmi", none)

    def test_neighbours_are_not_grouped_and_say_why(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn("Voisins, non regroupés", page)
        self.assertIn("Possible, non regroupé", page)
        self.assertIn("Un fil n’est pas un événement.", page)
        self.assertIn("Abribus : un citoyen réclame plus de bancs", page)

    def test_ledger_lists_headline_changes_verbatim(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        change = BYLAW["ledger"]["changes"][0]
        self.assertIn(f'<span lang="fr" class="gone">{ck.esc(change["before"])}</span>', page)
        self.assertIn(f'<span lang="fr">{ck.esc(change["after"])}</span>', page)
        self.assertIn("Aucun changement de titre observé.", PAGES["evenements/ev-2b3c4d5e6f708192.html"])

    def test_window_state_is_never_ended_or_resolved(self):
        ev = copy.deepcopy(BYLAW)
        ev["window_state"] = "out_of_window"
        page = ck.event_page(ev, "en")
        self.assertIn("has left Vigie’s window", page)
        self.assertNotRegex(parse(page).visible_text.lower(), r"\b(resolved|ended|closed case)\b")


class TimelinesAreSvgWithoutStyle(unittest.TestCase):
    def test_positions_are_attributes_within_bounds(self):
        for name, page in PAGES.items():
            if "evenements/ev-" not in name:
                continue
            dom = parse(page)
            marks = ("dot", "tl-", "dot-ring")
            xs = ([a["cx"] for t, a in dom.tags if t == "circle" and a.get("class", "").startswith(marks)]
                  + [a["x"] for t, a in dom.tags if t == "text" and a.get("class", "").startswith("tl-")])
            self.assertTrue(xs, name)
            for x in xs:
                self.assertTrue(x.endswith("%"), f"{name}: {x}")
                self.assertGreaterEqual(float(x[:-1]), 0.0, name)
                self.assertLessEqual(float(x[:-1]), 100.0, name)

    def test_dots_link_to_existing_voice_cards_on_current_pages_only(self):
        page = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        dom = parse(page)
        targets = [a["href"][1:] for t, a in dom.tags if t == "a" and a.get("class", "").startswith("tl-a")]
        self.assertEqual(len(targets), 3)
        for target in targets:
            self.assertIn(target, dom.ids)
        for a in dom.find("a"):
            if a.get("class", "").startswith("tl-a"):
                self.assertIn("aria-label", a)
                self.assertEqual(a.get("data-jump"), a["href"][1:])
        perm = parse(PAGES["permanent/evenements/ev-1a2b3c4d5e6f7081.html"])
        self.assertFalse([a for t, a in perm.tags if t == "a" and a.get("class", "").startswith("tl-a")])
        self.assertFalse([i for i in perm.ids if i.startswith("v-")])

    def test_first_voice_is_ringed_and_lanes_group_by_institution(self):
        html = ck.full_timeline(FIRE, "fr")
        self.assertEqual(html.count('class="tl-lane"'), 3)
        self.assertEqual(html.count("dot-ring"), 1)
        self.assertIn('role="group"', html)
        self.assertEqual(ck.full_timeline({"members": []}, "fr"), "")
        self.assertEqual(ck.mini_timeline({"members": []}, "fr"), "")

    def test_hour_ticks_sit_on_a_linear_axis(self):
        """The axis is padded around the event window; the ticks before the
        first voice were once pinned to its dot like out-of-window members, so
        two hours were printed at the same place (seen on the live edition:
        "3 h" and "4 h" both under a 4 h 15 voice)."""
        def ticks(html):
            svg = html[html.index('class="tl-ticks"'):]
            return [(float(x), label) for x, label in
                    re.findall(r'<text class="tl-tick" x="([\d.]+)%"[^>]*>([^<]*)<', svg)]

        def epoch(m):
            return ck._epoch(ck._shown_instant(m))

        for ev in [FIRE, BYLAW, EN_ONLY, *VIEWS["events"]]:
            for lang in ("fr", "en"):
                html = ck.full_timeline(ev, lang)
                if not html:
                    continue
                marks = ticks(html)
                xs = [x for x, _label in marks]
                self.assertEqual(xs, sorted(set(xs)), (ev.get("event_id"), lang, marks))
                self.assertAlmostEqual(xs[0], 0.0, places=1)
                gaps = {round(b - a, 1) for a, b in zip(xs, xs[1:])}
                self.assertLessEqual(max(gaps) - min(gaps), 0.1, (ev.get("event_id"), marks))
        # a voice at 4 h 15 (Quebec City) sits between the 4 h and 5 h ticks
        ev = copy.deepcopy(EN_ONLY)
        ev["members"] = ev["members"][:1]
        ev["members"][0]["published_at"] = "2026-10-05T08:15:00Z"
        html = ck.full_timeline(ev, "fr")
        marks = dict((label, x) for x, label in ticks(html))
        dot = float(re.search(r'<circle class="tl-hit" cx="([\d.]+)%"', html).group(1))
        self.assertLess(marks["4 h"], dot)
        self.assertLess(dot, marks["5 h"])
        self.assertIn("3 h", marks)
        self.assertLess(marks["3 h"], marks["4 h"])
        self.assertIsNotNone(epoch(ev["members"][0]))

    def test_single_dot_is_centred_and_tick_labels_follow_the_language(self):
        mini = ck.mini_timeline(EN_ONLY, "fr")
        self.assertIn('cx="50%"', mini)
        fr = ck.full_timeline(BYLAW, "fr")
        en = ck.full_timeline(BYLAW, "en")
        self.assertRegex(fr, r">\d{1,2} h<")
        self.assertRegex(en, r">\d{1,2}:00<")
        self.assertNotRegex(en, r"tl-tick[^>]*>\d{1,2}:00 [ap]\.m\.")


class DateSuspectMembersCannotStretchTheAxis(unittest.TestCase):
    """A parseable but wrong published_at is kept and flagged (docs/EVENTS.md),
    never corrected: it must not blow up the page or crash the render."""

    @staticmethod
    def with_date(value, index=0):
        ev = copy.deepcopy(BYLAW)
        ev["members"][index]["published_at"] = value
        return ev

    def test_wrong_years_give_a_thin_bounded_page(self):
        for value in ("1970-01-01T00:00:00Z", "1969-12-31T12:00:00Z", "1900-05-05T00:00:00Z", "2027-10-01T00:00:00Z",
                      "2126-10-01T00:00:00Z", "9999-12-31T23:59:59Z", "0001-01-01T00:00:00Z", "2026-10-06T12:00:00-23:59"):
            for index in (0, 1, 2):
                ev = self.with_date(value, index)
                for lang in ("fr", "en"):
                    for perm in (False, True):
                        page = ck.event_page(dict(ev, permanent=perm), lang)
                        self.assertLess(len(page.encode("utf-8")), 60_000, (value, index, lang, perm))
                        self.assertLessEqual(page.count('class="tl-tick"'), 12, (value, index))
                        self.assertEqual(parse(page).count("h1"), 1)
                        ck.event_card(ev, lang)

    def test_ticks_stay_bounded_on_a_normal_event_too(self):
        for ev in VIEWS["events"]:
            self.assertLessEqual(ck.full_timeline(ev, "fr").count('class="tl-tick"'), 12)

    def test_outliers_are_pinned_inside_the_axis_with_their_full_date(self):
        ev = self.with_date("1970-01-01T00:00:00Z", 0)
        html = ck.full_timeline(ev, "fr")
        self.assertEqual(html.count("tl-a "), 3)  # every voice still has its dot
        for x in re.findall(r'cx="([\d.]+)%"', html) + re.findall(r'class="tl-t" x="([\d.]+)%"', html):
            self.assertTrue(0.0 <= float(x) <= 100.0, x)
        self.assertIn("1969", html)  # 31 Dec 1969 evening in Quebec City: the full date is on the label
        # the two honest voices keep their plain time-of-day labels
        self.assertRegex(html, r'class="tl-t"[^>]*>\d{1,2} h(?: \d\d)?<')
        en = ck.full_timeline(self.with_date("2027-10-06T12:00:00Z", 2), "en")
        self.assertIn("2027", en)

    def test_the_densest_window_wins_not_the_first_dot(self):
        # the earliest dot is the wrong one; the two others are an hour apart
        ev = self.with_date("2020-01-01T00:00:00Z", 0)
        html = ck.full_timeline(ev, "fr")
        self.assertLessEqual(html.count('class="tl-tick"'), 12)
        self.assertNotIn("2020", ck.mini_timeline(ev, "fr") + "")  # strip labels are times of day only
        # the two honest voices are not both piled on one edge
        xs = sorted({float(x) for x in re.findall(r'<circle class="tl-hit" cx="([\d.]+)%"', html)})
        self.assertGreaterEqual(len(xs), 2)

    def test_tick_label_epoch_and_formatters_never_raise(self):
        for e in (-1e12, -62135596800.0, 1e18, 9e15):
            self.assertIsInstance(ck._tick_label(e, "fr"), str)
        for bad in ("0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59", "9999-12-31T23:59:59Z",
                    "1970-01-01T00:00:00Z", "", None, 5):
            ck._epoch(bad)
            i18n.fmt_time(bad, "fr")
            i18n.fmt_datetime(bad, "en")


class PermanentPagesCarryNoPublisherText(unittest.TestCase):
    def _tokenised(self):
        ev = copy.deepcopy(FIRE)
        n = 0

        def tok(label):
            nonlocal n
            n += 1
            return f"PUBTEXT{label}{n}"

        for m in ev["members"]:
            m["title"], m["excerpt"], m["author"], m["photo_credit"] = tok("title"), tok("excerpt"), tok("author"), tok("credit")
        ev["neighbours"] = [{"member": {**FIRE["members"][0], "item_id": "n1", "title": tok("neighbour"), "excerpt": tok("nx")}, "reason": "shared_word", "why": {"fr": tok("why"), "en": tok("why")}}]
        ev["ledger"] = {"changes": [{"at": "2026-10-06T14:40:00Z", "institution_name": "Radio Fleuve", "before": tok("before"), "after": tok("after")}]}
        for a in ev["anchors"]:
            if a["type"] == "official_item":
                a["title"] = tok("anchor")
        for m in ev["members"]:
            m["excerpt"] += " PUBTEXTform0"
        ev["words"] = [{"k": "w", "label": tok("word"), "forms": ["PUBTEXTform0"], "alias": False}]
        ev["why"] = {**ev["why"], "shared": [{"label": tok("shared")}], "place_anchor": tok("place")}
        ev["facts"] = [{"slot": {"kind": "entity", "unit": "x", "subject": "y"}, "values": [{"value": tok("fact"), "stated_by": [], "institutions": ["radio-fleuve"]}], "divergent": True}]
        return ev

    def test_permanent_variant_renders_none_of_it(self):
        ev = self._tokenised()
        live = {lang: ck.event_page(ev, lang, edition=VIEWS["edition"]) for lang in ("fr", "en")}
        for lang, page in live.items():
            self.assertIn("PUBTEXTtitle", page, f"sanity: the live {lang} page shows publisher text")
            self.assertIn("PUBTEXTword", page)
            self.assertIn("PUBTEXTfact", page)
        perm = dict(ev, permanent=True)
        for lang in ("fr", "en"):
            page = ck.event_page(perm, lang, edition=VIEWS["edition"])
            self.assertNotIn("PUBTEXT", page, lang)
            card = ck.event_card(perm, lang)
            self.assertNotIn("PUBTEXT", card, lang)
            for part in (ck.words_panel, ck.numbers_panel, ck.neighbours_panel, ck.ledger_panel, ck.official_link_panel,
                         ck.why_panel, ck.origins_panel, ck.archive_panel, ck.silence_panel, ck.coverage_list):
                self.assertNotIn("PUBTEXT", part(perm, lang), part.__name__)

    def test_demo_permanent_pages_hold_only_labels_ids_names_times_links_counts_anchors_seals(self):
        names = {m["institution_name"] for e in VIEWS["events"] for m in e["members"]}
        for name, page in PAGES.items():
            if not name.startswith("permanent/"):
                continue
            for ev in VIEWS["events"]:
                for m in ev["members"]:
                    for field in ("title", "excerpt", "author", "photo_credit"):
                        if m.get(field):
                            self.assertNotIn(m[field], page, f"{name}: {field}")
                for a in ev["anchors"]:
                    if a.get("title"):
                        self.assertNotIn(a["title"], page, name)
                for n in ev["neighbours"]:
                    self.assertNotIn(n["member"]["title"], page, name)
            if "ev-2b3c4d5e6f708192" in name:
                self.assertIn("Radio Fleuve", page)
                self.assertIn('href="/memoire/57.html"', page)
                self.assertIn("<code>DEMO-20261006-01</code>", page)
                self.assertIn("Read at" if "/en/" in name else "Lire chez", page)
        self.assertTrue(names)

    def test_permanent_page_keeps_the_record_and_explains_itself(self):
        page = PAGES["permanent/evenements/ev-2b3c4d5e6f708192.html"]
        for needle in ("Couverture collectée", "Cette fiche permanente ne garde aucun texte d’éditeur",
                       "Comment cette page est conservée", "Incendie de bâtiment · Limoilou", "3 articles", "<details class=\"disc\" open>",
                       "Édition scellée n° 57"):
            self.assertIn(needle, page)

    def test_permanent_card_shows_the_label_as_the_headline(self):
        card = ck.event_card(permanent_of(BYLAW), "fr")
        self.assertIn('<h2 class="ev-h"><a href="/evenements/ev-1a2b3c4d5e6f7081.html">Règlement municipal · Québec</a></h2>', card)
        self.assertNotIn("Abribus chauffants", card)


def permanent_of(ev):
    return ck.permanent_view(ev)


class HostileStringsAreEscapedEverywhere(unittest.TestCase):
    def _allowed_tags(self):
        tags = set()
        for page in PAGES.values():
            tags |= {t for t, _a in parse(page).tags}
        return tags

    def test_fuzzed_views_render_inert_pages(self):
        allowed = self._allowed_tags()
        views = copy.deepcopy(VIEWS)
        ed = stringify(views["edition"], keep=("events",))
        ed["events"] = [stringify(e) for e in views["events"]]
        ed["clock"] = views["edition"]["clock"]
        rw = stringify(views["roadworks"])
        rw["rows"][0]["impact"] = "all-lanes-closed"
        pages = []
        for lang in ("fr", "en"):
            pages.append(ck.edition_page(ed, lang, roadworks=rw))
            for ev in ed["events"]:
                pages.append(ck.event_page(ev, lang, edition=ed))
                pages.append(ck.event_page(dict(ev, permanent=True), lang, edition=ed))
                pages.append(ck.event_card(ev, lang))
        for page in pages:
            dom = parse(page)
            self.assertNotIn("<img", page)
            self.assertNotIn("<script>alert", page)
            self.assertNotIn(HOSTILE, page)
            self.assertFalse({t for t, _a in dom.tags} - allowed, {t for t, _a in dom.tags} - allowed)
            for tag, a in dom.tags:
                self.assertFalse([k for k in a if k.startswith("on")], f"{tag} {a}")
            self.assertEqual(len([a for t, a in dom.tags if t == "script" and a.get("src")]), 1 if "<html" in page else 0)

    def test_hostile_text_is_visible_as_text_not_markup(self):
        ev = copy.deepcopy(BYLAW)
        ev["members"][0]["title"] = HOSTILE
        page = ck.event_page(ev, "fr")
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertIn("&quot;&gt;", page)
        self.assertIn(HOSTILE, parse(page).visible_text.replace("  ", " "))

    def test_only_http_urls_become_links(self):
        ev = copy.deepcopy(BYLAW)
        for bad in ("javascript:alert(1)", "data:text/html,x", "//evil.example/x", "/relative", "ftp://x.example/", "http://", "https://a b.example/", 'https://x.example/"onmouseover="y'):
            ev["members"][0]["url"] = bad
            page = ck.event_page(ev, "fr")
            self.assertNotIn(bad, page, bad)
            self.assertNotIn(">Lire chez Radio Fleuve ↗</a>", page.split("Voisins")[0], bad)
            hrefs = [a.get("href", "") for t, a in parse(page).tags if t == "a"]
            self.assertFalse([h for h in hrefs if h.startswith(("javascript", "data:", "//", "ftp"))], bad)
        ev["members"][0]["url"] = "https://radio-fleuve.example/a?b=1&c=2"
        page = ck.event_page(ev, "fr")
        self.assertIn('href="https://radio-fleuve.example/a?b=1&amp;c=2"', page)

    def test_spoofing_controls_are_stripped(self):
        self.assertEqual(ck.esc("a‮b​c\x00d"), "abcd")
        self.assertEqual(ck.esc(None), "")
        self.assertEqual(ck.esc("<&>\"'"), "&lt;&amp;&gt;&quot;&#x27;")

    def test_dom_ids_are_sanitised(self):
        self.assertEqual(ck.dom_id("v", 'a"b c>d'), "v-abcd")
        ev = copy.deepcopy(BYLAW)
        ev["members"][0]["item_id"] = 'x" onfocus="y'
        dom = parse(ck.event_page(ev, "fr"))
        self.assertIn("v-xonfocusy", dom.ids)
        self.assertFalse([a for t, a in dom.tags if any(k.startswith("on") for k in a)])


class Roadworks(unittest.TestCase):
    def test_always_prints_collection_time_counts_and_age_note(self):
        rw = VIEWS["roadworks"]
        for lang, needles in (
            ("fr", ("Collecte du 6 oct. 2026, 18 h 53 (heure de l’Est)", "128 entraves déclarées, dont 31 avec toutes les voies fermées",
                    "Instantané, pas un flux en direct", "La carte officielle fait foi.", "44 planifiées")),
            ("en", ("Collected Oct 6, 2026, 6:53 p.m. (Eastern time)", "128 obstructions declared, 31 with all lanes closed",
                    "A snapshot, not a live feed", "The official map prevails.", "44 planned")),
        ):
            block = ck.roadworks_block(rw, lang)
            for needle in needles:
                self.assertIn(needle, block, lang)
            self.assertIn('<time class="age-time" datetime="2026-10-06T22:53:00Z" data-age>', block)

    def test_even_an_empty_or_missing_lane_prints_all_three(self):
        for rw in (None, {}, {"rows": []}, {"collected_at": "garbage", "total": 0, "closed": 0}):
            block = ck.roadworks_block(rw, "fr")
            self.assertIn("Heure de collecte non établie", block)
            self.assertIn("Instantané, pas un flux en direct", block)
            self.assertRegex(block, r"(Nombre d’entraves non établi|0 entrave déclarée)")
        empty = ck.roadworks_block({"collected_at": "2026-10-06T10:00:00Z", "total": 0, "closed": 0, "rows": []}, "fr")
        self.assertIn("Aucune entrave déclarée dans cette collecte. Cela ne signifie pas", empty)
        self.assertIn("Collecte du 6 oct. 2026, 6 h", empty)

    def test_stale_flag_prints_the_warning(self):
        stale = ck.roadworks_block(dict(VIEWS["roadworks"], stale=True), "fr")
        self.assertIn("Collecte à actualiser", stale)
        self.assertIn("plus de six heures", stale)
        self.assertNotIn("Collecte à actualiser", ck.roadworks_block(VIEWS["roadworks"], "fr"))

    def test_a_collection_newer_than_the_render_clock_is_not_stale(self):
        # The render clock is the edition clock in practice; the hourly lane is
        # routinely fresher than the edition. That is not "more than six hours".
        store = {"fetched_at": "2026-10-06T02:53:47Z", "events": [{"event_id": "a", "road_names": ["Rue A"]}]}
        for clock in ("2026-10-06T00:03:00Z", "2026-10-05T23:00:00Z"):
            view = ck.roadworks_view(store, render_clock=clock)
            self.assertFalse(view["stale"], clock)
            for lang in ("fr", "en"):
                html = ck.roadworks_block(view, lang)
                self.assertNotIn("plus de six heures", html)
                self.assertNotIn("more than six hours", html)
        # within five minutes of the clock: neither stale nor skewed
        near = ck.roadworks_view(store, render_clock="2026-10-06T02:50:00Z")
        self.assertFalse(near["stale"])
        self.assertFalse(near["clock_skew"])
        skew = ck.roadworks_view(store, render_clock="2026-10-05T23:00:00Z")
        self.assertTrue(skew["clock_skew"])
        self.assertIn("Horodatage incohérent", ck.roadworks_block(skew, "fr"))
        self.assertIn("Inconsistent timestamp", ck.roadworks_block(skew, "en"))
        # a genuinely old collection is still stale, with its own sentence
        old = ck.roadworks_view(store, render_clock="2026-10-06T09:00:00Z")
        self.assertTrue(old["stale"])
        self.assertFalse(old["clock_skew"])
        self.assertIn("plus de six heures", ck.roadworks_block(old, "fr"))
        self.assertNotIn("Horodatage incohérent", ck.roadworks_block(old, "fr"))

    def test_rows_keep_the_given_order_and_label_closures_in_words(self):
        block = ck.roadworks_block(VIEWS["roadworks"], "fr")
        order = [block.index(f'<span class="st">{s}</span>') for s in ("Avenue des Érables", "Rue des Fabricants", "Boulevard de la Rivière", "Rue du Quai")]
        self.assertEqual(order, sorted(order))
        for needle in ("Toutes les voies fermées", "Voies partiellement fermées", "Circulation en alternance", "Planifiée",
                       "En cours", "direction est", "(dates estimées par la Ville)", "Les 4 plus restrictives sont affichées."):
            self.assertIn(needle, block)
        self.assertIn("Toutes les voies fermées", ck.roadworks_block(VIEWS["roadworks"], "fr"))
        self.assertIn("All lanes closed", ck.roadworks_block(VIEWS["roadworks"], "en"))
        self.assertIn("The most restrictive one is shown.", ck.roadworks_block({"collected_at": "2026-10-06T10:00:00Z", "total": 1, "closed": 0, "rows": [VIEWS["roadworks"]["rows"][0]]}, "en"))

    def test_block_is_independent_of_the_edition(self):
        ed = copy.deepcopy(VIEWS["edition"])
        old = ck.edition_page(ed, "fr", roadworks=VIEWS["roadworks"])
        newer = dict(VIEWS["roadworks"], collected_at="2026-10-07T01:00:00+00:00", total=129)
        fresh = ck.edition_page(ed, "fr", roadworks=newer)
        self.assertIn("129 entraves", fresh)
        self.assertIn("128 entraves", old)
        strip = lambda p: re.sub(r'<section class="card panel roads".*?</section>', "", p, flags=re.S)  # noqa: E731
        self.assertEqual(strip(old), strip(fresh))

    def test_attribution_and_official_map_link(self):
        block = ck.roadworks_block(VIEWS["roadworks"], "fr")
        self.assertIn("via Données Québec (CC-BY 4.0).", block)
        self.assertIn('href="https://carte.ville.quebec.qc.ca/" target="_blank" rel="noopener noreferrer"', block)
        self.assertIn('href="/partir.html"', block)
        self.assertIn('href="/partir.html" lang="fr" hreflang="fr-CA"', ck.roadworks_block(VIEWS["roadworks"], "en"))

    def test_adapter_from_the_live_store_shape(self):
        import resident_brief

        self.assertEqual(ck.RW_SEVERITY, resident_brief.RW_SEVERITY)
        store = {
            "fetched_at": "2026-10-06T02:53:47.110034+00:00", "institution_name": "Ville de Québec",
            "dataset_url": "https://data.example/set",
            "events": [
                {"event_id": "b", "road_names": ["Rue B", "Rue C"], "vehicle_impact": "some-lanes-closed", "event_status": "active",
                 "event_type": "road-work", "direction": "eastbound", "start_date": "2026-10-01T10:00:00Z", "end_date": "2026-10-09T10:00:00Z",
                 "start_date_accuracy": "estimated", "update_date": "2026-10-05T00:00:00Z", "description": "  Rue B   entre X et Y  "},
                {"event_id": "a", "road_names": ["Rue A"], "vehicle_impact": "all-lanes-closed", "event_status": "planned", "update_date": "2026-10-01T00:00:00Z"},
                {"event_id": "c", "road_names": ["Rue D"], "vehicle_impact": "all-lanes-closed", "event_status": "pending", "update_date": "2026-10-04T00:00:00Z"},
                {"event_id": "d", "road_names": [], "vehicle_impact": "all-lanes-open", "event_status": "active"},
                {"road_names": ["no id"], "vehicle_impact": "all-lanes-closed"},
                "junk",
            ],
        }
        view = ck.roadworks_view(store, limit=3)
        self.assertEqual([r["id"] for r in view["rows"]], ["c", "a", "b"])  # severity, then newest update, then id
        self.assertEqual((view["total"], view["closed"], view["planned"]), (4, 2, 2))
        self.assertEqual(view["rows"][2]["places"], "Rue B · Rue C")
        self.assertEqual(view["rows"][2]["text"], "Rue B entre X et Y")
        self.assertTrue(view["rows"][2]["estimated"])
        self.assertEqual(view["rows"][2]["from"], "2026-10-01")
        self.assertFalse(view["stale"])
        self.assertTrue(ck.roadworks_view(store, render_clock="2026-10-06T09:00:00Z")["stale"])
        self.assertFalse(ck.roadworks_view(store, render_clock="2026-10-06T04:00:00Z")["stale"])
        html = ck.roadworks_block(view, "fr")
        self.assertIn("4 entraves déclarées, dont 2 avec toutes les voies fermées", html)
        self.assertIn("Collecte du 5 oct. 2026, 22 h 53", html)  # 02:53Z is the evening before in Quebec City

    def test_adapter_corrupt_store_is_an_empty_view(self):
        for bad in (None, {}, [], "x", {"events": "nope"}, {"fetched_at": 5, "events": [1, 2]}):
            view = ck.roadworks_view(bad)  # type: ignore[arg-type]
            self.assertEqual(view["rows"], [])
            self.assertEqual(view["collected_at"], "")
            self.assertIn("Heure de collecte non établie", ck.roadworks_block(view, "fr"))


class EditionPage(unittest.TestCase):
    def test_edition_head_cards_aside_end(self):
        page = PAGES["evenements.html"]
        for needle in ("Édition du matin · mardi 6 octobre 2026", "L’essentiel pour Québec", "5 événements · 9 institutions suivies · collectée à 10 h 12",
                       "Copié, jamais réécrit", "Sans compte, sans traceur", "Vous êtes à jour.", "Fin de l’édition du matin : 5 événements.",
                       "Prochaine collecte vers 16 h 10.", "Qui a parlé, qui n’a rien publié dans nos flux", "Scellée au Registre :", "n° 57", "0a8246b0c1d2…",
                       "Le sceau ne contient aucun texte d’éditeur.", "Déclaré par les autorités", "dans 2 événements", "18 articles collectés, aucun dans un événement affiché",
                       "notre collecte a échoué", "aucun article collecté", "4 communiqués collectés, aucun lié à un événement"):
            self.assertIn(needle, page)
        dom = parse(page)
        self.assertEqual(len(dom.find("article", **{"class": "card ev", "data-mine-card": ""})), 5)
        order = [page.index(f"ev-{i}") for i in ("1a2b3c4d5e6f7081", "2b3c4d5e6f708192", "3c4d5e6f70819203", "4d5e6f7081920314", "5e6f708192031425")]
        self.assertEqual(order, sorted(order), "the kit never reorders the given ranking")

    def test_english_edition(self):
        page = PAGES["en/evenements.html"]
        for needle in ("Morning edition · Tuesday, October 6, 2026", "Québec City, in brief", "5 events · 9 institutions followed · collected at 10:12 a.m.",
                       "You’re up to date.", "End of the morning edition: 5 events.", "Next collection around 4:10 p.m.", "Sealed in the Registre:", "no. 57",
                       "Before you leave", "My area", "Compare the 3 voices →", "See the record →"):
            self.assertIn(needle, page)

    def test_period_is_derived_from_the_local_hour_unless_given(self):
        for iso, period in (("2026-10-06T14:00:00Z", "morning"), ("2026-10-06T17:00:00Z", "afternoon"), ("2026-10-06T22:30:00Z", "evening"),
                            ("2026-10-07T04:00:00Z", "night"), ("2026-10-06T08:00:00Z", "night")):
            self.assertEqual(ck._period({"clock": iso}), period, iso)
        self.assertEqual(ck._period({"clock": "2026-10-06T14:00:00Z", "period": "evening"}), "evening")

    def test_empty_edition_is_honest_about_absence(self):
        page = ck.edition_page({"clock": "2026-10-06T14:00:00Z", "events": []}, "fr", roadworks=None)
        self.assertIn("Aucun événement dans cette édition. Une absence dans nos flux n’est pas un silence absolu.", page)
        self.assertEqual(parse(page).count("h1"), 1)
        self.assertIn("0 événement", page)

    def test_chez_moi_is_complete_without_javascript(self):
        page = PAGES["evenements.html"]
        self.assertIn('id="chez-moi"', page)
        self.assertIn("<noscript>", page)
        self.assertIn("Cette fonction a besoin de JavaScript", page)
        self.assertRegex(page, r'<div class="js-only" hidden>')
        self.assertIn('href="#chez-moi">Chez moi</a>', page)
        ev = PAGES["evenements/ev-1a2b3c4d5e6f7081.html"]
        self.assertIn('href="/evenements.html#chez-moi">Chez moi</a>', ev)
        self.assertIn('href="/en/evenements.html#chez-moi">My area</a>', PAGES["en/evenements/ev-1a2b3c4d5e6f7081.html"])

    def test_island_carries_the_script_texts_and_is_inert(self):
        for lang in ("fr", "en"):
            page = PAGES["evenements.html" if lang == "fr" else "en/evenements.html"]
            m = re.search(r'<script type="application/json" id="vigie-i18n">(.*?)</script>', page, re.S)
            self.assertTrue(m)
            import json

            data = json.loads(m.group(1))
            self.assertEqual(data, {k[3:]: v for k, v in i18n.catalogue(lang).items() if k.startswith("js.")})
            self.assertNotIn("<", m.group(1))

    def test_card_why_disclosure_and_counts(self):
        card = ck.event_card(FIRE, "fr")
        self.assertIn("<summary>Pourquoi ici ?</summary>", card)
        for needle in ("Lieu : </b>Limoilou", "Fraîcheur", "de 7 h 20 à 8 h 30", "3 articles · 2 organisations", "seulement l’étiquette « Incendie de bâtiment »"):
            self.assertIn(needle, card)
        self.assertIn("<b>3 voix</b> · 2 organisations", card)
        self.assertIn("Comparer les 3 voix →", card)
        solo = ck.event_card(EN_ONLY, "en")
        self.assertIn("a single article, a single organization", solo)
        self.assertIn(">See the record →<", solo)

    def test_lead_headline_follows_the_interface_language(self):
        fr = ck.event_card(FIRE, "fr")
        en = ck.event_card(FIRE, "en")
        self.assertIn("Incendie dans un entrepôt", fr)
        self.assertIn("Warehouse fire in Limoilou leaves two people treated", en)
        self.assertIn("the first published in the interface language", en)
        self.assertIn("le premier publié (heure déclarée)", fr)


class FailSoftOnSkeletalViews(unittest.TestCase):
    """A thin or malformed view renders a thin page, never a traceback."""

    def test_events(self):
        cases = [
            {}, {"event_id": "ev-x"}, {"event_id": None, "members": None, "label": None}, {"members": [{}], "label": {"fr": "x"}},
            {"members": [{"item_id": "1", "published_at": "x"}], "independence": None, "why": None, "facts": [{}],
             "anchors": [{}, None, {"type": "official_item"}], "silence": [{}], "neighbours": [{"member": None}], "ledger": None, "words": [{}]},
            {"members": [{"item_id": "a", "language": "zz", "ownership_class": "x", "origin_class": 5, "published_at": "2026-10-06T10:00:00Z",
                          "title": 5, "excerpt": None}], "tier": "nonsense", "activity": 3, "seals": ["x", -1, None]},
        ]
        for case in cases:
            for lang in ("fr", "en"):
                for perm in (False, True):
                    ev = dict(case, permanent=perm)
                    page = ck.event_page(ev, lang)
                    ck.event_card(ev, lang)
                    self.assertEqual(parse(page).count("h1"), 1)

    def test_junk_entries_in_every_list_are_skipped(self):
        base = copy.deepcopy(BYLAW)
        fields = ("neighbours", "silence", "words", "facts", "language_pairs", "anchors", "members", "seals")
        for junk in (None, 5, "x", [], {}):
            for field in fields:
                for container in ([junk], [junk, None, 5], junk):
                    ev = copy.deepcopy(base)
                    if field == "members":
                        ev[field] = list(ev["members"]) + (container if isinstance(container, list) else [])
                    else:
                        ev[field] = container
                    for lang in ("fr", "en"):
                        for perm in (False, True):
                            page = ck.event_page(dict(ev, permanent=perm), lang)
                            ck.event_card(dict(ev, permanent=perm), lang)
                            self.assertEqual(parse(page).count("h1"), 1, (field, junk))
        nested = (("independence", [None]), ("independence", {"groups": [None, 5, "ab"], "count": "x"}),
                  ("independence", {"groups": 5}), ("independence", 5), ("why", {"shared": [None, 5]}),
                  ("why", {"shared": 5}), ("why", [1]), ("ledger", {"changes": [None]}), ("ledger", 5),
                  ("languages", 5), ("languages", [None, 5]),
                  ("facts", [{"slot": 5, "values": [None, {"institutions": 5}]}]), ("facts", [{"values": 5}]),
                  ("words", [{"forms": 5}, {"forms": [None], "label": None}]),
                  ("neighbours", [{"member": 5}, {"member": {"title": "t"}, "why": 5}]))
        for key, value in nested:
            ev = dict(copy.deepcopy(base), **{key: value})
            for lang in ("fr", "en"):
                for perm in (False, True):
                    ck.event_page(dict(ev, permanent=perm), lang)
                    ck.event_card(dict(ev, permanent=perm), lang)

    def test_card_with_mixed_or_missing_languages_and_no_languages_key(self):
        members = [{"item_id": "a", "language": "fr", "title": "t"}, {"item_id": "b", "title": "u"},
                   {"item_id": "c", "language": None}, {"item_id": "d", "language": 5}]
        for lang in ("fr", "en"):
            for ms in (members, members[:2], members[1:2]):
                html = ck.event_card({"event_id": "ev-x", "label": {"fr": "x", "en": "x"}, "members": ms}, lang)
                self.assertIn("<article", html)

    def test_editions_and_roadworks(self):
        editions = ({}, {"events": None}, {"events": [{}], "roster": [None, {}], "official": [None, {}], "seal": {"seq": "x"}, "suggestions": None, "clock": 5})
        lanes = (None, {}, {"rows": [None, {}]}, {"rows": [{"what": {"fr": "a"}, "direction": 5, "from": 5}], "total": "x"})
        for ed in editions:
            for rw in lanes:
                page = ck.edition_page(ed, "fr", roadworks=rw)
                self.assertEqual(parse(page).count("h1"), 1)
                self.assertIn("Instantané, pas un flux en direct", page)

    def test_an_event_without_timed_members_has_no_empty_timeline_panel(self):
        page = ck.event_page({"event_id": "ev-x", "label": {"fr": "x", "en": "x"}, "members": [{"item_id": "a"}]}, "fr")
        self.assertNotIn('id="tl-h"', page)


class DeterminismAndDemoSite(unittest.TestCase):
    def test_pages_are_byte_identical_across_runs_and_hash_seeds(self):
        again = ck.demo_pages()
        self.assertEqual(PAGES, again)
        script = "import sys,hashlib;sys.path.insert(0,sys.argv[1]);import composants;h=hashlib.sha256();[h.update((k+v).encode('utf-8')) for k,v in composants.demo_pages().items()];print(h.hexdigest())"
        digests = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            out = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(harness.SCRIPTS)], env=env, capture_output=True, text=True, check=True, timeout=120)
            digests.add(out.stdout.strip())
        self.assertEqual(len(digests), 1, digests)
        local = hashlib.sha256()
        for k, v in PAGES.items():
            local.update((k + v).encode("utf-8"))
        self.assertEqual(digests, {local.hexdigest()})

    def test_input_order_does_not_change_the_page(self):
        ev = copy.deepcopy(FIRE)
        shuffled = copy.deepcopy(FIRE)
        shuffled["members"].reverse()
        shuffled["neighbours"].reverse()
        shuffled["anchors"].reverse()
        self.assertEqual(ck.event_page(ev, "fr"), ck.event_page(shuffled, "fr"))

    def test_write_demo_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = ck.write_demo_site(root)
            self.assertEqual(written, sorted(written))
            self.assertIn("evenements.html", written)
            self.assertIn("en/evenements/ev-2b3c4d5e6f708192.html", written)
            self.assertIn("permanent/evenements/ev-2b3c4d5e6f708192.html", written)
            for rel in ("assets/evenements.css", "assets/evenements.js", "assets/fonts.css"):
                self.assertTrue((root / rel).is_file(), rel)
            self.assertTrue((root / "assets" / "fonts").is_dir())
            first = {rel: (root / rel).read_bytes() for rel in written}
            ck.write_demo_site(root)
            self.assertEqual(first, {rel: (root / rel).read_bytes() for rel in written})
            for rel in written:
                if rel.endswith(".html"):
                    for ref in re.findall(r'(?:href|src)="(/assets/[^"]+)"', (root / rel).read_text(encoding="utf-8")):
                        self.assertTrue((root / ref.lstrip("/")).is_file(), f"{rel} -> {ref}")

    def test_demo_data_is_invented(self):
        text = " ".join(str(v) for v in VIEWS["events"])
        for real in ("radio-canada", "ici.radio-canada", "lesoleil", "journaldequebec", "cbc.ca", "lapresse", "ledevoir"):
            self.assertNotIn(real, text.lower())
        self.assertTrue(all(m["url"].split("/")[2].endswith(".example") for e in VIEWS["events"] for m in e["members"] if m.get("url")))

    def test_demo_cli_usage(self):
        self.assertEqual(ck.main([]), 2)


class CssAndScript(unittest.TestCase):
    @staticmethod
    def _lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def _ratio(self, a, b):
        la, lb = sorted((self._lum(a), self._lum(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    def _themes(self):
        root = re.search(r":root \{(.*?)\n\}", CSS, re.S).group(1)
        dark = re.search(r'prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\}', CSS, re.S).group(1)
        tok = lambda s: dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", s))  # noqa: E731
        light = tok(root)
        night = dict(light)
        night.update(tok(dark))
        return light, night

    def test_text_contrast_meets_wcag_aa_in_both_themes(self):
        pairs = [("ink", "paper"), ("ink", "card"), ("ink2", "paper"), ("ink2", "card"), ("ink3", "paper"), ("ink3", "card"),
                 ("ink3", "accent-soft"), ("ink3", "warn-soft"), ("accent", "paper"), ("accent", "card"), ("accent", "accent-soft"),
                 ("accent-ink", "accent"), ("warn", "warn-soft"), ("ok", "ok-soft"), ("paper", "ink"), ("ink", "mark"),
                 ("ink", "accent-soft"), ("ink", "warn-soft"), ("ink2", "accent-soft"), ("ink2", "warn-soft"), ("warn", "card"), ("ok", "card")]
        for name, theme in zip(("light", "dark"), self._themes()):
            for fg, bg in pairs:
                self.assertGreaterEqual(self._ratio(theme[fg], theme[bg]), 4.5, f"{name}: {fg} on {bg}")

    def test_ownership_colours_are_visible_marks_in_both_themes(self):
        for name, theme in zip(("light", "dark"), self._themes()):
            for c in ("c-public", "c-coop", "c-private", "c-indep", "c-gov"):
                for bg in ("card", "paper"):
                    self.assertGreaterEqual(self._ratio(theme[c], theme[bg]), 3.0, f"{name}: {c} on {bg}")
            self.assertEqual(len({theme[c] for c in ("c-public", "c-coop", "c-private", "c-indep", "c-gov")}), 5)

    def test_every_ownership_class_has_a_colour_class_and_rule(self):
        for own, cls in ck.OWN_COLOR.items():
            self.assertIn(f".{cls} {{ --c: var(--{cls}); }}", CSS.replace("\n", " ").replace("} .", "}\n."), own)
        self.assertEqual(set(ck.OWN_COLOR), set(ck.OWNERSHIPS))

    def test_every_class_the_kit_emits_is_styled_or_a_known_hook(self):
        used = set()
        for page in PAGES.values():
            for _t, a in parse(page).tags:
                used.update(a.get("class", "").split())
        hooks = {"js-only", "mine", "words-panel", "roads", "disc", "rules-d", "why", "wrap", "end-p", "tl-a", "kind", "bt",
                 "lang", "origin", "own", "act", "certain", "probable", "possible", "origin-wire", "origin-own_reporting",
                 "origin-unknown", "origin-press_release", "act-developed", "note", "impact", "ledger", "act-quiet",
                 "mt-xs", "age-time"}
        css_classes = set(re.findall(r"\.([A-Za-z_][\w-]*)", CSS))
        missing = sorted(c for c in used if c not in css_classes and c not in hooks)
        self.assertEqual(missing, [])

    def test_design_laws_hold(self):
        low = CSS.lower()
        for banned in ("radial-gradient", "#f3f0e8", "#f4f1ea", "#7c3aed", "#6366f1", "#8b5cf6", "@import", "url(", "!important;}"):
            self.assertNotIn(banned, low)
        self.assertIn("prefers-reduced-motion: reduce", CSS)
        self.assertIn("prefers-color-scheme: dark", CSS)
        self.assertIn("prefers-contrast: more", CSS)
        self.assertIn("@media print", CSS)
        self.assertIn(":focus-visible", CSS)
        self.assertIn("outline: 3px solid var(--accent)", CSS)
        self.assertIn('"Newsreader"', CSS)
        self.assertIn('"Figtree"', CSS)
        self.assertNotIn("Inter", CSS)

    def test_touch_targets_are_44px(self):
        for selector in (".btn", ".seg-cur, .seg-link", ".word", ".sugg button", "details.disc > summary, details.why > summary",
                         ".crumb", ".chips-mine button", ".mine-input input", ".brand"):
            rule = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", CSS)
            self.assertTrue(rule, selector)
            self.assertRegex(rule.group(1), r"min-(height|width): 44px", selector)

    def test_no_inline_style_is_needed_so_no_unsafe_inline(self):
        self.assertNotIn("style-src", CSS)
        for name, page in PAGES.items():
            self.assertNotIn(" style=", page, name)

    def test_script_is_es2017_offline_and_defensive(self):
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "document.cookie", "eval(", "new Function", "innerHTML", "outerHTML",
                       "insertAdjacentHTML", "document.write", "import(", "?.", "??", "async ", "await ", "\\p{", "**", "BigInt", ".at(", "Object.fromEntries", "flatMap(", "matchAll("):
            self.assertNotIn(banned, JS, banned)
        self.assertIn("'use strict'", JS)
        self.assertIn("localStorage", JS)
        self.assertRegex(JS, r"try \{\s*localStorage\.setItem")
        self.assertRegex(JS, r"try \{\s*var raw = JSON\.parse\(localStorage\.getItem")
        self.assertIn("vigie.corridors.v1", JS)
        self.assertIn("prefers-reduced-motion", JS)
        self.assertNotIn(".style", JS)
        self.assertNotIn("setAttribute('style'", JS)

    def test_script_syntax(self):
        import shutil

        node = shutil.which("node")
        if not node:
            self.skipTest("node is required by verify.py and absent here")
        subprocess.run([node, "--check", str(ROOT / "public" / "assets" / "evenements.js")], check=True, timeout=60)


class WiringGaps(unittest.TestCase):
    """The gaps the wiring tranche closed in the kit (invented data only)."""

    def test_withdrawn_silence_is_said_never_no_linked_article(self):
        import takedown

        ev = dict(BYLAW, silence=[{"institution_name": "Radio Zorblax", "state": "withdrawn"},
                                  {"institution_name": "Le Plimbourgeois", "state": "no_linked_item"}])
        for lang in ("fr", "en"):
            panel = ck.silence_panel(ev, lang)
            self.assertIn(f"<b>Radio Zorblax</b><span>{ck.esc(i18n.t('sil.withdrawn', lang))}</span>", panel)
            self.assertEqual(panel.count(ck.esc(i18n.t("sil.no_linked_item", lang))), 1, "only the voice that has none")
        self.assertEqual(i18n.t("sil.withdrawn", "fr"), takedown.WITHDRAWN_LABEL_FR)
        self.assertIn("withdrawn", ck.SILENCE_STATES)

    def test_an_unknown_silence_state_is_not_established_never_no_linked_article(self):
        for state in ("", "banana", None):
            ev = dict(BYLAW, silence=[{"institution_name": "Radio Zorblax", "state": state}])
            panel = ck.silence_panel(ev, "fr")
            self.assertIn(i18n.t("sil.not_established", "fr"), panel, state)
            self.assertNotIn(i18n.t("sil.no_linked_item", "fr"), panel, state)

    def test_place_codes_are_worded_everywhere_they_appear(self):
        self.assertEqual(ck.place_name("elsewhere", "fr"), "Hors Québec")
        self.assertEqual(ck.place_name("elsewhere", "en"), "Outside Quebec")
        self.assertEqual(ck.place_name("unplaced", "fr"), "Lieu non établi")
        self.assertEqual(ck.place_name("unplaced", "en"), "Place not established")
        self.assertEqual(ck.place_name("zz-not-a-place", "fr"), "zz-not-a-place", "unknown: the code, never a guess")
        fact = {"slot": {"kind": "place", "unit": "place", "subject": "fire-building"},
                "values": [{"value": "elsewhere", "stated_by": ["a1b2c3d4e5f60001"], "institutions": ["x"]},
                           {"value": "limoilou", "stated_by": ["a1b2c3d4e5f60002"], "institutions": ["y"]}],
                "divergent": True}
        ev = dict(FIRE, facts=[fact])
        for lang, words in (("fr", ("Hors Québec", "Limoilou")), ("en", ("Outside Quebec", "Limoilou"))):
            panel = ck.numbers_panel(ev, lang)
            for w in words:
                self.assertIn(f"<code>{ck.esc(w)}</code>", panel)
            self.assertNotIn("<code>elsewhere</code>", panel)

    def test_unplaced_card_and_page_say_the_place_is_not_established(self):
        ev = dict(BYLAW, places=["unplaced"], place_label={"fr": "Lieu non établi", "en": "Place not established"})
        for lang in ("fr", "en"):
            self.assertIn(ck.esc(ck.place_name("unplaced", lang)), ck.event_card(ev, lang))
            self.assertIn(ck.esc(ck.place_name("unplaced", lang)), ck.event_page(ev, lang))

    def test_official_rows_outside_the_window_are_marked_and_the_fill_is_said(self):
        rows = [{"title": "Avis zorblaxien frais", "institution_name": "Ville Xénon", "published_at": "2026-09-22T10:00:00Z"},
                {"title": "Avis zorblaxien ancien", "institution_name": "Ville Xénon",
                 "published_at": "2026-09-10T10:00:00Z", "older": True}]
        for lang in ("fr", "en"):
            block = ck.official_block(rows, lang, 2, {"city": 1, "province": 0, "other": 0}, fresh=1, older=1)
            self.assertEqual(block.count(ck.esc(i18n.t("off.older", lang, h="72"))), 1)
            self.assertIn("data-off-fill", block)
            self.assertIn(ck.esc(i18n.tn("off.fill", 1, lang, h="72")), block)
            self.assertNotIn("data-off-cap", block, "everything that qualifies is shown")
        fresh_only = ck.official_block(rows[:1], "fr", 1, None)
        self.assertNotIn("data-off-fill", fresh_only)
        self.assertNotIn(ck.esc(i18n.t("off.older", "fr", h="72")), fresh_only)

    def test_the_live_front_door_links_the_river_and_anchors_the_roadworks(self):
        ed = dict(VIEWS["edition"], river="/le-point.html", roads_id="travaux")
        for lang in ("fr", "en"):
            page = ck.edition_page(ed, lang, roadworks=VIEWS["roadworks"], fr_path="/")
            dom = parse(page)
            river = [a for a in dom.find("a", href="/le-point.html")]
            self.assertEqual(len(river), 1, lang)
            if lang == "en":
                self.assertEqual(river[0].get("lang"), "fr", "an English page flags the French-only brief")
            self.assertIn("travaux", dom.ids)
            self.assertIn('<section class="card panel roads" id="travaux"', page)
            self.assertIn('<link rel="canonical" href="https://vigieqc.com/' + ("en/" if lang == "en" else "") + '">', page)
        plain = ck.edition_page(VIEWS["edition"], "fr", roadworks=VIEWS["roadworks"])
        self.assertNotIn("data-river", plain, "no river link unless the live front door asks for it")
        self.assertNotIn('id="travaux"', plain)
        self.assertEqual(ck.roadworks_block(VIEWS["roadworks"], "fr", anchor='x" onload="y'),
                         ck.roadworks_block(VIEWS["roadworks"], "fr", anchor="xonloady"), "the id is sanitised")


if __name__ == "__main__":
    unittest.main()
