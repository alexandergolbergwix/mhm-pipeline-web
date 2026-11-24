"""Cross-references for reviewed NLI authorities with different identifiers."""

from dataclasses import dataclass

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_source_choices import _claim_values


@dataclass(frozen=True)
class ReviewedLinkedPerson:
    qid: str
    property_id: str | None
    identifier: str
    url: str
    mazal_ids: frozenset[str]
    sitelink: str | None = None


REVIEWED_LINKED_PEOPLE = {
    "987007310194705171": ReviewedLinkedPerson(
        "Q59628092", "P244", "n86814129", "https://id.loc.gov/authorities/names/n86814129.html",
        frozenset({"987007310194705171", "987007263551105171"}),
    ),
    "987007589984205171": ReviewedLinkedPerson(
        "Q4794967", "P244", "n85005814", "https://id.loc.gov/authorities/names/n85005814.html",
        frozenset({"987007589984205171", "987007258975905171"}),
    ),
    "987007310485405171": ReviewedLinkedPerson(
        "Q139518245", "P227", "142126152", "https://d-nb.info/gnd/142126152",
        frozenset({"987007310485405171"}),
    ),
    "987007265556505171": ReviewedLinkedPerson(
        "Q324313", "P244", "n87892593", "https://id.loc.gov/authorities/names/n87892593.html",
        frozenset({"987007265556505171"}),
    ),
    "987013431293505171": ReviewedLinkedPerson(
        "Q141175662", "P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q2878",
        "https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987013431293505171.jsonld",
        frozenset({"987011546334905171", "987013431293505171"}),
    ),
    "987007310247005171": ReviewedLinkedPerson(
        "Q124365728", None, "יצחק בואינו",
        "https://www.nli.org.il/he/books/NNL_ALEPH990031556390205171/NLI",
        frozenset({"987007310247005171"}), sitelink="hewiki",
    ),
    "987007579348905171": ReviewedLinkedPerson(
        "Q142271124", "P8189", "987007579348905171", "https://www.nli.org.il/he/a-topic/987007579348905171",
        frozenset({"987007579348905171"}),
    ),
    "987007520832505171": ReviewedLinkedPerson(
        "Q142271125", "P8189", "987007520832505171", "https://www.nli.org.il/he/a-topic/987007520832505171",
        frozenset({"987007520832505171"}),
    ),
}


def _matches_reviewed_person(candidate, proof):
    if proof.property_id:
        identity_matches = _claim_values(candidate, proof.property_id) == [proof.identifier]
    else:
        identity_matches = bool(proof.sitelink) and (
            candidate.get("sitelinks", {}).get(proof.sitelink, {}).get("title") == proof.identifier
        )
    return (
        candidate.get("id") == proof.qid
        and identity_matches
        and set(_claim_values(candidate, "P8189")) <= proof.mazal_ids
        and any(isinstance(value, dict) and value.get("id") == "Q5"
                for value in _claim_values(candidate, "P31"))
    )


def reviewed_linked_person_url(candidate, authority_id):
    proof = REVIEWED_LINKED_PEOPLE.get(authority_id)
    return proof.url if proof and _matches_reviewed_person(candidate, proof) else None


@dataclass(frozen=True)
class ReviewedWorkAuthor:
    work: str
    heading: str
    person: ReviewedLinkedPerson

    def matches(self, candidate):
        return _matches_reviewed_person(candidate, self.person)


@dataclass(frozen=True)
class ReviewedWorkTitle:
    work: str
    source_title: str
    item_title: str
    source_author: str


REVIEWED_WORK_TITLES = {
    "990001360250205171": ReviewedWorkTitle(
        "Q141754946", "מאמר שבתות ה'", "מאמר שבתות ה", "מנחם עזריה",
    ),
    "990001722330205171": ReviewedWorkTitle(
        "Q141755330", "Sermão", "דרשה", "Fonseca, Isaac Aboab da",
    ),
    "990001629740205171": ReviewedWorkTitle(
        "Q141754701", "כתובה לשבועות", "כתובה לשבועות לר' ישראל נג'רה", "נג'רה, ישראל בן משה",
    ),
}


_EZOBI = ReviewedWorkAuthor(
    "Q141755114", "אזובי, שלמה בן יהודה",
    ReviewedLinkedPerson("Q107059242", "P269", "067065791", "https://www.idref.fr/067065791", frozenset()),
)
_HAMMERSCHLAG = ReviewedLinkedPerson(
    "Q142259340", "P973", "https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend",
    "https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend", frozenset(),
)
_REVIEWED_PRIMARY_AUTHORS = {
    **dict.fromkeys((f"9900007988{suffix}0205171" for suffix in range(4, 10)), _EZOBI),
    "990000988150205171": ReviewedWorkAuthor(
        "Q141755348", "המרשלק, נתן נטע בן משה נפתלי הירש", _HAMMERSCHLAG,
    ),
    "990001285010205171": ReviewedWorkAuthor(
        "Q141754989", "המרשלק, נתן נטע בן משה נפתלי הירש", _HAMMERSCHLAG,
    ),
    "990001719610205171": ReviewedWorkAuthor(
        "Q141754934", "דאנון, יוסף בן יעקב", ReviewedLinkedPerson(
            "Q121084345", None, "Joseph ibn Danon", "https://www.jstor.org/stable/29777565",
            frozenset(), sitelink="enwiki",
        ),
    ),
}


def reviewed_primary_author(control, field, variants, occurrences):
    proof = _REVIEWED_PRIMARY_AUTHORS.get(control)
    if (
        proof and marc.source_key(field) == "100:1"
        and marc.values(field, "a") == [proof.heading]
        and sum(other["tag"] == "100" for other, _ in occurrences) == 1
        and not any(marc.values(variant, code) for variant in variants for code in ("0", "1", "j", "t"))
    ):
        return proof
    return None
