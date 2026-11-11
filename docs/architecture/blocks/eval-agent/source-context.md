# Immutable source context

> Up: [Eval Agent](README.md) · Rule W-308

**R45 — A merged MARC context must not change a source record.**
`backend/app/pipeline/marc_verify_context.py` copies nested values before each merge.
The combined context cannot share mutable values from either input record.
The single-record path follows the same rule.

The previous shallow copy added other records' languages to the primary source.
That change created false source facts and could change later verification context.
`test_marc_verify_context.py` checks the source before and after the merge.
It also checks nested changes to the result and the single-record path.
