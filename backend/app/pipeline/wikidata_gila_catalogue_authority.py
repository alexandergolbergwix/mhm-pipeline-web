"""Read NLI links between an exact bibliographic record and a personal authority."""

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree as ET

import httpx

from converter.wikidata.retry_policy import retry_after_seconds

NLI_SRU = "https://nli.alma.exlibrisgroup.com/view/sru/972NNL_INST"
_CONTROL = re.compile(r"[0-9]{16,24}\Z")
_IDENTITY_QUERY = re.compile(r"\(alma\.mms_id=([0-9]{16,24})\) and \(alma\.authority_id=(987[0-9]{6,17})\)\Z")
_MAZAL = re.compile(r"987[0-9]{6,17}\Z")
_SRU_NS = "{http://www.loc.gov/zing/srw/}"
_BATCH_SIZE = 15
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewedScribeSource:
    person_qid: str
    heading: str
    dates: str
    url: str
    corroborating_control: str = ""


_VATICAN_CATALOGUE = "https://www.nli.org.il/media/26037/vaticanhebmss.pdf"


REVIEWED_SCRIBE_SOURCES = {
    ("990001011060205171", "987007310213405171"): ReviewedScribeSource(
        "Q5290097", "דומיניקו ירושלמי", "1555-1621", f"{_VATICAN_CATALOGUE}#page=580",
    ),
    ("990001011110205171", "987007310213405171"): ReviewedScribeSource(
        "Q5290097", "דומיניקו ירושלמי", "1555-1621", f"{_VATICAN_CATALOGUE}#page=581",
    ),
    ("990001057000205171", "987007310213405171"): ReviewedScribeSource(
        "Q5290097", "דומיניקו ירושלמי", "1555-1621", f"{_VATICAN_CATALOGUE}#page=581",
    ),
    ("990000569120205171", "987007298185505171"): ReviewedScribeSource(
        "Q7021701", "ריקי רפאל עמנואל חי בן אברהם", "1687-1743", "", "990012776960205171",
    ),
    ("990001190680205171", "987007310565205171"): ReviewedScribeSource(
        "Q6119523", "צמח יעקב בן חיים", "1570?-1667", "https://www.nli.org.il/he/a-topic/987007310565205171",
    ),
}


def _params(authority_id: str, controls: list[str]) -> dict[str, str | int]:
    return {
        "operation": "searchRetrieve", "version": "1.2", "recordSchema": "marcxml",
        "maximumRecords": len(controls),
        "query": "(" + " or ".join(f"alma.mms_id={cn}" for cn in controls)
        + f") and (alma.authority_id={authority_id})",
    }


def catalogue_authority_url(control_number: str, authority_id: str) -> str:
    return f"{NLI_SRU}?{urlencode(_params(authority_id, [control_number]))}"


def cited_catalogue_identities(claim: dict) -> set[tuple[str, str]]:
    pairs = set()
    for reference in claim["references"]:
        for snak in reference.get("snaks", {}).get("P854", []):
            url = snak.get("datavalue", {}).get("value")
            if not isinstance(url, str) or not url.startswith(f"{NLI_SRU}?"):
                continue
            query = parse_qs(urlsplit(url).query).get("query", [])
            match = _IDENTITY_QUERY.fullmatch(query[0]) if len(query) == 1 else None
            if match and url == catalogue_authority_url(*match.groups()):
                pairs.add(match.groups())
    return pairs


async def read_catalogue_authority_records(
    pairs: set[tuple[str, str]], *,
    client: httpx.AsyncClient | None = None, checked: set[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], dict]:
    """Return confirmed MARC records and record complete checks when requested."""
    from app.pipeline.wikidata_gila_people import parse_nli_records

    if not pairs:
        return {}
    if client is None:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=12,
            headers={"User-Agent": "MHMPipeline/1.0 (https://mhm-pipeline.org; NLI catalogue verification)"},
        ) as http_client:
            return await read_catalogue_authority_records(pairs, client=http_client, checked=checked)

    by_id = defaultdict(set)
    for control_number, authority_id in pairs:
        if _CONTROL.fullmatch(control_number) and _MAZAL.fullmatch(authority_id):
            by_id[authority_id].add(control_number)
    results = {}
    for authority_id, controls in sorted(by_id.items()):
        controls = sorted(controls)
        for start in range(0, len(controls), _BATCH_SIZE):
            batch = controls[start:start + _BATCH_SIZE]
            try:
                response = await client.get(NLI_SRU, params=_params(authority_id, batch))
                if response.status_code == 429:
                    delay = max(0.25, retry_after_seconds(response.headers.get("Retry-After")))
                    if delay > 8:
                        return results
                    await asyncio.sleep(delay)
                    response = await client.get(NLI_SRU, params=_params(authority_id, batch))
                    if response.status_code == 429:
                        return results
                response.raise_for_status()
                records = parse_nli_records(response.content, set(batch))
                total = ET.fromstring(response.content).findtext(f"{_SRU_NS}numberOfRecords")
                if total is not None and total.isdecimal() and int(total) == len(records):
                    results.update({(cn, authority_id): record for cn, record in records.items()})
                    if checked is not None:
                        checked.update((cn, authority_id) for cn in batch)
            except (httpx.HTTPError, ET.ParseError, UnicodeError, ValueError) as exc:
                logger.info("NLI catalogue identity check failed for %s (%s)", authority_id, type(exc).__name__)
            await asyncio.sleep(0.25)
    return results
