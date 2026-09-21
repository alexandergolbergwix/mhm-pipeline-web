"""Jev (TypeSafe System One) question sets.

Tuned question sets ported verbatim from
``backend/scripts/typesafe_bakeoff.py::questions_for`` — they encode the
rubric nuances that produced the certified bake-off numbers (Jev-primary
rollout, steps 1–8). Do NOT regenerate these from schemas: the wording is
what was measured.

A generic schema→questions fallback exists only for evaluators outside the
six certified ones (authority / hmo_wikibase_schema channels in v1).
"""

from __future__ import annotations

from typing import Any

NAXIS, TAXIS, RAXIS = "name_ok", "type_ok", "role_ok"

# Trailing-line replacement: the state stays byte-identical to the tier-1
# prompt except this one line (the parity the certification measured).
JEV_CLOSE = (
    "Do not produce a JSON verdict object. Instead, answer the questions "
    "attached to this request: each question is one axis of the rubric's "
    "output contract, applied to the prediction and MARC context above."
)

_NAME_OK = (
    "Applying the evaluation brief in the state — including its per-entity-type "
    "and label-quality exceptions (system labels, controlled-vocabulary terms, "
    "subject headings, honest-negative descriptions) — is the predicted "
    "span/label acceptable as the identity of a real entity? 'yes' = the brief "
    "declares the label complete (exact/vowelized MARC match, or an accepted "
    "system/controlled-vocabulary label with substance); 'partial' = "
    "trimmed/extended or weakened label; 'no' = absent, a different entity, "
    "generic-placeholder-only, or malformed. Boundary noise is 'partial', not "
    "exact: a span that cuts a bracket/parenthesis mid-way (unclosed '(' or an "
    "unterminated title) or appends a parenthetical not part of the entity. An "
    "empty MARC context with no accepted-exception coverage forces 'no'."
)
_TYPE_OK = (
    "Applying the evaluation brief in the state, is the predicted entity type "
    "correct for this span/label? 'yes' = clearly correct; 'partial' = correct "
    "but truncated/ambiguous, or the relationship is different than claimed — "
    "e.g. in a provenance note 'בן/בכמה\"ר <name>' names a father or teacher, "
    "not the owner/acquirer; 'no' = clearly the wrong kind of thing."
)
_ROLE_OK_PERSON = (
    "Applying the evaluation brief in the state, does the MARC evidence "
    "support the predicted role for this person? 'yes' = the role-mapped "
    "field(s) support it; 'partial' = adjacent but weaker evidence; "
    "'no' = MARC unambiguously assigns a different role. A dictation note "
    "(מכתיבת יד / 'dictated to…') records the author or dictator — it does "
    "not make the named person a copyist (TRANSCRIBER). Honor the "
    "deterministic grounding signal stated in the state."
)
_EVIDENCE_FIELDS = {
    "person_ner": {
        "authors": "authors list", "contributors": "contributors list",
        "colophon_text": "colophon text", "data_from_colophon": "colophon data",
        "provenance": "provenance", "notes": "notes", "title": "title",
        "none": "no evidence anywhere",
    },
    "contents_ner": {
        "contents": "contents / table of contents", "notes": "notes",
        "colophon_text": "colophon text", "title": "title",
        "authors": "authors", "contributors": "contributors", "subjects": "subjects",
        "none": "no evidence anywhere",
    },
    "provenance_ner": {
        "provenance": "provenance", "notes": "notes", "colophon_text": "colophon text",
        "subjects": "subjects", "contributors": "contributors", "title": "title",
        "none": "no evidence anywhere",
    },
    "genre_classifier": {
        "genres": "MARC 655 genre field", "subjects": "subject headings",
        "notes": "notes", "title": "title", "variant_titles": "variant titles",
        "none": "no evidence anywhere",
    },
    "hmo_wikibase_item": {
        "title": "MARC title", "authors": "authors", "contributors": "contributors",
        "subjects": "subjects", "provenance": "provenance", "notes": "notes",
        "colophon_text": "colophon text", "contents": "contents", "dates": "dates",
        "place": "place", "shelfmark": "shelfmark",
        "claims": "the item's own claims",
        "descriptions": "the item's own descriptions",
        "none": "no evidence anywhere",
    },
    "wikidata_item": {
        "title": "MARC title", "authors": "authors", "contributors": "contributors",
        "subjects": "subjects", "provenance": "provenance", "notes": "notes",
        "contents": "contents", "claims": "the item's own statements",
        "descriptions": "the item's own descriptions",
        "authority_evidence": "VIAF/Mazal authority evidence",
        "none": "no evidence anywhere",
    },
}
_MATCH_KIND = {
    "exact_or_vowelized": "exact match or vowelization variant (same consonants)",
    "trimmed_or_extended": "trimmed or extended: a prefix/suffix/substring match",
    "absent": "not present in the MARC evidence",
    "different_entity": "present in MARC but refers to a different real-world entity",
}
_NAME_QUALITY = {
    "specific_and_substantive": (
        "label/description identify the entity specifically and carry substance"
    ),
    "system_label_ok": (
        "intentional system label carrying MS control number/period "
        "plus a substantive description"
    ),
    "generic_fallback": (
        "generic placeholder (e.g. '... in the Hebrew Manuscripts "
        "Ontology (HMO)') without substance"
    ),
    "malformed": (
        "malformed label (unbalanced quotes, trailing punctuation in "
        "quotes, 'und' language code)"
    ),
}

# Evaluators with a tuned, certified question set (from the bake-off).
TUNED_EVALUATORS = frozenset(_EVIDENCE_FIELDS)


def _choice(qid: str, instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def questions_for(
    evaluator_id: str,
    candidate: Any = None,
    max_claims: int = 8,
) -> dict[str, dict[str, Any]]:
    qs: dict[str, dict[str, Any]] = {
        NAXIS: _choice(NAXIS, _NAME_OK, {"yes": "present as claimed",
                                         "partial": "trimmed/extended match",
                                         "no": "absent or different entity"}),
        TAXIS: _choice(TAXIS, _TYPE_OK, {"yes": "type is correct",
                                         "partial": "correct but truncated/ambiguous",
                                         "no": "clearly the wrong type"}),
    }
    if evaluator_id == "person_ner":
        qs[RAXIS] = _choice(RAXIS, _ROLE_OK_PERSON, {
            "yes": "role supported by the role-mapped MARC field(s)",
            "partial": "adjacent but weaker evidence for the role",
            "no": "MARC assigns a different role",
        })
    elif evaluator_id == "hmo_wikibase_item":
        qs[RAXIS] = _choice(
            RAXIS,
            "Applying the evaluation brief in the state, are the item's claims "
            "and structural checks acceptable? 'not_applicable' ONLY for "
            "structural entity types — CatalogStep, EvidenceStep, "
            "EvidenceChain, Evidence, PhilologicalView, CatalogingView, "
            "ViewType, paradigm individuals — with no SHACL blocking issues. "
            "For every other entity type judge the claims themselves: 'yes' = "
            "claims are plausible and consistent with the item's labels and "
            "the MARC context; 'partial' = claims are weak, thin, or carry "
            "minor attribution doubts (removable, curator decides); 'no' = a "
            "claim contradicts the item's labels/MARC context, or SHACL "
            "issues block approval.",
            {"yes": "claims plausible and consistent",
             "partial": "claims weak or thin",
             "no": "a wrong claim or SHACL blocking issues",
             "not_applicable": "structural item only"},
        )
        qs["name_quality"] = _choice(
            "name_quality",
            "Applying the evaluation brief in the state (especially the "
            "per-entity-type guidance), which best describes the "
            "label/description quality of this item?",
            _NAME_QUALITY,
        )
        qs["text_quality"] = {
            "type": "noul",
            "instructions": (
                "Read the item's label and description text character by "
                "character. Flag ANY generation artifact: unclosed parentheses "
                "or quotes, text cut off mid-word or mid-folio-range (e.g. "
                "'folios 1' with no end value), duplicated function words "
                "('for in'), a label that names a different manuscript than "
                "the description, 'und' language codes, or a template "
                "description that only repeats the label. 1.0 = at least one "
                "artifact present; 0.0 = the text is clean and complete."
            ),
        }
    else:  # wikidata_item — role_ok = statement/evidence acceptability
        qs[RAXIS] = _choice(
            RAXIS,
            "Applying the evaluation brief in the state, are the item's "
            "statements, qualifiers, references, and any listed validation "
            "issues acceptable for the supplied evidence pack (MARC + VIAF + "
            "Mazal + existing Wikidata + HMO Wikibase)? 'yes' = every present "
            "claim is supported by at least one evidence channel and no "
            "validator issue signals a real public-data problem; 'partial' = "
            "mostly correct but carries removable bad claims; 'no' = a "
            "present claim is unsupported by every channel, modeled in the "
            "wrong place, contaminated, OR an ERROR-severity validation issue "
            "is present (an error-severity issue is a real public-data "
            "problem — an identifierless person item is not publishable). "
            "Absent claims are never defects — judge what is present. "
            "Provenance is ESTABLISHED by the claim's own references: a "
            "claim carrying P248 (stated in) + P3959 (NNL catalog ID) / P854 "
            "URL is supported even when the MARC slice does not repeat the "
            "fact. A claim backed by the Mazal pack (e.g. P8189 with its "
            "Ktiv reference) is supported.",
            {"yes": "statements acceptable for the evidence",
             "partial": "mostly correct, removable bad claims present",
             "no": "unsupported claim, wrong place, or ERROR-severity issue"},
        )
        # Statement-level checks: one noul per claim in the item's statements
        # sample — the explicit counterpart of the tier-1 judge's claim-by-
        # claim rubric pass ("same statements, same rules").
        claims = (getattr(candidate, "payload", None) or {}).get("statements") or []
        for i, stmt in enumerate(claims[:max_claims]):
            if not isinstance(stmt, dict):
                continue
            prop = stmt.get("property") or "?"
            value = stmt.get("value_label") or stmt.get("value") or "?"
            prop_label = stmt.get("property_label") or ""
            qs[f"claim_{i}"] = {
                "type": "noul",
                "instructions": (
                    f"The item carries the statement `statements[{i}]`: "
                    f"{prop_label} ({prop}) = {value}. Is this statement "
                    "supported by at least one evidence channel in the state? "
                    "Channels: (a) the statement's own `references` (stated-in "
                    "item, external-id, or URL); (b) `work_candidate_evidence` "
                    "— an accepted/curator-approved work entry with a "
                    "source_field supports the work's P31/title/author "
                    "claims; (c) per-claim MARC provenance `claim_sources`; "
                    "(d) the MARC slice itself (title, authors, 500/505 "
                    "fields); (e) VIAF / Mazal authority packs; (f) existing "
                    "Wikidata or the HMO Wikibase pack. Property hints: P31 "
                    "written-work is supported by an accepted "
                    "work_candidate_evidence entry; P1476 title by a MARC "
                    "245/500 title or the accepted work title; P2093 "
                    "author-name string by the name in MARC authors/500; "
                    "P50 by authority evidence. P973 (described-at URL) is "
                    "supported when the catalog/access URL appears in the "
                    "MARC slice (856/966 digital access) or the claim's own "
                    "references. P569/P570 (birth/death dates) are supported "
                    "when the dates appear in the MARC slice (100$d, 008, "
                    "260$c, 264$c, notes) or an authority pack. "
                    "1.0 = at least one channel "
                    "supports it; 0.0 = no channel names this property or "
                    "its source."
                ),
            }
    if evaluator_id == "wikidata_item":
        # Instance-of typing + duplicate status: the two Wikidata-contract
        # checks the per-claim sample can miss (P31 may sit beyond the first
        # `max_claims` statements) and the live-probe identity rule (W-139).
        # Wikidata-only — the NER evaluators share the else-branch above for
        # their certified role question, but must not receive these.
        qs["p31_ok"] = _choice(
            "p31_ok",
            "Applying the WikiProject Manuscripts data model in the state, is "
            "the item's instance-of typing (P31) right? 'yes' = P31 is present "
            "and matches the entity type — person → Q5 (human), manuscript → "
            "Q87167 (manuscript) or a specific legitimate subclass, work → a "
            "written-work class; 'partial' = P31 is present but questionable "
            "as the primary class (a discouraged manuscript subclass such as "
            "Q213924 codex, a lectionary as P31 instead of P136 genre, or a "
            "facsimile still typed only as manuscript); 'no' = P31 is absent "
            "entirely, contradicts the entity type (a manuscript marked Q5, "
            "an institution keyword on a person), or carries a known-wrong "
            "class. Wrong public modeling here is a real data problem — the "
            "community deletes mistyped items.",
            {"yes": "instance-of typing is correct for this entity",
             "partial": "present but a questionable/discouraged primary class",
             "no": "P31 missing, contradicts the entity type, or known-wrong"},
        )
        qs["duplicate_risk"] = _choice(
            "duplicate_risk",
            "Applying the state's existing-QID and live duplicate-check "
            "channel (verify_evidence.wikidata_existing.duplicate_check), "
            "what is this item's duplicate status? 'no_duplicate_risk' = the "
            "item carries an existing_qid (an UPDATE), or duplicate_check "
            "reports already_linked, or reports absent with an identifier "
            "probed, or adoption.adopted is true; 'duplicate_found' = "
            "duplicate_check.status is candidates_found WITHOUT an adopted "
            "existing_qid — an item with this identifier already exists and "
            "creating another is a duplicate (an identifier match is an "
            "identity match even when labels differ); 'unknown' = not_run / "
            "skipped / unavailable — inconclusive, and an inconclusive check "
            "must not move any axis.",
            {"no_duplicate_risk": "update, adopted, or probe says absent",
             "duplicate_found": "an identifier-matching item already exists",
             "unknown": "probe inconclusive — no conclusion"},
        )
    if evaluator_id not in _EVIDENCE_FIELDS:
        return qs
    fields = _EVIDENCE_FIELDS[evaluator_id]
    qs["evidence_field"] = _choice(
        "evidence_field",
        "Which evidence source in the state is the primary basis for your "
        f"'{NAXIS}' answer? Pick where the deciding text lives.",
        fields,
    )
    if evaluator_id not in ("genre_classifier", "hmo_wikibase_item", "wikidata_item"):
        qs["match_kind"] = _choice(
            "match_kind",
            "Applying the universal definitions in the state, what kind of "
            "textual match is the predicted span against the MARC evidence?",
            _MATCH_KIND,
        )
    return qs


def generic_questions(schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Schema-driven fallback for evaluators outside the certified set.

    One Choice question per rubric axis the verdict schema declares. The
    harness-only ``unknown`` value is never offered (Rule W-158).
    """
    qs: dict[str, dict[str, Any]] = {}
    props = (schema or {}).get("properties") or {}
    criteria = {
        "yes": "supported by the evidence in the state",
        "partial": "partially supported or ambiguous",
        "no": "not supported by the evidence",
        "n/a": "not applicable for this entity type",
        "not_applicable": "not applicable for this entity type",
    }
    for qid in (NAXIS, TAXIS, RAXIS):
        prop = props.get(qid)
        if not isinstance(prop, dict):
            continue
        enum = [str(v) for v in (prop.get("enum") or []) if str(v) != "unknown"]
        if not enum:
            continue
        qs[qid] = _choice(
            qid,
            "Applying the evaluation brief in the state, judge this axis of "
            f"the rubric's output contract: {qid}.",
            {c: criteria.get(c, c) for c in enum},
        )
    return qs
