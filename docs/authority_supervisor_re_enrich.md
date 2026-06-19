# Authority re-enrich — curator checklist (Gila audit)

After deploying supervisor-fix releases, refresh an existing run without losing approvals:

1. Open the run → **Authority** tab.
2. Click **Re-enrich** (not a full re-import).
3. Confirm **Group duplicates** is on (default) — author + contributor collapse; author + subject stay as role chips.
4. Use **Search notes** with `קולופון`, `כולל`, or shelfmark fragments to find note-sourced entities.
5. Spot-check **Allony-class rows**: author/contributor should show אישיות Mazal (`main_marc_tag=100`); subject rows may show `linked_personality_mazal_id` in the drawer.
6. Review guard flags in the drawer (`mazal_subject_not_personality`, `wikidata_crosscheck_fail`, etc.).

Biodata (occupations, name variants, birth/death places) appears after re-enrich on v140+.
