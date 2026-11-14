# Gila completion packet

> Up: [Wikidata Studio](README.md) · W-296, W-305, W-307, W-322

The packet classifies every source fact and source case against all nine email points.
It separates `verified`, `manual_review`, `repair_required`, and `not_checked`.
An unresolved work target remains a manual case.
A known target with an absent supported statement remains a repair case.
A present value with missing or mismatched references does not pass.

Each source record needs a unique control number and a current public entity response.
Each fact needs a unique fact ID and the same revision as its public response.
A stale audit row becomes `not_checked`.
A redirect response does not create another manuscript identity.
Two distinct public entities with the same source ID remain an explicit duplicate case.
The packet does not merge them or alter the ledger.

`corrected_qids` requires a receipt with `status=verified`, `corrected=true`, and the current public revision.
A later revision requires a new verification before that receipt can supply current proof.
The packet does not grant authorization for any public edit.
It retains historical proposals for the separate guarded executor.
Each historical proposal must match the current target revision and complete entity digest.
A changed target becomes `not_checked` instead of a ready correction.

From the repository root:

```bash
backend/.venv/bin/python backend/scripts/export_gila_completion.py \
  --audit state/gila-completion-2026-11-12/full-audit.json \
  --live-snapshot state/gila-completion-2026-11-12/live-snapshot.json \
  --output-dir state/gila-completion-2026-11-12/acceptance
```

Use `--receipts PATH` for each verified receipt array.
The exporter writes complete JSON, separate CSV inventories, a nine-point table, and a SHA256 manifest.
It performs no network request, database write, or public edit.
`all_records_read` does not mean `all_requirements_verified`.

## Exact Troki authority

The audited exact heading `יצחק בן אברהם מטרוקי` resolves to `Q168171`.
The public entity has NLI ID `987007297464205171` and VIAF ID `67288238`.
The official [NLI authority](https://www.nli.org.il/en/a-topic/987007297464205171) gives the same name and years, 1533–1594.
The [manuscript source](https://www.nli.org.il/en/manuscripts/NNL_ALEPH990000885680205171/NLI) gives that complete author heading.
The shorter heading `יצחק בן אברהם` does not select this identity.
The source author must also retain the exact life dates `1533-1594`.
Absent, uncertain, or different life dates do not select this audited fallback.
An explicit rejected or conflicting authority match still blocks the fallback.
The local authority entry does not prove that the current public work already has P50.

The current build versions are `source-aware-works-v24` and `hmo-wikidata-v28`.
A fresh normal build must precede a public source update.

## Files and tests

- `backend/app/pipeline/wikidata_gila_completion.py` defines the packet and its proof checks.
- `backend/scripts/export_gila_completion.py` exports the packet from saved evidence.
- `backend/tests/unit/test_wikidata_gila_completion.py` checks stale revisions, source gaps, duplicate IDs, and receipt proof.
- `backend/tests/unit/test_wikidata_troki_author_identity.py` checks exact identity and rejection guards.
- [Person dates](../extraction/person-dates.md) defines the flattened-date boundary and its tests.
