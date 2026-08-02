"""Central Wikidata property and QID mapping for Hebrew manuscripts.

All Wikidata property IDs (PIDs) and item IDs (QIDs) used by the upload
system are defined here. No API calls — pure constants and helper functions.

Sources:
- WikiProject Manuscripts Data Model
- Digital Scriptorium (McCandless & Coladangelo, 2025)
- Prebor (iConference 2020) — NLI Hebrew manuscripts
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

# ── Wikidata PIDs ────────────────────────────────────────────────────

# Instance / classification
P_INSTANCE_OF = "P31"
P_COLLECTION = "P195"
P_FONDS = "P12095"  # WikiProject Manuscripts housing property
P_INVENTORY_NUMBER = "P217"

# Content
P_TITLE = "P1476"
P_SUBTITLE = "P1680"  # subtitle (Proposal / MARC 245$b)
P_LANGUAGE = "P407"
P_WRITING_SYSTEM = "P282"
P_GENRE = "P136"
P_MAIN_SUBJECT = "P921"
P_EXEMPLAR_OF = "P1574"

# Creation / production
P_INCEPTION = "P571"
P_LOCATION_OF_CREATION = "P1071"
P_AUTHOR = "P50"
# Author name string (used when an extracted author cannot be safely linked to
# a notable Wikidata person item).
P_AUTHOR_NAME_STRING = "P2093"
P_TRANSCRIBED_BY = "P11603"
P_ANNOTATOR = "P11105"  # WikiProject Manuscripts creation property
P_COMMISSIONED_BY = "P88"
P_SCHOOL_OF = "P1780"  # artistic attribution qualifier (DS paper)
P_WORKSHOP_OF = "P1774"  # artistic attribution qualifier (DS paper)

# Provenance
P_OWNED_BY = "P127"
P_START_TIME = "P580"
P_END_TIME = "P582"
# Verified live 2026-08-02 (Rule W-26):
#   P3342 "significant person" — "person linked to the item in any possible way"
#   P1891 "signatory"          — "person, country, or organization that has
#                                 signed an official document"
#
# Why a former owner lands on P3342 and not on P127 (Rule W-146): P127 means
# *owner of the subject*. The data model does describe P127 as the ownership
# chain distinguished by P580/P582 and preferred rank — but those dates come from
# 561$a provenance prose we do not yet parse, and an unqualified P127 reads as
# current ownership. P3342 is unambiguously true for a former owner and cannot be
# misread, so it is the honest edge until date evidence exists.
P_SIGNIFICANT_PERSON = "P3342"
P_SIGNATORY = "P1891"

# Physical description
P_MATERIAL = "P186"
P_HEIGHT = "P2048"
P_WIDTH = "P2049"
P_NUMBER_OF_PAGES = "P1104"
# P7416 "folio(s)" is a STRING qualifier used to cite a specific folio in a
# reference (e.g. "this scribe note is on folio 15r"). It is NOT a count property.
# To record the physical extent of a manuscript in folios/leaves, use P1104
# (number of pages) with unit Q107256474 (leaf) — the WikiProject Manuscripts
# Data Model explicitly recommends this: "Specifies the number of folia of a
# manuscript." (https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model)
P_NUMBER_OF_FOLIOS = "P7416"  # folio reference QUALIFIER only — not a count

# Unit QID for leaf (a bound sheet; each leaf = 2 pages/sides). Use with P1104.
Q_LEAF_UNIT = "Q107256474"

# Digital access
P_DESCRIBED_AT_URL = "P973"
P_IIIF_MANIFEST = "P6108"
P_IMAGE = "P18"

# Authority identifiers
P_NLI_J9U_ID = "P8189"
P_NLI_CATALOG_ID = "P3959"   # NNL item ID — MARC 001 / bibliographic catalog record
P_VIAF_ID = "P214"
P_GEONAMES_ID = "P1566"

# References
P_STATED_IN = "P248"
P_REFERENCE_URL = "P854"
P_RETRIEVED = "P813"

# Qualifiers
P_OBJECT_NAMED_AS = "P1932"
P_SOURCING_CIRCUMSTANCES = "P1480"

# Part relationships
P_PART_OF = "P361"
P_HAS_PARTS = "P527"

# WikiProject tagging
P_ON_FOCUS_LIST = "P5008"

# Conservation / condition
P_CONDITION = "P5816"

# Script / paleography
P_SCRIPT_STYLE = "P9302"

# Content description
P_SUMMARY = "P7535"

# Illustrator (for illuminated manuscripts)
P_ILLUSTRATOR = "P110"

# Provenance
P_SIGNIFICANT_EVENT = "P793"
P_DONATED_BY = "P1028"

# Binding
P_HAS_QUALITY = "P1552"

# Date qualifiers
P_EARLIEST_DATE = "P1319"
P_LATEST_DATE = "P1326"

# Occupation (for persons)
P_OCCUPATION = "P106"

# Date of birth / death (persons)
P_DATE_OF_BIRTH = "P569"
P_DATE_OF_DEATH = "P570"

# Country of citizenship
P_COUNTRY_OF_CITIZENSHIP = "P27"

# Catalog code
P_CATALOG_CODE = "P528"
P_CATALOG = "P972"

# Scope and content (summary / abstract)
P_SCOPE_AND_CONTENT = "P7535"

# Full work available at URL (digitized manuscript)
P_FULL_WORK_URL = "P953"

# Copyright status
P_COPYRIGHT_STATUS = "P6216"

# Folio/section qualifier (used on P1574 exemplar of)
# WikiProject Manuscripts recommends P958 (section) over P7416 (folio)
# for specifying where a work appears within a manuscript.
P_FOLIO = "P958"

# Provenance chain qualifiers
P_BEFOREHAND_OWNED_BY = "P11811"
P_AFTERWARD_OWNED_BY = "P11812"

# First/last line (manuscript incipits/explicits)
P_FIRST_LINE = "P1922"
P_LAST_LINE = "P3132"

# Inscription (colophons, scribal interventions)
P_INSCRIPTION = "P1684"
P_OBJECT_HAS_ROLE = "P3831"

# Codicological structure
P_NUMBER_OF_PARTS = "P2635"

# Volume
P_VOLUME = "P478"

# Significant place (associated, not creation location)
P_SIGNIFICANT_PLACE = "P7153"

# Epistemological provenance
P_BASED_ON_HEURISTIC = "P887"

# ── Phase 1 HMO-fidelity enrichment (Rule 42) ────────────────────────
# Added 2026-05-17. See plans/smooth-humming-feather.md.

P_NATURE_OF_STATEMENT = "P5102"      # nature of statement (hypothesis, dubious, ...)
P_APPLIES_TO_PART = "P518"           # applies to part / sub-section scoping
P_STATEMENT_SUPPORTED_BY = "P3680"   # supported by (claim-level evidence)
P_REASON_DEPRECATED_RANK = "P2241"   # reason for deprecated rank
P_EXACT_MATCH = "P2888"              # exact match (URI; bridge to HMO IRI)

# Fallback HMO IRI template for the sidecar path in hmo_crosswalk._records_from_rdf.
# The canonical HMO namespace lives in converter.config.namespaces.HM; this is
# only used when the RDF graph isn't loaded and we need to synthesize an IRI
# from a control number. A warning is logged when this path fires.
# This is the INTERNAL graph identifier used by output.ttl for RDF traversal;
# it is NOT the URL emitted into Wikidata P2888 (see HMO_WIKIBASE_BASE_URL).
HMO_NS_TEMPLATE = "https://w3id.org/mhm/ontology#MS_{control_number}"

# Project-owned Wikibase Cloud instance hosting the HMO graph entities. This
# is the public, resolvable URI that Wikidata P2888 (exact match) points at.
# Only ``/wiki/Item:Q<n>`` pages resolve on Wikibase Cloud — the planned
# ``/wiki/MS_<cn>`` redirect pages were never created (404). Ontology IRIs
# under ``w3id.org/mhm/ontology#…`` are RDF identifiers, not browse pages
# (and the w3id ontology redirect historically served a Git LFS pointer).
HMO_WIKIBASE_BASE_URL = "https://mhm-hmo.wikibase.cloud"
_HMO_QID_RE = re.compile(r"^Q[1-9][0-9]*$")
_HMO_ITEM_PAGE_RE = re.compile(
    rf"^{re.escape(HMO_WIKIBASE_BASE_URL)}/wiki/Item:(Q[1-9][0-9]*)$"
)
_HMO_ONTOLOGY_IRI_RE = re.compile(r"^https?://w3id\.org/mhm/ontology#", re.I)
_HMO_MANUSCRIPT_PERMALINK_RE = re.compile(
    r"^https?://w3id\.org/mhm/manuscript/", re.I
)


def hmo_wikibase_item_url(qid: str) -> str:
    """Build a browseable ``/wiki/Item:Q<n>`` URL, or ``\"\"`` if invalid."""
    cleaned = str(qid or "").strip()
    if not _HMO_QID_RE.fullmatch(cleaned):
        return ""
    return f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:{cleaned}"


def is_browseable_hmo_wikibase_url(url: str) -> bool:
    """True only for live project Wikibase item pages (Rule W-85 / W-122)."""
    return bool(_HMO_ITEM_PAGE_RE.fullmatch(str(url or "").strip()))


def is_hmo_identity_placeholder_url(url: str) -> bool:
    """True for RDF IRIs / dead MS_ slug URLs that must not be P2888 targets."""
    text = str(url or "").strip()
    if not text:
        return False
    if _HMO_ONTOLOGY_IRI_RE.match(text) or _HMO_MANUSCRIPT_PERMALINK_RE.match(text):
        return True
    return f"{HMO_WIKIBASE_BASE_URL}/wiki/MS_" in text


def hmo_wikibase_page_url(control_number: str) -> str:
    """Deprecated MS_ slug fallback — always empty (pages 404 on Wikibase).

    Kept so callers that still OR this with :func:`hmo_wikibase_entity_url`
    fail closed instead of emitting a dead link. Prefer
    :func:`resolve_hmo_bridge_url`.
    """
    _ = control_number
    return ""


def hmo_wikibase_entity_url(
    control_number: str, instance_qids: dict[str, str] | None
) -> str | None:
    """Build the real ``/wiki/Item:Q<n>`` URL once Phase 5 has uploaded
    this manuscript to the HMO Wikibase Cloud.

    ``instance_qids`` maps ``control_number -> live QID`` — callers with
    database access (``app.pipeline.wikidata_studio``) build this dict
    once per build from ``wikibase_entity_mappings``; this module stays
    DB-agnostic. Returns ``None`` when the manuscript hasn't been
    uploaded yet (do not invent a dead MS_ slug).
    """
    if not instance_qids:
        return None
    qid = str(instance_qids.get((control_number or "").strip()) or "").strip()
    url = hmo_wikibase_item_url(qid)
    return url or None


def resolve_hmo_bridge_url(
    control_number: str,
    instance_qids: dict[str, str] | None = None,
    *,
    wikibase_qid: str | None = None,
) -> str:
    """Browseable HMO Wikibase item URL for P2888/P973, or ``\"\"`` to skip."""
    direct = hmo_wikibase_item_url(str(wikibase_qid or ""))
    if direct:
        return direct
    return hmo_wikibase_entity_url(control_number, instance_qids) or ""

# ── Wikidata QIDs ────────────────────────────────────────────────────

# Type classifications
Q_MANUSCRIPT = "Q87167"
Q_PRINTED_BOOK = "Q571"  # book; used for explicit printed facsimile editions
Q_CODEX = "Q213924"  # WPM-discouraged as primary P31 — prefer Q87167 / Q_COMPOSITE_MANUSCRIPT
Q_ILLUMINATED_MANUSCRIPT = "Q48498"
Q_MANUSCRIPT_FRAGMENT = "Q30103158"
Q_COMPOSITE_MANUSCRIPT = "Q33308141"  # multi-text / multi-volume / anthology strata
Q_PALIMPSEST = "Q274076"              # manuscript reused after scraping (Q179808 = Palme d'Or — WRONG)
Q_PALM_LEAF_MANUSCRIPT = "Q1641020"
Q_CHAINED_BOOK = "Q19602268"  # optional additive P31 (WPM)
Q_UNKNOWN_TEXT = "Q234460"  # DS fallback when no suitable work item exists (P1574 + P1932)
Q_LOWER_SCRIPT = "Q122901270"  # palimpsest layer qualifier via P518
Q_UPPER_SCRIPT = "Q122901275"  # palimpsest layer qualifier via P518
Q_HUMAN = "Q5"
Q_WRITTEN_WORK = "Q47461344"
Q_ORGANIZATION = "Q43229"
Q_ISRAEL_MUSEUM = "Q46815"  # verified Israel Museum, Jerusalem

# WPM discourages these as primary manuscript P31 values (prefer Q87167 + genre).
DISCOURAGED_MANUSCRIPT_P31: frozenset[str] = frozenset({
    Q_CODEX,
    "Q113016548",
    "Q95065857",
    "Q284465",  # lectionary — use P136, not P31
})

# Collections / institutions
# Verified live against the Wikidata API on 2026-07-29 (Rule W-26): each label,
# description and P31 was read from wbgetentities before being added here.
Q_BRITISH_LIBRARY = "Q23308"    # "national library of the United Kingdom"
Q_BODLEIAN = "Q82133"           # "main research library of the University of Oxford"
Q_NLI = "Q188915"
Q_KTIV = "Q118384267"

# Writing system
Q_HEBREW_ALPHABET = "Q33513"

# Sourcing / certainty
Q_CIRCA = "Q5727902"
Q_PRESUMABLY = "Q18122778"  # presumably — uncertain attribution (e.g. NER-identified scribe)
Q_POSSIBLY = "Q30230067"    # possibly — very uncertain attribution (Q21857942 = Stolpersteine in Upper Austria — WRONG)
Q_HYPOTHESIS = "Q41719"     # hypothesis — used as P5102 value for inferred claims
Q_DUBIOUS = "Q104378399"    # dubious — used as P5102 value for contested claims

# WikiProject
Q_WIKIPROJECT_MANUSCRIPTS = "Q123078816"

# Condition states (WikiProject Manuscripts P5816 vocabulary)
Q_GOOD_CONDITION = "Q56557591"  # preserved
Q_NOT_COMPLETED = "Q20734200"
Q_MILDLY_DAMAGED = "Q107531416"
Q_DAMAGED = "Q106379705"  # damaged
Q_DEMOLISHED_OR_DESTROYED = "Q56556915"
Q_UNLOCATED_PROBABLY_DESTROYED = "Q106959824"
Q_UNKNOWN_PRESERVATION = "Q66890153"
Q_DISASSEMBLED = "Q61962974"
Q_FRAGMENT = "Q3749265"  # fragment (object) — NOT a P5816 value; use Q_MANUSCRIPT_FRAGMENT as P31
Q_RESTORED = "Q75505084"  # restored
Q_POOR_CONDITION = "Q136350185"  # poor (project extension; prefer WPM damaged/mildly damaged)

# Inscription roles (colophon, gloss, correction, marginalia)
Q_COLOPHON = "Q372474"
Q_GLOSS = "Q860740"
Q_CORRECTION = "Q3299332"
Q_MARGINALIA = "Q1136474"

# Copyright status
Q_COPYRIGHTED = "Q50423863"  # copyrighted
Q_PUBLIC_DOMAIN = "Q19652"  # public domain

# Occupations for persons
Q_SCRIBE = "Q916292"
Q_AUTHOR_OCCUPATION = "Q482980"
Q_TRANSLATOR_OCCUPATION = "Q333634"
Q_COMMENTATOR_OCCUPATION = "Q106313281"

# Script styles (P9302 values) — mapped from HMO TypeScriptType
SCRIPT_TYPE_TO_QID: dict[str, str] = {
    "AshkenaziScript": "Q121094898",
    "SepharadicScript": "Q133177480",
    "ItalianScript": "Q133370075",
    "ByzantineScript": "Q133370466",
    "YemeniteScript": "Q121094936",
    "OrientalScript": "Q133327488",
}

# Genre mappings — from HMO genre → Wikidata QID
GENRE_TO_QID: dict[str, str] = {
    # HMO ontology genre types
    "BiblicalText": "Q55017318",  # biblical literature
    "TalmudicText": "Q43290",  # Talmud
    "MishnaicText": "Q191825",  # Mishnah
    "HalachicText": "Q107427",  # Halakha
    "KabbalisticText": "Q123006",  # Kabbalah
    "PhilosophicalText": "Q5891",  # philosophy
    "PoeticText": "Q482",  # poetry
    "LiturgicalText": "Q172331",  # liturgy
    "MedicalText": "Q11190",  # medicine
    "CommentaryText": "Q1749541",  # commentary
    "GrammaticalText": "Q8091",  # grammar
    # MARC genre/form strings (from NLI catalog data)
    "Poetry": "Q482",  # poetry
    "Piyyutim": "Q781402",  # piyyut (Hebrew liturgical poetry)
    "Personal correspondence": "Q133492",  # letter
    "Mezuzot": "Q247034",  # mezuzah
    "Legislation (Jewish law)": "Q107427",  # Halakha
    "Drama": "Q25372",  # drama
    "Pinkasim": "Q2095829",  # pinkas (communal record book)
    "Family records": "Q485228",  # family register
    "Registers of births, etc.": "Q18562479",  # vital record
    "Autograph manuscripts": "Q9026959",  # autograph (handwritten by author)
    "Bibliographies": "Q1631107",  # bibliography
    "Tales": "Q49084",  # short story / tale
    "Negotiable instruments": "Q3359388",  # negotiable instrument
    "Riddles": "Q47054",  # riddle
    "Death registers": "Q3348095",  # register of deaths
    "Business records (Manuscript)": "Q804154",  # business record
    "Licenses": "Q79719",  # license
    "Records (Documents)": "Q49848",  # document
    "Community records (Manuscript)": "Q2095829",  # pinkas (communal record)
    "Literature (Miscellaneous, in manuscript)": "Q8242",  # literature
    "Biographies (Manuscript)": "Q36279",  # biography
    "Parodies": "Q170539",  # parody
    "Ketubbot": "Q1543943",  # ketubah (marriage contract)
    "Prayer books": "Q471894",  # Siddur (Jewish prayerbook)
    "Sermons": "Q60797",  # sermon
    "Commentaries": "Q1749541",  # commentary
    "Responsa (Jewish law)": "Q3427762",  # Rabbinic responsa
    "Manuscripts, Hebrew": "Q87167",  # Hebrew manuscript
    "Wills": "Q25538572",  # will / testament
    "Letters": "Q133492",  # letter
}

# Well-known works that already exist on Wikidata (labels verified live 2026-07-26)
KNOWN_WORK_QIDS: dict[str, str] = {
    "Torah": "Q34990",
    "תורה": "Q34990",
    "Talmud": "Q43290",
    "תלמוד": "Q43290",
    "Mishnah": "Q191825",
    "משנה": "Q191825",
    "Zohar": "Q205388",
    "זוהר": "Q205388",
    "Shulchan Aruch": "Q822206",
    "שלחן ערוך": "Q822206",
    "Mishneh Torah": "Q201029",
    "משנה תורה": "Q201029",
    # Q1845 — Bible (collection of sacred books in Judaism and Christianity)
    "Bible": "Q1845",
    # Q83367 — Tanakh / Hebrew Bible
    "Tanakh": "Q83367",
    "Tanach": "Q83367",
    "Hebrew Bible": "Q83367",
    'תנ"ך': "Q83367",
    "תנך": "Q83367",
    # Q623354 — Haggadah / Passover Haggadah (הגדה של פסח)
    "Haggadah": "Q623354",
    "Passover Haggadah": "Q623354",
    "הגדה של פסח": "Q623354",
    "הגדה": "Q623354",
    # Q2740944 — Tikkun Chatzot (תיקון חצות)
    "Tikkun Chatzot": "Q2740944",
    "Tikun Chatzot": "Q2740944",
    "תיקון חצות": "Q2740944",
    "תקון חצות": "Q2740944",
}

# Exact, verified aliases for common Hebrew work labels emitted by NLI MARC
# records. Keep this list deliberately small: an uncertain QID is worse than
# a new, source-backed work item. The parenthetical-prefix rules below cover
# editions/chapters of the same canonical work.
KNOWN_WORK_TITLE_ALIASES: dict[str, str] = {
    "יוסיפון": "Q1561132",                  # Josippon
    "פרוש המשנה לרמבם": "Q6124976",         # Pirush Hamishnayot
    "פירוש המשנה לרמבם": "Q6124976",
}


def known_work_qid_for_title(title: str) -> str | None:
    """Return a verified QID for an exact or edition-qualified work title.

    Hebrew gershayim are punctuation in the canonical aliases (``רמב"ם``),
    so matching removes only internal quote marks and normalises whitespace.
    No fuzzy matching is performed: ambiguous titles must remain local work
    candidates and go through the normal reconcile-before-upload guard.
    """
    clean = re.sub(r"\s+", " ", str(title or "").strip())
    clean = clean.rstrip(" .,;:/-")
    if not clean:
        return None
    direct = KNOWN_WORK_QIDS.get(clean) or KNOWN_WORK_TITLE_ALIASES.get(clean)
    if direct:
        return direct
    canonical = clean.replace('"', "").replace("׳", "").replace("״", "")
    direct = KNOWN_WORK_QIDS.get(canonical) or KNOWN_WORK_TITLE_ALIASES.get(canonical)
    if direct:
        return direct
    if canonical.startswith("פרוש המשנה לרמבם ("):
        return KNOWN_WORK_TITLE_ALIASES["פרוש המשנה לרמבם"]
    if canonical.startswith("פירוש המשנה לרמבם ("):
        return KNOWN_WORK_TITLE_ALIASES["פירוש המשנה לרמבם"]
    return None

# Bible books → Wikidata QIDs (for P921 main subject from canonical_references)
BIBLE_BOOK_TO_QID: dict[str, str] = {
    "Genesis": "Q9184",
    "Exodus": "Q9190",
    "Leviticus": "Q23767",
    "Numbers": "Q23775",
    "Deuteronomy": "Q23790",
    "Joshua": "Q131168",
    "Samuel": "Q178547",
    "Kings": "Q182060",
    "Isaiah": "Q131135",
    "Jeremiah": "Q131144",
    "Psalms": "Q41064",
    "Proverbs": "Q29539",
    "Job": "Q43304",
}

# Talmud Bavli tractates → Wikidata QIDs (for P921 main subject)
TALMUD_TRACTATE_TO_QID: dict[str, str] = {
    "ברכות": "Q598626",
    "שבת": "Q2276714",
    "פסחים": "Q2364178",
    "יומא": "Q2605561",
    "סוטה": "Q1544949",
    "קידושין": "Q2360571",
    "בבא קמא": "Q806189",
    "בבא בתרא": "Q806186",
    "סנהדרין": "Q605375",
    "עבודה זרה": "Q1135584",
    "כתובות": "Q2360474",
    "נדרים": "Q2604843",
    "נזיר": "Q2605296",
    "שבועות": "Q2606013",
}

# LCSH subject terms → Wikidata QIDs (for P921 main subject)
SUBJECT_TO_QID: dict[str, str] = {
    "Eretz Israel": "Q155321",  # Land of Israel
    "Jews": "Q7325",  # Jews
    "Karaites": "Q208398",  # Karaite Judaism
    "Jewish law": "Q107427",  # Halakha
    "Cabala": "Q123006",  # Kabbalah
    "Astronomy": "Q333",  # astronomy
    "Responsa": "Q3427762",  # Rabbinic responsa
    "Philosophy": "Q5891",  # philosophy
    "Jewish philosophy": "Q837795",  # Jewish philosophy
    "Shehitah": "Q861258",  # shechita (kosher slaughter)
    "Christianity": "Q5043",  # Christianity
    "Jewish sermons, Hebrew": "Q60797",  # sermon
    "Jewish calendar": "Q44722",  # Hebrew calendar
    "Hebrew language": "Q9288",  # Hebrew language
    "Dreams": "Q36348",  # dream
    "Earthquakes": "Q7944",  # earthquake
    "Medicine": "Q11190",  # medicine
    "Astrology": "Q34362",  # astrology
    "Phlebotomy": "Q3595842",  # phlebotomy
    "Berit milah": "Q848599",  # brit milah
    "Bar mitzvah": "Q28807008",  # Bar Mitzvah (Jewish ceremony)
    "Gematria": "Q840378",  # gematria
    "Masorah": "Q3850835",  # verified Masorah concept
    "Purim": "Q180115",  # Purim
    "Apostasy": "Q223681",  # apostasy
    "Liturgy": "Q172331",  # liturgy
    "Prayer": "Q40953",  # prayer
    "Bible": "Q1845",  # Bible
    "Talmud": "Q43290",  # Talmud
    "Torah scrolls": "Q2350579",  # Sefer Torah
    "Sepulchral monuments": "Q56055312",  # sepulchral monument
    "Christian converts from Judaism": "Q814999",  # conversion to Christianity
    "Devil": "Q6674",  # devil
    "Tombs": "Q381885",  # tomb
    "Abbreviations, Hebrew": "Q102786",  # abbreviation
    "Sheluhe de-rabanan": "Q7487201",  # Shaliah (Jewish legal emissary)
    # Hebrew topical headings (MARC 650)
    "מקרא": "Q1845",  # Bible
    "תורה": "Q34990",  # Torah
    "תלמוד": "Q43290",  # Talmud
    "משנה": "Q191825",  # Mishnah
    "הלכה": "Q107427",  # Halakha
    "קבלה": "Q123006",  # Kabbalah
    "פילוסופיה": "Q5891",  # philosophy
    "שירה": "Q482",  # poetry
    "ליטורגיה": "Q172331",  # liturgy
    "תפילה": "Q40953",  # prayer
}

# ── Language code → QID mapping ──────────────────────────────────────

LANG_TO_QID: dict[str, str] = {
    "heb": "Q9288",
    "ara": "Q13955",
    "arc": "Q28602",
    "jrb": "Q37733",
    "lad": "Q36196",
    "yid": "Q8641",
    "jpr": "Q33367",
    "lat": "Q397",
    "grc": "Q35497",
    "per": "Q9168",
    "ger": "Q188",
    "spa": "Q1321",
    "ita": "Q652",
    "fre": "Q150",
    "eng": "Q1860",
    "por": "Q5146",
    "tur": "Q256",
    "dut": "Q7411",  # Dutch (MARC language code)
    "gre": "Q36510",  # Modern Greek (MARC language code)
    "tat": "Q25285",  # Tatar (MARC language code)
}

# ── Material → QID mapping ───────────────────────────────────────────

MATERIAL_TO_QID: dict[str, str] = {
    "Parchment": "Q226697",
    "parchment": "Q226697",
    "Vellum": "Q378274",
    "vellum": "Q378274",
    "Paper": "Q11472",
    "paper": "Q11472",
    "Papyrus": "Q125576",
    "papyrus": "Q125576",
    # Hebrew forms
    "קלף": "Q226697",
    "נייר": "Q11472",
    "פפירוס": "Q125576",
}

# ── Condition keyword → QID mapping ─────────────────────────────────
# "fragment" is intentionally absent: it is a P31 class (Q_MANUSCRIPT_FRAGMENT),
# not a conservation-status value (WPM Data Model).

CONDITION_TO_QID: dict[str, str] = {
    "good": Q_GOOD_CONDITION,
    "preserved": Q_GOOD_CONDITION,
    "טוב": Q_GOOD_CONDITION,
    "mildly damaged": Q_MILDLY_DAMAGED,
    "damaged": Q_DAMAGED,
    "פגום": Q_DAMAGED,
    "not completed": Q_NOT_COMPLETED,
    "incomplete": Q_NOT_COMPLETED,
    "destroyed": Q_DEMOLISHED_OR_DESTROYED,
    "demolished": Q_DEMOLISHED_OR_DESTROYED,
    "unlocated": Q_UNLOCATED_PROBABLY_DESTROYED,
    "disassembled": Q_DISASSEMBLED,
    "unknown": Q_UNKNOWN_PRESERVATION,
    "restored": Q_RESTORED,
    "repaired": Q_RESTORED,
    "משוקם": Q_RESTORED,
    "poor": Q_DAMAGED,  # map poor → WPM damaged rather than a non-WPM QID
}

# Keywords that force P31=manuscript fragment instead of P5816.
FRAGMENT_CONDITION_KEYWORDS: tuple[str, ...] = ("fragment", "קטע")

# ── NER/MARC role → Wikidata PID mapping ─────────────────────────────
# Manuscript-side roles only. Author/editor/contributor authorship belongs on
# the WORK (MS → P1574 → work → P50), never as P50 on the manuscript.
# Unsupported roles are omitted so the linker skips them fail-closed.

ROLE_TO_PID: dict[str, str] = {
    # NER roles (uppercase)
    "AUTHOR": P_AUTHOR,  # creates/links person; never emitted as MS P50
    "TRANSCRIBER": P_TRANSCRIBED_BY,
    "OWNER": P_OWNED_BY,
    "ANNOTATOR": P_ANNOTATOR,
    "COMMISSIONER": P_COMMISSIONED_BY,
    "ILLUMINATOR": P_ILLUSTRATOR,
    # Censor identity is retained in source evidence; it is not ownership.
    # Fix 2026-04-15 third audit Fix #15: translators belong on P655, not P50
    # (author). Commentators belong on P9046 (commentary by). Using P50 for
    # these roles produces constraint violations and misleading author links.
    "TRANSLATOR": "P655",  # translator
    "COMMENTATOR": "P9046",  # commentary by
    # MARC roles (lowercase)
    "author": P_AUTHOR,
    "scribe": P_TRANSCRIBED_BY,
    "copyist": P_TRANSCRIBED_BY,
    "illuminator": P_ILLUSTRATOR,
    "annotator": P_ANNOTATOR,
    "commissioned by": P_COMMISSIONED_BY,
    "commissioner": P_COMMISSIONED_BY,
    "patron": P_COMMISSIONED_BY,
    "translator": "P655",
    "commentator": "P9046",
    # Hebrew role variants
    "סופר": P_TRANSCRIBED_BY,
    "מעתיק": P_TRANSCRIBED_BY,
    "בעלים": P_OWNED_BY,
    "בעל": P_OWNED_BY,
    "(ממנו)": P_OWNED_BY,
    "owner": P_OWNED_BY,
    "transcriber": P_TRANSCRIBED_BY,
    # Rule W-146. These roles were absent from this map, so every approved match
    # carrying one was silently dropped: on the reference run, 49 former-owner,
    # 27 mentioned and 13 signatory rows produced no statement at all, which is
    # why 124 of 140 person items had nothing pointing at them.
    "former owner": P_SIGNIFICANT_PERSON,
    "בעלים קודמים": P_SIGNIFICANT_PERSON,
    "mentioned": P_SIGNIFICANT_PERSON,
    "נזכר": P_SIGNIFICANT_PERSON,
    "signatory": P_SIGNATORY,
    "חותם": P_SIGNATORY,
}

# ── Date precision constants (Wikidata time model) ───────────────────

PRECISION_GIGAYEAR = 0
PRECISION_CENTURY = 7
PRECISION_DECADE = 8
PRECISION_YEAR = 9
PRECISION_MONTH = 10
PRECISION_DAY = 11


# ── Helper functions ─────────────────────────────────────────────────


def nli_j9u_id(control_number: str) -> str:
    """Extract or format an NLI J9U identifier from a control number.

    The J9U format expected by Wikidata P8189 is the raw NLI system number,
    typically matching pattern 98[0-9]{12}5171.

    Args:
        control_number: NLI system number (e.g., "990001188700205171").

    Returns:
        The control number as-is (it is already in J9U format for NLI records).
    """
    return control_number.strip()


def nli_catalog_url(control_number: str) -> str:
    """Build a URL to the NLI catalog record for a manuscript.

    Args:
        control_number: NLI system number.

    Returns:
        URL string pointing to the NLI catalog viewer.
    """
    cn = control_number.strip()
    return f"https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH{cn}"


def nli_reference(control_number: str) -> list[dict[str, str]]:
    """Build a Wikidata reference snak set for NLI catalog sourcing.

    Every statement added to Wikidata should include this reference.
    P3959 (NNL item ID / MARC 001) is included so the exact source
    bibliographic record is queryable via SPARQL on every item — not
    just buried inside a URL string.

    Args:
        control_number: NLI system number (MARC 001 field, e.g. '990000403370205171').

    Returns:
        List of reference snak dicts with P248, P3959, P854, P813.
    """
    today = datetime.now(tz=UTC).strftime("+%Y-%m-%dT00:00:00Z")
    snaks: list[dict[str, str]] = [
        {"property": P_STATED_IN, "value": Q_KTIV, "type": "item"},
    ]
    if control_number:
        snaks.append(
            {"property": P_NLI_CATALOG_ID, "value": control_number.strip(), "type": "external-id"}
        )
    snaks += [
        {"property": P_REFERENCE_URL, "value": nli_catalog_url(control_number), "type": "url"},
        {"property": P_RETRIEVED, "value": today, "type": "time", "precision": PRECISION_DAY},
    ]
    return snaks


def nli_authority_reference(authority_id: str) -> list[dict[str, str]]:
    """Build a reference for an NLI authority record (9870… J9U ID)."""
    today = datetime.now(tz=UTC).strftime("+%Y-%m-%dT00:00:00Z")
    value = authority_id.strip()
    return [
        {"property": P_STATED_IN, "value": Q_KTIV, "type": "item"},
        {"property": P_NLI_J9U_ID, "value": value, "type": "external-id"},
        {
            "property": P_REFERENCE_URL,
            "value": f"https://www.nli.org.il/en/authorities/{value}",
            "type": "url",
        },
        {"property": P_RETRIEVED, "value": today, "type": "time", "precision": PRECISION_DAY},
    ]

def viaf_reference(viaf_id: str) -> list[dict[str, str]]:
    """Build a Wikidata reference snak set for VIAF-cluster sourcing.

    Used on person/work statements where the data was harvested from a
    VIAF cluster (mirror of nli_reference). Bug fix 2026-04-16 (deeper
    audit Fix #1): person items previously emitted ALL statements with
    no references, which is a WikiProject Authority Control violation
    and the most common trigger for bot-blocks at WD:AN.

    Args:
        viaf_id: numeric VIAF cluster ID (e.g. "51777166").

    Returns:
        Reference snak dicts. Stated-in: VIAF (Q54919); reference URL:
        https://viaf.org/viaf/<id>; retrieved: today.
    """
    today = datetime.now(tz=UTC).strftime("+%Y-%m-%dT00:00:00Z")
    return [
        {"property": P_STATED_IN, "value": "Q54919", "type": "item"},
        {
            "property": P_REFERENCE_URL,
            "value": f"https://viaf.org/viaf/{viaf_id}",
            "type": "url",
        },
        {"property": P_RETRIEVED, "value": today, "type": "time", "precision": PRECISION_DAY},
    ]


# ── Identifier-format normalisers ────────────────────────────────────
# Wikidata enforces strict format constraints on external identifiers.
# These helpers normalise raw values from VIAF clusters to the canonical
# format Wikidata expects, so we do not generate mass constraint
# violations on every person item we write.
# Bug fix 2026-04-16 (deeper audit Fixes #4-#6).


def normalize_lccn(raw: str | None) -> str | None:
    """Normalise an LCCN string to Wikidata's P244 canonical form.

    P244 format constraint: ``^(n|nb|nr|no|ns|sh|sj) [0-9]{2,10}$`` —
    a recognised prefix, exactly one space, then digits.

    Returns the canonical form or ``None`` if the input cannot be
    normalised (callers should drop unmatched values rather than
    write them and trigger a constraint violation).
    """
    if not raw:
        return None
    s = str(raw).strip()
    m = re.match(r"^(n|nb|nr|no|ns|sh|sj)\s*([0-9]{2,10})$", s)
    if not m:
        return None
    return f"{m.group(1)} {m.group(2)}"


def normalize_isni(raw: str | None) -> str | None:
    """Normalise an ISNI string to Wikidata's P213 canonical form.

    P213 format constraint: ``\\d{4} \\d{4} \\d{4} \\d{3}[\\dX]`` —
    sixteen alphanumerics in four space-separated groups (final char
    may be 'X' as ISNI checksum).

    Returns the canonical form or ``None`` if the input is not a
    valid ISNI.
    """
    if not raw:
        return None
    s = re.sub(r"\s+", "", str(raw))
    if not re.fullmatch(r"\d{15}[\dX]", s):
        return None
    return f"{s[0:4]} {s[4:8]} {s[8:12]} {s[12:16]}"


def normalize_bnf(raw: str | None) -> str | None:
    """Normalise a BnF identifier to Wikidata's P268 canonical form.

    P268 format constraint: ``\\d{8}[0-9bcdfghjkmnpqrstvwxz]`` — eight
    digits followed by a single check character. The "cb" prefix used
    by some BnF systems must be stripped.

    Returns the canonical form or ``None`` if invalid.
    """
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s.startswith("cb"):
        s = s[2:]
    if not re.fullmatch(r"\d{8}[0-9bcdfghjkmnpqrstvwxz]", s):
        return None
    return s


PRECISION_CENTURY = 7

# Hebrew ordinal → century number mapping
_HEBREW_ORDINAL_TO_INT: dict[str, int] = {
    "א": 1,
    "ב": 2,
    "ג": 3,
    "ד": 4,
    "ה": 5,
    "ו": 6,
    "ז": 7,
    "ח": 8,
    "ט": 9,
    "י": 10,
    'י"א': 11,
    'י"ב': 12,
    'י"ג': 13,
    'י"ד': 14,
    'י"ה': 15,
    'ט"ו': 15,
    'י"ו': 16,
    'ט"ז': 16,
    'י"ז': 17,
    'י"ח': 18,
    'י"ט': 19,
    "כ": 20,
    'כ"א': 21,
}


def _parse_hebrew_century(text: str) -> int | None:
    """Parse Hebrew century string like 'מאה ט"ז' → 16 (= 1500s)."""
    # Clean CSV double-quote escaping
    text = text.replace('""', '"')
    match = re.search(r'מאה\s+([א-ת]["\u05F4\']?[א-ת]?)', text)
    if not match:
        return None
    ordinal = match.group(1).strip()
    # Try direct lookup
    century = _HEBREW_ORDINAL_TO_INT.get(ordinal)
    if century:
        return century
    # Try with quote variations
    for variant in [ordinal, ordinal.replace("'", '"'), ordinal.replace('"', "'")]:
        century = _HEBREW_ORDINAL_TO_INT.get(variant)
        if century:
            return century
    return None


# Calendar model URIs for Wikidata time values.
# Fix 2026-04-15 third audit Fix #13: all pre-1583 dates should use the
# proleptic Julian calendar (Help:Dates). Default was Gregorian for everything.
GREGORIAN_CALENDAR = "http://www.wikidata.org/entity/Q1985727"
JULIAN_CALENDAR = "http://www.wikidata.org/entity/Q1985786"


def _calendar_for_year(year: int | None) -> str:
    """Return the correct calendar model URI for a given year.

    Dates before 1583 (when the Gregorian calendar was first adopted) should
    use the proleptic Julian calendar. See Help:Dates on Wikidata.
    """
    if year is not None and year < 1583:
        return JULIAN_CALENDAR
    return GREGORIAN_CALENDAR


# DateResult: (ISO time string, precision int, calendarmodel URI,
#              earliest_year int | None, latest_year int | None)
# The last two fields are non-None only for century-precision dates and are
# used to add P1319/P1326 qualifiers (Fix 2026-04-15 third audit Fix #12).
DateResult = tuple[str, int, str, "int | None", "int | None"]


def date_to_wikidata(dates_dict: dict[str, object]) -> DateResult | None:
    """Convert a pipeline dates dict to a Wikidata time value and precision.

    Handles: structured years, English century strings, Hebrew century strings
    (מאה ט"ז = 16th century), and approximate dates.

    Returns:
        Tuple of (ISO time string, precision int, calendarmodel URI,
        earliest_year | None, latest_year | None) or None if no date available.
        The earliest/latest years are set for century-precision dates and should
        be used to add P1319/P1326 qualifiers to the inception statement.
    """
    if not dates_dict:
        return None

    year = dates_dict.get("year")
    date_format = dates_dict.get("date_format", "")

    if year is not None:
        year_int = int(year)
        calendar = _calendar_for_year(year_int)
        if date_format == "FullDate":
            return f"+{year_int:04d}-01-01T00:00:00Z", PRECISION_YEAR, calendar, None, None
        return f"+{year_int:04d}-00-00T00:00:00Z", PRECISION_YEAR, calendar, None, None

    # No structured year — try to parse from original string
    original = str(dates_dict.get("original_string", "")).replace('""', '"')
    if not original:
        return None

    # English century: "16th century"
    # Wikidata precision-7 (PRECISION_CENTURY) interprets the stored year
    # as the START of the century, NOT the midpoint. The 16th century
    # (1501-1600) must be encoded as +1501-00-00, not +1550-00-00.
    # Bug fix 2026-04-15: changed +50 → +1.
    century_match = re.search(r"(\d{1,2})(?:th|st|nd|rd)\s*cent", original, re.IGNORECASE)
    if century_match:
        century = int(century_match.group(1))
        start_year = (century - 1) * 100 + 1
        end_year = century * 100
        return (
            f"+{start_year:04d}-00-00T00:00:00Z",
            PRECISION_CENTURY,
            _calendar_for_year(start_year),
            start_year,
            end_year,
        )

    # Hebrew century: "מאה ט"ז" (16th century)
    heb_century = _parse_hebrew_century(original)
    if heb_century:
        start_year = (heb_century - 1) * 100 + 1
        end_year = heb_century * 100
        return (
            f"+{start_year:04d}-00-00T00:00:00Z",
            PRECISION_CENTURY,
            _calendar_for_year(start_year),
            start_year,
            end_year,
        )

    # Hebrew century range: "מאה י"ד-ט"ו" — use the EARLIER century as the
    # main value (precision 7 = century); the full range is captured via
    # P1319/P1326 (Fix 2026-04-15 third audit Fix #12).
    range_match = re.search(
        r'מאה\s+([א-ת]["\u05F4\']?[א-ת]?)\s*[-–]\s*([א-ת]["\u05F4\']?[א-ת]?)',
        original.replace('""', '"'),
    )
    if range_match:
        c1 = _HEBREW_ORDINAL_TO_INT.get(range_match.group(1).strip())
        c2 = _HEBREW_ORDINAL_TO_INT.get(range_match.group(2).strip())
        if c1 and c2:
            earlier = min(c1, c2)
            later = max(c1, c2)
            start_year = (earlier - 1) * 100 + 1
            end_year = later * 100
            return (
                f"+{start_year:04d}-00-00T00:00:00Z",
                PRECISION_CENTURY,
                _calendar_for_year(start_year),
                start_year,
                end_year,
            )

    # Gregorian year in string: extract 4-digit year
    year_match = re.search(r"\b(\d{4})\b", original)
    if year_match:
        year_int = int(year_match.group(1))
        return (
            f"+{year_int:04d}-00-00T00:00:00Z",
            PRECISION_YEAR,
            _calendar_for_year(year_int),
            None,
            None,
        )

    return None


def extract_viaf_id(viaf_uri: str) -> str | None:
    """Extract the numeric VIAF ID from a VIAF URI.

    Args:
        viaf_uri: Full VIAF URI (e.g., "https://viaf.org/viaf/97223111").

    Returns:
        The numeric ID string, or None if parsing fails.
    """
    if not viaf_uri:
        return None
    match = re.search(r"viaf/(\d+)", viaf_uri)
    return match.group(1) if match else None


def extract_wikidata_qid(wikidata_uri: str) -> str | None:
    """Extract a QID from a Wikidata entity URI.

    Args:
        wikidata_uri: Full Wikidata URI (e.g., "https://www.wikidata.org/entity/Q1218").

    Returns:
        The QID string (e.g., "Q1218"), or None if parsing fails.
    """
    if not wikidata_uri:
        return None
    match = re.search(r"(Q\d+)", wikidata_uri)
    return match.group(1) if match else None


# Negations that must suppress a material term found nearby ("not on paper").
_MATERIAL_NEGATIONS = ("לא ", "אינו ", "not ", "no ")


def materials_in_text(text: str) -> list[str]:
    """Closed-vocabulary material QIDs mentioned as whole words in *text*.

    340$a in the NLI corpus is a free-text note about the physical copy —
    autographs, binding, multiple hands — and only sometimes names the writing
    support ("...על קלף"). Matching whole words against MATERIAL_TO_QID keeps
    the vocabulary closed while recovering the prose cases (Rule W-140).
    """
    import re as _re

    haystack = str(text or "")
    if not haystack.strip():
        return []
    found: list[str] = []
    for term, qid in MATERIAL_TO_QID.items():
        if qid in found:
            continue
        # Hebrew glues its conjunction/preposition prefixes to the word
        # ("ופפירוס" = "and papyrus"), so allow one leading prefix letter.
        pattern = rf"(?<![\w֐-׿])[ובלהמכש]?{_re.escape(term)}(?![\w֐-׿])"
        for match in _re.finditer(pattern, haystack):
            prefix = haystack[max(0, match.start() - 12):match.start()].lower()
            if any(neg in prefix for neg in _MATERIAL_NEGATIONS):
                continue
            found.append(qid)
            break
    return found
