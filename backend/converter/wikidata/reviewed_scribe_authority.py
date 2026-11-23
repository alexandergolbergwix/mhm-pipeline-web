"""Identity records for scribes with a reviewed scholarly catalogue entry."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
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
    "13270": ReviewedScribeAuthority(
        "שמואל בן סעדיה",
        frozenset({"990001011990205171"}),
    ),
    "11602": ReviewedScribeAuthority(
        "טוב בן אברהם מדנואר",
        frozenset({"990001022840205171"}),
    ),
    "10953": ReviewedScribeAuthority(
        "אברהם בן זכריה מאלבאקלי",
        frozenset({"990001022870205171"}),
    ),
    "13136": ReviewedScribeAuthority(
        "ישראל בן יוסף בן ישראל משתא",
        frozenset({"990035802410205171"}),
    ),
    "12264": ReviewedScribeAuthority(
        "מעודד בן שמריה בן נחום בניחגאג",
        frozenset({"990000942930205171"}),
    ),
    "10967": ReviewedScribeAuthority(
        "אברהם בן סעדיה בן עמרם בן דוד",
        frozenset({"990001022860205171"}),
    ),
    "12279": ReviewedScribeAuthority(
        "זכריה בן סעדיה מעלם",
        frozenset({"990001219500205171"}),
    ),
    "12061": ReviewedScribeAuthority(
        "ישע בן דוד בן נחום הלוי",
        frozenset({"990001062450205171"}),
    ),
    "11973": ReviewedScribeAuthority(
        "זכריה בן יוסף אלכלפי",
        frozenset({"990001313900205171"}),
    ),
    "11624": ReviewedScribeAuthority(
        "דוד בן סעדיה בן דוד אלטיאני",
        frozenset({"990001239240205171"}),
    ),
    "10987": ReviewedScribeAuthority(
        "יוסף בן שלום בן דוד בן שלמה אלאחמר",
        frozenset({"990001901520205171"}),
    ),
    "11633": ReviewedScribeAuthority(
        "יהודה בן אברהם בן יוסף בן זכריה",
        frozenset({"990001689720205171"}),
    ),
    "11489": ReviewedScribeAuthority(
        "חיים בן סעדיה",
        frozenset({"990000550940205171"}),
    ),
    "12047": ReviewedScribeAuthority(
        "יוסף בן שלמה בן יוסף הלוי",
        frozenset({"990000558280205171", "990001136420205171"}),
    ),
    "12080": ReviewedScribeAuthority(
        "סעדיה בן שלום בן שלמה בן בניה הלוי",
        frozenset({"990000594010205171"}),
    ),
    "12529": ReviewedScribeAuthority(
        "סעדיה בן נחום מאלכלה",
        frozenset({"990001136560205171"}),
    ),
    "11029": ReviewedScribeAuthority(
        "משה בן יוסף בן מעודד אלאשרסי",
        frozenset({"990001022880205171"}),
    ),
}

_BASE = "https://teman.org.il/content/"
_YEMEN_ENTRY_ALIASES = {"13136": ("11841", "14197")}
_CJA_ENTRY = "https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend"
_YAHYA_ENTRY = "https://www.jewishencyclopedia.com/articles/15051-yahya#anchor25"
_MERARI_ENTRY = "https://sammlungen.ub.uni-frankfurt.de/freimann/content/pageview/6117251"
_FIRUZ_ENTRY = "https://www.orientalstudies.ru/rus/images/pdf/belkina_2024.pdf#page=12"
_POGGETTI_ENTRY = "https://sammlungen.ub.uni-frankfurt.de/freimann/content/pageview/6117264"
_MIZRAHI_ENTRY = "https://files.ybz.org.il/periodicals/peamim/13/Article_13.3.pdf#page=10"
_REVIEWED_INDEX_ENTRIES = {
    _CJA_ENTRY: ReviewedScribeAuthority(
        "נתן נטע בן משה נפתלי הירש המרשלק",
        frozenset({"990001285000205171"}),
    ),
    _YAHYA_ENTRY: ReviewedScribeAuthority(
        "תם בן גדליה אבן יחיא",
        frozenset({"990001350000205171"}),
    ),
    _MERARI_ENTRY: ReviewedScribeAuthority(
        "שאול בן יוסף מררי",
        frozenset({"990000856190205171"}),
    ),
    _FIRUZ_ENTRY: ReviewedScribeAuthority(
        "דניאל בן משה בן ישעיה פירוז",
        frozenset({"990001762200205171"}),
    ),
    _POGGETTI_ENTRY: ReviewedScribeAuthority(
        "יהודה אריה בן יעקב פוג׳טי",
        frozenset({"990001860230205171"}),
    ),
    _MIZRAHI_ENTRY: ReviewedScribeAuthority(
        "שמעון בן יונה מזרחי",
        frozenset({"990001679060205171", "990001679300205171"}),
    ),
}
_INDEX_ENTRY_VARIANTS = {
    _MIZRAHI_ENTRY: (
        "https://files.ybz.org.il/periodicals/peamim/13/Article_13.3.pdf",
        "https://mhm-hmo.wikibase.cloud/wiki/Item:Q13051",
        "https://mhm-hmo.wikibase.cloud/wiki/Q13051",
    ),
    _YAHYA_ENTRY: ("https://jewishencyclopedia.com/articles/15051-yahya#anchor25",),
    _FIRUZ_ENTRY: (
        "https://orientalstudies.ru/rus/images/pdf/belkina_2024.pdf#page=12",
        "https://www.orientalstudies.ru/rus/images/pdf/belkina_2024.pdf#page=11",
        "https://www.orientalstudies.ru/rus/images/pdf/belkina_2024.pdf",
        "https://doi.org/10.31696/2618-7043-2024-7-1-118-136",
    ),
}


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
    if claim.value_type != "url" or not isinstance(url, str):
        return ()
    if url not in _REVIEWED_INDEX_ENTRIES and not url.startswith(_BASE):
        return ()
    number = url.removeprefix(_BASE)
    profile = _REVIEWED_INDEX_ENTRIES.get(url) or _YEMEN_SCHOLARS.get(number)
    if not profile or item.labels.get("he") != profile.hebrew_name or set(item.records) != profile.controls:
        return ()
    if (
        {statement.value for statement in active if statement.property_id == "P31"} != {"Q5"}
        or {statement.value for statement in active if statement.property_id == "P106"} != {"Q916292"}
    ):
        return ()
    if url == _CJA_ENTRY:
        return tuple(
            f"{scheme}://cja.huji.ac.il/{path}?{'&'.join(query)}"
            for scheme in ("https", "http")
            for path in ("", "browser.php")
            for query in permutations(("f=maker_name", "id=660", "mode=treefriend"))
        )
    if url in _REVIEWED_INDEX_ENTRIES:
        variants = (url, *_INDEX_ENTRY_VARIANTS.get(url, ()))
        return tuple(
            variant.replace("https://", f"{scheme}://", 1)
            for variant in variants
            for scheme in ("https", "http")
        )
    return tuple(
        f"{scheme}://{host}/{language}{route}/{entry}{suffix}"
        for entry in (number, *_YEMEN_ENTRY_ALIASES.get(number, ()))
        for scheme in ("https", "http")
        for host in ("teman.org.il", "www.teman.org.il")
        for language in ("", "he/", "en/", "ar/")
        for route in ("content", "node")
        for suffix in ("", "/")
    )
