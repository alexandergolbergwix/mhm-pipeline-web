"""Top-30 Wikidata entity-creation rule manifest for Jev verification.

Each rule is one checkable statement about a proposed public Wikidata item,
distilled from ``eval-agent/config/rubrics/wikidata_item.md``, the
WikiProject Manuscripts data model, and the block rules (W-68..W-71,
W-98..W-100, W-139, W-140, W-164, W-171..W-175).

Two kinds:

- ``deterministic`` — checked in code (``eval_agent.jev_gates`` gates and
  the build's validators). Never asked of the model; the explanation
  builder renders their findings.
- ``judgment`` — needs reading and weighing of the evidence pack. Each one
  becomes exactly one TypeSafe question (the curator chose per-rule
  granularity), answered yes/partial/no like the certified axes.

The certified axis questions (name_ok / type_ok / role_ok) stay
byte-identical (block rule R44): rule answers are reconciled into the axes
IN CODE, and ``overall`` stays code-computed. Added questions extend the
certified set — re-run the wikidata-channel bake-off before defaulting
that channel to Jev.
"""

from __future__ import annotations

from typing import Any

_DETERMINISTIC = "deterministic"
_JUDGMENT = "judgment"

# entity-type applicability: "all" or a subset of manuscript/person/work.


def rule_question_id(rule_id: str) -> str:
    return f"rule_{rule_id}"


RULES: tuple[dict[str, Any], ...] = (
    # ── Deterministic rules (checked in code, never asked) ────────────
    {
        "id": "p31_present",
        "kind": _DETERMINISTIC,
        "name": "instance-of statement present",
        "statement": "Every item carries at least one P31 (instance of) claim.",
        "axis": "type_ok",
    },
    {
        "id": "duplicate_probe",
        "kind": _DETERMINISTIC,
        "name": "no duplicate creation",
        "statement": (
            "The live Wikidata probe ran: an item carrying this identifier "
            "already exists (candidates_found) only when it is adopted or an "
            "UPDATE — creating a second item with the same identifier is a "
            "duplicate (Rule W-139)."
        ),
        "axis": "type_ok",
    },
    {
        "id": "validator_errors",
        "kind": _DETERMINISTIC,
        "name": "no ERROR-severity validation issue",
        "statement": (
            "No ERROR-severity validation issue remains on the item — such "
            "an issue blocks upload."
        ),
        "axis": "role_ok",
    },
    {
        "id": "text_artifacts",
        "kind": _DETERMINISTIC,
        "name": "no mechanical text artifacts",
        "statement": (
            "Labels and descriptions are free of mechanical artifacts: "
            "unclosed parentheses or quotes, truncated folio ranges, "
            "quote-collision garbles."
        ),
        "axis": "name_ok",
    },
    {
        "id": "one_title_per_work",
        "kind": _DETERMINISTIC,
        "name": "one title per work",
        "statement": "A work carries exactly one P1476 (title) claim.",
        "axis": "role_ok",
        "applies": ("work",),
    },
    {
        "id": "one_catalog_record",
        "kind": _DETERMINISTIC,
        "name": "one catalog record per manuscript",
        "statement": (
            "A manuscript carries exactly one P217 (shelfmark) and one "
            "P3959 (catalog ID) — a manuscript owns exactly one catalog "
            "record."
        ),
        "axis": "role_ok",
        "applies": ("manuscript",),
    },
    {
        "id": "local_targets_resolve",
        "kind": _DETERMINISTIC,
        "name": "internal reference targets resolve",
        "statement": (
            "Every __LOCAL:<id> statement value resolves in "
            "local_reference_targets — an unresolved internal reference is "
            "a build defect."
        ),
        "axis": "role_ok",
    },
    {
        "id": "author_string_clean",
        "kind": _DETERMINISTIC,
        "name": "author-name strings hold one name",
        "statement": (
            "Every P2093 (author name string) value is a single name — a "
            "comma-joined list of several names is a defect."
        ),
        "axis": "role_ok",
    },
    # ── Judgment rules (one TypeSafe question each) ───────────────────
    {
        "id": "label_identity",
        "kind": _JUDGMENT,
        "name": "label names the right entity",
        "axis_native": True,
        "statement": (
            "The label names the real entity the evidence describes — not a "
            "different person, work, or manuscript."
        ),
        "axis": "name_ok",
    },
    {
        "id": "label_substance",
        "axis_native": True,
        "kind": _JUDGMENT,
        "name": "label and description carry substance",
        "statement": (
            "The label is not a generic placeholder (e.g. '... in the "
            "Hebrew Manuscripts Ontology (HMO)') and is not malformed."
        ),
        "axis": "name_ok",
        "question": (
            "Do the label and description carry substance? 'yes' = "
            "specific and substantive — including an intentional system "
            "label with control number and period, and a manuscript "
            "designation built from catalog identity fragments (holder, "
            "collector, shelfmark); 'partial' = a generic placeholder "
            "without substance; 'no' = malformed (unbalanced quotes, 'und' "
            "language code)."
        ),
        "fix": "Replace the placeholder or malformed text with the evidenced name.",
    },
    {
        "id": "description_accuracy",
        "axis_native": True,
        "kind": _JUDGMENT,
        "name": "description matches the evidence",
        "statement": (
            "The description is the intended house style — an evidenced "
            "fragment set (language, date, script, material, holder) — and "
            "never mixes in another manuscript's details."
        ),
        "axis": "name_ok",
        "question": (
            "Does the description match the evidence? A description is a "
            "disambiguator in house style: '<language> manuscript, <date>, "
            "<script>, <material>, <holder>' — any evidenced subset. 'yes' "
            "= every fragment in the description appears in the MARC "
            "context or the evidence packs (nothing invented); 'partial' = "
            "NAME the specific fragment that looks wrong or borrowed; "
            "'no' = it describes a different manuscript or asserts "
            "unsupported facts. Hedging without a named fragment is 'yes'."
        ),
        "fix": "Rebuild the description from the evidenced fragments only.",
    },
    {
        "id": "alias_intent",
        "kind": _JUDGMENT,
        "name": "aliases are intentional",
        "statement": (
            "When the label is the holder+shelfmark designation, the Hebrew "
            "MARC 245 title remaining as an alias is intentional (Rule "
            "W-164); aliases must not name other contained works that are "
            "not the 245."
        ),
        "axis": "name_ok",
        "applies": ("manuscript", "work"),
        "question": (
            "Are the aliases intentional? For a designation-label "
            "manuscript the Hebrew MARC 245 title as alias is intentional — "
            "do not penalize it. 'yes' = aliases are the 245 title or known "
            "variants; 'partial' = redundant or oddly formatted but "
            "harmless; 'no' = an alias names a different work or entity."
        ),
        "fix": "Remove the foreign alias.",
    },
    {
        "id": "entity_type_fit",
        "kind": _JUDGMENT,
        "name": "entity type fits the evidence",
        "axis_native": True,
        "statement": (
            "The predicted entity type (person / manuscript / work) is "
            "correct for what the evidence describes. Span length is a "
            "label question, never a type question."
        ),
        "axis": "type_ok",
    },
    {
        "id": "p31_class_choice",
        "kind": _JUDGMENT,
        "name": "instance-of class is appropriate",
        "axis_native": True,
        "statement": (
            "The chosen P31 class fits: person → Q5, manuscript → Q87167 "
            "or a legitimate subclass, work → a written-work class; no "
            "discouraged subclass as primary class, and a printed facsimile "
            "typed as book is acceptable."
        ),
        "axis": "type_ok",
    },
    {
        "id": "existing_qid_identity",
        "kind": _JUDGMENT,
        "name": "updated QID is the right entity",
        "statement": (
            "When the item updates an existing QID, that QID is the same "
            "real-world entity — never a wrong-entity update."
        ),
        "axis": "type_ok",
        "question": (
            "If the item updates an existing QID, is that QID the same "
            "real-world entity? The EXISTING-Wikidata pack is the identity "
            "evidence: when verify_evidence.wikidata_existing.live.labels "
            "equals (or contains) the item's own label — the live item's "
            "Hebrew or English label matches the claim you are judging — "
            "the answer is 'yes' by definition; the pipeline adopted that "
            "QID through the duplicate probe. Do not demand more "
            "corroboration: identifier or exact-label match IS identity "
            "(Rule W-139). 'no' = the live labels contradict the item's "
            "identity (a different person/work), never a missing extra "
            "corroboration."
        ),
        "fix": "Unlink the wrong QID and re-run the duplicate check.",
    },
    {
        "id": "person_role",
        "axis_native": True,
        "kind": _JUDGMENT,
        "name": "person role supported",
        "statement": (
            "A person's role is supported by the role-mapped MARC fields. A "
            "dictation note (מכתיבת יד / 'dictated to…') records the author "
            "or dictator — it does not make the person a copyist."
        ),
        "axis": "role_ok",
        "applies": ("person",),
        "question": (
            "Does the MARC evidence support the person's predicted role? "
            "'yes' = the role-mapped field(s) support it; 'partial' = "
            "adjacent but weaker evidence; 'no' = the record assigns a "
            "different role (a dictation note makes the person an author, "
            "not a copyist). Honor the deterministic grounding signal in "
            "the state."
        ),
        "fix": "Relabel the role (e.g. author instead of copyist) or drop the role edge.",
    },
    {
        "id": "claims_supported",
        "kind": _JUDGMENT,
        "name": "claims supported by a channel",
        "statement": (
            "Every present claim is supported by at least one evidence "
            "channel: the claim's own references, work_candidate_evidence, "
            "per-claim claim_sources, the MARC slice, VIAF/Mazal authority "
            "packs, existing Wikidata, or the HMO Wikibase. Absent claims "
            "are never defects."
        ),
        "axis": "role_ok",
        "question": (
            "Are the present statements supported by at least one evidence "
            "channel? Channels: the claim's own references; "
            "work_candidate_evidence; claim_sources (a PID with non-empty "
            "evidence is supported; channel names say which pack — "
            "authority.viaf/mazal, hmo_wikibase, work_candidate_evidence, "
            "authority.person_link); the MARC slice; VIAF/Mazal packs; "
            "existing Wikidata or HMO Wikibase. Structural claims (P31, "
            "P3959, P5008) need no source. channel_empty = catalog "
            "sparsity, not a defect; no_channel_mapped = build defect. "
            "'yes' = every present claim supported; 'partial' = mostly "
            "correct with removable bad claims; 'no' = a present claim is "
            "supported by no channel."
        ),
        "fix": "Remove the unsupported claim or add its source.",
    },
    {
        "id": "modeling_place",
        "kind": _JUDGMENT,
        "name": "claims modeled in the right place",
        "statement": (
            "Claims sit on the right property and the right kind of value: "
            "P11603 names a human who transcribed a work, P195 names the "
            "holding institution; never accept a building or institution "
            "as a person or scribe."
        ),
        "axis": "role_ok",
        "question": (
            "Are claims modeled in the right place? 'yes' = correct "
            "property and value kind; 'partial' = defensible but improvable "
            "modeling; 'no' = wrong place — an institution used as a "
            "person or scribe, a transcriber claim on a building, or a "
            "manuscript fact stored on the work."
        ),
        "fix": "Move the claim to the correct property or item.",
    },
    {
        "id": "author_modeling",
        "axis_native": True,
        "kind": _JUDGMENT,
        "name": "author claims modeled correctly",
        "statement": (
            "Author facts use P50 when a verified person QID exists, else "
            "P2093 as a name string — content-level authors and "
            "work-authors become P50, a local target, or P2093 (Rules "
            "W-69/W-70)."
        ),
        "axis": "role_ok",
        "applies": ("work",),
        "question": (
            "Are the work's author claims modeled correctly? 'yes' = P50 "
            "with a verified QID when authority evidence exists, else P2093 "
            "name strings matching MARC — never both for one person; "
            "'partial' = a mix or formatting drift a curator can fix; "
            "'no' = an author attributed to the wrong or invented person."
        ),
        "fix": "Replace the string with the verified QID, or correct the name.",
    },
    {
        "id": "subject_specificity",
        "kind": _JUDGMENT,
        "name": "subjects are specific enough",
        "statement": (
            "P921 is the primary subject, not a dump of every 650 note; "
            "broad headings (e.g. 'Jews') stay out unless the record makes "
            "them the primary subject. An exact controlled MARC 650 mapping "
            "supports P921."
        ),
        "axis": "role_ok",
        "applies": ("manuscript", "work"),
        "question": (
            "Are the P921 (subject) claims specific enough? 'yes' = each "
            "subject is a controlled, well-matched heading the record is "
            "actually about; 'partial' = one subject broader than "
            "warranted but removable; 'no' = a generic heading (e.g. "
            "'Jews') asserted as primary subject without the record "
            "making it so."
        ),
        "fix": "Remove the over-broad subject claim.",
    },
    {
        "id": "p7535_scope",
        "kind": _JUDGMENT,
        "name": "P7535 used for archival scope only",
        "statement": (
            "P7535 (archival collection scope and content) is not a dump "
            "for arbitrary manuscript catalog notes or provenance prose."
        ),
        "axis": "role_ok",
        "question": (
            "If P7535 is present, does it hold archival-collection "
            "scope-and-content text? 'yes' = archival collection text (or "
            "P7535 absent); 'partial' = borderline catalog prose; 'no' = "
            "ordinary catalog notes or unsupported text in P7535."
        ),
        "fix": "Remove P7535 or move the text to the right property.",
    },
    {
        "id": "dates_plausible",
        "kind": _JUDGMENT,
        "name": "dates agree with the evidence",
        "statement": (
            "Birth/death/creation dates agree with the authority packs and "
            "the MARC record; a year-precision value (+1950-00-00T00:00:00Z "
            "with precision=9) is valid; only chronological contradictions "
            "or authority conflicts fail."
        ),
        "axis": "role_ok",
        "applies": ("person", "work"),
        "question": (
            "Do the date claims agree with the evidence? 'yes' = dates "
            "match an authority pack or the MARC record (year precision is "
            "valid); 'partial' = plausible but mildly conflicting with one "
            "channel; 'no' = chronologically impossible or contradicting "
            "the authority evidence."
        ),
        "fix": "Correct the date from the authority pack.",
    },
    {
        "id": "person_dates_authority",
        "kind": _JUDGMENT,
        "name": "person description dates are authority-backed",
        "statement": (
            "A person description carries dates only when an "
            "identifier-backed authority row supplies them; a dateless "
            "'scribe' description is correct, not incomplete."
        ),
        "axis": "role_ok",
        "applies": ("person",),
        "question": (
            "Does the description carry dates only when an authority row "
            "backs them? 'yes' = authority-backed dates or a dateless "
            "description (correct); 'partial' = thin authority backing; "
            "'no' = dates with no authority backing — invented."
        ),
        "fix": "Drop the invented dates from the description.",
    },
    {
        "id": "value_identity_trust",
        "kind": _JUDGMENT,
        "name": "item values use supplied labels",
        "statement": (
            "Item-valued claims use the supplied value_label and "
            "verify_evidence.value_labels — never an identity guessed from "
            "QID shape or model memory."
        ),
        "axis": "role_ok",
        "question": (
            "Do item-valued claims use the supplied value_label / "
            "verify_evidence.value_labels glosses? 'yes' = every value is "
            "the glossed entity, or a value lacks a gloss but is plausible "
            "(a missing gloss alone is never a defect); 'partial' = a "
            "gloss CONTRADICTS the claim it glosses; 'no' = an identity "
            "invented from QID shape or model memory."
        ),
        "fix": "Correct the value to the glossed entity or drop the claim.",
    },
    {
        "id": "holder_identity",
        "kind": _JUDGMENT,
        "name": "holding institution is the right one",
        "statement": (
            "P195 holding claims name the correct audited holder QID "
            "(trust value_label, then verify_evidence.value_labels, then "
            "the label's holder fragment) — never re-invent the "
            "institution behind an audited QID."
        ),
        "axis": "role_ok",
        "applies": ("manuscript",),
        "question": (
            "Does P195 (held by) name the correct holding institution? "
            "Trust value_label, then verify_evidence.value_labels, then "
            "the label's holder fragment. 'yes' = the audited holder QID "
            "is right, or the gloss agrees with the label's holder "
            "fragment; 'partial' = the gloss CONTRADICTS the label's "
            "holder fragment; 'no' = a different institution (e.g. "
            "inventing NLI for a Cambridge or British Library QID). A "
            "missing gloss alone is never a defect — answer 'yes'."
        ),
        "fix": "Correct the holder QID to the glossed institution.",
    },
    {
        "id": "p973_agreement",
        "kind": _JUDGMENT,
        "name": "described-at URL agrees with the shelfmark",
        "statement": (
            "A P973 (described at URL) whose embedded shelfmark or "
            "reference disagrees with the item's P217 is a removable bad "
            "claim."
        ),
        "axis": "role_ok",
        "applies": ("manuscript",),
        "question": (
            "Do the P973 (described at) URLs agree with this item? Check "
            "an embedded shelfmark/reference against the item's P217. "
            "'yes' = URLs agree, or no P973 present (absent claims are "
            "never defects); 'partial' = right repository but ambiguous "
            "reference; 'no' = the URL points at a different record."
        ),
        "fix": "Remove or correct the described-at URL.",
    },
    {
        "id": "contained_work_modeling",
        "kind": _JUDGMENT,
        "name": "contained works modeled correctly",
        "statement": (
            "An unidentified contained work maps to P1574 → Q234460 "
            "(text) with a P1932 catalog title — that is correct "
            "modeling, not a missing work item; a verified work QID is "
            "reused only after safe validation."
        ),
        "axis": "role_ok",
        "applies": ("work", "manuscript"),
        "question": (
            "Are contained-work claims modeled correctly? 'yes' = verified "
            "work QIDs linked, unidentified ones mapped to Q234460 with a "
            "P1932 catalog title, or __LOCAL: targets that resolve in "
            "local_reference_targets — the data-model shapes; 'partial' = "
            "a contained work left as a bare string where the data model "
            "wants a work item; 'no' = an invented work-QID link or a "
            "target that resolves to the wrong kind of thing."
        ),
        "fix": "Remodel the contained work per the data model.",
    },
    {
        "id": "title_claim_accuracy",
        "kind": _JUDGMENT,
        "name": "title claim matches the catalog title",
        "statement": (
            "A work's P1476 matches the MARC 245/500 title (normalized "
            "form; internal Hebrew gershayim preserved) — a second title "
            "form is a defect (handled deterministically); the title "
            "itself must be the evidenced one."
        ),
        "axis": "name_ok",
        "applies": ("work",),
        "question": (
            "Does the P1476 title match the evidenced catalog title (MARC "
            "245/500 main ISBD part or the accepted work entry), or the "
            "live label of the updated QID (verify_evidence."
            "wikidata_existing.live.labels)? Compare word-content skeletons "
            "— an ISBD split, a colon inside a parenthetical verse range "
            "('תורה (דברים כט : ט-לא:ל)'), or punctuation variance is the "
            "SAME title, not a drift. 'yes' = the same title in any of "
            "these forms; 'partial' = minor orthographic drift beyond "
            "punctuation; 'no' = a title no evidence or the live item "
            "supplies."
        ),
        "fix": "Correct the title to the evidenced form.",
    },
    {
        "id": "no_proposal_projection",
        "kind": _JUDGMENT,
        "name": "no claim rests on an LLM proposal alone",
        "statement": (
            "LLM-extracted proposals (verify_evidence.llm_proposals) are "
            "candidates for the curator, never evidence — no claim may "
            "rest only on one (Rule W-140)."
        ),
        "axis": "role_ok",
        "question": (
            "Does any present claim rest ONLY on an LLM-extracted proposal "
            "(verify_evidence.llm_proposals)? Generation is not an "
            "evidence channel. 'yes' = every claim has a real channel "
            "beyond proposals (or no proposals exist); 'partial' = a "
            "proposal agrees with a real channel but is the loudest "
            "source; 'no' = a claim is supported by nothing but a "
            "proposal."
        ),
        "fix": "Remove the proposal-only claim.",
    },
    {
        "id": "contamination_check",
        "kind": _JUDGMENT,
        "name": "no other record's content leaked in",
        "statement": (
            "Labels, descriptions, and claims describe this record only — "
            "a second manuscript's shelfmark, title, or contents is "
            "cross-record contamination and a real failure."
        ),
        "axis": "name_ok",
        "applies": ("manuscript",),
        "question": (
            "Do the label, description, aliases, and claims describe only "
            "THIS record? 'yes' = everything belongs to this manuscript; "
            "'partial' = one fragment looks borrowed but is deniable; "
            "'no' = another record's shelfmark, title, or contents is "
            "present — cross-record contamination."
        ),
        "fix": "Remove the other record's data.",
    },
)


def rules_by_kind(kind: str) -> list[dict[str, Any]]:
    return [rule for rule in RULES if rule["kind"] == kind]


def judgment_rules(entity_type: str = "") -> list[dict[str, Any]]:
    """Judgment rules that apply to *entity_type*.

    An unknown entity type ("" → the payload did not say) gets every
    judgment rule — omitting a rule is worse than asking it.
    """
    out: list[dict[str, Any]] = []
    for rule in rules_by_kind(_JUDGMENT):
        applies = rule.get("applies") or ("all",)
        if "all" in applies or not entity_type or entity_type in applies:
            out.append(rule)
    return out


def applicable_rules(entity_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Judgment rules worth a question for THIS item.

    Entity-type filter, then the claim-conditional filter: a rule that
    judges property X is only asked when the item actually carries X (or
    the surface it reads from exists). This keeps the TypeSafe response
    under its token cap — ~30 unconditional questions per item made the
    API reject the call (HTTP 400 max_tokens_exceeded, 127 abstains on
    2026-10-04).

    Three rules are axis-native (label_identity → name_ok,
    entity_type_fit → type_ok, p31_class_choice → p31_ok): the certified
    axis questions already ask exactly that judgment, so no separate
    question goes out — the explanation renders the axis/p31 answer.
    """
    statements = [s for s in (payload.get("statements") or []) if isinstance(s, dict)]
    props = {
        str(s.get("property") or s.get("property_id") or "") for s in statements
    }
    has = lambda prop: prop in props  # noqa: E731
    aliases = payload.get("aliases") or {}
    claim_gates = {
        "holder_identity": has("P195"),
        "p973_agreement": has("P973"),
        "title_claim_accuracy": has("P1476"),
        "author_modeling": has("P50") or has("P2093"),
        "contained_work_modeling": has("P1574"),
        "p7535_scope": has("P7535"),
        "dates_plausible": has("P569") or has("P570") or has("P571"),
        "subject_specificity": has("P921"),
        "alias_intent": bool(aliases),
        "existing_qid_identity": bool(str(payload.get("existing_qid") or "").strip()),
    }
    out: list[dict[str, Any]] = []
    for rule in judgment_rules(entity_type):
        if rule.get("axis_native"):
            continue
        gate = claim_gates.get(rule["id"])
        if gate is False:
            continue
        out.append(rule)
    return out


def rule_by_id(rule_id: str) -> dict[str, Any] | None:
    for rule in RULES:
        if rule["id"] == rule_id:
            return rule
    return None


def rule_states(answers: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Judgment-rule answers as (rule, state) pairs.

    state ∈ pass / partial / fail — the same vocabulary the per-claim
    checks use. Rules the model never answered are skipped (backward
    compatibility with cached answers and non-wikidata channels).
    """
    out: list[tuple[dict[str, Any], str]] = []
    for rule in rules_by_kind(_JUDGMENT):
        ans = answers.get(rule_question_id(rule["id"]))
        if not isinstance(ans, dict):
            continue
        choice = str(ans.get("choice") or "").strip().lower()
        if choice in ("yes", "partial", "no"):
            out.append((rule, {"yes": "pass", "partial": "partial", "no": "fail"}[choice]))
    return out
