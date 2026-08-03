# Task index — where to start

> Up: [System Design](system-design.md) · [AGENTS.md](../../AGENTS.md)

| You are asked to… | Read first |
|---|---|
| Add/modify a background operation | [job-service](blocks/job-service/README.md) |
| Touch NER, ingest, or the entity table | [extraction](blocks/extraction/README.md) |
| Change matching, guards, or authority data | [authority](blocks/authority/README.md) |
| Change ontology output or graph build | [rdf-graph](blocks/rdf-graph/README.md) |
| Touch anything that writes to Wikidata | [wikidata-studio](blocks/wikidata-studio/README.md) — read its `rules.md` **before** coding |
| Enrich canonical Studio with full MARC/authority claims (W-125) | [wikidata-studio build](blocks/wikidata-studio/build-and-cache.md) + `wikidata_canonical_enrichment.py` |
| Debug incomplete AI verify (judged ≪ scope, false “eval-agent error”) | [eval-agent](blocks/eval-agent/README.md) R30–R31 + Rules W-126/W-127 + `verify_outcome.py` |
| Fix verify stalling ~50 items on DeepSeek / no runner.exit | [eval-agent](blocks/eval-agent/README.md) R31 + [job-service](blocks/job-service/README.md) R20 + Rule W-127 |
| Fix verify modal stuck RUNNING / VERDICTS (0) after R14/H12 | [job-service](blocks/job-service/README.md) R20 + Rule W-128 + reopen modal |
| Continue AI verify after dyno OOM / restart | [job-service](blocks/job-service/README.md) R22 + [eval-agent](blocks/eval-agent/README.md) R33 + [frontend](blocks/frontend/README.md) R18 + Rule W-130 |
| Avoid Basic-dyno OOM during Studio + verify | [wikidata-studio](blocks/wikidata-studio/README.md) R55 + [eval-agent](blocks/eval-agent/README.md) R34 + [frontend](blocks/frontend/README.md) R18 + Rule W-131 |
| Limit parallel verify/build on one dyno (crash-safe queue) | [job-service](blocks/job-service/README.md) R21 + Rule W-129 + `RUN_JOB_MAX_*` env |
| Map HMO / project Wikibase P/Q → public Wikidata | [wikidata-studio](blocks/wikidata-studio/README.md) R38 + `hmo_wikidata_pq_mapper.py` + [docs/wikidata-data-access.md](../wikidata-data-access.md) |
| Change upload ownership / foreign-modify accept | [wikidata-studio guards](blocks/wikidata-studio/guards-and-upload.md) + Rule W-99 |
| Align projection with WikiProject Manuscripts | [docs/wikidata-manuscripts-data-model.md](../wikidata-manuscripts-data-model.md) + studio R36 / Rule W-98 |
| Change MARC 500/505 work candidates or Studio item counts | [extraction ingest](blocks/extraction/ingest.md) + [Wikidata Studio R22](blocks/wikidata-studio/rules.md) |
| Touch Wikibase Cloud items/schema | [hmo-wikibase-studio](blocks/hmo-wikibase-studio/README.md) — schema AI verify: eval-agent [R17](blocks/eval-agent/rules.md), Rule W-47 |
| Enforce four HMO pillars (Wikibase root / WD map / ontology 1:1 / enrichment) | [hmo-wikibase-studio](blocks/hmo-wikibase-studio/README.md) R45 + Rule W-102 |
| Choose Wikidata upload target (dry-run / test / live) | [wikidata-studio](blocks/wikidata-studio/README.md) R6 + Rule W-103 |
| Add an AI-verify surface | [eval-agent](blocks/eval-agent/README.md) |
| Debug a missing P407/extent/shelfmark on a TSV-ingested run | [Wikidata Studio R60](blocks/wikidata-studio/rules.md) + Rule W-138 + `marc_ingest._unwrap_marc_value` |
| Debug an unresolved `__LOCAL:` target or duplicate work title | [Wikidata Studio R60](blocks/wikidata-studio/rules.md) + Rule W-138 + `wikidata_local_refs.py` |
| Check whether a CREATE candidate already exists on Wikidata | [Wikidata Studio R61](blocks/wikidata-studio/rules.md) + Rule W-139 + `wikidata_duplicate_probe.py` |
| Fix `unsupported by MARC` verdicts on evidenced claims | [Wikidata Studio R59](blocks/wikidata-studio/rules.md) + Rule W-137 + `marc_verify_context.RAW_TAG_FALLBACK` |
| Debug manuscripts sharing a label/shelfmark or emitting several P3959 | [Wikidata Studio R59](blocks/wikidata-studio/rules.md) + Rule W-137 + `identity_control_number` |
| Debug AI verdicts persisted but showing `—` in the review table | [Wikidata Studio R64–R67](blocks/wikidata-studio/rules.md) + Rules W-136/W-148/W-149/W-150/W-151 + `wikidata_verdict_cache.py` / merged item view |
| Analyze partial/fail AI verdicts with Codex | [eval-agent](blocks/eval-agent/README.md) + [Wikidata Studio R25](blocks/wikidata-studio/rules.md) for item/QID evidence |
| Audit Wikidata export quality/failure counts | [wikidata-studio](blocks/wikidata-studio/README.md) — run `check_wikidata_export_quality.py` |
| Add caching or an external API call | [caching](blocks/caching/README.md) |
| Add a curator-mutable field | [versioning-export](blocks/versioning-export/README.md) |
| Add an endpoint (esp. unauthenticated) | [platform-security](blocks/platform-security/README.md) |
| Build UI | [frontend](blocks/frontend/README.md) |
| Deploy, migrate, configure | [deployment](blocks/deployment/README.md) |
| Finish up any code change (docs sync) | [.cursor/skills/docs-on-code-change/SKILL.md](../../.cursor/skills/docs-on-code-change/SKILL.md) (gate) → [.codex/skills/docs-architecture-sync/SKILL.md](../../.codex/skills/docs-architecture-sync/SKILL.md) (how) |
| Deploy or push to GitHub/Heroku | [.cursor/skills/pre-deploy-docs-sync/SKILL.md](../../.cursor/skills/pre-deploy-docs-sync/SKILL.md) — **mandatory** final docs audit via [task-index](task-index.md) before any push/deploy |
| Add or fix a MARC-derived manuscript claim (extent, digital access, material) | [wikidata-studio](blocks/wikidata-studio/README.md) — Rule W-140; recover deterministically before generating |
| Change LLM extraction of MARC provenance prose | [wikidata-studio](blocks/wikidata-studio/README.md) — `marc_llm_extract.py`, Rule W-140; proposals stay advisory |

| Add or change a holding-institution → QID mapping | [wikidata-studio](blocks/wikidata-studio/README.md) — `holding_institutions.py`, Rules W-143 / W-26; fetch the QID live and record its label |
| Change duplicate detection (keys, batching, statuses) | [wikidata-studio](blocks/wikidata-studio/README.md) — `wikidata_duplicate_probe.py`, Rules W-144 / W-145; `absent` requires every key answered |
| Add or change an internal entity link (person/work/manuscript roles) | [wikidata-studio](blocks/wikidata-studio/README.md) — `ROLE_TO_PID`, `person_linking.py`, Rule W-146; never create an item for the sake of an edge |
| Change what a verify job reports before the first verdict | [job-service](blocks/job-service/README.md) — `verify_job._scope_progress`, `VERIFY_SCOPE_PHASES`, Rule W-147 |
