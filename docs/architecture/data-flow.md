# End-to-end data flow

> Up: [System Design](system-design.md) · [AGENTS.md](../../AGENTS.md)

```
MARC upload (.mrc / TSV / JSON)
  └─ ingest → run_records + named entities            [extraction]
       └─ NER job → Modal → extraction_approvals       [extraction, job-service]
            └─ curator review + AI verify              [frontend, eval-agent]
  └─ authority job → Mazal/KIMA/VIAF/WD → authority_matches  [authority]
       └─ guards / homonym picker / auto-approve       [authority]
  └─ RDF build job → TTL + coverage (durable)          [rdf-graph]
       ├─ Research maps / graphs / queries             [research]
       ├─ HMO Studio items → Wikibase Cloud            [hmo-wikibase-studio]
       └─ Wikidata Studio (PQ mapper + own/accept) → guarded upload  [wikidata-studio]
  every curator mutation → project_events              [versioning-export]
  every external inference → cache tiers               [caching]
```

Canonical HMO → Wikidata uses `hmo_wikidata_pq_mapper` (Rule W-100). Live
writes also run smart existence + own-or-accept modify (Rule W-99;
`docs/wikidata-data-access.md`).

Block links: [extraction](blocks/extraction/README.md) ·
[job-service](blocks/job-service/README.md) ·
[eval-agent](blocks/eval-agent/README.md) ·
[authority](blocks/authority/README.md) ·
[rdf-graph](blocks/rdf-graph/README.md) ·
[research](blocks/research/README.md) ·
[hmo-wikibase-studio](blocks/hmo-wikibase-studio/README.md) ·
[wikidata-studio](blocks/wikidata-studio/README.md) ·
[versioning-export](blocks/versioning-export/README.md) ·
[caching](blocks/caching/README.md) ·
[frontend](blocks/frontend/README.md)
