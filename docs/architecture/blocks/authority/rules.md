# Authority Enrichment — Rules

> Up: [Authority Enrichment](README.md)

1. **R1 — Matchers route by entity kind; person matchers NEVER fire on place/work rows.** Places run KIMA → gazetteer → Mazal place; works run `_mazal_match_work` only; persons run Mazal → VIAF → Wikidata. *Why:* person-label SPARQL/SRU on a place name conflates toponyms with homonymous people (Rule W-33).
2. **R2 — A KIMA hit is NEVER overridden by the Ashkenazi gazetteer**; the gazetteer is consulted only after a KIMA miss. *Why:* KIMA is the authoritative geographic source; the gazetteer is a curated gap-filler.
3. **R3 — Coordinates are never fabricated.** A coord-bearing place (KIMA/gazetteer) survives without external ids because its coordinates ARE the payload; everything else with no id is dropped. *Why:* downstream RDF/maps must only plot real centroids (Rule 60/W-32).
4. **R4 — On Mazal homonym ties the matcher MUST abstain, not guess** (`pick_mazal_candidate`: gap ≤15 without date overlap → abstain; person VIAF/Wikidata searches are suppressed on abstain). *Why:* a wrong person identification poisons RDF and Wikidata; the curator picker resolves it auditably (Rule W-37).
5. **R5 — MARC $d dates MUST flow into `match_person` and are the primary homonym discriminator** (+50 overlap score; exact name+dates query first in `resolve_personality_mazal_id`). *Why:* dates are the only reliable disambiguator between same-named personalities.
6. **R6 — tag-100 אישיות always outranks tag-150 נושא** (ORDER BY in every backend query, +100/−40 scoring, personality rematch, `mazal_subject_not_personality` safety-net guard). *Why:* subject headings for the same person carry wrong metadata for authorship statements (Rule W-33; needs migration 0020 + Mazal re-import).
7. **R7 — Hard-reject guard flags clear ALL resolved ids and biographical payload** (`placeholder_name`, `non_person_heading`, `date_conflict`, `biographical_inconsistency`, `modern_person`, `mazal_entity_type_mismatch`), and `authority_payload_blocked` keeps them out of RDF/Wikidata. *Why:* an impossible match must never look partially valid downstream.
8. **R8 — VIAF SRU hits are fail-closed on `nameType`**: typed searches require the matching nameType; person rows require `Personal`; SRU without nameType and without a Mazal anchor is stripped. *Why:* VIAF SRU drifts across entity types — cluster contamination was the desktop's F4 failure mode.
9. **R9 — Wikidata label search NEVER stands alone**: label hits on works/corporates require a Mazal/VIAF anchor (`wikidata_orphan_label`), places accept no label-resolved QIDs at all (`wikidata_label_on_place`), and Q5 is stripped from non-person rows. *Why:* bare label matching is the highest-precision-loss path to wrong QIDs.
10. **R10 — Every external authority lookup MUST route through `cache_lookup_or_call`** (Redis 24 h hot / Postgres 90-30-180 d ground truth per kind); `skip_cache=True` refreshes but still writes both tiers. *Why:* Rule W-25 — repeated VIAF/WDQS calls are slow, rate-limited, and cost latency for every curator.
11. **R11 — The canonical dedup/upsert key is `(control_number, normalize_entity_key(text), kind, normalize_role(role))`** at run insert, re-enrich, and the SSE stream alike; ingest dedup keys on `(normalized_text, kind)` with role-priority merge and `alt_roles` audit. *Why:* mismatched keys created duplicate rows the curator had to approve twice (Rule W-33).
12. **R12 — Payload completeness is a contract** (Rules W-23/W-29): `cluster_ids` never `{}` on a VIAF match, `preferred_name_lat/heb` fallback chains, full `kima_*` slice, `sources` includes `"kima"` when KIMA supplied the QID. *Why:* RDF `owl:sameAs`, Wikibase labels, and identifier statements all read these fields; missing keys silently degrade three downstream blocks.
13. **R13 — Auto-approve MUST skip rows carrying any `AUTO_APPROVE_BLOCKED_GUARDS` flag**, and already-approved rows. *Why:* those flags mean "human judgement required"; bulk rules must not launder ambiguity into approvals.
14. **R14 — Curator homonym picks are validated against `payload.homonym_candidates` and logged as `match.edited` project events.** *Why:* the pick must be one of the scored candidates (no arbitrary ID injection) and every curator decision routes through the event log (Rule W-21).
15. **R15 — `AUTHORITY_MODE=postgres` is production; Postgres normalization mirrors the SQLite matchers exactly**, and Postgres failures fall back to the local backend, never to silent empty results. *Why:* Rule W-28 — the `normalized_name` columns are only compatible if normalization is byte-identical.
16. **R16 — `stage3_guards.py` edits MUST be synced to the desktop `converter/authority/`** (and vice versa via `sync_converter_to_web.sh`). *Why:* the vendored tree is a byte-identical mirror; divergence makes desktop and web disagree on the same records (Rule W-37).
17. **R17 — Independent VIAF and Wikidata candidates must agree on the live P214 cross-reference.** If a Wikidata QID's P214 differs from the independently matched VIAF cluster, the QID is cleared and the candidate remains review-only. *Why:* label matches can attach a plausible but different scholar; cross-source disagreement is a hard false-positive signal.
18. **R18 — Retired Authority mutations fail closed and are observable.** The
legacy mutation routes MUST return HTTP 410 by default and emit structured
`legacy_authority_mutation_retired` telemetry containing the route family,
run, actor, and status. The legacy run bookmark MUST explain the move to HMO
Wikibase Studio before redirecting. *Why:* silent compatibility behavior would
make it impossible to prove that the standalone curator surface is unused or
to investigate stale clients.
19. **R19 — Postgres KIMA matching MUST use multi-row QID abstain (W-84 / W-101).**
    `PostgresAuthorityBackend.match_place` fetches all exact (and top fuzzy)
    hits and runs `pick_kima_place_row`; never `LIMIT 1` when Wikidata QIDs
    conflict. *Why:* production used to pick an arbitrary first row while
    SQLite already abstained — that silently attached the wrong place QID.
20. **R20 — Enrichment richness is fail-closed.** Accepted Mazal / KIMA /
    VIAF / Wikidata payload fields (preferred names, cluster ids, geonames,
    coords) MUST flow into RDF/HMO claims, but ambiguous matches stay withheld
    in evidence. *Why:* richest entities without smart matching recreate the
    mass-false-positive failure mode.
