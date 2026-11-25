"""Cross-references for reviewed NLI authorities with different identifiers."""

from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlencode

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_gila_catalogue_authority import NLI_SRU
from app.pipeline.wikidata_gila_execution import semantic_entity
from app.pipeline.wikidata_source_choices import _claim_values, digest


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
    "987007308192805171": ReviewedLinkedPerson(
        "Q125872350", None, "יעקב מארג'י",
        "https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007308192805171.jsonld",
        frozenset({"987007308192805171"}), sitelink="hewiki",
    ),
    "987007496358505171": ReviewedLinkedPerson(
        "Q120599174", "P244", "nb2008014478", "https://id.loc.gov/authorities/names/nb2008014478.html",
        frozenset({"987007496358505171"}),
    ),
    "987007568307305171": ReviewedLinkedPerson(
        "Q1949059", "P227", "119038927", "https://d-nb.info/gnd/119038927",
        frozenset({"987007568307305171"}),
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
    catalogue_id: str = ""
    dates: str = ""
    retired_text_id: str = ""
    retired_text_digest: str = ""
    role_note_digest: str = ""

    def matches(self, candidate):
        return _matches_reviewed_person(candidate, self.person)


@dataclass(frozen=True)
class ReviewedWorkTitle:
    work: str
    source_title: str
    item_title: str
    source_author: str


REVIEWED_WORK_TITLES = {
    **dict.fromkeys(("990001191230205171", "990001238980205171"), ReviewedWorkTitle(
        "Q141175558", "מנחת יהודה", "מנחת יהודה : פרוש על שמואל, מלכים וישעיהו",
        "חנין, יהודה בן יעקב",
    )),
    "990001029260205171": ReviewedWorkTitle(
        "Q141755039", "בעל ברית אברם", "בעל ברית אברהם", "אזולאי, אברהם בן מרדכי",
    ),
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
_HANIN = ReviewedWorkAuthor(
    "Q141175558", "חנין, יהודה בן יעקב",
    ReviewedLinkedPerson(
        "Q118186113", "P8189", "987007595556205171",
        "https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007595556205171.jsonld",
        frozenset({"987007595556205171"}),
    ),
    dates="1540 בערך-1619 בערך",
)

_REVIEWED_PRIMARY_AUTHORS = {
    **dict.fromkeys(("990001191230205171", "990001238980205171"), _HANIN),
    "990000413570205171": ReviewedWorkAuthor(
        "Q141755350", "אליהו בן אשר",
        ReviewedLinkedPerson(
            "Q66031", "P244", "n86807326", "https://id.loc.gov/authorities/names/n86807326.html",
            frozenset({"987007260876405171"}),
        ),
        dates="1469-1549",
    ),
    "990001029260205171": ReviewedWorkAuthor(
        "Q141755039", "אזולאי, אברהם בן מרדכי",
        ReviewedLinkedPerson(
            "Q1977082", "P244", "nr88012645", "https://id.loc.gov/authorities/names/nr88012645.html",
            frozenset({"987007300208005171"}),
        ),
        dates="1570 בערך-1643",
        retired_text_id="Q141755039$3945E3C2-8C2E-4705-93A4-76E34FD363A9",
        retired_text_digest="9c78c99635e7243027644b6d0d377fb7509f80393035da2f2c7bc95470873ea4",
        role_note_digest="e4c0b8e0dd4c7b2951f2ad2febde2f4d4fe8807b75091b71d984bc30bcd08c7b",
    ),
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
    "990001737540205171": ReviewedWorkAuthor(
        "Q141755001", "שוונטר, דניאל", ReviewedLinkedPerson(
            "Q77509", "P227", "117417904", "https://www.deutsche-biographie.de/sfz45388.html",
            frozenset({"987007312199205171"}),
        ),
    ),
    "990001515370205171": ReviewedWorkAuthor(
        "Q141755064", "פירוז, דניאל בן משה ירושלמי בן ישעיה", ReviewedLinkedPerson(
            "Q142259384", "P973", "https://www.orientalstudies.ru/rus/images/pdf/belkina_2024.pdf#page=12",
            "https://www.orientalstudies.ru/rus/images/pdf/belkina_2024.pdf#page=12", frozenset(),
        ),
    ),
    "990000650360205171": ReviewedWorkAuthor(
        "Q141754601", "מולן, יעקב בן משה", REVIEWED_LINKED_PEOPLE["987007265556505171"],
        catalogue_id="987007265556505171", dates="1360-1427",
    ),
    "990001973970205171": ReviewedWorkAuthor(
        "Q141754995", "רינטו די מודינה", ReviewedLinkedPerson(
            "Q142065938", "P8034", "495/222282",
            "https://www.nli.org.il/media/26037/vaticanhebmss.pdf#page=537", frozenset(),
        ),
    ),
}


def reviewed_primary_author(control, field, variants, occurrences, catalogue_id=""):
    proof = _REVIEWED_PRIMARY_AUTHORS.get(control)
    if (
        proof and (proof.catalogue_id == catalogue_id or (
            not proof.catalogue_id and catalogue_id in proof.person.mazal_ids
        )) and marc.source_key(field) == "100:1"
        and [name.strip().rstrip(",") for name in marc.values(field, "a")] == [proof.heading]
        and (not proof.dates or marc.values(field, "d") == [proof.dates])
        and sum(other["tag"] == "100" for other, _ in occurrences) == 1
        and not any(marc.values(variant, code) for variant in variants for code in ("0", "1", "j", "t"))
    ):
        return proof
    return None


def reviewed_author_text_patch(proof, record, target):
    """Retain an old claim at deprecated rank only if the exact review still applies."""
    if not proof or not proof.retired_text_id:
        return None
    old = next((
        claim for claim in target.get("claims", {}).get("P2093", [])
        if claim.get("id") == proof.retired_text_id
    ), None)
    if not old or old.get("rank") == "deprecated":
        return None
    notes = marc.occurrences(record, "500")
    if (
        target.get("id") != proof.work
        or digest(semantic_entity(old)) != proof.retired_text_digest
        or not any(
            sha256(text.encode("utf-8")).hexdigest() == proof.role_note_digest
            for row in notes if marc.source_key(row) == "500:1"
            for text in marc.values(row, "a")
        )
    ):
        raise ValueError("The reviewed author text or its MARC note changed.")
    return {**old, "rank": "deprecated"}


@dataclass(frozen=True)
class ReviewedFormerOwner:
    person: ReviewedLinkedPerson
    occurrence: str
    heading: str
    note_occurrence: str
    note_digest: str

    def matches(self, candidate):
        return _matches_reviewed_person(candidate, self.person)


_REVIEWED_FORMER_OWNERS = {
    "990001740360205171": ReviewedFormerOwner(
        ReviewedLinkedPerson(
            "Q303765", "P227", "101528258X", "https://d-nb.info/gnd/101528258X",
            frozenset({"987007259574305171"}),
        ),
        "700:1", "כרמולי, אליקים", "561:2",
        "967205f96b3d733467383851f692e82e72b8191eb2e8afea7d3c874a0a5b7cab",
    ),
    "990001347920205171": ReviewedFormerOwner(
        ReviewedLinkedPerson(
            "Q6726127", "P244", "nb2008026991",
            "https://hdbg.eu/biografien/detail/ariel-loeb-ben-abraham-mer-berlin/10251", frozenset(),
        ),
        "700:2", "ברלין, ליב", "561:2",
        "a3df68311f1cd0043b8526331b028f756ceffa93933b185d789ddd715e500abd",
    ),
    "990000953620205171": ReviewedFormerOwner(
        ReviewedLinkedPerson(
            "Q111081113", "P227", "1053150970", "https://etshaimmanuscripts.nl/about/",
            frozenset({"987007501465505171"}),
        ),
        "700:1", "מונטיזינוס, דוד", "561:1",
        "d559eb8dc0654680d13c7b851234e3693cc24537d9acd202e316168529592042",
    ),
}


def reviewed_former_owner_url(control):
    return NLI_SRU + "?" + urlencode({
        "version": "1.2", "operation": "searchRetrieve",
        "query": f"alma.mms_id={control}", "recordSchema": "marcxml",
    })


def reviewed_former_owner(control, record, occurrence, heading):
    proof = _REVIEWED_FORMER_OWNERS.get(control)
    if not proof or (proof.occurrence, proof.heading) != (occurrence, heading):
        return None
    for row in marc.occurrences(record, "561"):
        if (marc.source_key(row) != proof.note_occurrence or row["indicators"][0] == "0"
                or any(marc.values(row, code) for code in ("3", "5"))):
            continue
        notes = marc.values(row, "a")
        if len(notes) == 1 and sha256(notes[0].encode()).hexdigest() == proof.note_digest:
            return proof
    return None
