# Repeated Wikidata sources, 12 November 2026

The screenshot shows repeated citations of one source, with different retrieval dates.
[Q141175529, revision 2568055253](https://www.wikidata.org/w/index.php?title=Q141175529&oldid=2568055253) has four such references on P1476.
Each reference names Ktiv, NLI record `990001580110205171`, and the same catalogue URL.
The dates are 11 August, 5 November, 10 November, and 11 November 2026.
The existing upload code includes P813 in the reference identity.
Each later upload therefore adds another reference for that source.

The local fix retains the reference dated 11 November 2026.
It preserves other sources and all other source metadata.
The [reference contract](../architecture/blocks/wikidata-studio/reference-retrievals.md) defines the exact limits.
The agent prepared the fix and proposals locally. This operation made no public edit.

## Corpus result

The read-only analysis covers 2,167 public entities from the complete source audit.
It uses the public scan from 10:42:53–10:47:18, Israel time, with explicit target follow-ups.
The example follow-up took place at 11:55:55, Israel time.
The analysis finds 16,633 affected claims on 1,619 entities.
It finds 45,202 redundant blocks that differ only by an eligible retrieval date.
It also finds ten exact redundant blocks.
The local proposals would remove 45,212 redundant blocks if every separate execution guard permits the repair.

The [complete claim list](data/gila-completion-2026-11-12-repeated-sources.csv) records QIDs, revisions, claim IDs, counts, and retained hashes.
The proposal count does not establish ownership or authorize every target.
The current-source executor can reject foreign targets or targets outside its manuscript scope.
Work targets require the normal owned-work path or a separate guarded repair path.

The earlier audit counted exact repeated blocks, including P813.
It did not count one citation with different retrieval dates.
Its zero result for exact repeats on mapped manuscripts does not establish an absence of repeated sources.
The new audit records these two counts separately.

## Evidence and remaining action

Evidence is under `state/gila-completion-2026-11-12/`:

- `reference-example-live.json`: complete public response and timestamp.
- `reference-example-repair.json`: revision-bound, non-executable proposal for the screenshot item.
- `reference-retrieval-summary.json`: complete counts and the example result.
- `current-audit.json`: historical proposals for the current corpus snapshot.
- `acceptance/`: complete requirements, source cases, proposals, receipts, and SHA256 manifest.

The tests cover normal updates, partial updates, append-only paths, date uncertainty, distinct sources, and exact proposal validation.
The implementation report records the final test count.
The browser approval check rejected access to `https://mhm-pipeline.org` because the origin permission was declined.
The agent did not bypass that decision. Deployment and live cleanup remain incomplete.
