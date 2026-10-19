"""MARC field → Wikidata projection targets, with a deliberate fate per field.

One table, three consumers:

* ``scripts/marc_wikidata_coverage.py`` — measures coverage against it and
  enforces the per-field / per-property targets.
* ``docs/`` coverage ledger (WP8) — every catalog-only field names its fate so
  nothing stays silently absent.
* unit tests — pin the mapping so a projection change cannot silently move a
  field to the wrong property.

``fate`` is one of:

``map``
    The field is projected onto one of ``pids``.
``evidence``
    The field is preserved as source evidence / a reference, never as a public
    claim. It is not a coverage failure to emit no claim for it.
``out_of_model``
    Deliberately not represented on Wikidata. Recorded so the absence is a
    decision, not a gap.

``kind`` describes what a covered record must carry: a ``claim`` statement, a
``label``/``alias``, a ``ref``-only statement, or nothing (``none``).
"""
from __future__ import annotations

from dataclasses import dataclass

CLAIM = "claim"
LABEL = "label"
REF = "ref"
NONE = "none"

MAP = "map"
EVIDENCE = "evidence"
OUT_OF_MODEL = "out_of_model"


@dataclass(frozen=True)
class MarcFieldTarget:
    """One MARC column's deliberate home on Wikidata."""

    tag: str
    pids: tuple[str, ...]
    kind: str
    fate: str
    layer: str
    note: str
    target_coverage: float | None = None
    """Minimum acceptable coverage of the records that carry this field.

    ``None`` means "no floor" — the field is measured and reported, but a low
    number is not a regression on its own (e.g. the field is too rare to gate).
    """

    linked_tag: bool = False
    """The field resolves into another MARC tag's mapping (MARC 880).

    Such a field has no property of its own, so ``pids`` is empty by design and
    the coverage harness measures it under its linked tag.
    """

    @property
    def is_measured(self) -> bool:
        """Whether the harness reports a coverage row for this field."""
        return (
            self.fate == MAP
            and bool(self.pids)
            and self.kind != NONE
            and not self.linked_tag
        )


# ── The mapping ──────────────────────────────────────────────────────────
#
# ``target_coverage`` values are the plan's expected lifts
# (docs/plans/marc-wikidata-coverage-improvement.md). They are floors for the
# coverage harness; the plan's gates are the release bar.
MARC_FIELD_TARGETS: tuple[MarcFieldTarget, ...] = (
    # ── Identity ────────────────────────────────────────────────────────
    MarcFieldTarget(
        tag="001",
        pids=("P3959",),
        kind=CLAIM,
        fate=MAP,
        layer="identity",
        note="NNL catalog id — the identity key of every manuscript item.",
        target_coverage=1.0,
    ),
    MarcFieldTarget(
        tag="035$a",
        pids=("P3959",),
        kind=CLAIM,
        fate=MAP,
        layer="identity",
        note=(
            "System control number — the same P3959 channel as 001. Falls back "
            "into it when 001 is absent (WP7.2)."
        ),
        target_coverage=1.0,
    ),
    MarcFieldTarget(
        tag="008",
        pids=("P571", "P1319", "P1326", "P407"),
        kind=CLAIM,
        fate=MAP,
        layer="identity",
        note="Fixed-field dates and language.",
        target_coverage=1.0,
    ),
    MarcFieldTarget(
        tag="041$a",
        pids=("P407",),
        kind=CLAIM,
        fate=MAP,
        layer="identity",
        note="Language code → language of work.",
        target_coverage=1.0,
    ),
    MarcFieldTarget(
        tag="024$a",
        pids=("P2888",),
        kind=REF,
        fate=MAP,
        layer="identity",
        note="Standard number → stated-in / exact-match bridge.",
        target_coverage=1.0,
    ),
    # ── Title ───────────────────────────────────────────────────────────
    MarcFieldTarget(
        tag="245$a",
        pids=("P1476",),
        kind=CLAIM,
        fate=MAP,
        layer="title",
        note="Title → title.",
        target_coverage=1.0,
    ),
    MarcFieldTarget(
        tag="245$b",
        pids=("P1680",),
        kind=CLAIM,
        fate=MAP,
        layer="title",
        note="Remainder of title → subtitle.",
        target_coverage=1.0,
    ),
    MarcFieldTarget(
        tag="245$c",
        pids=("P2093",),
        kind=CLAIM,
        fate=MAP,
        layer="work",
        note="Statement of responsibility → author name string (WP1.3).",
    ),
    MarcFieldTarget(
        tag="246$a",
        pids=(),
        kind=LABEL,
        fate=MAP,
        layer="title",
        note="Varying title → aliases. A label, not a claim (W-65).",
    ),
    # ── Authorship (on the WORK, never the manuscript — WPM / W-69) ─────
    MarcFieldTarget(
        tag="100$a",
        pids=("P50", "P2093"),
        kind=CLAIM,
        fate=MAP,
        layer="work",
        note=(
            "Main entry author → author, else author name string. Emitted on "
            "the WORK via P1574, never on the physical manuscript (WPM)."
        ),
        target_coverage=0.6,
    ),
    MarcFieldTarget(
        tag="100$d",
        pids=("P569", "P570"),
        kind=CLAIM,
        fate=MAP,
        layer="work",
        note="Author dates → birth / death, only on an exact authority-ID match (W-67).",
    ),
    MarcFieldTarget(
        tag="700$a",
        pids=("P50", "P2093"),
        kind=CLAIM,
        fate=MAP,
        layer="work",
        note="Added entry person → author / author name string, on the WORK.",
        target_coverage=0.6,
    ),
    # ── Work link ───────────────────────────────────────────────────────
    MarcFieldTarget(
        tag="505$a",
        pids=("P1574",),
        kind=CLAIM,
        fate=MAP,
        layer="work",
        note="Contents note → exemplar of. Nearly empty in this corpus (1 record).",
    ),
    MarcFieldTarget(
        tag="730$a",
        pids=("P1574",),
        kind=CLAIM,
        fate=MAP,
        layer="work",
        note="Uniform title → exemplar of, evidence-gated (W-68).",
    ),
    MarcFieldTarget(
        tag="740$a",
        pids=("P1574",),
        kind=CLAIM,
        fate=MAP,
        layer="work",
        note="Added entry uniform title → exemplar of, evidence-gated (W-68).",
    ),
    # ── Production date ─────────────────────────────────────────────────
    MarcFieldTarget(
        tag="260$c",
        pids=("P571",),
        kind=CLAIM,
        fate=MAP,
        layer="identity",
        note="Production date → inception.",
        target_coverage=1.0,
    ),
    MarcFieldTarget(
        tag="264$c",
        pids=("P571",),
        kind=CLAIM,
        fate=MAP,
        layer="identity",
        note="Production date → inception.",
    ),
    # ── Physical description ────────────────────────────────────────────
    MarcFieldTarget(
        tag="300$a",
        pids=("P1104",),
        kind=CLAIM,
        fate=MAP,
        layer="physical",
        note="Extent → number of pages/leaves (P1104 with an explicit unit).",
        target_coverage=0.9,
    ),
    MarcFieldTarget(
        tag="300$c",
        pids=("P2048", "P2049"),
        kind=CLAIM,
        fate=MAP,
        layer="physical",
        note=(
            "Dimensions → height / width. Fail closed on unparseable dimensions "
            "(W-140 / R2)."
        ),
        target_coverage=0.5,
    ),
    MarcFieldTarget(
        tag="340$a",
        pids=("P186",),
        kind=CLAIM,
        fate=MAP,
        layer="physical",
        note=(
            "Support/material → material used, from a closed vocabulary only. "
            "Free-text hand/script notes in 340$a are not materials (W-140)."
        ),
        target_coverage=0.4,
    ),
    MarcFieldTarget(
        tag="563$a",
        pids=("P1552",),
        kind=CLAIM,
        fate=MAP,
        layer="physical",
        note="Binding note → has quality. Absent from the 17th-century corpus.",
    ),
    # ── Places ──────────────────────────────────────────────────────────
    MarcFieldTarget(
        tag="260$a",
        pids=("P1071", "P276"),
        kind=CLAIM,
        fate=MAP,
        layer="place",
        note=(
            "Place of publication → location of creation, where a geographic "
            "object is verified (W-170)."
        ),
    ),
    MarcFieldTarget(
        tag="264$a",
        pids=("P1071", "P276"),
        kind=CLAIM,
        fate=MAP,
        layer="place",
        note="Place of publication → location of creation, verified.",
    ),
    MarcFieldTarget(
        tag="751$a",
        pids=("P7153",),
        kind=CLAIM,
        fate=MAP,
        layer="place",
        note="Associated place → significant place.",
        target_coverage=0.4,
    ),
    MarcFieldTarget(
        tag="752$a",
        pids=("P1071", "P276"),
        kind=CLAIM,
        fate=MAP,
        layer="place",
        note="Place of production → located in / location of creation.",
        target_coverage=0.6,
    ),
    MarcFieldTarget(
        tag="034$a",
        pids=("P625",),
        kind=CLAIM,
        fate=EVIDENCE,
        layer="place",
        note=(
            "Cartographic coordinates. Held as evidence; P625 only with a "
            "verified place QID (R2/R4 fail-closed)."
        ),
    ),
    # ── Notes family ────────────────────────────────────────────────────
    MarcFieldTarget(
        tag="520$a",
        pids=("P7535",),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note=(
            "Summary → scope and content. The one source P7535 is meant to "
            "hold on a manuscript (WP5, Rule W-67 retune)."
        ),
        target_coverage=0.5,
    ),
    MarcFieldTarget(
        tag="500$a",
        pids=("P1684", "P5102", "P1922", "P3132", "P9302"),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note=(
            "General notes → inscription / nature of statement / incipit / "
            "explicit / script, by note classifier."
        ),
    ),
    MarcFieldTarget(
        tag="561$a",
        pids=("P3342", "P11811", "P11812"),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note=(
            "Provenance → significant person, with before/after ordering where "
            "known (W-146: never a bare P127)."
        ),
    ),
    MarcFieldTarget(
        tag="593$a",
        pids=("P9302",),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note="Script note → script or style. Absent from the 17th-century corpus.",
    ),
    MarcFieldTarget(
        tag="597$a",
        pids=("P9302",),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note=(
            "Colophon/script hand in this export's convention → script or style. "
            "In the 17th-century corpus 597$a is a Ktiv credit line, so it is "
            "held as evidence rather than a claim (see the ledger)."
        ),
    ),
    MarcFieldTarget(
        tag="957$a",
        pids=("P7535", "P1684"),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note=(
            "Colophon text → scope and content, or inscription where the text "
            "names an inscription (WP5.5, decide in code)."
        ),
    ),
    MarcFieldTarget(
        tag="958$a",
        pids=("P9302",),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note="Script tradition (תימנית/איטלקית/…) → script or style.",
    ),
    # ── Subjects and genres ─────────────────────────────────────────────
    MarcFieldTarget(
        tag="650$a",
        pids=("P921",),
        kind=CLAIM,
        fate=MAP,
        layer="subject",
        note=(
            "Subject heading → main subject. Primary-subject gate (R26/W-71); "
            "subdivisions $v/$x/$y/$z are kept as qualifiers (WP2)."
        ),
        target_coverage=0.6,
    ),
    MarcFieldTarget(
        tag="655$a",
        pids=("P136",),
        kind=CLAIM,
        fate=MAP,
        layer="subject",
        note="Genre/form → genre, on a verified QID only (W-71).",
        target_coverage=0.8,
    ),
    # ── Holding institution (WP7.1: P195, NOT P50) ──────────────────────
    MarcFieldTarget(
        tag="710$a",
        pids=("P195",),
        kind=CLAIM,
        fate=MAP,
        layer="place",
        note=(
            "Corporate name (holding library) → collection. Never P50: a "
            "library is not an author (WP7.1)."
        ),
        target_coverage=0.75,
    ),
    # ── Digital access ──────────────────────────────────────────────────
    MarcFieldTarget(
        tag="856$u",
        pids=("P953", "P973", "P6108"),
        kind=CLAIM,
        fate=MAP,
        layer="digital",
        note=(
            "Digital URL → full work URL / described at URL; IIIF manifest "
            "detected → P6108."
        ),
        target_coverage=0.8,
    ),
    # ── Linked fields (WP7.3) ───────────────────────────────────────────
    MarcFieldTarget(
        tag="880",
        pids=(),
        kind=CLAIM,
        fate=MAP,
        layer="title",
        linked_tag=True,
        note=(
            "Linked field (880$6 names the target tag) — projects as its linked "
            "tag, not as a field of its own."
        ),
    ),
    # ── RDA (WP7.4) ─────────────────────────────────────────────────────
    MarcFieldTarget(
        tag="336$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note=(
            "RDA content type. Declared out-of-model: kept as carrier evidence, "
            "not a P31 refinement (WP7.4)."
        ),
    ),
    MarcFieldTarget(
        tag="337$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note="RDA media type. Out-of-model, kept as carrier evidence.",
    ),
    MarcFieldTarget(
        tag="338$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note="RDA carrier type. Out-of-model, kept as carrier evidence.",
    ),
    # ── Catalog-only policy (WP8) ───────────────────────────────────────
    MarcFieldTarget(
        tag="903$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note="Source-system access restriction. Out-of-model.",
    ),
    MarcFieldTarget(
        tag="907$a",
        pids=(),
        kind=NONE,
        fate=EVIDENCE,
        layer="catalog-only",
        note=(
            "Digital-file identity. Held as evidence — a local file id is not a "
            "public claim (2026-07-16 audit)."
        ),
    ),
    MarcFieldTarget(
        tag="915$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note="Local batch/contract id. Out-of-model.",
    ),
    MarcFieldTarget(
        tag="921$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note="Local project id. Out-of-model.",
    ),
    MarcFieldTarget(
        tag="934$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note="Local id. Out-of-model.",
    ),
    MarcFieldTarget(
        tag="943$a",
        pids=(),
        kind=NONE,
        fate=OUT_OF_MODEL,
        layer="catalog-only",
        note="Extent duplicate of 300$a. Out-of-model — P1104 already covers it.",
    ),
    MarcFieldTarget(
        tag="952$a",
        pids=("P6216",),
        kind=CLAIM,
        fate=MAP,
        layer="catalog-only",
        note=(
            "Rights/export status → copyright status, where the status names "
            "public domain (already projected via 540)."
        ),
    ),
    MarcFieldTarget(
        tag="958$b",
        pids=("P9302",),
        kind=CLAIM,
        fate=MAP,
        layer="note",
        note="Writing style (מרובעת/רהוטה) → script or style.",
    ),
)


BY_TAG: dict[str, MarcFieldTarget] = {target.tag: target for target in MARC_FIELD_TARGETS}

MEASURED_TARGETS: tuple[MarcFieldTarget, ...] = tuple(
    target for target in MARC_FIELD_TARGETS if target.is_measured
)

# Property-level release bars from the plan. The harness reports these even when
# no single MARC field owns the property (P1574 is fed by several fields).
PROPERTY_TARGETS: dict[str, float] = {
    "P3959": 1.0,
    "P571": 1.0,
    "P407": 1.0,
    "P1476": 1.0,
    "P1104": 0.85,
    "P195": 0.70,
    "P217": 0.95,
    "P1574": 0.90,  # WP1
    "P50": 0.60,  # WP1 — on works
    "P2093": 0.60,  # WP1 — on works
    "P921": 0.60,  # WP2
    "P136": 0.80,  # WP2
    "P2048": 0.50,  # WP3
    "P186": 0.40,  # WP3
    "P1071": 0.60,  # WP4
    "P7153": 0.40,  # WP4
    "P7535": 0.50,  # WP5
    "P953": 0.80,  # WP6
}


def ledger_rows() -> list[dict[str, str]]:
    """The WP8 coverage ledger, one row per field.

    Deliberately plain dicts so the Markdown writer and the JSON export share
    one shape without a second schema.
    """
    rows: list[dict[str, str]] = []
    for target in MARC_FIELD_TARGETS:
        if target.linked_tag:
            pid_text = "→ linked tag"
        elif target.pids:
            pid_text = ", ".join(target.pids)
        else:
            pid_text = "—"
        rows.append(
            {
                "tag": target.tag,
                "fate": target.fate,
                "kind": target.kind,
                "pids": pid_text,
                "layer": target.layer,
                "note": target.note,
                "target": (
                    f"{target.target_coverage * 100:.0f}%"
                    if target.target_coverage is not None
                    else "—"
                ),
            }
        )
    return rows
