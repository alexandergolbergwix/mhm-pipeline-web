# Gila builder review follow-up

## State

The user asks for code fixes for all issues in Gila's full nine-point letter.
The user chose separate physical manuscripts and written works.
Use `docs/plans/gila-builder-completion.md` for the acceptance criteria.
Commits `90e4cbf`, `0d05a7ed`, and `84942a1c` include the builder changes.
Heroku and Modal received those releases. Full-corpus upload `24802798` completed.
Preserve unrelated Research Assistant changes in the worktree.

## Completed first pass

- Preserve named unresolved scribes through `P11603 somevalue` and `P1932`.
- Preserve separate person roles and qualified statement identity.
- Add the exact audited Cantarini identity.
- Restrict secondary-work author fallback in both projection paths.
- Add the anchored opening sermon and its source-scoped identity and genre.
- Reject image-only codex inference in both projection paths.
- Remove stale circa for the same exact source date and precision.
- Deduplicate owned-item reference blocks without loss of a distinct source.
- Add the deterministic Gila audit and old-work review proposals.
- Bump schemas to `source-aware-works-v13` and `hmo-wikidata-v16`.
- Correct the original ninth audit point in the architecture rules.

The read-only legacy build covers 897 records and produces 1,996 items.
It has zero validator-error items, 413 named unknown scribe claims on 400
manuscripts, 897 qualified Ktiv memberships, and 563 qualified colophons.
The report contains 897 known QIDs, 147 absent-source cases, zero transfer cases
in selected checks, and 1,163 identity/scope review cases.
These are proposals, not live corrections.

Local report: `backend/state/gila-builder-audit-47e03437.json`.
Live work snapshot: `backend/state/gila-live-work-Q141175543.json`.
Both files are ignored run state. They contain no credentials.
The work proposal retains revision `2558074052` and two author claim IDs.

## Review findings to fix next

### Standards

1. **Author trust regression.** `_context_work_author_evidence` accepts raw
   approved QIDs without the heading and guard checks. A full canonical build
   can emit P50 Q42 for MARC author `אהרן בן יצחק` when the authority heading
   is `משה בן יעקב` and `guard_flags=["wikidata_crosscheck_fail"]`.
   Add a full-build regression before the fix.
2. **Duplicate scribe.** An approved NLI/VIAF scribe with no public QID creates
   a local person. The raw contributor uses a name-only key and also creates
   `P11603 somevalue`. Both survive `finish_native_items`.
   Test the complete builder/finalizer path.

### Spec

1. **Upload identity.** Source scope stops title reconciliation only inside
   `work_projection`. `wikidata_upload._reconcile_for_upload` can still choose
   polluted `Q141175543` before `Q141755239`. The reconciler does not compare
   proposed P50 identity with conflicting P2093 identities.
2. **Supported local authors.** Canonical sanitation accepts public author QIDs
   but drops identifier-backed local P50 targets. It also rejects the legacy
   builder's valid fallback for one unambiguous MARC 505 entry.
3. **Mixed inscriptions.** The canonical pass joins title-page and colophon
   text. The merge keeps that combined claim beside the separate qualified
   claims because only complete text equality triggers replacement.
4. **Lost source references.** `dedupe_statements` chooses one equal statement
   by reference count. It discards the other statement's distinct source
   before the uploader can preserve it.
5. **Wrong absence class.** The audit labels unparsed `300$a` text such as
   `דפים לא ממוספרים` as `absent_in_source`. Check raw evidence before that label.

## Review agents

Standards session: `ses_e55db9390ffeUrOZbvTdCUR45V`.
Spec session: `ses_e55db9339ffen2ENoTeCde0QO0`.
Both agents performed read-only reviews. Their probes confirmed the cases above.
Resume those sessions for exact probe details if needed.

## Verification and remaining work

The read-only inspection grouped 10 failures as owned-manuscript label conflicts.
The API named sibling items with the same Hebrew title and description pair.
The target's existing label is an item-specific manuscript designation.
The local uploader now preserves it and adds the literary title as an alias.
This fix is not deployed yet.
The remaining failure, `work:מדרש_איכה`, has no same-language pair in the
cached or locally constructed WBI payload. Exact title reconciliation found no
candidate. The new code reports that API response after one attempt for manual review.

Focused upload tests now cover the label-conflict retry and equal-description
removal. The full backend suite is not verified here. The environment has no
`mypy`. Ruff has baseline findings; compare new findings with HEAD.

The next safe upload scope is the ten failed manuscript local IDs only.
Exclude `work:מדרש_איכה` until its create payload is understood.
Do not include the 407 blocked or 19 skipped items in that retry.
The local audit report remains a proposal. Keep `corrected_qids` empty until a
separate live write and read-back confirm corrections.
