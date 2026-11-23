"""Identity records for scribes with a reviewed scholarly catalogue entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from converter.wikidata.item_models import WikidataItem


@dataclass(frozen=True)
class ReviewedScribeAuthority:
    hebrew_name: str
    controls: frozenset[str]


_YEMEN_SCHOLARS: dict[str, ReviewedScribeAuthority] = {
    "11620": ReviewedScribeAuthority(
        "שלום בן יהודה אלטוילי",
        frozenset({"990000598840205171", "990000898590205171"}),
    ),
    "12181": ReviewedScribeAuthority(
        "יהודה בן אברהם בן נחום אלמליח",
        frozenset({"990001827110205171", "990001979390205171"}),
    ),
    "13290": ReviewedScribeAuthority(
        "עמרם בן שלום בן יתר שרארה",
        frozenset({"990001153290205171", "990001191160205171"}),
    ),
    "12670": ReviewedScribeAuthority(
        "זכריה בן יוסף אלעזאני",
        frozenset({
            "990000550960205171", "990000554010205171",
            "990001121850205171", "990001137910205171",
        }),
    ),
    "13042": ReviewedScribeAuthority(
        "סעדיה בן שלמה בן סעדיה אלקיסי",
        frozenset({"990000448060205171", "990001162290205171", "990001875220205171"}),
    ),
    "12251": ReviewedScribeAuthority(
        "סעדיה בן מעודד אלמעברי",
        frozenset({"990001338390205171", "990001921540205171"}),
    ),
    "10994": ReviewedScribeAuthority(
        "זכריה בן נתן בן יהודה כלופי",
        frozenset({"990001239200205171", "990001878160205171"}),
    ),
    "11972": ReviewedScribeAuthority(
        "אברהם בן שלמה בן יונה אלכלפי",
        frozenset({"990001689710205171"}),
    ),
    "13241": ReviewedScribeAuthority(
        "שלמה בן ישראל בן יוסף בן ישראל",
        frozenset({"990001799180205171"}),
    ),
    "12236": ReviewedScribeAuthority(
        "זכריה בן אברהם בן זכריה אלמסורי",
        frozenset({"990001209890205171"}),
    ),
    "12800": ReviewedScribeAuthority(
        "יהודה בן דוד בן שלמה אלפתיחי",
        frozenset({"990001952110205171"}),
    ),
    "12778": ReviewedScribeAuthority(
        "זכריה בן דוד בן זכריה אלפהים",
        frozenset({"990001689780205171"}),
    ),
    "13153": ReviewedScribeAuthority(
        "יוסף בן שלום בן סעדיה אלשיחאני",
        frozenset({"990001689730205171"}),
    ),
    "12140": ReviewedScribeAuthority(
        "מימון בן משה בן סעדיה בן מעודד אלמוסאי",
        frozenset({"990000914680205171"}),
    ),
    "12568": ReviewedScribeAuthority(
        "יעקב בן יצחק בן עודד אלעדוי",
        frozenset({"990001192190205171"}),
    ),
    "11611": ReviewedScribeAuthority(
        "אהרן בן שלמה בן חלפון בן אביגד אלטוילי",
        frozenset({"990000990320205171"}),
    ),
    "11613": ReviewedScribeAuthority(
        "זכריה בן אביגד בן אברהם אלטוילי",
        frozenset({"990001022890205171"}),
    ),
    "11334": ReviewedScribeAuthority(
        "אברהם בן יוסף בן סעדיה ונה",
        frozenset({"990001205430205171"}),
    ),
    "12524": ReviewedScribeAuthority(
        "סעדיה בן מעודד בן סעדיה בן יוסף",
        frozenset({"990001324600205171", "990001827620205171"}),
    ),
}

_BASE = "https://teman.org.il/content/"


def reviewed_scribe_authority_urls(item: WikidataItem) -> tuple[str, ...]:
    """Return duplicate lookup URLs for the exact approved creation scope."""
    if getattr(item, "entity_type", "") != "person":
        return ()
    active = [statement for statement in item.statements if getattr(statement, "rank", "normal") != "deprecated"]
    claims = [statement for statement in active if statement.property_id == "P973"]
    if len(claims) != 1:
        return ()
    claim = claims[0]
    url = claim.value
    if claim.value_type != "url" or not isinstance(url, str) or not url.startswith(_BASE):
        return ()
    number = url.removeprefix(_BASE)
    profile = _YEMEN_SCHOLARS.get(number)
    if not profile or item.labels.get("he") != profile.hebrew_name or set(item.records) != profile.controls:
        return ()
    if (
        {statement.value for statement in active if statement.property_id == "P31"} != {"Q5"}
        or {statement.value for statement in active if statement.property_id == "P106"} != {"Q916292"}
    ):
        return ()
    return tuple(
        f"{scheme}://{host}/{language}{route}/{number}{suffix}"
        for scheme in ("https", "http")
        for host in ("teman.org.il", "www.teman.org.il")
        for language in ("", "he/", "en/", "ar/")
        for route in ("content", "node")
        for suffix in ("", "/")
    )
