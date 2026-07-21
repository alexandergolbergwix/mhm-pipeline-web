"""Fail-closed bridge from HMO Wikibase mappings to Wikidata projection.

The Wikidata builder remains DB-agnostic.  This module is the small boundary
where the web pipeline turns the HMO upload ledger into links that can be
passed to the builder.  Only an exact canonical manuscript URI is accepted;
ambiguous or malformed rows are omitted rather than guessed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.pipeline.marc_verify_context import canonical_control_number

_QID_RE = re.compile(r"^Q[1-9][0-9]*$")


def canonical_hmo_instance_qids(
    rows: Iterable[tuple[str, str]],
    expected_uri_by_control_number: dict[str, str],
) -> dict[str, str]:
    """Return safe ``control_number -> HMO Wikibase QID`` links.

    ``rows`` contains the upload-ledger ``(ontology_uri, wikibase_id)``
    values.  A row is accepted only when its URI exactly equals the canonical
    HMO manuscript URI for one requested control number and its QID is valid.
    Conflicting QIDs for one control number are rejected entirely.
    """
    uri_to_cn = {
        uri: canonical_control_number(control_number)
        for control_number, uri in expected_uri_by_control_number.items()
        if canonical_control_number(control_number)
    }
    candidates: dict[str, set[str]] = {}
    for ontology_uri, wikibase_id in rows:
        cn = uri_to_cn.get(str(ontology_uri))
        qid = str(wikibase_id or "").strip()
        if not cn or not _QID_RE.fullmatch(qid):
            continue
        candidates.setdefault(cn, set()).add(qid)
    return {
        control_number: next(iter(qids))
        for control_number, qids in candidates.items()
        if len(qids) == 1
    }
