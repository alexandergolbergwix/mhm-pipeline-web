# Source-choice history and exports

> Up: [Versioning and export](README.md) · Rule W-327

Migration `0055_source_review_choices` adds a nullable JSONB value to `wikidata_item_overrides`.
The source-review endpoint records the complete proof in an event before it changes the row.
Both changes use the same transaction.
The proof retains the candidate, source hash, evidence URL, curator reason, and public identity metadata.
The event records the actor.

The endpoint clears item approval and prior verdicts.
The next build validates each source hash and candidate again.
A missing source or changed occurrence blocks reuse.
The generic item PATCH cannot accept a proof.

Project JSON, Studio JSON, section JSON, and diagnostic CSV retain the proof.
The complete authority-evidence JSON also retains the projection proof.
Studio and section JSON imports recheck source scope and re-prove public identities.
An import cannot authorize an identity from a self-supplied digest alone.
An imported choice requires a rebuild and a new item review.
An import error cannot silently attach an unverified proof.

History reverts restore source choices explicitly.
A revision without choices clears the value to an empty array.
That clear also requires a rebuild; cached old claims cannot survive the clear unnoticed.
The build fingerprint includes the choices.
The current build versions are `source-aware-works-v24` and `hmo-wikidata-v28`.

The separate extraction migration 0053 retains its approval/rejection check.
Its batch operation permits the SQLite migration test to check the same constraint.
The fixture supplies the older extraction table before later migrations alter it.
