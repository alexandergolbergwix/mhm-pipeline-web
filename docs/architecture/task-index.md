# Task index — where to start

> Up: [System Design](system-design.md) · [AGENTS.md](../../AGENTS.md)

| You are asked to… | Read first |
|---|---|
| Add/modify a background operation | [job-service](blocks/job-service/README.md) |
| Touch NER, ingest, or the entity table | [extraction](blocks/extraction/README.md) |
| Change matching, guards, or authority data | [authority](blocks/authority/README.md) |
| Change ontology output or graph build | [rdf-graph](blocks/rdf-graph/README.md) |
| Touch anything that writes to Wikidata | [wikidata-studio](blocks/wikidata-studio/README.md) — read its `rules.md` **before** coding |
| Touch Wikibase Cloud items/schema | [hmo-wikibase-studio](blocks/hmo-wikibase-studio/README.md) — schema AI verify: eval-agent [R17](blocks/eval-agent/rules.md), Rule W-47 |
| Add an AI-verify surface | [eval-agent](blocks/eval-agent/README.md) |
| Add caching or an external API call | [caching](blocks/caching/README.md) |
| Add a curator-mutable field | [versioning-export](blocks/versioning-export/README.md) |
| Add an endpoint (esp. unauthenticated) | [platform-security](blocks/platform-security/README.md) |
| Build UI | [frontend](blocks/frontend/README.md) |
| Deploy, migrate, configure | [deployment](blocks/deployment/README.md) |
| Finish up any code change (docs sync) | [.cursor/skills/docs-on-code-change/SKILL.md](../../.cursor/skills/docs-on-code-change/SKILL.md) (gate) → [.codex/skills/docs-architecture-sync/SKILL.md](../../.codex/skills/docs-architecture-sync/SKILL.md) (how) |
| Deploy or push to GitHub/Heroku | [.cursor/skills/pre-deploy-docs-sync/SKILL.md](../../.cursor/skills/pre-deploy-docs-sync/SKILL.md) — **mandatory** final docs audit via [task-index](task-index.md) before any push/deploy |
