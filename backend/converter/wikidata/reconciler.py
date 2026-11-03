"""Reconcile pipeline entities against existing Wikidata items via SPARQL.

Checks whether manuscripts, persons, and places already exist on Wikidata
before creating new items. Uses the Wikidata Query Service SPARQL endpoint.

Rate-limited to 1 query/second per Wikidata API policy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import requests

from converter.wikidata.property_mapping import (
    Q_MANUSCRIPT,
    Q_NLI,
    extract_viaf_id,
    extract_wikidata_qid,
)

logger = logging.getLogger(__name__)

_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
_RATE_LIMIT_SECONDS = 1.0
_TIMEOUT_SECONDS = 15
_QUERY_ATTEMPTS = 3
_QUERY_BACKOFF_SECONDS = 2.0
_USER_AGENT = (
    "MHMPipeline/0.1 (https://github.com/alexgoldberg/mhm-pipeline; alexander.goldberg@biu.ac.il)"
)


class ReconciliationUnavailableError(Exception):
    """A SPARQL lookup could not be completed (network error, timeout, 429/5xx).

    This is DISTINCT from "no match found" (an empty result set). Callers in the
    upload path MUST treat it as a fail-closed signal: when we cannot confirm
    whether an item already exists, we refuse to CREATE it — a transient WDQS
    outage during a batch must never be mistaken for "absent" and mint
    duplicates (the 2026-04 mass-duplicate failure mode).
    """


@dataclass
class ReconciliationResult:
    """Result of reconciling a single entity."""

    entity_type: str  # "manuscript" | "person" | "place"
    local_id: str
    label: str
    existing_qid: str | None = None
    action: str = "create"  # "create" | "update" | "skip"


@dataclass
class ReconciliationReport:
    """Aggregated reconciliation results."""

    results: list[ReconciliationResult] = field(default_factory=list)
    manuscripts_found: int = 0
    manuscripts_new: int = 0
    persons_found: int = 0
    persons_new: int = 0
    places_found: int = 0
    places_new: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict."""
        return {
            "manuscripts_found": self.manuscripts_found,
            "manuscripts_new": self.manuscripts_new,
            "persons_found": self.persons_found,
            "persons_new": self.persons_new,
            "places_found": self.places_found,
            "places_new": self.places_new,
            "total_results": len(self.results),
            "results": [
                {
                    "entity_type": r.entity_type,
                    "local_id": r.local_id,
                    "label": r.label,
                    "existing_qid": r.existing_qid,
                    "action": r.action,
                }
                for r in self.results
            ],
        }


class WikidataReconciler:
    """Reconcile pipeline entities against Wikidata via SPARQL.

    Usage::

        reconciler = WikidataReconciler()
        report = reconciler.reconcile_all(records)
        print(f"Found {report.manuscripts_found} existing manuscripts")
    """

    def __init__(self, sparql_endpoint: str = _SPARQL_ENDPOINT) -> None:
        self._endpoint = sparql_endpoint
        self._cache: dict[str, str | None] = {}
        self._last_request_time: float = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": _USER_AGENT,
                "Accept": "application/sparql-results+json",
            }
        )

    def _rate_limit(self) -> None:
        """Enforce rate limiting between SPARQL requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < _RATE_LIMIT_SECONDS:
            time.sleep(_RATE_LIMIT_SECONDS - elapsed)
        self._last_request_time = time.time()

    def _query(self, sparql: str) -> list[dict[str, object]]:
        """Execute a SPARQL query and return results bindings.

        Args:
            sparql: The SPARQL query string.

        Returns:
            List of result binding dicts.
        """
        # Transient WDQS failures (timeouts, 429, 502) must not read as
        # "confirmed absent": every caller fail-closes on
        # ReconciliationUnavailableError and blocks a good item. Retry a
        # bounded number of times with backoff before giving up
        # (2026-10-11 dry-run: 58 of 1850 items blocked on one flaky pass).
        last_error: ReconciliationUnavailableError | None = None
        for attempt in range(_QUERY_ATTEMPTS):
            if attempt:
                time.sleep(_QUERY_BACKOFF_SECONDS * attempt)
            self._rate_limit()
            try:
                resp = self._session.get(
                    self._endpoint,
                    params={"query": sparql, "format": "json"},
                    timeout=_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("results", {}).get("bindings", [])
            except requests.RequestException as exc:
                # FAIL CLOSED: do NOT return [] here. An empty list means "confirmed
                # absent" to every caller and would let a transient WDQS outage be
                # read as "no existing item → safe to CREATE", which is exactly how
                # the 2026-04 import minted thousands of duplicates. Raise instead so
                # the upload path blocks creation on uncertainty.
                logger.warning("SPARQL query failed (attempt %s): %s", attempt + 1, exc)
                last_error = ReconciliationUnavailableError(str(exc))
                last_error.__cause__ = exc
        assert last_error is not None
        raise last_error

    def reconcile_manuscript_by_nli_id(self, control_number: str) -> str | None:
        """Check if a manuscript item exists via P8189 (NLI J9U ID).

        Args:
            control_number: NLI system number.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"ms:nli:{control_number}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Bug fix 2026-04-16 (deeper audit Fix #10): escape control_number
        # before injecting into SPARQL. NLI IDs are typically numeric so
        # this is defence-in-depth, but the shelfmark path uses the same
        # escaping and consistency matters.
        safe_ctrl = str(control_number).replace("\\", "\\\\").replace('"', '\\"')
        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P8189 "{safe_ctrl}" .
          ?item wdt:P31 wd:{Q_MANUSCRIPT} .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_manuscript_by_shelfmark(
        self,
        shelfmark: str,
        collection_qid: str = Q_NLI,
    ) -> str | None:
        """Check if a manuscript item exists via P195 (collection) + P217 (shelfmark).

        Args:
            shelfmark: The inventory/shelf mark string.
            collection_qid: QID of the holding institution.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"ms:shelf:{collection_qid}:{shelfmark}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        safe_shelfmark = shelfmark.replace('"', '\\"')
        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P195 wd:{collection_qid} .
          ?item wdt:P217 "{safe_shelfmark}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_manuscript_by_nnl_id(self, nnl_id: str) -> str | None:
        """Check if a manuscript exists via P3959 (NNL / NLI catalog item ID).

        P3959 is the identifier the pipeline actually emits on EVERY manuscript
        item (the MARC 001 bibliographic control number, prefix 990…).
        ``reconcile_manuscript_by_nli_id`` queries P8189, which holds J9U
        AUTHORITY ids (prefix 9870…) that manuscripts never carry — so manuscript
        deduplication MUST go through P3959. Querying the wrong property was a
        silent duplicate vector in the 2026-04 import (manuscript lookups could
        never match, so every manuscript was created new).

        Returns:
            QID if found, None if confirmed absent.

        Raises:
            ReconciliationUnavailableError: the SPARQL lookup could not be completed.
        """
        cache_key = f"ms:nnl:{nnl_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        safe_id = str(nnl_id).replace("\\", "\\\\").replace('"', '\\"')
        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P3959 "{safe_id}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_manuscript_by_identifiers(
        self,
        nnl_id: str | None,
        shelfmark: str | None,
    ) -> str | None:
        """Reconcile a manuscript by P3959 (NNL id) first, then shelfmark.

        This is the manuscript entry point the upload path uses. Unlike
        ``reconcile_manuscript`` (which queries the wrong property, P8189), it
        checks P3959 — the identifier our manuscript items actually carry.

        Raises:
            ReconciliationUnavailableError: a SPARQL lookup could not be completed,
            so the caller must fail closed and refuse to CREATE.
        """
        if nnl_id:
            qid = self.reconcile_manuscript_by_nnl_id(nnl_id)
            if qid:
                return qid
        if shelfmark:
            return self.reconcile_manuscript_by_shelfmark(shelfmark)
        return None

    def reconcile_person_by_viaf(self, viaf_id: str) -> str | None:
        """Check if a person item exists via P214 (VIAF ID).

        Args:
            viaf_id: Numeric VIAF cluster ID.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"person:viaf:{viaf_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P214 "{viaf_id}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_person_by_nli_id(self, nli_id: str) -> str | None:
        """Check if a person item exists via P8189 (NLI J9U ID).

        Args:
            nli_id: NLI authority ID (Mazal ID).

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"person:nli:{nli_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Safety: only match authority-record IDs (prefix "9870").
        # Bibliographic IDs start with "990" and belong on manuscripts, not persons.
        if not nli_id.startswith("9870"):
            logger.debug(
                "reconcile_person_by_nli_id: skipping non-authority ID %r (not 9870 prefix)",
                nli_id,
            )
            self._cache[cache_key] = None
            return None

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P8189 "{nli_id}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    def reconcile_work_by_label_and_author(
        self,
        title: str,
        lang: str = "he",
        author_qid: str | None = None,
        author_name: str | None = None,
    ) -> str | None:
        """Find an existing Wikidata work item matching the given label.

        Bug fix 2026-04-15 (web audit Fix #2): the pipeline was creating
        duplicate work items for classical Hebrew texts (Talmud tractates,
        Rashi commentaries, Maimonides, etc.) that already exist on
        Wikidata. This method does the SPARQL lookup before creating.

        Args:
            title: The work title (typically Hebrew).
            lang: BCP-47 language tag for the title (default "he").
            author_qid: Optional. If provided, the candidate's P50 (author)
                must either match this QID or be absent. If the candidate
                has a DIFFERENT P50 author, REJECT the match — the same
                title by a different author is a different work.
            author_name: Optional P2093 author name string. If provided, a
                candidate with a conflicting P2093 author name string is
                rejected so generic titles (e.g. "דרושים") from different
                authors do not collapse onto one item.

        Returns:
            QID of the matching Wikidata work, or None if no safe match.
        """
        clean_title = (title or "").strip()
        if not clean_title:
            return None
        norm_author_name = (
            " ".join(str(author_name or "").strip().rstrip(" ,.;:/-").split()).casefold()
        )
        cache_key = (
            f"work:{lang}:{clean_title}:{author_qid or ''}:{norm_author_name}"
            if norm_author_name
            else f"work:{lang}:{clean_title}:{author_qid or ''}"
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Escape double quotes in the title for SPARQL string-literal safety.
        safe_title = clean_title.replace("\\", "\\\\").replace('"', '\\"')
        # Q47461344 = written work; cover subclasses via wdt:P31/wdt:P279*.
        sparql = f"""
        SELECT ?item ?author ?authorStr WHERE {{
          ?item wdt:P31/wdt:P279* wd:Q47461344 .
          ?item rdfs:label "{safe_title}"@{lang} .
          OPTIONAL {{ ?item wdt:P50 ?author . }}
          OPTIONAL {{ ?item wdt:P2093 ?authorStr . }}
        }} LIMIT 10
        """
        results = self._query(sparql)
        by_cand: dict[str, tuple[set[str], set[str]]] = {}
        order: list[str] = []
        for binding in results:
            cand_uri = binding.get("item", {}).get("value", "")
            cand_qid = extract_wikidata_qid(cand_uri)
            if not cand_qid:
                continue
            if cand_qid not in by_cand:
                by_cand[cand_qid] = (set(), set())
                order.append(cand_qid)
            cand_author = binding.get("author", {}).get("value", "")
            cand_author_qid = extract_wikidata_qid(cand_author) if cand_author else None
            if cand_author_qid:
                by_cand[cand_qid][0].add(cand_author_qid)
            cand_str = str(binding.get("authorStr", {}).get("value", "") or "").strip()
            if cand_str:
                by_cand[cand_qid][1].add(
                    " ".join(cand_str.rstrip(" ,.;:/-").split()).casefold()
                )

        qid: str | None = None
        for cand_qid in order:
            cand_qids, cand_strs = by_cand[cand_qid]
            if author_qid and cand_qids and author_qid not in cand_qids:
                logger.warning(
                    "RECONCILE WORK REJECT: %s matches title %r but its P50=%s "
                    "differs from proposed author %s — treating as different work",
                    cand_qid,
                    clean_title,
                    sorted(cand_qids),
                    author_qid,
                )
                continue
            if norm_author_name and cand_strs and norm_author_name not in cand_strs:
                logger.warning(
                    "RECONCILE WORK REJECT: %s matches title %r but its P2093=%s "
                    "differs from proposed author %r — treating as different work",
                    cand_qid,
                    clean_title,
                    sorted(cand_strs),
                    author_name,
                )
                continue
            qid = cand_qid
            break

        self._cache[cache_key] = qid
        return qid

    def reconcile_by_exact_match_uri(self, uri: str) -> str | None:
        """Find an existing Wikidata item carrying ``wdt:P2888 <uri>``."""
        clean = (uri or "").strip()
        if (
            not clean.startswith(("http://", "https://"))
            or any(ch in clean for ch in ("<", ">", '"', " ", "\t", "\n"))
        ):
            return None
        cache_key = f"p2888:{clean}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P2888 <{clean}> .
        }} LIMIT 2
        """
        results = self._query(sparql)
        qid: str | None = None
        if len(results) == 1:
            cand_uri = str(results[0].get("item", {}).get("value", ""))
            qid = extract_wikidata_qid(cand_uri) or None
        self._cache[cache_key] = qid
        return qid

    def reconcile_place(self, wikidata_uri: str) -> str | None:
        """Validate that a KIMA-provided Wikidata QID exists.

        Args:
            wikidata_uri: Full Wikidata URI from KIMA data.

        Returns:
            QID if valid and exists, None otherwise.
        """
        qid = extract_wikidata_qid(wikidata_uri)
        if not qid:
            return None

        cache_key = f"place:{qid}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # KIMA QIDs are already validated; just confirm format
        self._cache[cache_key] = qid
        return qid

    def reconcile_manuscript(
        self,
        control_number: str,
        shelfmark: str | None,
    ) -> str | None:
        """Reconcile a manuscript by NLI ID first, then shelfmark fallback.

        Args:
            control_number: NLI system number.
            shelfmark: Optional inventory number.

        Returns:
            QID if found, None otherwise.
        """
        qid = self.reconcile_manuscript_by_nli_id(control_number)
        if qid:
            return qid

        if shelfmark:
            return self.reconcile_manuscript_by_shelfmark(shelfmark)

        return None

    def reconcile_person_by_external_id(
        self,
        prop: str,
        ext_id: str,
    ) -> str | None:
        """Check if a person item exists via any external-id property.

        Args:
            prop: Wikidata property ID (e.g., "P244" for LCCN).
            ext_id: The external identifier value.

        Returns:
            QID if found, None otherwise.
        """
        cache_key = f"person:{prop}:{ext_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:{prop} "{ext_id}" .
        }} LIMIT 1
        """
        results = self._query(sparql)
        qid = None
        if results:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri)

        self._cache[cache_key] = qid
        return qid

    # Properties used for cross-identifier conflict verification.
    # If a candidate item already has a value on these properties that
    # differs from the value we would attach, the candidate is REJECTED
    # as a match — they are almost certainly different real-world entities
    # who happen to share one identifier.
    _IDENTITY_PROPS = ("P214", "P8189", "P244", "P227", "P213")  # VIAF, NLI, LCCN, GND, ISNI

    def _fetch_identity_claims(self, qid: str) -> dict[str, set[str]]:
        """Fetch a candidate item's identity claims (VIAF/NLI/LCCN/GND/ISNI/DOB/POB).

        Returns a dict mapping property → set of string values currently on the item.
        Empty dict on lookup failure (caller should treat as 'no info, allow match').
        """
        cache_key = f"identity:{qid}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if isinstance(cached, dict):
                return cached
        sparql = f"""
        SELECT ?p ?v WHERE {{
          VALUES ?p {{ wdt:P214 wdt:P8189 wdt:P244 wdt:P227 wdt:P213 wdt:P569 wdt:P19 }}
          wd:{qid} ?p ?v .
        }}
        """
        results = self._query(sparql)
        out: dict[str, set[str]] = {}
        for b in results:
            prop_uri = b.get("p", {}).get("value", "")
            val = str(b.get("v", {}).get("value", ""))
            # Convert wdt:P… URI back to bare property id
            prop = prop_uri.rsplit("/", 1)[-1]
            out.setdefault(prop, set()).add(val)
        # Cache (cast through Any since _cache type is narrower)
        self._cache[cache_key] = out  # type: ignore[assignment]
        return out

    def _candidate_conflicts(
        self,
        qid: str,
        proposed: dict[str, str],
    ) -> list[str]:
        """Return list of property IDs where the candidate conflicts with proposed values.

        A conflict is when the candidate already has a value for the property AND
        none of those values matches the proposed value. Properties the candidate
        does not have are not conflicts (we'd just be adding new info).
        """
        existing = self._fetch_identity_claims(qid)
        conflicts: list[str] = []
        for prop, proposed_value in proposed.items():
            if not proposed_value:
                continue
            existing_vals = existing.get(prop, set())
            if existing_vals and proposed_value not in existing_vals:
                conflicts.append(prop)
        return conflicts

    def reconcile_person(
        self,
        name: str,
        viaf_uri: str | None,
        nli_id: str | None,
        lc_id: str | None = None,
        gnd_id: str | None = None,
        isni: str | None = None,
    ) -> str | None:
        """Reconcile a person by all available identifiers.

        SAFETY (added 2026-04-13): When an identifier matches a candidate item,
        we cross-verify against ALL other available identifiers we have. If the
        candidate already has a DIFFERENT value on any of those identifiers,
        we REJECT the match — they are different real-world entities who happen
        to share one identifier (e.g., two lawyers sharing an ISNI). This prevents
        the wrong-merge disaster where 902+ items got merged because the pipeline
        trusted a single shared identifier.

        Checks in order: VIAF → NLI → LCCN → GND → ISNI.
        Returns QID on first verified match; None otherwise.
        """
        viaf_id = extract_viaf_id(viaf_uri) if viaf_uri else None
        return self.reconcile_person_by_identifiers(
            viaf_id, nli_id, lc_id=lc_id, gnd_id=gnd_id, isni=isni,
        )

    def reconcile_person_by_identifiers(
        self,
        viaf_id: str | None,
        nli_id: str | None,
        lc_id: str | None = None,
        gnd_id: str | None = None,
        isni: str | None = None,
    ) -> str | None:
        """Reconcile a person by bare identifier values, with cross-identifier
        conflict verification.

        This is the conflict-checked entry point the upload path uses (it holds
        bare P214/P8189/… statement values, not VIAF URIs). ``reconcile_person``
        delegates here after extracting the VIAF id from a URI.

        SAFETY (2026-04-13): when an identifier matches a candidate item, we
        cross-verify against ALL the other identifiers we hold. If the candidate
        already has a DIFFERENT value on any of them, REJECT the match — two
        different real-world entities sharing one identifier must never be
        merged (the wrong-merge disaster, 902+ items). Checks in order:
        VIAF → NLI → LCCN → GND → ISNI.

        Returns:
            QID on the first verified match, None if no safe match.

        Raises:
            ReconciliationUnavailableError: a SPARQL lookup could not be completed.
        """
        proposed: dict[str, str] = {}
        if viaf_id:
            proposed["P214"] = viaf_id
        if nli_id:
            proposed["P8189"] = nli_id
        if lc_id:
            proposed["P244"] = lc_id
        if gnd_id:
            proposed["P227"] = gnd_id
        if isni:
            proposed["P213"] = isni

        candidates: list[tuple[str, str]] = []  # (matching_prop, qid)
        if viaf_id:
            qid = self.reconcile_person_by_viaf(viaf_id)
            if qid:
                candidates.append(("P214", qid))
        if nli_id:
            qid = self.reconcile_person_by_nli_id(nli_id)
            if qid:
                candidates.append(("P8189", qid))
        if lc_id:
            qid = self.reconcile_person_by_external_id("P244", lc_id)
            if qid:
                candidates.append(("P244", qid))
        if gnd_id:
            qid = self.reconcile_person_by_external_id("P227", gnd_id)
            if qid:
                candidates.append(("P227", qid))
        if isni:
            qid = self.reconcile_person_by_external_id("P213", isni)
            if qid:
                candidates.append(("P213", qid))

        for matched_prop, qid in candidates:
            conflicts = self._candidate_conflicts(qid, proposed)
            if conflicts:
                logger.warning(
                    "RECONCILE REJECT: %s matched %s=%s but has conflicting %s — "
                    "treating as different entity, will create new item",
                    qid,
                    matched_prop,
                    proposed.get(matched_prop),
                    ", ".join(conflicts),
                )
                continue
            return qid

        return None

    def reconcile_person_by_label(self, name: str, lang: str = "he") -> str | None:
        """Find an existing Wikidata human item with exactly this label.

        Used as a last-resort deduplication check when no identifier-based
        match was found.  Only returns a QID when exactly one human (P31=Q5)
        item has this label — ambiguous matches (multiple candidates) return
        None to avoid wrong merges.

        Args:
            name: Person label to search (Hebrew by default).
            lang: BCP-47 language tag (default "he").

        Returns:
            QID of the unique matching human, or None if zero or multiple found.
        """
        clean = (name or "").strip()
        if not clean or len(clean) < 3:
            return None
        # Detect language: if name contains no Hebrew characters, use English.
        has_hebrew = any("\u0590" <= c <= "\u05ff" for c in clean)
        lang = "he" if has_hebrew else "en"
        cache_key = f"person_label:{lang}:{clean}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        safe_name = clean.replace("\\", "\\\\").replace('"', '\\"')
        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:P31/wdt:P279* wd:Q5 .
          ?item rdfs:label "{safe_name}"@{lang} .
        }} LIMIT 3
        """
        results = self._query(sparql)
        qid: str | None = None
        if len(results) == 1:
            uri = results[0].get("item", {}).get("value", "")
            qid = extract_wikidata_qid(uri) or None
        elif len(results) > 1:
            logger.debug(
                "reconcile_person_by_label: %r is ambiguous (%d candidates) — skipping",
                clean, len(results),
            )

        self._cache[cache_key] = qid
        return qid

    # ── Batch reconciliation (efficient for large uploads) ────────

    def reconcile_batch_by_nli_id(
        self,
        nli_ids: list[str],
    ) -> dict[str, str]:
        """Reconcile multiple items by NLI J9U ID in a single SPARQL query.

        Uses the VALUES clause to batch-query up to 100 IDs at once.

        Returns:
            Dict mapping NLI ID → Wikidata QID for found items.
        """
        results: dict[str, str] = {}
        # Safety: filter out bibliographic IDs (prefix "990") — they are manuscript
        # control numbers and must never be looked up as person P8189 values.
        authority_ids = [nid for nid in nli_ids if nid.startswith("9870")]
        if not authority_ids:
            return results
        # Process in chunks of 100 (SPARQL VALUES limit)
        for i in range(0, len(authority_ids), 100):
            chunk = authority_ids[i : i + 100]
            values = " ".join(f'"{nid}"' for nid in chunk)
            sparql = f"""
            SELECT ?item ?nli WHERE {{
              VALUES ?nli {{ {values} }}
              ?item wdt:P8189 ?nli .
            }}
            """
            try:
                bindings = self._query(sparql)
                for b in bindings:
                    nli = b.get("nli", {}).get("value", "")
                    item_uri = b.get("item", {}).get("value", "")
                    qid = extract_wikidata_qid(item_uri)
                    if qid and nli:
                        results[nli] = qid
            except Exception as exc:
                logger.warning("Batch reconciliation failed for chunk %d: %s", i, exc)

        logger.info("Batch reconciliation: %d/%d NLI IDs matched", len(results), len(nli_ids))
        return results

    def reconcile_all(
        self,
        records: list[dict[str, object]],
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> ReconciliationReport:
        """Reconcile all entities across all records.

        Args:
            records: List of record dicts from authority_enriched.json.
            progress_cb: Optional callback called with (current, total).

        Returns:
            A ReconciliationReport with per-entity results and aggregate counts.
        """
        report = ReconciliationReport()
        total = len(records)

        # Collect unique persons to avoid redundant queries
        persons_seen: dict[str, ReconciliationResult] = {}

        for idx, record in enumerate(records):
            control_number = str(record.get("_control_number", ""))
            title = str(record.get("title", ""))
            shelfmark = record.get("shelfmark")

            # Reconcile manuscript
            ms_qid = self.reconcile_manuscript(
                control_number,
                str(shelfmark) if shelfmark else None,
            )
            ms_result = ReconciliationResult(
                entity_type="manuscript",
                local_id=control_number,
                label=title[:80] if title else control_number,
                existing_qid=ms_qid,
                action="update" if ms_qid else "create",
            )
            report.results.append(ms_result)
            if ms_qid:
                report.manuscripts_found += 1
            else:
                report.manuscripts_new += 1

            # Reconcile persons from MARC authority matches
            for match in record.get("marc_authority_matches") or []:
                name = str(match.get("name", ""))
                viaf_uri = match.get("viaf_uri")
                mazal_id = match.get("mazal_id")
                person_key = f"{name}:{viaf_uri}:{mazal_id}"

                if person_key in persons_seen:
                    continue

                person_qid = self.reconcile_person(
                    name,
                    viaf_uri,
                    mazal_id,
                    lc_id=match.get("lc_id"),
                    gnd_id=match.get("gnd_id"),
                    isni=match.get("isni"),
                )
                person_result = ReconciliationResult(
                    entity_type="person",
                    local_id=person_key,
                    label=name[:80],
                    existing_qid=person_qid,
                    action="skip" if person_qid else "create",
                )
                persons_seen[person_key] = person_result
                report.results.append(person_result)
                if person_qid:
                    report.persons_found += 1
                else:
                    report.persons_new += 1

            # Reconcile NER entities — pass ALL available identifiers so the
            # reconciler can find existing Wikidata items matched by any of
            # P244 (LCCN), P227 (GND), P213 (ISNI), in addition to VIAF/NLI.
            # Bug fix (2026-04-15, Geagea complaint): omitting lc_id/gnd_id/isni
            # caused the pipeline to create duplicate person items even when
            # the existing community item could have been found by one of those.
            for entity in record.get("entities") or []:
                name = str(entity.get("person", ""))
                viaf_uri = entity.get("viaf_uri")
                mazal_id = entity.get("mazal_id")
                person_key = f"{name}:{viaf_uri}:{mazal_id}"

                if person_key in persons_seen:
                    continue

                person_qid = self.reconcile_person(
                    name,
                    viaf_uri,
                    mazal_id,
                    lc_id=entity.get("lc_id"),
                    gnd_id=entity.get("gnd_id"),
                    isni=entity.get("isni"),
                )
                person_result = ReconciliationResult(
                    entity_type="person",
                    local_id=person_key,
                    label=name[:80],
                    existing_qid=person_qid,
                    action="skip" if person_qid else "create",
                )
                persons_seen[person_key] = person_result
                report.results.append(person_result)
                if person_qid:
                    report.persons_found += 1
                else:
                    report.persons_new += 1

            # Reconcile KIMA places (already have Wikidata QIDs)
            for _place_name, wikidata_uri in (record.get("kima_places") or {}).items():
                qid = self.reconcile_place(str(wikidata_uri))
                if qid:
                    report.places_found += 1

            if progress_cb:
                progress_cb(idx + 1, total)

        logger.info(
            "Reconciliation complete: %d MS found / %d new, "
            "%d persons found / %d new, %d places validated",
            report.manuscripts_found,
            report.manuscripts_new,
            report.persons_found,
            report.persons_new,
            report.places_found,
        )
        return report


def _sparql_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


_PREWARM_BATCH = 40


def prewarm_identifier_cache(
    reconciler: WikidataReconciler,
    probes: Iterable[tuple[str, str, str]],
) -> None:
    """Batch-fill single-property identifier lookups (Rule W-280).

    One SPARQL per 40 values replaces one query per item — the run 47e03437
    dry-run crawled at ~1.3 items/min on per-item WDQS lookups. Each probe is
    ``(cache_prefix, property_pid, value)`` and must use the same key format
    as the per-item methods (``person:viaf:…``, ``ms:nnl:…``, …).
    """
    pending: dict[tuple[str, str], list[str]] = {}
    seen: set[str] = set()
    for prefix, pid, value in probes:
        value = str(value or "").strip()
        key = f"{prefix}:{value}"
        if not value or key in reconciler._cache or key in seen:
            continue
        seen.add(key)
        pending.setdefault((prefix, pid), []).append(value)
    for (prefix, pid), values in pending.items():
        for start in range(0, len(values), _PREWARM_BATCH):
            chunk = values[start:start + _PREWARM_BATCH]
            values_expr = " ".join(f'"{_sparql_escape(v)}"' for v in chunk)
            sparql = (
                "SELECT ?id ?item WHERE { "
                f"?item wdt:{pid} ?id . VALUES ?id {{ {values_expr} }} }}"
            )
            found: dict[str, str] = {}
            try:
                for row in reconciler._query(sparql):
                    idv = str(row.get("id", {}).get("value", ""))
                    qid = extract_wikidata_qid(
                        str(row.get("item", {}).get("value", "")),
                    )
                    if idv and qid and idv not in found:
                        found[idv] = qid
            except ReconciliationUnavailableError:
                continue
            for v in chunk:
                reconciler._cache[f"{prefix}:{v}"] = found.get(v)


def prewarm_manuscript_shelfmarks(
    reconciler: WikidataReconciler,
    shelfmarks: Iterable[str],
) -> None:
    """Batch-fill ``ms:shelf:{Q_NLI}:{shelfmark}`` lookups (P195 + P217)."""
    pending: list[str] = []
    seen: set[str] = set()
    for shelfmark in shelfmarks:
        shelfmark = str(shelfmark or "").strip()
        key = f"ms:shelf:{Q_NLI}:{shelfmark}"
        if not shelfmark or key in reconciler._cache or key in seen:
            continue
        seen.add(key)
        pending.append(shelfmark)
    for start in range(0, len(pending), _PREWARM_BATCH):
        chunk = pending[start:start + _PREWARM_BATCH]
        values_expr = " ".join(f'"{_sparql_escape(v)}"' for v in chunk)
        sparql = (
            "SELECT ?shelf ?item WHERE { "
            f"?item wdt:P195 wd:{Q_NLI} . ?item wdt:P217 ?shelf . "
            f"VALUES ?shelf {{ {values_expr} }} }}"
        )
        found: dict[str, str] = {}
        try:
            for row in reconciler._query(sparql):
                shelf = str(row.get("shelf", {}).get("value", ""))
                qid = extract_wikidata_qid(str(row.get("item", {}).get("value", "")))
                if shelf and qid and shelf not in found:
                    found[shelf] = qid
        except ReconciliationUnavailableError:
            continue
        for v in chunk:
            reconciler._cache[f"ms:shelf:{Q_NLI}:{v}"] = found.get(v)


_PERSON_PROBE_PREFIXES = (
    ("person:viaf", "P214"),
    ("person:nli", "P8189"),
    ("person:P244", "P244"),
    ("person:P227", "P227"),
    ("person:P213", "P213"),
)


def prewarm_reconciler_for_items(
    reconciler: WikidataReconciler,
    items: Iterable[Any],
) -> None:
    """Collect identifier probes from built items and batch-fill the cache."""
    if not hasattr(reconciler, "_cache") or not hasattr(reconciler, "_query"):
        return
    probes: list[tuple[str, str, str]] = []
    shelfmarks: list[str] = []
    p2888_uris: list[str] = []
    work_specs: list[tuple[str, str, str | None]] = []
    for item in items:
        entity_type = str(getattr(item, "entity_type", "") or "")
        statements = getattr(item, "statements", []) or []

        def _value(pid: str) -> str:
            for statement in statements:
                if str(getattr(statement, "property_id", "") or "") == pid:
                    value = getattr(statement, "value", None)
                    if value not in (None, ""):
                        return str(value)
            return ""

        if entity_type == "person":
            for prefix, pid in _PERSON_PROBE_PREFIXES:
                value = _value(pid)
                if value:
                    probes.append((prefix, pid, value))
        elif entity_type == "manuscript":
            nnl = _value("P3959")
            if nnl:
                probes.append(("ms:nnl", "P3959", nnl))
            shelf = _value("P217")
            if shelf:
                shelfmarks.append(shelf)
        elif entity_type == "work":
            p2888 = _value("P2888")
            if p2888:
                p2888_uris.append(p2888)
            author_qid = _value("P50") or None
            labels = getattr(item, "labels", {}) or {}
            for lang in ("he", "en"):
                title = str(labels.get(lang) or "").strip()
                if title:
                    work_specs.append((title, lang, author_qid))
    prewarm_identifier_cache(reconciler, probes)
    prewarm_manuscript_shelfmarks(reconciler, shelfmarks)
    prewarm_exact_match_uris(reconciler, p2888_uris)
    prewarm_work_titles(reconciler, work_specs)


def prewarm_exact_match_uris(
    reconciler: WikidataReconciler,
    uris: Iterable[str],
) -> None:
    """Batch-fill ``p2888:{uri}`` lookups for HMO Wikibase exact-match bridges."""
    pending: list[str] = []
    seen: set[str] = set()
    for raw in uris:
        clean = str(raw or "").strip()
        key = f"p2888:{clean}"
        if (
            not clean.startswith(("http://", "https://"))
            or any(ch in clean for ch in ("<", ">", '"', " ", "\t", "\n"))
            or key in reconciler._cache
            or key in seen
        ):
            continue
        seen.add(key)
        pending.append(clean)
    for start in range(0, len(pending), _PREWARM_BATCH):
        chunk = pending[start:start + _PREWARM_BATCH]
        values_expr = " ".join(f"<{u}>" for u in chunk)
        sparql = (
            "SELECT ?uri ?item WHERE { "
            f"?item wdt:P2888 ?uri . VALUES ?uri {{ {values_expr} }} }}"
        )
        found: dict[str, list[str]] = {}
        try:
            for row in reconciler._query(sparql):
                uval = str(row.get("uri", {}).get("value", ""))
                qid = extract_wikidata_qid(str(row.get("item", {}).get("value", "")))
                if uval and qid:
                    found.setdefault(uval, []).append(qid)
        except ReconciliationUnavailableError:
            continue
        for u in chunk:
            hits = list(dict.fromkeys(found.get(u, [])))
            reconciler._cache[f"p2888:{u}"] = hits[0] if len(hits) == 1 else None


def prewarm_work_titles(
    reconciler: WikidataReconciler,
    specs: Iterable[tuple[str, str, str | None]],
) -> None:
    """Batch-fill ``work:{lang}:{title}:{author}`` lookups (Rule W-290).

    Each spec is ``(title, lang, author_qid)``; the per-item method's exact
    label query and its P50 rejection rule are replicated on the batched
    results so the cache holds exactly what the per-item call would decide.
    """
    pending: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for title, lang, author_qid in specs:
        title = str(title or "").strip()
        key = f"work:{lang}:{title}:{author_qid or ''}"
        if not title or key in reconciler._cache or key in seen:
            continue
        seen.add(key)
        pending.append((title, lang, author_qid))
    by_lang: dict[str, list[tuple[str, str, str | None]]] = {}
    for title, lang, author_qid in pending:
        by_lang.setdefault(lang, []).append((title, lang, author_qid))
    for lang, rows in by_lang.items():
        for start in range(0, len(rows), _PREWARM_BATCH):
            chunk = rows[start:start + _PREWARM_BATCH]
            titles = list(dict.fromkeys(t for t, _, _ in chunk))
            values_expr = " ".join(f'"{_sparql_escape(t)}"@{lang}' for t in titles)
            sparql = (
                "SELECT ?title ?item ?author WHERE { "
                "?item wdt:P31/wdt:P279* wd:Q47461344 . "
                f"?item rdfs:label ?title . VALUES ?title {{ {values_expr} }} "
                "OPTIONAL { ?item wdt:P50 ?author . } }"
            )
            rows_by_title: dict[str, list[tuple[str | None, str | None]]] = {}
            try:
                for row in reconciler._query(sparql):
                    title_val = str(row.get("title", {}).get("value", ""))
                    cand_qid = extract_wikidata_qid(
                        str(row.get("item", {}).get("value", "")),
                    )
                    author_uri = str(row.get("author", {}).get("value", ""))
                    cand_author = extract_wikidata_qid(author_uri) if author_uri else None
                    if title_val and cand_qid:
                        rows_by_title.setdefault(title_val, []).append(
                            (cand_qid, cand_author),
                        )
            except ReconciliationUnavailableError:
                continue
            for title, lang_key, author_qid in chunk:
                chosen: str | None = None
                for cand_qid, cand_author in rows_by_title.get(title, []):
                    if author_qid and cand_author and cand_author != author_qid:
                        continue
                    chosen = cand_qid
                    break
                reconciler._cache[f"work:{lang_key}:{title}:{author_qid or ''}"] = chosen
