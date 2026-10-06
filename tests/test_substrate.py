"""The machine substrate and the affiche: same store, attribution intact, no rewriting."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import affiche
import events
import registre
import stage_public
import substrate
import takedown
import test_events as TE
from test_registre import history, issue, payload

NOW = "2026-09-10T14:00:00+00:00"
GOLDEN = Path(__file__).resolve().parent / "golden"
ROOT = Path(__file__).resolve().parents[1]


def story(uid, *, title="Un titre <em>verbatim</em> & fidèle", geo="quebec-city", summary="Résumé de l’éditeur.",
          published="2026-09-10T09:00:00+00:00", area_hint=""):
    return {
        "id": uid, "title": title + (" " + area_hint if area_hint else ""), "url": f"https://presse.example.com/{uid}",
        "source_id": "le-soleil", "source_name": "Le Soleil", "summary": summary, "published_at": published,
        "enrich": {"geo": {"geo": geo}, "topics": [{"topic": "transport"}]},
    }


def run():
    return {"fetched_at": "2026-09-10T13:30:00+00:00", "enabled_rss": ["le-soleil", "ville-quebec"],
            "results": [{"source_id": "le-soleil", "ok": True}, {"source_id": "ville-quebec", "ok": True}]}


def state_with_edition(ledger=None):
    p = payload([issue("a")], ledger=ledger)
    state = registre.empty_state()
    for iid, meta in registre.institution_names(p).items():
        state["names"][iid] = meta
    state, _ = registre.seal_edition(state, registre.edition_record(p, "2026-09-10T13:30:00+00:00"))
    return state, p


class MarkdownTwin(unittest.TestCase):
    def test_twin_keeps_titles_verbatim_with_publisher_and_url(self):
        state, p = state_with_edition()
        rows = substrate.story_rows([story("s1")], registre.parse_ts(NOW))
        md = substrate.render_markdown(rows, p["issues"], p["change_ledger"], None, state, {"at": NOW, "ok": 2, "total": 2})
        self.assertIn("Un titre verbatim & fidèle", md.replace("\\&", "&"))
        self.assertIn("<https://presse.example.com/s1>", md)
        self.assertIn("Le Soleil", md)
        self.assertIn("Dans ce dossier (2)", md)
        self.assertIn("Absents de ce dossier (1)", md)
        # A dossier-scoped list must never be labelled as a collection-wide silence.
        self.assertNotIn("N’ont pas parlé dans cette collecte", md)
        self.assertIn(state["seals"][0]["root"], md)
        self.assertNotIn("<em>", md)  # markup stripped, words kept

    def test_twin_without_stories_or_dossiers_says_so(self):
        md = substrate.render_markdown([], [], {}, None, registre.empty_state(), {})
        self.assertIn("Aucun article récent", md)
        self.assertIn("Aucun dossier", md)


class Delta(unittest.TestCase):
    def test_delta_is_cursor_addressed_and_attributed(self):
        ledger = {"has_previous": True, "new": [{"issue_id": "a", "question": "Le tramway"}], "developed": [], "quiet": [
            {"issue_id": "gone", "question": "Ancien dossier", "geo_focus": ["quebec-city"]}]}
        state, p = state_with_edition(ledger)
        delta = substrate.build_delta(p["issues"], ledger, {"fetched_at": "2026-09-10T13:00:00+00:00", "events": [
            {"event_id": "e1", "road_names": ["Rue Saint-Jean"], "vehicle_impact": "all-lanes-closed"}]},
            state, {"at": NOW, "ok": 2, "total": 2, "partial": False})
        self.assertEqual(delta["method"], substrate.METHOD)
        self.assertEqual(delta["cursor"], state["seals"][0]["root"])
        self.assertEqual(delta["previous_cursor"], "")
        self.assertEqual([d["issue_id"] for d in delta["dossiers"]["new"]], ["a"])
        self.assertEqual(delta["dossiers"]["quiet"][0]["question"], "Ancien dossier")
        items = delta["dossiers"]["new"][0]["items"]
        self.assertEqual({i["source_name"] for i in items}, {"Le Soleil", "Ville de Québec"})
        self.assertTrue(all(i["url"].startswith("https://exemple.test/") for i in items))
        self.assertEqual({i["title"] for i in items}, {"Titre le-soleil", "Titre ville-quebec"})  # markup stripped, never rewritten
        # state_with_edition seals no collection facts (schema 1), so the honest
        # answer is "not established" -- never "silent".
        self.assertEqual([s["institution_id"] for s in delta["institutions"]["not_established"]],
                         ["gouv-quebec", "cbc"])  # officials first
        self.assertEqual([s["institution_id"] for s in delta["institutions"]["spoke"]],
                         ["ville-quebec", "le-soleil"])  # officials first
        self.assertEqual(delta["institutions"]["published"], [])
        self.assertNotIn("silent", delta["institutions"])
        self.assertEqual(delta["roadworks"]["most_restrictive"][0]["impact_label"], "Toutes les voies fermées")
        self.assertTrue(any("Never infer that an institution was silent" in r for r in delta["rules_for_agents"]))
        json.dumps(delta)  # serialisable

    def test_delta_reports_published_institutions_with_their_item_counts(self):
        state, p = state_with_edition()
        coll = {"gouv-quebec": {"items": 10, "feeds_ok": 1, "feeds_total": 1},
                "cbc": {"items": 0, "feeds_ok": 0, "feeds_total": 2},
                "ville-quebec": {"items": 9, "feeds_ok": 1, "feeds_total": 1},
                "le-soleil": {"items": 40, "feeds_ok": 1, "feeds_total": 1}}
        state, _ = registre.seal_edition(
            state, registre.edition_record(p, "2026-09-11T13:30:00+00:00", coll))
        delta = substrate.build_delta(p["issues"], {}, None, state, {"at": NOW})
        published = {s["institution_id"]: s for s in delta["institutions"]["published"]}
        self.assertEqual(published["gouv-quebec"]["items_collected"], 10)
        self.assertEqual([s["institution_id"] for s in delta["institutions"]["collection_gap"]], ["cbc"])
        self.assertEqual(delta["institutions"]["collection_gap"][0]["collection_gap_streak"], 1)
        self.assertEqual(delta["institutions"]["not_established"], [])
        self.assertIsNotNone(delta["correction"])
        self.assertEqual(delta["correction"]["affects_seal_min"], 1)

    def test_delta_without_previous_has_empty_buckets_but_full_list(self):
        state, p = state_with_edition()
        delta = substrate.build_delta(p["issues"], p["change_ledger"], None, state, {})
        self.assertFalse(delta["dossiers"]["has_previous"])
        self.assertEqual(delta["dossiers"]["new"], [])
        self.assertEqual(len(delta["dossiers"]["all"]), 1)
        self.assertIsNone(delta["roadworks"])


class LlmsTxt(unittest.TestCase):
    def test_llms_txt_follows_v2_shape_and_maps_the_record(self):
        state, _ = state_with_edition()
        text = substrate.render_llms_txt(state, {"at": NOW})
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Vigie")
        self.assertTrue(lines[2].startswith("> "))
        for path in ("/index.html.md", "/delta/latest.json", "/registre/chain.json", "/registre/checkpoint.txt",
                     "/methode/sources.html", "/methode/legal.html", "/methode/registre.html"):
            self.assertIn(f"https://vigieqc.com{path}", text)
        self.assertIn("## Optional", text)
        self.assertLess(len(text.encode("utf-8")), 10_000)


class Emit(unittest.TestCase):
    def test_emit_writes_three_files_deterministically(self):
        state, p = state_with_edition()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = dict(out_llms=root / "llms.txt", out_md=root / "index.html.md", out_delta=root / "delta" / "latest.json",
                         out_delta_v2=root / "delta" / "v2" / "latest.json")
            kw = dict(run=run(), events_input=None, **paths)
            substrate.emit([story("s1")], p["issues"], p["change_ledger"], None, state, NOW, **kw)
            first = {k: v.read_bytes() for k, v in paths.items() if v.exists()}
            self.assertNotIn("out_delta_v2", first)
            substrate.emit([story("s1")], p["issues"], p["change_ledger"], None, state, NOW, **kw)
            for k, v in paths.items():
                if k in first:
                    self.assertEqual(v.read_bytes(), first[k], k)
            self.assertEqual(json.loads(paths["out_delta"].read_text(encoding="utf-8"))["collection"]["feeds_ok"], 2)


class Affiche(unittest.TestCase):
    def test_sheet_groups_by_quartier_and_carries_the_seal(self):
        state, p = state_with_edition()
        ranked = [story("s1", area_hint="à Limoilou"), story("s2", area_hint="à Beauport"), story("s3", geo="quebec")]
        rw = {"fetched_at": "2026-09-10T13:00:00+00:00", "events": [
            {"event_id": "e1", "road_names": ["Rue Saint-Jean"], "vehicle_impact": "all-lanes-closed",
             "end_date": "2026-09-20T00:00:00+00:00", "end_date_accuracy": "estimated"}]}
        page = affiche.render_affiche(ranked, p["issues"], rw, state, NOW, run())
        self.assertIn("La Cité-Limoilou", page)
        self.assertIn("Beauport", page)
        self.assertIn("Rue Saint-Jean", page)
        self.assertIn("Toutes les voies fermées", page)
        self.assertIn("(estimé)", page)
        self.assertIn(state["seals"][0]["root"][:16], page)
        self.assertIn("Un titre verbatim &amp; fidèle", page)  # publisher markup stripped, words kept, escaped
        self.assertNotIn("<script", page)           # paper needs no JavaScript
        self.assertIn('href="/assets/affiche.css"', page)
        self.assertIn("Les voix de cette édition", page)
        # A wall sheet must never name an institution as silent on our clustering.
        self.assertIn("n’est pas muette", page)
        self.assertNotIn("n’a pas parlé", page)

    def test_sheet_with_nothing_still_prints(self):
        page = affiche.render_affiche([], [], None, registre.empty_state(), NOW, {})
        self.assertIn("Aucun article local", page)
        self.assertIn("Édition non scellée", page)
        self.assertIn("flux officiel indisponible", page)

    def test_affiche_and_registre_pass_release_navigation_validation(self):
        state, p = state_with_edition()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "affiche.html").write_text(affiche.render_affiche([story("s1")], p["issues"], None, state, NOW, run()), encoding="utf-8")
            (root / "registre.html").write_text(registre.render_registre_html(state), encoding="utf-8")
            (root / "assets").mkdir()
            (root / "registre").mkdir()
            (root / "delta").mkdir()
            for name in ("index.html", "assets/brief.css", "assets/fonts.css", "assets/affiche.css", "favicon.svg", "llms.txt",
                         "registre/chain.json", "registre/checkpoint.txt", "registre/institutions.json", "registre/travaux.json",
                         "delta/latest.json", *stage_public.METHODS):
                (root / name).write_text("placeholder", encoding="utf-8")
            (root / "partir.html").write_text("placeholder", encoding="utf-8")
            (root / "memoire.html").write_text("placeholder", encoding="utf-8")
            (root / "memoire").mkdir()
            (root / "memoire" / "1.html").write_text("placeholder", encoding="utf-8")
            (root / "methode").mkdir(exist_ok=True)
            for name in (*stage_public.METHOD_PAGES, "index"):
                (root / "methode" / f"{name}.html").write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


class Staging(unittest.TestCase):
    def test_markdown_twin_is_a_publishable_asset_and_optional_pages_enter_the_sitemap(self):
        self.assertIn(".md", stage_public.ASSET_EXTENSIONS)
        self.assertIn("REGISTRE.md", stage_public.METHODS)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<p>x</p>", encoding="utf-8")
            self.assertNotIn("/registre.html", stage_public.sitemap_xml(root))
            (root / "registre.html").write_text("<p>r</p>", encoding="utf-8")
            (root / "affiche.html").write_text("<p>a</p>", encoding="utf-8")
            (root / "methode").mkdir()
            (root / "methode" / "registre.html").write_text("<p>m</p>", encoding="utf-8")
            xml = stage_public.sitemap_xml(root)
            self.assertIn("https://vigieqc.com/registre.html", xml)
            self.assertIn("https://vigieqc.com/affiche.html", xml)
            self.assertIn("https://vigieqc.com/methode/registre.html", xml)


# --------------------------------------------------------------------------- #
# delta-v2: events (every publisher string below is invented by test_events)
# --------------------------------------------------------------------------- #
ID16 = re.compile(r"^ev-[0-9a-f]{16}$")
CODE = re.compile(r"^[a-z0-9-]{2,48}$")
ORIGINS = {"official", "wire", "press_release", "own_reporting", "unknown"}
V2_TOP = {"method", "site", "attribution", "edition", "previous_edition", "cursor", "previous_cursor", "checkpoint",
          "collection", "events_edition", "events_edition_is_cursor_edition", "event_count", "by_activity",
          "events", "links", "rules_for_agents"}
V2_EVENT = {"event_id", "type", "family", "places", "label", "activity", "window_state", "tier", "grouping",
            "born_edition", "last_edition", "counts", "origin_classes", "institutions", "languages", "anchors",
            "lineage", "seals", "pages", "members"}
V2_COUNTS = {"members", "members_in_edition", "institutions", "reporting_origins", "origins", "declarations",
             "languages", "language_pairs"}
V2_MEMBER_CODES = {"item_id", "institution", "language", "origin_class", "published_at", "first_seen", "in_edition"}
V2_MEMBER_TEXT = {"publisher", "author", "title", "url"}


def check_v2(doc) -> list[str]:
    """A hand-written checker for delta-v2 (stdlib only): required keys, exact key
    sets, enums, patterns, and the invariants a machine reader relies on."""
    errs: list[str] = []

    def keys(obj, allowed, required, where):
        if not isinstance(obj, dict):
            errs.append(f"{where}: not an object")
            return False
        for k in sorted(set(obj) - allowed):
            errs.append(f"{where}: unexpected key {k}")
        for k in sorted(required - set(obj)):
            errs.append(f"{where}: missing key {k}")
        return True

    if not keys(doc, V2_TOP, V2_TOP, "delta"):
        return errs
    if doc["method"] != substrate.METHOD_V2:
        errs.append("delta: method")
    if not isinstance(doc["events"], list) or doc["event_count"] != len(doc["events"]):
        errs.append("delta: event_count")
    ids = [e.get("event_id") for e in doc["events"] if isinstance(e, dict)]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        errs.append("delta: events not unique and sorted by id")
    for k in ("new", "developed", "quiet"):
        if not isinstance(doc["by_activity"].get(k), list):
            errs.append(f"delta: by_activity.{k}")
    for e in doc["events"]:
        eid = e.get("event_id") if isinstance(e, dict) else None
        where = f"event {eid}"
        if not keys(e, V2_EVENT, V2_EVENT, where):
            continue
        if not (isinstance(eid, str) and ID16.match(eid)):
            errs.append(f"{where}: event_id")
        if not (isinstance(e["type"], str) and CODE.match(e["type"])):
            errs.append(f"{where}: type code")
        if not e["places"] or not all(isinstance(x, str) and CODE.match(x) for x in e["places"]):
            errs.append(f"{where}: places")
        if keys(e["label"], {"fr", "en"}, {"fr", "en"}, where + " label"):
            if not (e["label"]["fr"] and e["label"]["en"]):
                errs.append(f"{where}: empty label")
        if e["activity"] not in ("new", "developed", "quiet"):
            errs.append(f"{where}: activity")
        if e["window_state"] not in ("in_window", "out_of_window"):
            errs.append(f"{where}: window_state")
        if e["tier"] not in (None, "certain", "probable"):
            errs.append(f"{where}: tier")
        if e["grouping"] != "automatic":
            errs.append(f"{where}: grouping")
        counts = e["counts"]
        if keys(counts, V2_COUNTS, V2_COUNTS, where + " counts"):
            if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts.values()):
                errs.append(f"{where}: counts not non-negative integers")
            elif counts["members"] != len(e["members"]) or counts["members"] < 1:
                errs.append(f"{where}: counts.members")
            elif counts["institutions"] != len(e["institutions"]) or counts["languages"] != len(e["languages"]):
                errs.append(f"{where}: counts disagree with lists")
            elif counts["origins"] < 1 or counts["reporting_origins"] > counts["origins"]:
                errs.append(f"{where}: origins")
        if e["tier"] is None and len(e["members"]) > 1:
            errs.append(f"{where}: grouped event without a tier")
        if not set(e["languages"]) <= {"fr", "en"} or e["languages"] != sorted(e["languages"]):
            errs.append(f"{where}: languages")
        if e["institutions"] != sorted(set(e["institutions"])):
            errs.append(f"{where}: institutions")
        if not set(e["origin_classes"]) <= ORIGINS or sum(e["origin_classes"].values()) != len(e["members"]):
            errs.append(f"{where}: origin_classes")
        for a in e["anchors"]:
            if not keys(a, {"type", "ref"}, {"type", "ref"}, where + " anchor"):
                continue
            if a["type"] not in events.ANCHOR_TYPES:
                errs.append(f"{where}: anchor type")
        keys(e["lineage"], {"merged_into", "absorbed", "detached"}, {"merged_into", "absorbed", "detached"}, where + " lineage")
        keys(e["pages"], {"fr", "en"}, {"fr", "en"}, where + " pages")
        if not all(isinstance(x, int) and x >= 1 for x in e["seals"]):
            errs.append(f"{where}: seals")
        if doc["by_activity"].get(e["activity"]) is not None and eid not in doc["by_activity"][e["activity"]]:
            errs.append(f"{where}: missing from by_activity")
        for m in e["members"]:
            if not isinstance(m, dict):
                errs.append(f"{where}: member not an object")
                continue
            has_text = bool(V2_MEMBER_TEXT & set(m))
            allowed = V2_MEMBER_CODES | (V2_MEMBER_TEXT if has_text else set())
            required = V2_MEMBER_CODES | ({"publisher", "title", "url"} if has_text else set())
            if not keys(m, allowed, required, f"{where} member"):
                continue
            if m["origin_class"] not in ORIGINS or m["language"] not in ("fr", "en"):
                errs.append(f"{where}: member codes")
            if m["in_edition"] is not has_text:
                errs.append(f"{where}: text on a member that is not in the edition (or the reverse)")
            if has_text:
                if not str(m["url"]).startswith(("http://", "https://")):
                    errs.append(f"{where}: member url")
                if "author" in m and not m["author"]:
                    errs.append(f"{where}: empty author")
    return errs


def keys_anywhere(node, banned):
    """Every dict key in a JSON document, recursively, that is in `banned`."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in banned:
                found.add(k)
            found |= keys_anywhere(v, banned)
    elif isinstance(node, list):
        for v in node:
            found |= keys_anywhere(v, banned)
    return found


def v2_inputs(edition=None, reg=None):
    """(view, texts, reg) as substrate reads them: the view round-tripped through JSON."""
    edition = edition or TE.E1
    reg = reg or TE.registry()
    _store, view, _ops = events.build(None, edition, TE.registry())
    view = json.loads(json.dumps(view))
    texts = {c["id"]: {**c, "source_name": TE.registry().by_id[c["source_id"]]["name"]}
             for c in edition["candidates"]}
    return view, texts, reg


def v2_doc(edition=None, reg=None, state=None):
    view, texts, reg = v2_inputs(edition, reg)
    state = state or state_with_edition()[0]
    return substrate.build_delta_v2(view, texts, reg, state, {"at": NOW, "ok": 2, "total": 2})


def rules_for(kind, value):
    return takedown.Rules([{"id": "td-t", "kind": kind, "value": value, "requested_at": "2026-09-20",
                            "by": "Éditeur Inventé", "status": "active"}])


class DeltaV1Frozen(unittest.TestCase):
    """delta-v1.1, llms.txt and the Markdown twin stay byte-identical without the event layer."""

    def fixture(self):
        ledger = {"has_previous": True, "new": [{"issue_id": "a", "question": "Le tramway"}], "developed": [], "quiet": [
            {"issue_id": "gone", "question": "Ancien dossier", "geo_focus": ["quebec-city"]}]}
        state, p = state_with_edition(ledger)
        rw = {"fetched_at": "2026-09-10T13:00:00+00:00", "events": [
            {"event_id": "e1", "road_names": ["Rue Saint-Jean"], "vehicle_impact": "all-lanes-closed"}]}
        # What emit derives from run(): the collection clock of the run, not the render clock.
        return ledger, state, p, rw, {"at": "2026-09-10T13:30:00+00:00", "ok": 2, "total": 2, "partial": False}

    def test_build_delta_matches_the_golden_bytes(self):
        ledger, state, p, rw, status = self.fixture()
        delta = substrate.build_delta(p["issues"], ledger, rw, state, status)
        golden = (GOLDEN / "delta_v1.json").read_text(encoding="utf-8")
        self.assertEqual(json.dumps(delta, ensure_ascii=False, indent=2), golden)
        self.assertEqual(delta["method"], "delta-v1.1 edition-cursor")
        self.assertNotIn("deprecation", delta)

    def test_llms_and_markdown_twin_match_their_golden_without_events(self):
        ledger, state, p, rw, status = self.fixture()
        self.assertEqual(substrate.render_llms_txt(state, {"at": NOW}), (GOLDEN / "llms_no_events.txt").read_text(encoding="utf-8"))
        rows = substrate.story_rows([story("s1")], registre.parse_ts(NOW))
        md = substrate.render_markdown(rows, p["issues"], ledger, rw, state, status)
        self.assertEqual(md, (GOLDEN / "index_twin.golden").read_text(encoding="utf-8"))
        self.assertNotIn("evenements", md)

    def test_emitted_v1_file_is_the_golden_when_there_are_no_events(self):
        ledger, state, p, rw, _ = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            substrate.emit([story("s1")], p["issues"], ledger, rw, state, NOW, run=run(), events_input=None,
                           out_llms=root / "llms.txt", out_md=root / "i.md", out_delta=root / "d.json",
                           out_delta_v2=root / "v2" / "latest.json")
            self.assertEqual((root / "d.json").read_bytes().replace(b"\r\n", b"\n"),
                             (GOLDEN / "delta_v1.json").read_bytes())
            self.assertFalse((root / "v2" / "latest.json").exists())

    def test_with_events_v1_only_gains_the_deprecation_notice(self):
        ledger, state, p, rw, _ = self.fixture()
        view, texts, reg = v2_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            substrate.emit([story("s1")], p["issues"], ledger, rw, state, NOW, run=run(),
                           events_input={"view": view, "texts": texts, "reg": reg},
                           out_llms=root / "llms.txt", out_md=root / "i.md", out_delta=root / "d.json",
                           out_delta_v2=root / "v2" / "latest.json")
            v1 = json.loads((root / "d.json").read_text(encoding="utf-8"))
            golden = json.loads((GOLDEN / "delta_v1.json").read_text(encoding="utf-8"))
            dep = v1.pop("deprecation")
            self.assertEqual(v1, golden)
            self.assertEqual(dep["status"], "deprecated")
            self.assertEqual(dep["successor"], "https://vigieqc.com/delta/v2/latest.json")
            self.assertEqual((root / "i.md").read_text(encoding="utf-8").replace("\r\n", "\n"),
                             (GOLDEN / "index_twin.golden").read_text(encoding="utf-8"))
            self.assertEqual(check_v2(json.loads((root / "v2" / "latest.json").read_text(encoding="utf-8"))), [])

    def test_sunset_is_ninety_days_after_the_first_edition_clock_at_or_after_the_announcement(self):
        early = {"seals": [{"edition": "2026-09-10T13:30:00+00:00"}]}
        dep = substrate.v1_deprecation(early)
        self.assertEqual((dep["since"], dep["sunset"]), ("2026-10-06", "2027-01-04"))
        later = {"seals": [{"edition": "2026-09-10T13:30:00+00:00"}, {"edition": "2026-10-07T06:03:00+00:00"},
                           {"edition": "2026-10-08T00:03:00+00:00"}]}
        dep = substrate.v1_deprecation(later)
        self.assertEqual((dep["since"], dep["sunset"]), ("2026-10-07", "2027-01-05"))
        # A newer edition never moves the date: it is a fact of the chain, not of the render.
        newer = {"seals": later["seals"] + [{"edition": "2026-12-30T00:03:00+00:00"}]}
        self.assertEqual(substrate.v1_deprecation(newer), dep)
        self.assertEqual(substrate.v1_deprecation({}), substrate.v1_deprecation(early))


class DeltaV2(unittest.TestCase):
    def test_schema_cursor_and_labels(self):
        state, _ = state_with_edition()
        doc = v2_doc(state=state)
        self.assertEqual(check_v2(doc), [])
        self.assertEqual(doc["method"], "delta-v2 edition-cursor")
        self.assertEqual(doc["cursor"], state["seals"][0]["root"])
        self.assertEqual(doc["previous_cursor"], "")
        self.assertEqual(doc["events_edition"], TE.E1["normalized_at"])
        self.assertFalse(doc["events_edition_is_cursor_edition"])  # the fixture seal is another clock
        self.assertGreaterEqual(doc["event_count"], 2)
        fire = next(e for e in doc["events"] if e["event_id"] == TE.FIRE_ID)
        self.assertTrue(fire["label"]["fr"] and fire["label"]["en"])
        self.assertEqual(fire["languages"], ["en", "fr"])
        self.assertEqual(fire["counts"]["members"], 3)
        self.assertEqual(fire["counts"]["institutions"], 3)
        self.assertEqual(fire["pages"]["en"], f"https://vigieqc.com/en/evenements/{TE.FIRE_ID}.html")
        self.assertEqual(fire["grouping"], "automatic")
        self.assertIn(fire["tier"], ("certain", "probable"))
        json.dumps(doc)

    def test_the_checker_catches_defects(self):
        doc = v2_doc()
        bad = copy.deepcopy(doc)
        bad["events"][0]["members"][0]["summary"] = "an excerpt"
        self.assertTrue(any("unexpected key summary" in e for e in check_v2(bad)))
        bad = copy.deepcopy(doc)
        bad["events"][0]["activity"] = "resolved"
        self.assertTrue(any("activity" in e for e in check_v2(bad)))
        bad = copy.deepcopy(doc)
        bad["events"][0]["counts"]["members"] += 1
        self.assertTrue(check_v2(bad))
        bad = copy.deepcopy(doc)
        bad["events"][0]["anchors"] = [{"type": "roadwork", "ref": "x", "rule": "r"}]
        self.assertTrue(check_v2(bad))
        bad = copy.deepcopy(doc)
        del bad["cursor"]
        self.assertTrue(check_v2(bad))

    def test_members_carry_publisher_author_title_url_and_never_an_excerpt(self):
        doc = v2_doc()
        text = json.dumps(doc, ensure_ascii=False)
        fire = next(e for e in doc["events"] if e["event_id"] == TE.FIRE_ID)
        by_id = {m["item_id"]: m for m in fire["members"]}
        f1 = by_id[TE.F1["id"]]
        self.assertEqual(f1["title"], TE.F1["title"])                       # verbatim
        self.assertEqual(f1["author"], "Odile Pétronille-Quartz")
        self.assertEqual(f1["publisher"], "Alpha Québec")
        self.assertEqual(f1["url"], TE.F1["url"])
        self.assertEqual(keys_anywhere(doc, {"summary", "excerpt", "excerpts", "description", "quote", "facts", "body"}), set())
        for candidate in TE.E1["candidates"] + TE.E2["candidates"]:
            if candidate.get("summary"):
                self.assertNotIn(candidate["summary"], text)
        # Nothing from the publisher text of an item the collection did not take in.
        self.assertNotIn(TE.CUT["title"], text)

    def test_a_withdrawn_voice_is_absent_and_uncounted_even_when_the_view_predates_the_takedown(self):
        view, texts, _ = v2_inputs()
        state = state_with_edition()[0]
        before = substrate.build_delta_v2(view, texts, TE.registry(), state, {})
        fire0 = next(e for e in before["events"] if e["event_id"] == TE.FIRE_ID)
        self.assertIn("delta", fire0["institutions"])
        reg = TE.registry(rules_for("url", takedown._canon(TE.F2["url"])))
        after = substrate.build_delta_v2(view, texts, reg, state, {})
        self.assertEqual(check_v2(after), [])
        fire = next(e for e in after["events"] if e["event_id"] == TE.FIRE_ID)
        self.assertNotIn(TE.F2["id"], {m["item_id"] for m in fire["members"]})
        self.assertNotIn("delta", fire["institutions"])
        self.assertEqual(fire["counts"]["members"], fire0["counts"]["members"] - 1)
        self.assertEqual(fire["counts"]["institutions"], fire0["counts"]["institutions"] - 1)
        self.assertLess(fire["counts"]["origins"], fire0["counts"]["origins"])
        text = json.dumps(after, ensure_ascii=False)
        for needle in (TE.F2["title"], TE.F2["url"], "Barnabé Quillon-Ruisseau", TE.F2["id"]):
            self.assertNotIn(needle, text)

    def test_a_withdrawn_source_or_domain_removes_every_trace_and_an_emptied_event(self):
        view, texts, _ = v2_inputs()
        state = state_with_edition()[0]
        for kind, value in (("source", "delta"), ("host", "delta.example.org")):
            doc = substrate.build_delta_v2(view, texts, TE.registry(rules_for(kind, value)), state, {})
            self.assertEqual(check_v2(doc), [], kind)
            text = json.dumps(doc, ensure_ascii=False)
            for iid in (TE.F2["id"], TE.X1["id"]):
                self.assertNotIn(iid, text, kind)
            self.assertNotIn("Delta Quotidien", text, kind)
            self.assertNotIn("delta.example.org", text, kind)
        # An event whose every member is withdrawn disappears altogether.
        doc = substrate.build_delta_v2(view, texts, TE.registry(rules_for("source", "beta-qc")), state, {})
        self.assertNotIn(TE.STRIKE_ID, [e["event_id"] for e in doc["events"]])

    def test_a_withdrawn_official_item_leaves_no_anchor_pointer(self):
        view, texts, _ = v2_inputs()
        official = [e for e in view["events"] if any(a["type"] == "official_item" for a in e["anchors"])]
        self.assertTrue(official, "the invented city feed anchors its own event")
        a = next(a for a in official[0]["anchors"] if a["type"] == "official_item")
        reg = TE.registry(rules_for("url", takedown._canon(texts[a["ref"]]["url"])))
        doc = substrate.build_delta_v2(view, texts, reg, state_with_edition()[0], {})
        self.assertNotIn(a["ref"], json.dumps(doc))

    def test_members_outside_the_edition_or_from_a_cut_source_carry_codes_only(self):
        # E3: the founding items have left the feeds, so the fire's early members have no current text.
        stores = TE.build_all()[0]
        _s, view3, _o = events.build(stores[1], TE.E3, TE.registry())
        view3 = json.loads(json.dumps(view3))
        texts3 = {c["id"]: c for c in TE.E3["candidates"]}
        doc = substrate.build_delta_v2(view3, texts3, TE.registry(), state_with_edition()[0], {})
        self.assertEqual(check_v2(doc), [])
        fire = next(e for e in doc["events"] if e["event_id"] == TE.FIRE_ID)
        old = [m for m in fire["members"] if not m["in_edition"]]
        self.assertTrue(old)
        self.assertTrue(all(not (V2_MEMBER_TEXT & set(m)) for m in old))
        self.assertGreater(fire["counts"]["members"], fire["counts"]["members_in_edition"])
        self.assertNotIn(TE.F1["title"], json.dumps(doc, ensure_ascii=False))
        # A source cut after the build: its current member keeps its codes and loses its title and URL.
        view, texts, _ = v2_inputs()
        cut = [dict(s) for s in TE.SOURCES]
        for rec in cut:
            if rec["id"] == "alpha-qc":
                rec["enabled"] = False
        doc = substrate.build_delta_v2(view, texts, events.Registry(cut), state_with_edition()[0], {})
        self.assertEqual(check_v2(doc), [])
        fire = next(e for e in doc["events"] if e["event_id"] == TE.FIRE_ID)
        f1 = next(m for m in fire["members"] if m["item_id"] == TE.F1["id"])
        self.assertFalse(f1["in_edition"])
        self.assertNotIn(TE.F1["url"], json.dumps(doc))

    def test_deterministic_and_independent_of_the_input_order(self):
        view, texts, reg = v2_inputs()
        state = state_with_edition()[0]
        a = json.dumps(substrate.build_delta_v2(view, texts, reg, state, {}), ensure_ascii=False)
        shuffled = copy.deepcopy(view)
        shuffled["events"] = list(reversed(shuffled["events"]))
        b = json.dumps(substrate.build_delta_v2(shuffled, dict(reversed(list(texts.items()))), reg, state, {}), ensure_ascii=False)
        self.assertEqual(a, b)

    def test_bytes_do_not_depend_on_pythonhashseed(self):
        code = (
            "import sys, json, hashlib\n"
            f"sys.path.insert(0, {str(ROOT / 'tests')!r}); sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import test_substrate as T\n"
            "print(hashlib.sha256(json.dumps(T.v2_doc(), ensure_ascii=False).encode()).hexdigest())\n"
        )
        digests = set()
        for seed in ("0", "1", "4242"):
            out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, text=True,
                                 env={**os.environ, "PYTHONHASHSEED": seed}, cwd=str(ROOT), timeout=300)
            self.assertEqual(out.returncode, 0, out.stderr[-500:])
            digests.add(out.stdout.strip().splitlines()[-1])
        self.assertEqual(len(digests), 1)


class EventsAbsent(unittest.TestCase):
    def test_loader_reports_why_there_is_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            got, why = substrate.load_events_input(root / "none.json", root / "e.json", reg=TE.registry())
            self.assertIsNone(got)
            self.assertIn("absent", why)
            (root / "v.json").write_text(json.dumps({"format": "events-latest-v1", "status": "no_edition", "events": []}), encoding="utf-8")
            got, why = substrate.load_events_input(root / "v.json", root / "e.json", reg=TE.registry())
            self.assertIsNone(got)
            self.assertIn("not built", why)
            (root / "v.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(substrate.load_events_input(root / "v.json", root / "e.json", reg=TE.registry())[0])

    def test_loader_reads_the_stored_files_the_builder_wrote(self):
        view, texts, reg = v2_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "v.json").write_text(events._dump(view), encoding="utf-8")
            (root / "e.json").write_text(json.dumps({"candidates": list(texts.values())}, ensure_ascii=False), encoding="utf-8")
            got, why = substrate.load_events_input(root / "v.json", root / "e.json", reg=reg)
            self.assertEqual(why, "")
            doc = substrate.build_delta_v2(got["view"], got["texts"], got["reg"], state_with_edition()[0], {})
            self.assertEqual(check_v2(doc), [])
            self.assertEqual(doc["event_count"], len(view["events"]))

    def test_without_events_v2_is_omitted_a_stale_one_removed_and_the_rest_unchanged(self):
        state, p = state_with_edition()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = dict(out_llms=root / "llms.txt", out_md=root / "i.md", out_delta=root / "d.json",
                         out_delta_v2=root / "delta" / "v2" / "latest.json")
            paths["out_delta_v2"].parent.mkdir(parents=True)
            paths["out_delta_v2"].write_text("{\"stale\": true}", encoding="utf-8")
            real = substrate.load_events_input
            try:
                substrate.load_events_input = lambda *a, **k: real(root / "missing.json", root / "e.json", reg=TE.registry())
                substrate.emit([story("s1")], p["issues"], p["change_ledger"], None, state, NOW, run=run(), **paths)
            finally:
                substrate.load_events_input = real
            self.assertFalse(paths["out_delta_v2"].exists())
            self.assertEqual(paths["out_llms"].read_text(encoding="utf-8").replace("\r\n", "\n"),
                             substrate.render_llms_txt(state, {"at": NOW, "ok": 2, "total": 2}))
            self.assertNotIn("deprecation", json.loads(paths["out_delta"].read_text(encoding="utf-8")))

    def test_a_fault_while_building_v2_never_stops_the_other_files(self):
        state, p = state_with_edition()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = dict(out_llms=root / "llms.txt", out_md=root / "i.md", out_delta=root / "d.json",
                         out_delta_v2=root / "v2.json")
            view, texts, _ = v2_inputs()
            substrate.emit([story("s1")], p["issues"], p["change_ledger"], None, state, NOW, run=run(),
                           events_input={"view": view, "texts": texts, "reg": None}, **paths)
            for k in ("out_llms", "out_md", "out_delta"):
                self.assertTrue(paths[k].exists(), k)
            self.assertFalse(paths["out_delta_v2"].exists())


class LlmsWithEvents(unittest.TestCase):
    def test_llms_lists_events_in_both_languages_the_quality_file_and_both_deltas(self):
        state, _ = state_with_edition()
        text = substrate.render_llms_txt(state, {"at": NOW}, events=True)
        for path in ("/evenements.html", "/en/evenements.html", "/evenements/<event_id>.html",
                     "/en/evenements/<event_id>.html", "/evenements/latest.json", "/qualite.json",
                     "/delta/v2/latest.json", "/delta/latest.json"):
            self.assertIn(f"https://vigieqc.com{path}", text, path)
        self.assertIn("deprecated since 2026-10-06 and stays unchanged until 2027-01-04", text)
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Vigie")
        self.assertTrue(lines[2].startswith("> "))
        self.assertLess(len(text.encode("utf-8")), 10_000)
        # Everything the file had before is still there, in the same order.
        golden = (GOLDEN / "llms_no_events.txt").read_text(encoding="utf-8")
        head, tail = golden.split("## Registre", 1)
        self.assertTrue(text.startswith(head))
        self.assertTrue(text.endswith("## Registre" + tail))


if __name__ == "__main__":
    unittest.main()
