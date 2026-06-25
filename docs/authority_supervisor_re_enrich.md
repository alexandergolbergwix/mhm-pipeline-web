# Authority re-enrich — curator checklist (Gila audit)

Full Hebrew response to Gila’s audit (issue-by-issue status): [`authority_supervisor_response_gila.md`](./authority_supervisor_response_gila.md).

After deploying supervisor-fix releases, refresh an existing run without losing approvals:

1. Open the run → **Authority** tab.
2. Click **Re-enrich** (not a full re-import).
3. Confirm **Group duplicates** is on (default) — author + contributor collapse; author + subject stay as role chips.
4. Use **Search notes** with `קולופון`, `כולל`, `בעריכת`, `הגהות`, `מהדורה`, or shelfmark fragments to find note-sourced entities.
5. For rows flagged `homonym_unresolved` or `short_name_homonym`, open the detail drawer → **Unresolved homonym** card → **Pick** the correct אישיות (Mazal tag 100).
6. Spot-check **Allony-class rows**: author/contributor should show אישיות Mazal (`main_marc_tag=100`); subject rows may show `linked_personality_mazal_id` in the drawer.
7. Review guard flags in the drawer (`mazal_subject_not_personality`, `viaf_date_mismatch`, `cross_source_conflict`, `wikidata_crosscheck_fail`, etc.).
8. Auto-approve skips rows with blocked guard flags — resolve homonyms and subject-heading mismatches first.

Biodata (occupations, name variants, birth/death places) appears after re-enrich on v140+.

## Gila re-review spot-check (post W-37)

| Control / fixture | Expected |
|-------------------|----------|
| נחמיה אלוני (author) | Mazal tag 100, QID via P8189 |
| שלמה (ambiguous) | `homonym_unresolved` or explicit picker pick — no silent wrong link |
| MS `990025632890205171` | Colophon year + scribe (regression) |
| MS `990000464110205171` | Editor «מאיר פופרש», searchable via `בעריכת` |
| Goldschmidt provenance | Corporate Mazal (regression) |
| VIAF when Mazal dates known | `viaf_id` stripped if `cross_source_conflict` / `viaf_date_mismatch` |

Migration 0020 + Mazal re-import (`import_mazal_to_postgres`) required once per environment for full tag-100 ordering.
