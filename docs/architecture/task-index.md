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
| Map HMO / project Wikibase P/Q → public Wikidata | [wikidata-studio](blocks/wikidata-studio/README.md) R38 + `hmo_wikidata_pq_mapper.py` + [docs/wikidata-data-access.md](../wikidata-data-access.md) |
| Change upload ownership / foreign-modify accept | [wikidata-studio guards](blocks/wikidata-studio/guards-and-upload.md) + Rule W-99 |
| Align projection with WikiProject Manuscripts | [docs/wikidata-manuscripts-data-model.md](../wikidata-manuscripts-data-model.md) + studio R36 / Rule W-98 |
| Change MARC 500/505 work candidates or Studio item counts | [extraction ingest](blocks/extraction/ingest.md) + [Wikidata Studio R22](blocks/wikidata-studio/rules.md) |
| Touch Wikibase Cloud items/schema | [hmo-wikibase-studio](blocks/hmo-wikibase-studio/README.md) — schema AI verify: eval-agent [R17](blocks/eval-agent/rules.md), Rule W-47 |
| Enforce four HMO pillars (Wikibase root / WD map / ontology 1:1 / enrichment) | [hmo-wikibase-studio](blocks/hmo-wikibase-studio/README.md) R45 + Rule W-102 |
| Choose Wikidata upload target (dry-run / test / live) | [wikidata-studio](blocks/wikidata-studio/README.md) R6 + Rule W-103 |
| Add an AI-verify surface | [eval-agent](blocks/eval-agent/README.md) |
| Analyze partial/fail AI verdicts with Codex | [eval-agent](blocks/eval-agent/README.md) + [Wikidata Studio R25](blocks/wikidata-studio/rules.md) for item/QID evidence |
| Audit Wikidata export quality/failure counts | [wikidata-studio](blocks/wikidata-studio/README.md) — run `check_wikidata_export_quality.py` |
| Add caching or an external API call | [caching](blocks/caching/README.md) |
| Add a curator-mutable field | [versioning-export](blocks/versioning-export/README.md) |
| Add an endpoint (esp. unauthenticated) | [platform-security](blocks/platform-security/README.md) |
| Build UI | [frontend](blocks/frontend/README.md) |
| Deploy, migrate, configure | [deployment](blocks/deployment/README.md) |
| Finish up any code change (docs sync) | [.cursor/skills/docs-on-code-change/SKILL.md](../../.cursor/skills/docs-on-code-change/SKILL.md) (gate) → [.codex/skills/docs-architecture-sync/SKILL.md](../../.codex/skills/docs-architecture-sync/SKILL.md) (how) |
| Deploy or push to GitHub/Heroku | [.cursor/skills/pre-deploy-docs-sync/SKILL.md](../../.cursor/skills/pre-deploy-docs-sync/SKILL.md) — **mandatory** final docs audit via [task-index](task-index.md) before any push/deploy |
