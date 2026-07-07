# MARC Ingest + AI (NER) Extraction

> Up: [System Design](../../system-design.md) · [AGENTS.md](../../../../AGENTS.md)

## What this block does

Turns an uploaded MARC catalogue file (`.mrc` / `.marc` / TSV / CSV / JSON /
JSONL) into normalised per-manuscript record dicts, then runs four ML models
(Person NER, Provenance NER, Contents NER, Genre classifier) over the
note-bearing MARC fields to surface entities the structured catalogue does not
carry. Every extracted entity lands as a durable `ExtractionApproval` row that
a curator reviews in an 11-capability table UI: approve/reject, per-field
override, auto-approve rules, AI-agent verdicts, and an "Exists in" badge that
classifies each candidate against the MARC record itself. Approved entities
(with overrides applied) feed the downstream Authority, RDF, and Studio blocks.

## Contents

- [Key files](key-files.md)
- How it works:
  - [Ingest](ingest.md) — `marc_ingest.py` parsing, subfield collapse, entity candidates
  - [NER backends and the Modal app](ner-backends.md) — `EXTRACTION_MODE`, `modal/modal_app.py`
  - [Extraction stream, persistence, and approvals](extraction-persistence.md) — SSE stream, `_bulk_persist_entities`, Exists-in model, auto-approve
  - [Review UI](review-ui.md) — the 11-capability curator surface (Rule W-16)
- [Rules](rules.md)
- [Skills](skills.md)
- [Tests pinning this block](tests.md)

## Related blocks

- [job-service](../job-service/README.md) — `run_extraction_job` claiming/heartbeat/cancel (Rule W-38)
- [eval-agent](../eval-agent/README.md) — `NerVerificationModal` sessions and `ai_verdict` persistence (Rules W-17/W-18)
- [authority](../authority/README.md) — consumes `extract_named_entities` output + approved entities
- [rdf-graph](../rdf-graph/README.md) — approved/overridden entities merged into the RDF projection (Rule W-34)
- [caching](../caching/README.md) — `inference_cache` + Redis L1 (Rule W-25), entities scoped cache
- [frontend](../frontend/README.md) — glass components, Zustand rules (W-35/W-36)
- [deployment](../deployment/README.md) — Heroku env vars, ephemeral-filesystem constraints, Modal deploy
