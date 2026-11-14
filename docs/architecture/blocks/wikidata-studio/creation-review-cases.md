# CREATE review cases from the source audit

> Up: [CREATE guards](creation-safety.md) · W-337

The 14 November 2026 audit rebuilt all 897 source records as 2,409 items.
It found six old positional edits that replaced P528 catalogue codes with work QIDs.
The current semantic guard blocks this corruption.
The audit did not change production data or public items.

| Source record | Old statement index | QID in the stale value edit |
|---|---:|---|
| 990000645310205171 | 17 | Q141754916 |
| 990000656400205171 | 16 | Q141755010 |
| 990000665230205171 | 18 | Q141755388 |
| 990000896070205171 | 17 | Q141754945 |
| 990001028930205171 | 17 | Q141755028 |
| 990001218770205171 | 17 | Q141754943 |

These QIDs are proposed work targets, not verified public manuscript identities.
The table does not prove that each proposed work target is correct.
Do not apply a patch to another statement from its old index alone.
Review the exact MARC work title, author, folio, and target identity first.
Keep the original catalogue code and its complete source references.
Use a current scoped source choice after the identity check.
Rebuild locally and repeat the quality gate before a public upload.
Public writes still require proof that Alexander created each target item.

The source audit also found seven records with field 090 and no resolved current 942 evidence.
The code withholds a microfilm fallback for these records.
A new manuscript requires source review for its current P217 before CREATE.
The source IDs are 990001580110205171, 990001641670205171, 990001876060205171,
990001983850205171, 990002076280205171, 990025887520205171, and 990026973440205171.
Ambiguous 942 cases retain their separate review evidence.

Local evidence lives under `state/gila-create-prevention-2026-11-14/`.
`override-proof.json` records the six stored edits without credentials.
`current-guard-measurement.json` records the gate results and the seven source IDs.
The current gate result supersedes the earlier zero-error count before W-337.
The six stale edits remain unresolved source cases, not corrected public items.
