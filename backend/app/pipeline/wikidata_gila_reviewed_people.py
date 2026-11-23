"""Cross-references for reviewed NLI authorities with different identifiers."""

from dataclasses import dataclass

from app.pipeline.wikidata_source_choices import _claim_values


@dataclass(frozen=True)
class ReviewedLinkedPerson:
    qid: str
    property_id: str
    identifier: str
    url: str
    mazal_ids: frozenset[str]


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
}


def reviewed_linked_person_url(candidate, authority_id):
    proof = REVIEWED_LINKED_PEOPLE.get(authority_id)
    if not proof or candidate.get("id") != proof.qid:
        return None
    if (
        _claim_values(candidate, proof.property_id) != [proof.identifier]
        or not set(_claim_values(candidate, "P8189")) <= proof.mazal_ids
        or not any(isinstance(value, dict) and value.get("id") == "Q5"
                   for value in _claim_values(candidate, "P31"))
    ):
        return None
    return proof.url
