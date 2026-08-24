# Architectural rules (W-1…W-195)

One file per area. Read the file that covers the code you are about to
change — the rules record real production incidents and the invariant that
closes each one. The one-line index lives in
[CLAUDE.md](../../../CLAUDE.md); it is a pointer, never a substitute.

| File | Read it before touching |
|---|---|
| [ai-verify.md](ai-verify.md) | verify streams/jobs, judges + tier-1 models, verdict caches and fingerprints, evidence packs, eval-agent I/O |
| [wikidata-studio.md](wikidata-studio.md) | public Wikidata projection, claim/label hygiene, reconcile + upload write path, QuickStatements |
| [hmo-wikibase.md](hmo-wikibase.md) | HMO item build/export, Wikibase Cloud writes, canonical read-back, ontology namespace |
| [jobs-and-progress.md](jobs-and-progress.md) | `run_jobs` claim/heartbeat/admission, progress shape, job-backed builds + publishes |
| [authority-marc.md](authority-marc.md) | Mazal/KIMA/VIAF matching, homonym abstain, MARC ingest + normalization |
| [rdf-ontology.md](rdf-ontology.md) | GraphBuilder, SHACL validation, ontology predicates, provenance places |
| [platform-infra.md](platform-infra.md) | Heroku/dyno constraints, Redis→Postgres cache stack, durable build caches, external-call transactions, Modal |
| [frontend-ui.md](frontend-ui.md) | curator review surfaces, glass components, Zustand selectors and render stability |
| [security-versioning.md](security-versioning.md) | public endpoints, rate limits, `project_events` versioning, export bundles |
| [testing-and-docs.md](testing-and-docs.md) | the e2e test surface and the pre-deploy docs-sync gate |

Adding a rule: append it to the matching file, add one index line in
[CLAUDE.md](../../../CLAUDE.md), and bump the `W-1…W-N` pointer there plus
in [AGENTS.md](../../../AGENTS.md) and [README.md](../../../README.md).
