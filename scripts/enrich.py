"""Vigie v0 — thin enrich: propose geo/topic/impact. Never truth.

Rules only. No LLM. Rent stays on the Inventor's wallet.
Every tag is status=proposed.

Primary media feed geography never substitutes for evidence in an article.
Scar 2026-09-15 (Marcus): province nest does NOT auto-cloak world wire as quebec —
without QC/CA impact tokens, park as linked.
Scar 2026-09-15 (Machiavelli): topic rules must not match inside longer words —
rent is not different, ges is not villages, import is not importants,
bare environnement is not climate.
Scar 2026-09-16 (geo-v0.4): place tokens are word-bounded — l[ée]vis is not
télévision / television; bare tramway is not a city crown (need tramway de Québec).
Scar 2026-09-16 (official-v0.1): source_kind=official primary → quebec-city;
official province → quebec (world-fog still parks linked). City hall is not a wire.
Scar 2026-09-16 (claims-v0.1): extract quote+speaker as proposed objects;
never leave claims as permanent empty theater when a speech pattern is on disk.
No bare title-colon (Élections 2026 : … is not a speaker).
Scar 2026-09-16 (impact-units-v0.1): falsifiable units on impacts —
price (CAD/%/¢/kWh/$/L), bylaw_id, housing_count. High precision; deny
bond-issuance dollars and English \"unit\" theater. w_impact stays 0 until
a later deliberate ACT.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import store_io

ROOT = Path(__file__).resolve().parent.parent
IN_PATH = ROOT / "data" / "normalized" / "latest_candidates.json"
OUT_PATH = ROOT / "data" / "normalized" / "latest_enriched.json"

# (?<![\w]) / (?![\w]) — Unicode-safe edges. \b alone is fine for ASCII but
# we keep the lookaround form explicit next to Lévis (the télévision scar).
STRICT_CITY = re.compile(
    r"(?<![\w])qu[ée]bec\s+city(?![\w])|(?<![\w])ville de qu[ée]bec(?![\w])|"
    r"(?<![\w])capitale-?nationale(?![\w])|"
    r"maire de qu[ée]bec|(?<![\w])bruno marchand(?![\w])|"
    r"(?<![\w])l[ée]vis(?![\w])|"
    r"(?<![\w])sainte-?foy(?![\w])|(?<![\w])charlesbourg(?![\w])|"
    r"(?<![\w])beauport(?![\w])|(?<![\w])limoilou(?![\w])|"
    r"(?<![\w])cap-?rouge(?![\w])|(?<![\w])val-?b[ée]lair(?![\w])|"
    r"(?<![\w])wendake(?![\w])|(?<![\w])ancienne-?lorette(?![\w])|"
    r"(?<![\w])chutes-?de-?la-?chaudi[èe]re(?![\w])|"
    r"tramway\s+de\s+qu[ée]bec|(?<![\w])fcvq(?![\w])|"
    r"\bcentre vid[ée]otron\b|\buniversit[ée] laval\b|\btramcit[ée]\b|"
    r"\bpont (?:de qu[ée]bec|pierre-laporte)\b|\ba[ée]roport jean-lesage\b|"
    r"\br[ée]seau de transport de la capitale\b|\bsaint-augustin-de-desmaures\b|"
    # Agglomeration toponyms probed 2026-09-20: unambiguous, local-only names.
    # Ambiguous ones (Vanier, Montcalm, Neufchâtel, Saint-Jean-Baptiste) are
    # deliberately absent — they name places elsewhere too.
    r"(?<![\w])sillery(?![\w])|(?<![\w])duberger(?![\w])|(?<![\w])les saules(?![\w])|"
    r"(?<![\w])lebourgneuf(?![\w])|(?<![\w])maizerets(?![\w])|(?<![\w])lairet(?![\w])|"
    r"(?<![\w])loretteville(?![\w])|(?<![\w])lac-saint-charles(?![\w])|"
    r"(?<![\w])pointe-aux-li[èe]vres(?![\w])|(?<![\w])[iî]le[- ]d.?orl[ée]ans(?![\w])|"
    r"(?<![\w])saint-romuald(?![\w])|(?<![\w])charny(?![\w])|(?<![\w])pintendre(?![\w])|"
    r"(?<![\w])saint-gabriel-de-valcartier(?![\w])|(?<![\w])sainte-brigitte-de-laval(?![\w])|"
    r"(?<![\w])lac-beauport(?![\w])|(?<![\w])lac-delage(?![\w])|(?<![\w])lac-sergent(?![\w])|"
    r"chu de qu[ée]bec|(?<![\w])chul(?![\w])|centre de foires|gare du palais|"
    r"port de qu[ée]bec|expo cit[ée]",
    re.I,
)
AMBIGUOUS_CITY = re.compile(r"\b(?:haute-ville|basse-ville|saint-roch|vanier|neufch[âa]tel)\b", re.I)
CITY_CONTEXT = re.compile(r"\b(?:[àa] qu[ée]bec|ville de qu[ée]bec|qu[ée]bec city)\b", re.I)
CITY_LOCATION = re.compile(r"\b[àa] qu[ée]bec\b", re.I)
GOVERNMENT_ADDRESSEE = re.compile(r"\b(?:demande|demandent|r[ée]clame|r[ée]clament|reproche|reprochent|ordonne|ordonnent|accorde|accordent|verse|versent|refuse|refusent|dit|disent)\b[^.!?]{0,45}$", re.I)
PROVINCE_HINT = re.compile(
    r"\bqu[ée]bec\b|montr[ée]al|ottawa|assembl[ée]e nationale|\bcaq\b|\bplq\b|\bqs\b|\bpq\b|"
    r"\blegault\b|\bduhaime\b|\bcanada\b|\bcanadien(?:ne)?s?\b|\bf[ée]d[ée]ral(?:e)?s?\b",
    re.I,
)
# Strong world anchors without local scar — do not let province nest cloak these
WORLD_FOG = re.compile(
    r"\bdanemark\b|\brussie\b|\brussia\b|\biran\b|\bisra[eë]l\b|\bukraine\b|"
    r"\bpalestine\b|\bchina\b|\bchine\b|\btrump\b|\bbiden\b|"
    r"jimmy fallon|rita mitsouko|catherine ringer|netflix|"
    r"pentagone|pentagon|otan\b|\bnato\b|\blituanie\b|\blithuania\b",
    re.I,
)

TOPIC_RULES: list[tuple[str, re.Pattern]] = [
    # Word boundaries on short stems — substring matches were theater.
    # Stems are word-bounded on both sides unless the suffix is the point
    # (climat/climatique, [ée]cole/[ée]coles); precision beats recall: a wrong
    # label is worse than "other".
    ("housing", re.compile(
        r"\bloyers?\b|\brents?\b|\blogements?\b|\bhabitations?\b|\bimmo(?:bilier|bili[èe]re)?\b|"
        r"\bhousing\b|\bschl\b|\bcmhc\b|\bevict|itin[ée]rance|\bexpulsion\b|"
        r"\blocataires?\b|\bpropri[ée]taires?\b|\bcoop[ée]ratives? d.?habitation\b|"
        r"\bcrise du logement\b|\btaxe fonci[èe]re\b",
        re.I,
    )),
    ("energy/hydro", re.compile(
        r"\bhydro\b|électricité|electricit|tarif d.?électricité|power rate|"
        r"\bessence\b|\bcarburant\b|\blitre\b|\bdiesel\b|\bgasoline\b|\bgallon\b|"
        r"\bhydro[- ]?qu[ée]bec\b|\bpanne(?:s)? d.?[ée]lectricit[ée]\b|\bcoupure(?:s)? de courant\b|"
        r"\bchauffage\b|\bpyl[ôo]nes?\b|\bbarrages?\b|\bcentrales? hydro[ée]lectriques?\b|\bkwh?\b",
        re.I,
    )),
    ("transport", re.compile(
        r"\btramway\b|\btgv\b|\bopus\b|\bmetro\b|\bmétro\b|\bautobus\b|"
        r"\btramcit[ée]\b|\brtc\b|\br[ée]seau de transport de la capitale\b|"
        r"\bd[ée]tours?\b|\bentraves?\b|\bpistes? cyclables?\b|"
        r"\bcyclis(?:te|tes)?\b|\bv[ée]los?\b|\bpi[ée]tons?\b|\bpi[ée]tonniers?\b|"
        r"\btransports? en commun\b|\bmobilit[ée]\b|\bcirculation\b|\bfluidit[ée]\b|"
        r"\bstationnement\b|\bcorridor(?:s)?\b|\bbrt\b",
        re.I,
    )),
    ("education", re.compile(
        r"\b[ée]ducation\b|\beducation\b|\b[ée]coles?\b|\becoles?\b|\bschools?\b|"
        r"\buniversit[ée]\b|\bc[ée]gep\b|\benseignants?\b|\b[ée]tudiants?\b|"
        r"\bcommission scolaire\b|\bcentre de services scolaire\b",
        re.I,
    )),
    ("law", re.compile(
        r"projet de loi|\bbill\b|r[èe]glement|\bbylaw\b|\btribunal\b|\bcour\b|\bcourt\b|\bloi\b|"
        r"\b[ée]lections?\b|\bscrutin\b|\bcampagne [ée]lectorale\b|\bmairesse?\b|"
        r"\bconseil municipal\b|\bassembl[ée]e nationale\b|\bcandidat(?:e|es|s)?\b|"
        r"\bministre\b|\bd[ée]put[ée]e?s?\b|\bpolitique\b|\bconsultation publique\b|\bavis public\b",
        re.I,
    )),
    ("health", re.compile(
        r"\bsanté\b|\bhealth\b|hôpital|\bhospital\b|\bchu\b|\burgence\b|\bclinique?s?\b|"
        r"\bchsld\b|\bciusss\b|\bsoins?\b|\bm[ée]dical(?:e|es|s)?\b|\bm[ée]decins?\b|"
        r"\bvaccin(?:s|ation)?\b|\bpharmac(?:ie|ien|ienne)\b|\bd[ée]pistage\b",
        re.I,
    )),
    ("trade", re.compile(
        r"\btarifs?\b|\btarif(?:aire|aires)?\b|\btariffs?\b|\bcommerce\b|\bdouanes?\b|\btrade\b|"
        r"\bexports?(?:ation|ations)?\b|\bimports?(?:ation|ations)?\b|\bsurtaxe\b|"
        # Place-first airport stem (Marcus 2026-09-15) — never bare private
        r"\ba[ée]roports?\b|\bairports?\b|\baluminium\b|\bacier\b|"
        r"\bbois d.?[œo]uvre\b|\bentente commerciale\b|\baceum\b|\balena\b",
        re.I,
    )),
    ("security", re.compile(
        r"\bpolice\b|\bcrimes?\b|fusillade|\bshooting\b|sécurité|\bsecurity\b|\bcoroner\b|"
        r"\bpompiers?\b|\bincendies?\b|\bs[ûu]ret[ée] du qu[ée]bec\b|\bagressions?\b|"
        r"\bviolence\b|\bharc[èe]lement\b|\bfraude\b",
        re.I,
    )),
    ("economy", re.compile(
        r"\bemplois?\b|chômage|\binflation\b|\bbudgets?\b|économie|\beconomy\b|\bgdp\b|\bpib\b|"
        r"\bsalaires?\b|\bmain[- ]d.?[œo]uvre\b|\bpme\b|\bentreprises?\b|\bcommerces?\b|"
        r"\bfiscal(?:e|es|it[ée])?\b|\bimp[ôo]ts?\b",
        re.I,
    )),
    # No bare environnement (French = surroundings). No ges word-end (matches villages).
    ("environment", re.compile(
        r"\bclimat(?:ique|iques)?\b|changements? climatiques?|\bGES\b|gaz [àa] effet|"
        r"\bcarbone\b|environnemental(?:e|es|aux)?|r[ée]chauffement|rechauffement|"
        r"\bbiodiversit[ée]\b|\benvironment\b|\b[ée]cosyst[èe]mes?\b|\bpollution\b|"
        r"\bqualit[ée] de l.?air\b|\bcanop[ée]e\b|\b[ée]nergies? renouvelables?\b",
        re.I,
    )),
    ("death", re.compile(
        r"\bmorts?\b|décès|\bdeath\b|\bkilled\b|tu[ée]e?s?\b|"
        r"\bobs[èe]ques?\b|\bfun[ée]raires?\b|\bnoyades?\b|\bhomicides?\b|\bcoroner\b",
        re.I,
    )),
    ("culture", re.compile(
        r"\bfestivals?\b|\bculture(?:l|ls|lle|lles)?\b|\bconcerts?\b|\bpatrimoine\b|"
        r"\bhumour\b|com[ée]die|\bfilms?\b|cin[ée]ma|\bmus[ée]es?\b|\bexpositions?\b|"
        r"\bth[ée][âa]tres?\b|\bmusiques?\b|\blivres?\b|\bsalon du livre\b|\bspectacles?\b",
        re.I,
    )),
]

IMPACT_FROM_TOPIC = {
    "housing": "housing",
    "energy/hydro": "price",
    "law": "law",
    "health": "health",
    "trade": "trade",
    "security": "security",
    "death": "death",
    "economy": "price",
    "transport": "mobility",
    "education": "education",
    "environment": "other",
    "culture": "other",
}

# --- Impact units (proposed only). Falsifiable amounts/ids — not topic theater. ---
# French/EN grouped thousands: 1 234 or 1,234 or 1234; decimal , or .
# (?<![\d]) blocks greedy .{0,N} from landing mid-number (scar: "1 450" → "0").
RE_NUM = (
    r"(?<![\d])("
    r"\d{1,3}(?:[\s\u00a0]\d{3})+(?:[.,]\d+)?"
    r"|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|"
    r"\d+(?:[.,]\d+)?"
    r")(?![\d])"
)
PRICE_CTX = (
    r"loyer|loyers|rent|rents|tarif|tarifs|facture|factures|"
    r"[ée]lectricit|electricit|essence|carburant|diesel|gasoline|"
    r"hydro|power\s+rate|utility|utilities"
)
PRICE_CTX_RE = re.compile(PRICE_CTX, re.I)
# Capital-market noise — never a citizen price check
PRICE_DENY = re.compile(
    r"obligation|obligations|billets?\s+[àa]\s+moyen|bond\s+issu|"
    r"[ée]mission\s+d.?oblig|"
    r"milliard|billion|million\s+d.?oblig",
    re.I,
)
FOREIGN_DOLLAR = re.compile(r"\bUSD\b|\bUS\s*\$|\$\s*(?:US|USD|am[ée]ricains?)\b|\bdollars? am[ée]ricains?\b|\b(?:AUD|NZD|HKD)\b", re.I)
CANADIAN_DOLLAR = re.compile(r"\bCAD\b|\bCA\s*\$|\$\s*(?:CA|CAD|canadiens?)\b|\bdollars? canadiens?\b|\bqu[ée]bec\b|\bcanada\b", re.I)
RE_PRICE_CAD = re.compile(
    rf"(?:{PRICE_CTX}).{{0,48}}?{RE_NUM}\s*\$|"
    rf"\$\s*{RE_NUM}.{{0,48}}?(?:{PRICE_CTX}|/\s*mois|per\s+month|par\s+mois)|"
    rf"{RE_NUM}\s*\$\s*(?:/\s*mois|par\s+mois|per\s+month)|"
    rf"{RE_NUM}\s*\$\s*/\s*L(?:itre)?|"
    rf"{RE_NUM}\s*(?:¢|cents?)\s*/\s*kWh",
    re.I,
)
RE_PRICE_PCT_HAUSSE = re.compile(
    rf"\b(?:hausse|augmentation|baisse|increase|decrease|cut)\b.{{0,48}}?{RE_NUM}\s*%",
    re.I,
)
RE_PRICE_PCT_CTX = re.compile(
    rf"(?:{PRICE_CTX}).{{0,48}}?{RE_NUM}\s*%|"
    rf"{RE_NUM}\s*%.{{0,48}}?(?:{PRICE_CTX})",
    re.I,
)
RE_BYLAW = re.compile(
    r"(?:projet\s+de\s+loi|bill)\s*(?:n[o°º]\.?\s*)?([A-Z]?-?\d{1,4}(?:-\d+)?[A-Z]?)|"
    r"r[èe]glement\s*(?:municipal\s*)?(?:n[o°º]\.?\s*)?([A-Z]?\d[\w\-.]*)|"
    r"\bbylaw\s*(?:no\.?\s*)?([A-Z]?\d[\w\-.]*)|"
    r"\bloi\s+(\d{1,3})\b",
    re.I,
)
RE_HOUSING_COUNT = re.compile(
    rf"{RE_NUM}\s*(?:logements?|habitations?|housing\s+units?|rental\s+units?)|"
    rf"(?:logements?|habitations?)\s*[:\-–]\s*{RE_NUM}",
    re.I,
)

UNIT_KIND_TO_IMPACT = {
    "price": "price",
    "bylaw_id": "law",
    "housing_count": "housing",
}
# --- Claims (proposed only). High precision; no title-colon theater. ---
CLAIM_MIN = 12
CLAIM_MAX = 220
SPEECH_VERBS = (
    r"affirme|dit|soutient|d[ée]clare|pr[ée]cise|estime|lance|pr[ée]vient|"
    r"annonce|demande|d[ée]nonce|rappelle|r[ée]pond|explique|assure|promet"
)
NAME = (
    r"[A-ZÉÈÊÀÂÎÔÛÇŒ][\w\-''']+"
    r"(?:\s+(?:de|du|des|d'|St\.?|Ste\.?|Saint|Sainte)\s*[A-ZÉÈÊÀÂÎÔÛÇŒ][\w\-''']+){0,2}"
    r"(?:\s+[A-ZÉÈÊÀÂÎÔÛÇŒ][\w\-''']+){0,3}"
)
RE_GUILLEMET = re.compile(r"«\s*(.+?)\s*»", re.S)
RE_ASCII_QUOTE = re.compile(r'"([^"]+)"')
RE_AFTER_QUOTE_SPEAKER = re.compile(
    rf"^\s*,?\s*(?:{SPEECH_VERBS})\s+({NAME})",
    re.I,
)
RE_BEFORE_SPEAKER_QUOTE = re.compile(
    rf"({NAME})\s+(?:{SPEECH_VERBS})\s*[:：,]?\s*[«\"]?\s*$",
    re.I,
)
RE_SELON = re.compile(
    rf"\b[Ss]elon\s+({NAME})\s*[,:]?\s+(.{{12,200}}?)(?:\.|$)",
    re.S,
)
RE_NAME_VERB_CLAIM = re.compile(
    rf"\b({NAME})\s+(?:{SPEECH_VERBS})\s*[:：]\s*(.{{12,200}}?)(?:\.|$)",
    re.I | re.S,
)
RE_SAYS_EN = re.compile(
    rf"\b({NAME})\s+says\b[,:]?\s*(?:that\s+)?[\"']?(.{{12,200}}?)(?:[\"']|$|\.)",
    re.I | re.S,
)
QUOTE_DENY = re.compile(
    r"^(?:face[\s\-]?à[\s\-]?face|face[\s\-]?a[\s\-]?face|breaking|live|update|"
    r"en\s+direct|exclusif|vid[ée]o)$",
    re.I,
)
SPEAKER_TRAIL = re.compile(
    r"\s+(?:apr[èe]s|lors|dans|devant|au|aux|en|sur|pour|avec|contre)\b.*$",
    re.I,
)


def blob(c: dict) -> str:
    # The publisher's domain, route and tracking query are not article evidence.
    return " ".join(x for x in (c.get("title"), c.get("summary")) if isinstance(x, str) and x)


def _clean_quote(q: str) -> str | None:
    q = " ".join((q or "").split()).strip(" \t\n\r«»\"'.,;:…")
    if len(q) < CLAIM_MIN or len(q) > CLAIM_MAX:
        return None
    if QUOTE_DENY.match(q):
        return None
    return q


def _clean_speaker(s: str | None) -> str | None:
    if not s:
        return None
    s = " ".join(s.split()).strip(" \t\n\r,.;:")
    s = SPEAKER_TRAIL.sub("", s).strip()
    # Drop trailing junk words left by greedy NAME
    parts = s.split()
    if len(parts) > 5:
        s = " ".join(parts[:5])
    if len(s) < 2 or len(s) > 80:
        return None
    return s


def _claim(
    *,
    quote: str,
    speaker: str | None,
    attribution: str,
    method: str,
    field: str,
) -> dict:
    return {
        "status": "proposed",
        "quote": quote,
        "speaker": speaker,
        "attribution": attribution,  # said | selon | reported
        "method": method,
        "field": field,
    }


def propose_claims(c: dict) -> list[dict]:
    """Rules-only speech objects. Proposed never truth. No title-colon priest."""
    title = c.get("title") if isinstance(c.get("title"), str) else ""
    summary = c.get("summary") if isinstance(c.get("summary"), str) else ""
    out: list[dict] = []
    seen: set[str] = set()

    def add(quote: str, speaker: str | None, attribution: str, method: str, field: str) -> None:
        q = _clean_quote(quote)
        if not q:
            return
        sp = _clean_speaker(speaker)
        key = f"{(sp or '').lower()}|{q.lower()}"
        if key in seen:
            return
        seen.add(key)
        out.append(
            _claim(
                quote=q,
                speaker=sp,
                attribution=attribution,
                method=method,
                field=field,
            )
        )

    for field, text in (("title", title), ("summary", summary)):
        if not text:
            continue
        # 1) Guillemets / ASCII quotes with nearby speaker
        for rx, kind in ((RE_GUILLEMET, "guillemets"), (RE_ASCII_QUOTE, "ascii-quotes")):
            for m in rx.finditer(text):
                quote = m.group(1)
                speaker = None
                after = text[m.end() : m.end() + 100]
                am = RE_AFTER_QUOTE_SPEAKER.match(after)
                if am:
                    speaker = am.group(1)
                else:
                    before = text[max(0, m.start() - 100) : m.start()]
                    bm = RE_BEFORE_SPEAKER_QUOTE.search(before)
                    if bm:
                        speaker = bm.group(1)
                if speaker is None and kind == "ascii-quotes":
                    sm = re.search(
                        rf",\s*({NAME})\s+says\b",
                        text[m.end() : m.end() + 60],
                        re.I,
                    )
                    if sm:
                        speaker = sm.group(1)
                add(
                    quote,
                    speaker,
                    "said" if speaker else "reported",
                    f"rules-claim-v0.1-{kind}",
                    field,
                )

        # 2) Selon X, rest…
        for m in RE_SELON.finditer(text):
            add(m.group(2), m.group(1), "selon", "rules-claim-v0.1-selon", field)

        # 3) Name verb: claim
        for m in RE_NAME_VERB_CLAIM.finditer(text):
            add(m.group(2), m.group(1), "said", "rules-claim-v0.1-name-verb", field)

        # 4) EN Name says …
        for m in RE_SAYS_EN.finditer(text):
            add(m.group(2), m.group(1), "said", "rules-claim-v0.1-says-en", field)

    return out[:5]  # temperance — not a quote farm


def propose_geo(c: dict, text: str) -> dict:
    source_geo = c.get("geo") or "unknown"
    source_nest = c.get("nest_role") or "unknown"
    source_kind = (c.get("source_kind") or "media").lower()
    city_match = STRICT_CITY.search(text)
    # French distinguishes the city (à Québec) from the province (au Québec).
    # Keep government-as-addressee phrasing provincial: Ottawa demande à Québec.
    title = c.get("title") if isinstance(c.get("title"), str) else ""
    location = CITY_LOCATION.search(title)
    if not city_match and location and not GOVERNMENT_ADDRESSEE.search(title[:location.start()]):
        city_match = location
    if city_match or (AMBIGUOUS_CITY.search(text) and CITY_CONTEXT.search(text)):
        return {
            "status": "proposed",
            "geo": "quebec-city",
            "source_geo": source_geo,
            "source_nest": source_nest,
            "reason": "strict city/Lévis/borough/mayor token (word-boundary)",
            "evidence": (city_match or AMBIGUOUS_CITY.search(text)).group(0),
        }
    # World fog without city scar must not wear quebec cloak
    # (summary may mention Canada; that is not Quebec impact)
    if WORLD_FOG.search(text) and not PROVINCE_HINT.search(text):
        return {
            "status": "proposed",
            "geo": "linked",
            "source_geo": source_geo,
            "source_nest": source_nest,
            "reason": "world-fog tokens without city scar — park linked",
        }
    # Official primary documents (Ville) crown Near me without keyword theater.
    if source_kind == "official" and source_nest == "primary":
        return {
            "status": "proposed",
            "geo": "quebec-city",
            "source_geo": source_geo,
            "source_nest": source_nest,
            "reason": "official primary document — city hall nest",
        }
    if source_kind == "official" and source_nest == "province":
        return {
            "status": "proposed",
            "geo": "quebec",
            "source_geo": source_geo,
            "source_nest": source_nest,
            "reason": "official province document — Gouv/Hydro nest",
        }
    if source_nest == "primary" and PROVINCE_HINT.search(text):
        return {
            "status": "proposed",
            "geo": "quebec",
            "source_geo": source_geo,
            "source_nest": source_nest,
            "reason": "primary source with explicit QC/CA token — province",
        }
    if source_nest == "province":
        if PROVINCE_HINT.search(text):
            return {
                "status": "proposed",
                "geo": "quebec",
                "source_geo": source_geo,
                "source_nest": source_nest,
                "reason": "province source + QC/CA token",
            }
        return {
            "status": "proposed",
            "geo": "linked",
            "source_geo": source_geo,
            "source_nest": source_nest,
            "reason": "province feed item without QC/CA token — do not cloak as quebec",
        }
    if PROVINCE_HINT.search(text) and source_nest == "linked":
        return {
            "status": "proposed",
            "geo": "quebec",
            "source_geo": source_geo,
            "source_nest": source_nest,
            "reason": "linked feed with Quebec/Montreal/Ottawa token — propose province",
        }
    return {
        "status": "proposed",
        "geo": "linked",
        "source_geo": source_geo,
        "source_nest": source_nest,
        "reason": "no explicit local evidence — source geography is not article geography",
    }


def propose_topics(text: str) -> list[dict]:
    found = []
    for label, pat in TOPIC_RULES:
        if pat.search(text):
            found.append({"status": "proposed", "topic": label})
    if not found:
        found.append({"status": "proposed", "topic": "other"})
    return found


def propose_impacts(topics: list[dict], title: str = "", summary: str = "") -> list[dict]:
    """Topic→impact labels plus attached falsifiable units when present."""
    units = propose_impact_units(title, summary)
    by_label: dict[str, list[dict]] = {}
    for u in units:
        lab = UNIT_KIND_TO_IMPACT.get(u["kind"])
        if lab:
            by_label.setdefault(lab, []).append(u)

    out: list[dict] = []
    seen: set[str] = set()
    for t in topics:
        lab = t.get("topic")
        if lab == "other":
            continue
        impact = IMPACT_FROM_TOPIC.get(lab, "other")
        if impact in seen:
            continue
        seen.add(impact)
        out.append(
            {
                "status": "proposed",
                "label": impact,
                "direction": "unclear",
                "from_topic": lab,
                "units": list(by_label.pop(impact, [])),
            }
        )
    for lab, ulist in by_label.items():
        if lab in seen:
            continue
        seen.add(lab)
        out.append(
            {
                "status": "proposed",
                "label": lab,
                "direction": "unclear",
                "from_topic": None,
                "units": ulist,
            }
        )
    return out


def _parse_number(raw: str) -> float | None:
    s = re.sub(r"\s+", "", raw or "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", ".") if len(parts[-1]) <= 2 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _unit(
    *,
    kind: str,
    value: float | str,
    unit: str,
    raw: str,
    method: str,
    field: str,
) -> dict:
    return {
        "status": "proposed",
        "kind": kind,
        "value": value,
        "unit": unit,
        "raw": " ".join(raw.split()).strip()[:80],
        "method": method,
        "field": field,
    }


def propose_impact_units(title: str, summary: str) -> list[dict]:
    """Extract checkable units only. Proposed never truth. Temperance <=5."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(u: dict) -> None:
        key = f"{u['kind']}|{u['value']}|{u['unit']}"
        if key in seen:
            return
        seen.add(key)
        out.append(u)

    for field, text in (("title", title or ""), ("summary", summary or "")):
        if not text:
            continue
        if not PRICE_DENY.search(text):
            for m in RE_PRICE_CAD.finditer(text):
                raw = m.group(0)
                # A dollar symbol alone does not establish Canadian currency.
                # Skip explicit foreign-dollar comparisons instead of relabelling them.
                if FOREIGN_DOLLAR.search(text):
                    continue
                num = next((g for g in m.groups() if g), None)
                val = _parse_number(num or "")
                if val is None:
                    continue
                unit = "CAD" if CANADIAN_DOLLAR.search(text) else "dollars"
                low = raw.lower()
                suffix = text[m.end():m.end() + 24].lower()
                price_context = low + suffix
                if re.search(r"\$\s*/\s*l\b", price_context):
                    unit += "_per_L"
                if "kwh" in low or "\u00a2" in raw or re.search(r"cents?\s*/\s*k", low):
                    unit = "cents_per_kWh"
                if re.search(r"\$\s*(?:/\s*mois\b|per month\b|par mois\b)", price_context):
                    unit += "_per_month"
                add(
                    _unit(
                        kind="price",
                        value=val,
                        unit=unit,
                        raw=raw,
                        method="rules-impact-unit-v0.1-price-cad",
                        field=field,
                    )
                )
            pct_iters = list(RE_PRICE_PCT_CTX.finditer(text))
            if PRICE_CTX_RE.search(text):
                pct_iters.extend(RE_PRICE_PCT_HAUSSE.finditer(text))
            for m in pct_iters:
                raw = m.group(0)
                num = next((g for g in m.groups() if g), None)
                val = _parse_number(num or "")
                if val is None:
                    continue
                add(
                    _unit(
                        kind="price",
                        value=val,
                        unit="percent",
                        raw=raw,
                        method="rules-impact-unit-v0.1-price-pct",
                        field=field,
                    )
                )
        for m in RE_BYLAW.finditer(text):
            bid = next((g for g in m.groups() if g), None)
            if not bid:
                continue
            add(
                _unit(
                    kind="bylaw_id",
                    value=str(bid).strip(),
                    unit="id",
                    raw=m.group(0),
                    method="rules-impact-unit-v0.1-bylaw",
                    field=field,
                )
            )
        for m in RE_HOUSING_COUNT.finditer(text):
            raw = m.group(0)
            num = next((g for g in m.groups() if g), None)
            val = _parse_number(num or "")
            if val is None:
                continue
            add(
                _unit(
                    kind="housing_count",
                    value=val,
                    unit="logements",
                    raw=raw,
                    method="rules-impact-unit-v0.1-housing",
                    field=field,
                )
            )
    return out[:5]


def enrich_one(c: dict) -> dict:
    item = dict(c)
    text = blob(c)
    geo = propose_geo(c, text)
    topics = propose_topics(text)
    impacts = propose_impacts(topics, c.get("title") or "", c.get("summary") or "")
    claims = propose_claims(c)
    item["enrich_status"] = "proposed"
    item["enrich"] = {
        "method": "rules-v0.7-impact-units-claims",
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "geo": geo,
        "topics": topics,
        "impacts": impacts,
        "claims": claims,
    }
    return item


def main() -> int:
    if not IN_PATH.exists():
        print(f"Missing {IN_PATH}. Run normalize first.")
        return 1
    try:
        payload = json.loads(IN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Unreadable {IN_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{IN_PATH} must be a JSON object with a candidates list")
    cands = payload.get("candidates")
    cands = cands if isinstance(cands, list) else []
    enriched: list[dict] = []
    skipped = 0
    for c in cands:
        if not isinstance(c, dict):
            # Same fail-soft posture as cluster_issues: a malformed entry is
            # diagnosed by its count, never a traceback that kills the chain.
            skipped += 1
            continue
        enriched.append(enrich_one(c))
    now = datetime.now(timezone.utc)
    with_claims = sum(1 for c in enriched if c["enrich"]["claims"])
    with_units = sum(
        1
        for c in enriched
        if any((imp.get("units") or []) for imp in c["enrich"]["impacts"])
    )
    out = {
        "enriched_at": now.isoformat(),
        "method": (
            "scripts/enrich.py rules-v0.7 impact-units claims word-boundary — proposals only; "
            "quote+speaker objects; falsifiable units (price/bylaw_id/housing_count); "
            "no title-colon theater; Lévis≠télévision; official nests; "
            "world-fog not cloaked as quebec; w_impact remains 0 until deliberate ACT"
        ),
        "source_file": "data/normalized/latest_candidates.json",
        "source_status": payload.get("source_status") or {},
        "normalized_at": payload.get("normalized_at"),
        "candidate_count": len(enriched),
        "skipped_malformed": skipped,
        "candidates_with_claims": with_claims,
        "candidates_with_impact_units": with_units,
        "candidates": enriched,
    }
    try:
        out_show = OUT_PATH.relative_to(ROOT)
    except ValueError:
        out_show = OUT_PATH
    store_io.write_json_atomic(OUT_PATH, out)
    topics_n = sum(
        1
        for c in enriched
        if any(t.get("topic") != "other" for t in c["enrich"]["topics"])
    )
    city_n = sum(1 for c in enriched if c["enrich"]["geo"]["geo"] == "quebec-city")
    prov_n = sum(1 for c in enriched if c["enrich"]["geo"]["geo"] == "quebec")
    linked_n = sum(1 for c in enriched if c["enrich"]["geo"]["geo"] == "linked")
    print(f"enriched {len(enriched)} -> {out_show}")
    print(f"  proposed non-other topic: {topics_n}")
    print(f"  claims on {with_claims}/{len(enriched)} candidates")
    print(f"  impact units on {with_units}/{len(enriched)} candidates")
    print(f"  geo quebec-city={city_n} quebec={prov_n} linked={linked_n}")
    return 0



if __name__ == "__main__":
    sys.exit(main())
