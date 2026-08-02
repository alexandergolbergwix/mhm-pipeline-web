"""Holding institution name → public Wikidata QID (Rule W-143).

Every QID here was fetched live from Wikidata and its English label recorded
beside it (Rule W-26) — never written from memory. The batch below was verified
on **2026-08-01** via `wbsearchentities` + `wbgetentities`.

The table serves two jobs at once, which is why it is worth maintaining:

1. `P195` (collection) — an outbound LOD edge to the holder.
2. The second duplicate key, `P195 + P217` (Rule W-144). Manuscripts held outside
   the NLI numbering are identifiable on Wikidata by holder + shelfmark and by
   nothing else: all 33 Samaritan manuscripts on Wikidata carry no `P3959`, but
   they do carry `CAJS Rar Ms 75-117` / `MS. Bodley Or. 699` against a holder.

An institution we cannot resolve **unambiguously** is absent from this table and
resolves to `None`. That is a deliberate abstention, not an omission to fix by
guessing (Rule W-84's reasoning, and Rule W-75: never default to NLI).
"""

from __future__ import annotations

# qid: (verified English label, name variants as they appear in MARC/HMO)
_INSTITUTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Q188915": ("National Library of Israel", (
        "the national library of israel",
        "national library of israel",
        "jerusalem, nli",
        "nli",
        "הספרייה הלאומית",
        "הספריה הלאומית",
    )),
    "Q46815": ("Israel Museum", (
        "the israel museum",
        "israel museum",
        "the israel museum, jerusalem",
        "israel museum, jerusalem",
        "מוזיאון ישראל",
    )),
    "Q23308": ("British Library", (
        "the british library",
        "british library",
        "הספרייה הבריטית",
    )),
    "Q82133": ("Bodleian Library", (
        "the bodleian library",
        "bodleian library",
        "the bodleian libraries",
        "bodleian libraries",
        "the bodleian libraries, university of oxford",
        "bodleian libraries, university of oxford",
    )),
    "Q107722626": ("Jewish Theological Seminary Library", (
        "the jewish theological seminary of america",
        "jewish theological seminary of america",
        "the library of the jewish theological seminary",
        "library of the jewish theological seminary",
        "jewish theological seminary library",
        "jts",
    )),
    "Q1048694": ("Russian State Library", (
        "the russian state library",
        "russian state library",
    )),
    "Q875587": ("University Library Johann Christian Senckenberg", (
        "university library johann christian senckenberg",
        "universitätsbibliothek johann christian senckenberg",
    )),
    "Q3648060": ("Institute of Oriental Manuscripts of the Russian Academy of Sciences", (
        "institute of oriental manuscripts, the russian academy of sciences",
        "institute of oriental manuscripts of the russian academy of sciences",
        "institute of oriental manuscripts",
    )),
    "Q5149897": ("Columbia University Libraries", (
        "columbia university library",
        "columbia university libraries",
        "columbia university rare book & manuscript library",
    )),
    "Q1028334": ("Cambridge University Library", (
        "cambridge university library",
        "the cambridge university library",
    )),
    "Q24568958": ("University of Leeds Libraries", (
        "leeds university library",
        "university of leeds libraries",
    )),
    "Q1574347": ("Jewish Historical Institute", (
        "library of the emanuel ringelblum jewish historical institute",
        "emanuel ringelblum jewish historical institute",
        "jewish historical institute",
    )),
    "Q1526305": ("Hebrew Union College – Jewish Institute of Religion", (
        "hebrew union college library",
        "hebrew union college",
        "hebrew union college - jewish institute of religion",
    )),
    "Q1316546": ("Alliance Israélite Universelle", (
        "library of the alliance israélite universelle",
        "library of the alliance israelite universelle",
        "alliance israélite universelle",
        "alliance israelite universelle",
    )),
    "Q1256981": ("San Francisco State University", (
        "san francisco state university library",
        "san francisco state university",
    )),
    "Q2201144": ("National University Library of Turin", (
        "turin national university library",
        "national university library of turin",
        "biblioteca nazionale universitaria di torino",
    )),
    "Q193196": ("University College London", (
        "university college london library - hebrew & jewish studies collection",
        "university college london library",
        "university college london",
    )),
    "Q458921": ("Hungarian Academy of Sciences Library and Information Centre", (
        "library of the hungarian academy of sciences",
        "hungarian academy of sciences library and information centre",
    )),
}

# Named so the abstention is reviewable rather than looking like an oversight.
# Each carries the reason it cannot be resolved (checked live 2026-08-01).
ABSTAINED_INSTITUTIONS: dict[str, str] = {
    "the ben zvi institute": (
        "two plausible entities — Q3571277 'Yad Yitzhak Ben-Zvi' (Israeli "
        "research institute) and Q99770351 'Ben Zvi Institute' (no description)"
    ),
    "ben zvi institute": "see 'the ben zvi institute'",
    "the montefiore library": "no Wikidata item found for the collection",
    "montefiore library": "no Wikidata item found for the collection",
    "manfred and anne lehmann foundation": "no Wikidata item found",
}

_BY_NAME: dict[str, str] = {
    variant: qid for qid, (_label, variants) in _INSTITUTIONS.items() for variant in variants
}


def institution_qid(name: str) -> str | None:
    """The verified QID for a holding institution, or None to abstain."""
    return _BY_NAME.get(_normalise(name))


def institution_label(qid: str) -> str | None:
    """The English label recorded when this QID was verified."""
    entry = _INSTITUTIONS.get(qid)
    return entry[0] if entry else None


def abstention_reason(name: str) -> str | None:
    """Why this institution is deliberately unresolved, if it is."""
    return ABSTAINED_INSTITUTIONS.get(_normalise(name))


def _normalise(name: str) -> str:
    return " ".join(str(name or "").split()).strip(" ,.;:\"'").casefold()
