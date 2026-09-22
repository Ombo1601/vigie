"""
Vigie v0.7 - cluster StoryCandidates into Issues (contradictions held open).
Rules only. No LLM. Never crowns a correct answer.

Scar 2026-09-15 (Nietzsche): require >=2 distinct source_ids.
Scar 2026-09-15 (Fuller v0.4-v0.5): named scars only. Generic anchors
(coroner, tramway alone, duhaime alone) must not glue unrelated stories.
Scar 2026-09-15 (Machiavelli v0.6): Marchand scar needs title name, or
maire-de-Quebec title + Marchand in summary - no passing summary hitchhikers.
Scar 2026-09-15 (cluster v0.7): add tramway (title: tramway + Marchand|Duhaime)
and airport (place + privatisation near place; Province fight allowed).
Scar 2026-09-15 (UI v2.6): scar-locked neutral questions — not outlet ledes.
Scar 2026-09-16 (v0.8): word-bound Lévis in maelyne scar — télévision+police
is not a named place.
Scar 2026-09-16 (v0.9): official voices carry source_kind; Stage prefers them
beside media — a fight without official text stays a remix flag.
Scar 2026-09-16 (v0.10): silence map — enabled chancellery sources absent from
this scar this run. Presence/absence only. Not a bias score.
Scar 2026-09-16 (v0.11): voice = institution, not RSS feed. Sister feeds
(CBC Montreal+Politics, Radio-Canada desks) share one voice / one silence seat.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from copy import deepcopy
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import change_ledger  # noqa: E402
import dossier_history  # noqa: E402
import ingest_rss  # noqa: E402
import store_io  # noqa: E402

IN_PATH = ROOT / "data" / "normalized" / "latest_enriched.json"
OUT_ISSUES = ROOT / "data" / "issues" / "latest_issues.json"
SOURCES_PATH = ROOT / "sources.yaml"

NEST_ORDER = {"primary": 0, "province": 1, "linked": 2}


def institution_of(src: dict) -> str:
    """Voice identity. Missing institution falls back to feed id (1:1)."""
    if not isinstance(src, dict):
        return ""
    return str(src.get("institution") or src.get("id") or "").strip()


def institution_name_of(src: dict) -> str:
    if not isinstance(src, dict):
        return ""
    iid = institution_of(src)
    return str(src.get("institution_name") or src.get("name") or iid)


def feed_to_institution(chancellery: list[dict]) -> dict[str, str]:
    if not isinstance(chancellery, (list, tuple)):
        return {}
    return {str(s["id"]): institution_of(s) for s in chancellery if isinstance(s, dict) and s.get("id")}


def collapse_institutions(chancellery: list[dict]) -> list[dict]:
    """One record per institution among enabled feeds.

    nest_role = best (primary < province < linked) among sister feeds.
    source_kind = official if any feed under the institution is official.
    """
    by_iid: dict[str, dict] = {}
    if not isinstance(chancellery, (list, tuple)):
        return []
    for src in chancellery:
        if not isinstance(src, dict):
            continue
        iid = institution_of(src)
        if not iid:
            continue
        kind = (src.get("source_kind") or "media").lower()
        nest = src.get("nest_role") or "unknown"
        fid = src.get("id")
        if iid not in by_iid:
            by_iid[iid] = {
                "institution_id": iid,
                "institution_name": institution_name_of(src),
                "nest_role": nest,
                "source_kind": kind,
                "feed_ids": [fid] if fid else [],
            }
            continue
        rec = by_iid[iid]
        if fid and fid not in rec["feed_ids"]:
            rec["feed_ids"].append(fid)
        if kind == "official":
            rec["source_kind"] = "official"
        if NEST_ORDER.get(str(nest), 9) < NEST_ORDER.get(str(rec.get("nest_role")), 9):
            rec["nest_role"] = nest
        # Prefer an explicit institution_name if later feeds omit it
        if src.get("institution_name") and not rec.get("institution_name"):
            rec["institution_name"] = src["institution_name"]
    out = list(by_iid.values())
    out.sort(
        key=lambda r: (
            0 if r.get("source_kind") == "official" else 1,
            NEST_ORDER.get(str(r.get("nest_role")), 9),
            r.get("institution_id") or "",
        )
    )
    return out

# Proper-name / named-place scars only. Not bare coroner, bare tramway, bare Duhaime.
# Order matters: more specific scars first (tramway before marchand).
SCARS = [
    (
        "maelyne-levis",
        # Named person, or place-Lévis + search/coroner (CBC EN often omits the name).
        # Word-bound Lévis — télévision + police must not found this scar.
        re.compile(
            r"ma[eë]lyne|lugez|"
            r"(?<![\w])l[eé]vis(?![\w]).{0,60}(coron|police|missing|teen|adolescente|manqu)|"
            r"(coron|police|missing|teen|adolescente|manqu).{0,60}(?<![\w])l[eé]vis(?![\w])",
            re.I,
        ),
    ),
    (
        "tramway",
        # Title must carry tramway AND (Marchand|Duhaime) — not bare tramway glue
        re.compile(
            r"tramway.{0,80}(marchand|duhaime)|(marchand|duhaime).{0,80}tramway",
            re.I,
        ),
    ),
    (
        "airport",
        # Place token + privatisation near place (same enrich vow). Province OK.
        re.compile(
            r"(a[eé]roports?|airports?).{0,80}privat|privat.{0,80}(a[eé]roports?|airports?)",
            re.I,
        ),
    ),
    (
        "marchand",
        re.compile(r"\bmarchand\b|bruno\s+marchand", re.I),
    ),
]

# Scars that may found a Province fight (no quebec-city-first gate)
PROVINCE_OK = {"airport"}


def silence_map(spoke_institutions: set[str], chancellery: list[dict]) -> dict:
    """Enabled institutions that did not appear on this scar this run.

    Voice = institution (sister RSS feeds share one seat).
    Proposed observation — not a trust score, not left/right.
    """
    spoke_set = set(spoke_institutions) if isinstance(spoke_institutions, (set, list, tuple)) else set()
    chancellery_list = list(chancellery) if isinstance(chancellery, (list, tuple)) else []
    institutions = collapse_institutions(chancellery_list)
    silent: list[dict] = []
    for inst in institutions:
        iid = inst["institution_id"]
        if iid in spoke_set:
            continue
        silent.append(
            {
                "institution_id": iid,
                "institution_name": inst.get("institution_name") or iid,
                # Compat aliases for Stage / older guards (same string as institution).
                "source_id": iid,
                "source_name": inst.get("institution_name") or iid,
                "feed_ids": list(inst.get("feed_ids") or []),
                "nest_role": inst.get("nest_role") or "unknown",
                "source_kind": (inst.get("source_kind") or "media").lower(),
                "status": "proposed",
            }
        )
    silent.sort(
        key=lambda s: (
            0 if s.get("source_kind") == "official" else 1,
            NEST_ORDER.get(str(s.get("nest_role")), 9),
            s.get("institution_id") or "",
        )
    )
    return {
        "status": "proposed",
        "scope": "enabled_institutions",
        "method": "rules-silence-v0.2-institution",
        "spoke_count": len(spoke_set),
        "silent_count": len(silent),
        "enabled_count": len(institutions),
        "enabled_feed_count": len(chancellery_list),
        "note": (
            "Absent from the collected snapshot, not proven editorial silence. "
            "Fetch failures, RSS limits, paywalls and unmatched wording may explain absence. "
            "Sister feeds share one voice; independence is not assessed. "
            "This is not a bias meter or a reliability score."
        ),
        "absence_is_editorial_silence": False,
        "coverage_completeness": "not_established",
        "silent": silent,
    }


def topic_of(c: dict) -> str:
    if not isinstance(c, dict):
        return "other"
    topics = ((c.get("enrich") or {}).get("topics") or [])
    if topics and isinstance(topics[0], dict):
        return topics[0].get("topic") or "other"
    return "other"


def geo_of(c: dict) -> str:
    if not isinstance(c, dict):
        return "unknown"
    en = c.get("enrich") or {}
    geo_block = en.get("geo") if isinstance(en, dict) else None
    if isinstance(geo_block, dict) and geo_block.get("geo"):
        return geo_block["geo"]
    return c.get("geo") or "unknown"


def dossier_anchor_ok(scar: str, items: list[dict]) -> bool:
    """Nest law: a dossier needs a Québec City or Québec anchor.

    Pure linked / world-wire groups never become dossiers; an airport scar is
    Province-OK by name.
    """
    if scar in PROVINCE_OK:
        return True
    return bool({geo_of(it) for it in items} & {"quebec-city", "quebec"})


def issue_id(scar: str, source_count: int = 0) -> str:
    """A dossier survives an outlet joining/leaving. Count is not identity."""
    key = f"dossier-v1|{scar}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# Scar-locked lookout questions (UI v2.6). Never crown one outlet's lede.
# Voice count lives in fight-meta / Stage chrome — not inside the question.
SCAR_QUESTIONS = {
    "maelyne-levis": "Maëlyne Lugez : les sources sur l'enquête et ses suites",
    "tramway": "Tramway de Québec : les positions rapportées",
    "marchand": "Les priorités présentées par le maire de Québec",
    "marchand-immigration": "Marchand et les cibles d'immigration à Québec",
    "airport": "Aéroports : le projet d'investissement privé",
}


def neutral_question(titles: list[str], source_count: int, scar: str) -> str:
    """Scar-locked neutral question. Not a newspaper wire. source_count unused in text."""
    q = SCAR_QUESTIONS.get(scar)
    if q:
        return q
    # Fallback only if a new scar ships without a locked string — still not a lede.
    return f"Que disent plusieurs sources sur {scar} ?"


def scar_of(c: dict) -> str | None:
    """Named scars. Specific scars first. No bare-anchor hitchhiking."""
    title = c.get("title") or ""
    summary = c.get("summary") or ""
    blob = f"{title} {summary}"
    for name, rx in SCARS:
        if name == "tramway":
            # Title only — tramway + Marchand|Duhaime in the headline
            if rx.search(title):
                return name
            continue
        if name == "airport":
            if rx.search(blob):
                return name
            continue
        if name == "marchand":
            # Title names the man, or title is clearly the mayor's agenda + summary names him.
            if re.search(r"\bmarchand\b|bruno\s+marchand", title, re.I):
                return name
            if re.search(r"maire\s+de\s+qu[ée]bec", title, re.I) and re.search(
                r"bruno\s+marchand|\bmarchand\b", summary, re.I
            ):
                return name
            continue
        if rx.search(blob):
            return name
    return None


MAX_AGE_DAYS = 7
EVENT_SPAN_HOURS = 72
FUTURE_TOLERANCE_HOURS = 6
METHOD = "rules-cluster-v1 evidence-bounded-dossiers bilingual-complete-link"


def published_when(c: dict) -> datetime | None:
    """Publication only. Fetch/rebuild timestamps cannot make old news new."""
    raw = str(c.get("published_at") or "").strip()
    if not raw:
        return None
    for parser in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            dt = parser(raw)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass
    return None


def edition_when(payload: dict) -> datetime | None:
    """The collection clock this edition represents (normalize's normalized_at),
    never the rebuild clock. The 7-day publication window is measured against
    the edition, so re-running the cluster over a stale store cannot exclude
    every dated candidate and wipe the live dossier store."""
    raw = payload.get("normalized_at") if isinstance(payload, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None  # never use the build machine's local timezone as edition truth
    return dt.astimezone(timezone.utc)


def folded(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


_STOP = frozenset("les des une dans pour avec sans sur sous apres avant cette leurs plus moins veut vont faire fait selon encore entre comme sont sera etre avoir vers tout tous ville quebec canada canadian canadian says dit bruno marchand maire nouvelles nouveau nouvelle voici report reports news minister ministre premier gouvernement police jour jours annee annees pourrait contre doit the and that from this with about into over will have were been".split())


def _stem(word: str) -> str:
    """Very light plural folding: "arrestation/arrestations" are one token.

    The >=3-shared + 0.55-Jaccard guard still does the precision work, so this
    only helps genuine same-event pairs, never broad topic overlap.
    """
    if len(word) >= 5 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    if len(word) >= 5 and word.endswith("x"):
        return word[:-1]
    return word


def headline_tokens(c: dict) -> set[str]:
    return {
        _stem(w)
        for w in re.findall(r"[a-z0-9]+", folded(str(c.get("title") or "")))
        if len(w) >= 4 and w not in _STOP
    }


def event_scar(c: dict) -> str | None:
    """A named person/place is insufficient evidence of the same event."""
    scar = scar_of(c)
    title = folded(str(c.get("title") or ""))
    text = folded(str(c.get("title") or "") + " " + str(c.get("summary") or ""))
    if scar == "marchand":
        if re.search(r"immigr|migrato", title):
            return "marchand-immigration"
        if re.search(r"priorit|liste|epicerie|projets|sur pause", title):
            return "marchand"
        return None
    if scar == "maelyne-levis":
        # A different missing person in Lévis is not Maëlyne Lugez.
        if re.search(r"maelyne|lugez", text):
            return scar if re.search(r"mort|deces|death|dead|coron|polic|enquete|recherch|missing|dispar", text) else None
        return scar if re.search(r"coroner.{0,100}2024|2024.{0,100}coroner", text) else None
    return scar


_LOCATION = re.compile(r"\b(?:rue|boulevard|avenue|autoroute|route|pont|quartier|hopital|ecole)\s+(?:(?:de|du|des|la|le|l)\s+)*([a-z0-9-]+)")
# Neighbourhood tokens used only as a *positive* bilingual guard (shared place
# or proper name). They must never join the road-name set: adding "limoilou"
# there would let Hamel and Charest share a place and merge.
_PLACE_HINTS = (
    "limoilou", "saint-roch", "saint-sauveur", "beauport", "charlesbourg",
    "sillery", "cap-rouge", "sainte-foy", "loretteville", "val-belair",
    "levis", "maizerets", "lairet", "vanier", "duberger", "lebourgneuf",
    "montcalm", "vieux-quebec", "haute-saint-charles", "saint-emile",
    "pointe-aux-lievres", "vieux-port",
)
# English surface (after folding + light plural stem) → French stem.
# Nouns only. Verbs and people-words stay unmapped: precision over recall.
_BILINGUAL = {
    "fire": "incendie", "blaze": "incendie",
    "mayor": "maire",
    "airport": "aeroport",
    "privatization": "privatisation", "privatize": "privatisation",
    "housing": "logement", "rent": "loyer",
    "school": "ecole", "hospital": "hopital",
    "street": "rue", "bridge": "pont",
    "strike": "greve", "election": "election",
    "building": "immeuble",
    "crash": "ecrasement", "helicopter": "helicoptere",
    "tram": "tramway",
    "council": "conseil",
    "flood": "inondation", "snow": "neige",
    "closure": "fermeture", "closed": "fermeture",
    "outage": "panne", "blackout": "panne",
    "protest": "manif",
    "three": "trois", "four": "quatre", "five": "cinq",
    "major": "majeur",
    "dead": "mort", "death": "mort",
    "missing": "disparu",
}


def language_of(c: dict) -> str:
    lang = str(c.get("language") or "").strip().lower()
    if lang.startswith("en"):
        return "en"
    if lang.startswith("fr"):
        return "fr"
    return ""


def road_places(c: dict) -> set[str]:
    return set(_LOCATION.findall(folded(str(c.get("title") or ""))))


def place_hints(c: dict) -> set[str]:
    title = folded(str(c.get("title") or ""))
    found: set[str] = set()
    for hint in _PLACE_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", title):
            found.add(hint)
    return found


def proper_names(c: dict) -> set[str]:
    """Capitalized tokens after the first word, folded. Not a NER."""
    title = str(c.get("title") or "")
    words = re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'’-]*", title)
    names: set[str] = set()
    for i, word in enumerate(words):
        if i == 0 or not word[:1].isupper():
            continue
        token = _stem(folded(word))
        if len(token) >= 4 and token not in _STOP:
            names.add(token)
    return names


def canon_tokens(tokens: set[str]) -> set[str]:
    return {_BILINGUAL.get(word, word) for word in tokens}


def event_features(c: dict) -> tuple:
    return (
        published_when(c),
        headline_tokens(c),
        road_places(c),
        language_of(c),
        proper_names(c),
        place_hints(c),
    )


def features_match(a: tuple, b: tuple) -> bool:
    ta, aa, roads_a, lang_a, names_a, hints_a = a
    tb, bb, roads_b, lang_b, names_b, hints_b = b
    if ta is None or tb is None or abs((ta - tb).total_seconds()) > EVENT_SPAN_HOURS * 3600:
        return False
    # Shared closure boilerplate must not merge different roads/neighbourhoods.
    # Preserve numeric identifiers too (route 138 is not route 175).
    if roads_a and roads_b and not roads_a.intersection(roads_b):
        return False
    bilingual = {lang_a, lang_b} == {"en", "fr"}
    if not bilingual:
        shared = aa & bb
        return len(shared) >= 3 and len(shared) / max(1, len(aa | bb)) >= 0.55
    # FR/EN same-event: date window already held; demand a shared place or
    # proper name, then complete-link on bilingual-canonical tokens.
    if not (roads_a & roads_b or hints_a & hints_b or names_a & names_b):
        return False
    ca, cb = canon_tokens(aa), canon_tokens(bb)
    shared = ca & cb
    return len(shared) >= 2 and len(shared) / max(1, len(ca | cb)) >= 0.40


def same_event(a: dict, b: dict) -> bool:
    """Conservative literal headline overlap; no semantic/contradiction inference."""
    return features_match(event_features(a), event_features(b))


def event_buckets(candidates: list[dict]) -> dict[str, list[dict]]:
    """Named ongoing dossiers plus bounded, complete-link headline clusters.

    Every automatic member must match every other member. A bridge headline
    cannot glue different events together. Names/topics alone never qualify.
    """
    buckets: dict[str, list[dict]] = {}
    remaining = []
    for c in candidates:
        scar = event_scar(c)
        if scar:
            buckets.setdefault(scar, []).append(c)
        else:
            remaining.append(c)
    groups: list[list[dict]] = []
    features = {id(c): event_features(c) for c in remaining}
    token_groups: dict[str, set[int]] = defaultdict(set)

    def index_keys(feature: tuple) -> set[str]:
        tokens = feature[1]
        return tokens | canon_tokens(tokens)

    for c in sorted(remaining, key=lambda x: (str(x.get("id") or ""), str(x.get("title") or ""))):
        feature = features[id(c)]
        if feature[0] is None or len(feature[1]) < 3:
            continue
        # Index original and bilingual-canonical tokens so a FR/EN pair can
        # find each other. Same-language still has to pass features_match
        # (≥3 shared original tokens, Jaccard 0.55); the looser lookup only
        # adds comparisons.
        keys = index_keys(feature)
        possible = Counter(g for word in keys for g in token_groups.get(word, ()))
        for group_index in sorted(g for g, count in possible.items() if count >= 2):
            group = groups[group_index]
            if all(features_match(feature, features[id(member)]) for member in group):
                group.append(c)
                break
        else:
            group_index = len(groups)
            groups.append([c])
            for word in keys:
                token_groups[word].add(group_index)
    for group in groups:
        if len(group) < 2:
            continue
        # Earliest known article anchors the first creation; main() also reuses
        # the previous dossier id when the same articles remain in the snapshot.
        seed = min(group, key=lambda c: (published_when(c), str(c.get("id") or c.get("url") or c.get("title"))))
        anchor = str(seed.get("id") or seed.get("url") or seed.get("title"))
        key = "event-" + hashlib.sha256(anchor.encode("utf-8")).hexdigest()[:16]
        buckets[key] = group
    return buckets


def evidence_metadata(items: list[dict]) -> dict:
    dates = sorted(d for c in items if (d := published_when(c)) is not None)
    # Exact normalized wording, including negation and word order. A bag of
    # topic words could incorrectly call opposing headlines duplicates.
    headlines = Counter(re.sub(r"\W+", " ", folded(str(c.get("title") or ""))).strip() for c in items)
    return {
        "relationship": "same_subject_proposed",
        "contradiction": "not_assessed",
        "source_independence": "not_assessed",
        "official_source_is_confirmation": False,
        "publication_oldest": dates[0].isoformat() if dates else None,
        "publication_latest": dates[-1].isoformat() if dates else None,
        "publication_unknown_count": len(items) - len(dates),
        "duplicate_headline_count": sum(n - 1 for text, n in headlines.items() if text and n > 1),
        "note": "Plusieurs sources ne prouvent ni une contradiction ni des confirmations indépendantes. Les extraits et rapprochements restent à vérifier.",
    }


def main() -> None:
    if not IN_PATH.exists():
        raise SystemExit(f"Missing {IN_PATH}. Run enrich first.")
    try:
        payload = json.loads(IN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Unreadable or corrupt {IN_PATH}: {exc}. Run enrich again.")
    if not isinstance(payload, dict):
        raise SystemExit(
            f"Corrupt {IN_PATH}: expected a JSON object, got {type(payload).__name__}."
        )
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list):
        raise SystemExit(f"Corrupt {IN_PATH}: candidates is not a list.")
    chancellery = ingest_rss.load_enabled_rss(SOURCES_PATH)
    sources = {str(s["id"]): s for s in chancellery}
    feed_inst = feed_to_institution(chancellery)
    institutions = collapse_institutions(chancellery)
    inst_name = {i["institution_id"]: i["institution_name"] for i in institutions}

    # Window law: the 7-day publication window is measured against the edition
    # (normalize's normalized_at), never the rebuild clock - a standalone rerun
    # over a stale enriched store must not wipe the live dossier store.
    instant = edition_when(payload) or datetime.now(timezone.utc)
    excluded = Counter()
    accepted = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            excluded["malformed_candidate"] += 1
            continue
        sid = str(candidate.get("source_id") or "")
        if sid not in sources:
            excluded["source_not_enabled"] += 1
            continue
        when = published_when(candidate)
        if when is not None:
            age_hours = (instant - when).total_seconds() / 3600
            if age_hours > MAX_AGE_DAYS * 24 or age_hours < -FUTURE_TOLERANCE_HOURS:
                excluded["publication_outside_window"] += 1
                continue
        identity = candidate.get("id") or candidate.get("url")
        if identity and identity in seen:
            excluded["duplicate_article"] += 1
            continue
        if identity:
            seen.add(identity)
        accepted.append(candidate)
    # Bucket every accepted candidate; the nest gate (`dossier_anchor_ok`)
    # drops pure linked / world-wire groups, but a linked voice can still join
    # an event anchored in the city or the province — a federal story carried
    # by one national and one local outlet is a real cross-source dossier.
    buckets = event_buckets(accepted)
    previous = []
    previous_loaded = False
    if OUT_ISSUES.exists():
        try:
            doc = json.loads(OUT_ISSUES.read_text(encoding="utf-8"))
            previous_issues = doc.get("issues") if isinstance(doc, dict) else None
            if isinstance(previous_issues, list):
                previous = [p for p in previous_issues if isinstance(p, dict)]
                previous_loaded = True
        except (OSError, ValueError):
            pass
    used_previous = set()

    issues = []
    dropped_single = 0
    dropped_no_city_anchor = 0
    now = instant.isoformat()
    for scar, items in buckets.items():
        if not items:
            continue
        # Nest law: a dossier must be anchored in Québec City or the province;
        # pure linked / world-wire groups never become dossiers.
        if not dossier_anchor_ok(scar, items):
            dropped_no_city_anchor += 1
            continue

        def voice_id(it: dict) -> str:
            sid = it.get("source_id") or ""
            return feed_inst.get(sid) or sid

        institutions_spoke = sorted({voice_id(it) for it in items if voice_id(it)})
        feeds_spoke = sorted({(it.get("source_id") or "") for it in items if it.get("source_id")})
        if len(institutions_spoke) < 2:
            dropped_single += 1
            continue
        by_inst: dict[str, list] = {}
        for it in items:
            iid = voice_id(it) or "unknown"
            by_inst.setdefault(iid, []).append(it)
        tensions = []
        for iid, src_items in by_inst.items():
            src_items.sort(key=lambda x: (published_when(x) or datetime.min.replace(tzinfo=timezone.utc), str(x.get("id") or "")), reverse=True)
            kind = "official" if any(
                (sources.get(str(x.get("source_id")), {}).get("source_kind") or "media") == "official" for x in src_items
            ) else "media"
            display = inst_name.get(iid) or iid
            tensions.append(
                {
                    "label": f"voice:{display}",
                    "institution_id": iid,
                    "institution_name": display,
                    "status": "proposed",
                    "source_kind": kind,
                    "items": [
                        {
                            "candidate_id": it.get("id"),
                            "title": it.get("title"),
                            "url": it.get("url"),
                            "summary": it.get("summary"),
                            "published_at": it.get("published_at"),
                            "fetched_at": it.get("fetched_at"),
                            "source_name": it.get("source_name") or it.get("source_id") or iid,
                            "source_id": it.get("source_id"),
                            "institution_id": iid,
                            "source_kind": sources.get(str(it.get("source_id")), {}).get("source_kind") or "media",
                            "language": it.get("language"),
                            "geo": geo_of(it),
                            "enrich_status": it.get("enrich_status") or "proposed",
                            "claims": [
                                {**deepcopy(cl), "status": "proposed"}
                                for cl in (
                                    it["enrich"].get("claims")
                                    if isinstance(it.get("enrich"), dict)
                                    and isinstance(it["enrich"].get("claims"), list)
                                    else []
                                )[:3]
                                if isinstance(cl, dict) and cl.get("quote")
                            ],
                        }
                        for it in src_items
                    ],
                }
            )
        # Official documents face the reader before media remix.
        tensions.sort(key=lambda t: (0 if t.get("source_kind") == "official" else 1, t.get("label") or ""))
        official_voices = sum(1 for t in tensions if t.get("source_kind") == "official")
        geos = sorted({geo_of(it) for it in items})
        titles = [it.get("title") or "" for it in items]
        silence = silence_map(set(institutions_spoke), chancellery)
        iid = issue_id(scar)
        label_source = None
        question = neutral_question(titles, len(institutions_spoke), scar)
        if scar.startswith("event-"):
            member_ids = {str(it.get("id")) for it in items if it.get("id")}
            matches = []
            for old in (previous if isinstance(previous, list) else []):
                if not isinstance(old, dict) or not str(old.get("scar") or "").startswith("event-") or old.get("issue_id") in used_previous:
                    continue
                old_ids = {str(it.get("candidate_id")) for t in (old.get("tensions") or []) if isinstance(t, dict) for it in (t.get("items") or []) if isinstance(it, dict) and it.get("candidate_id")}
                overlap = len(member_ids & old_ids)
                if overlap >= 2:
                    matches.append((overlap, str(old.get("issue_id"))))
            if matches:
                iid = max(matches)[1]
                used_previous.add(iid)
            label = min(items, key=lambda c: (published_when(c) or instant, str(c.get("id") or "")))
            question = str(label.get("title") or "Articles à comparer")
            label_source = {k: label.get(k) for k in ("id", "title", "url", "source_id", "source_name")}
        topic_counts = Counter(topic_of(it) for it in items if topic_of(it) != "other")
        topic = sorted(topic_counts, key=lambda t: (-topic_counts[t], t))[0] if topic_counts else "other"
        issues.append(
            {
                "issue_id": iid,
                "status": "proposed",
                "method": METHOD,
                "scar": scar,
                "clustered_at": now,
                "topic": {"status": "proposed", "topic": topic},
                "question": question,
                "label_kind": "attributed_headline" if label_source else "subject_label",
                "label_source": label_source,
                "evidence": evidence_metadata(items),
                "geo_focus": geos,
                "sources": institutions_spoke,
                "source_feeds": feeds_spoke,
                "item_count": len(items),
                "source_count": len(institutions_spoke),
                "official_voice_count": official_voices,
                "media_remix": official_voices == 0,
                "silence": silence,
                "tensions": tensions,
            }
        )

    # Place before popularity, then publication date, then stable identity.
    issues.sort(key=lambda x: x["issue_id"])
    issues.sort(key=lambda x: (x["evidence"].get("publication_latest") or "", x["source_count"]), reverse=True)
    issues.sort(key=lambda x: 0 if "quebec-city" in x["geo_focus"] else 1)
    ledger = change_ledger.diff_editions(issues, previous, has_previous=previous_loaded)
    # Durable dossier history: one edition per collection snapshot
    # (normalized_at), so offline re-runs never inflate the counts.
    edition_ts = str(payload.get("normalized_at") or now)
    history_path = OUT_ISSUES.parent / "history.json"
    history = dossier_history.load_history(history_path)
    history = dossier_history.update_history(history, issues, edition_ts)
    for issue in issues:
        tracking = dossier_history.tracking_of(history, issue["issue_id"])
        if tracking:
            issue["tracking"] = tracking
    OUT_ISSUES.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "clustered_at": now,
        "method": (
            "rules-cluster-v1 word-boundary+official+institution-silence bounded-dossiers; "
            "subject labels or explicitly attributed headline; "
            "Lévis≠télévision; tramway title+(Marchand|Duhaime); "
            "airport place+privatisation (Province OK); "
            "marchand agenda/immigration separated; conservative complete-link headline overlap; >=2 institutions (not RSS feeds); "
            "quebec-city-first except Province-OK; "
            "official voices first; media_remix flag when no official text; "
            "claims from enrich attached to voice items; "
            "silence map = snapshot absence only (sister feeds share one seat); "
            "7-day publication window; auto clusters <=72 hours; unknown dates remain unknown; "
            "FR/EN join requires shared place or proper name then bilingual-canonical complete-link; "
            "city then latest publication then source count; no inferred contradiction/independent confirmation"
        ),
        "issue_count": len(issues),
        "dropped_single_voice": dropped_single,
        "dropped_no_city_anchor": dropped_no_city_anchor,
        "excluded_candidates": dict(excluded),
        "publication_window_days": MAX_AGE_DAYS,
        "chancellery_enabled": [s.get("id") for s in chancellery],
        "chancellery_institutions": [i["institution_id"] for i in institutions],
        "change_ledger": ledger,
        "dossier_history": {
            "method": dossier_history.METHOD,
            "edition_count": history.get("edition_count"),
            "tracked_dossiers": len(history.get("dossiers") or {}),
        },
        "issues": issues,
    }
    store_io.write_json_atomic(OUT_ISSUES, out)
    store_io.write_json_atomic(history_path, history)
    print(f"issues {len(issues)} (dropped single-voice {dropped_single}, "
          f"no city anchor {dropped_no_city_anchor}) -> {OUT_ISSUES}")
    for i in issues[:8]:
        print("-", i["scar"], "|", i["question"][:90], "| voices", i["source_count"], "| geos", i["geo_focus"])


if __name__ == "__main__":
    main()
