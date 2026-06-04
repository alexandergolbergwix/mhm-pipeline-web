# /audit-wikidata-constants

Audit every Wikidata P and Q constant in `backend/converter/wikidata/property_mapping.py`
against live Wikidata pages to detect wrong QIDs, wrong data types, or constraint violations.

## When to run

- Before ANY change to `property_mapping.py` (adding, renaming, removing a constant).
- Before running the Wikidata Studio build or QuickStatements export on a new dataset.
- After `sync-from-desktop` if the desktop pipeline updated `converter/wikidata/`.

## Steps

### 1 — Run the validator suite first
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_item_validator.py -v
```
All 14+ tests must pass. Any failure is a blocker.

### 2 — For every P constant you touched, fetch its live Wikidata page
```
https://www.wikidata.org/wiki/Property:PXXX
```
Confirm:
- **Label** matches what the comment in `property_mapping.py` says.
- **Data type** (Item / String / URL / Quantity / Time / MonolingualText) matches `value_type` in `item_builder.py`.
- **Constraints panel** — look for "item of property constraint" notes. These are the community's formal "never use on X" rules (e.g. the P50 note: "never connect manuscript to author directly").

### 3 — For every Q constant you touched, fetch its live Wikidata page
```
https://www.wikidata.org/wiki/QXXX
```
Confirm:
- The **description** matches what the constant is supposed to represent.
- The item is NOT a redirect to something else (merges happen).

### 4 — Known-bad QID blocklist
If you discover a wrong QID that was previously in use, add it to
`_KNOWN_BAD_P31_QIDS` in `item_validator.py` so it can never silently sneak back:

```python
_KNOWN_BAD_P31_QIDS: dict[str, str] = {
    "Q179808": "Palme d'Or (Cannes film award) — correct palimpsest QID is Q274076",
    # add new entries here
}
```

Then write a unit test in `test_item_validator.py` to pin it.

## Bugs found by this audit (2026-06-04)

| Constant | Was | Correct | Impact |
|---|---|---|---|
| `Q_PALIMPSEST` | `Q179808` (Palme d'Or 🏆) | `Q274076` | Every palimpsest tagged "Palme d'Or" |
| `P_NUMBER_OF_FOLIOS` (as count) | `P7416` (citation qualifier, String) | `P1104` + unit `Q107256474` | Folio counts were string citations |
| `P50` on manuscripts | Direct on MS | Only via `P1574 → work → P50` | Explicit Wikidata constraint violation |

## References

- Rule W-26 in `CLAUDE.md` (added 2026-06-04)
- `backend/converter/wikidata/item_validator.py` — the moat checks
- `backend/tests/unit/test_item_validator.py` — regression suite
- WikiProject Manuscripts Data Model: https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model
