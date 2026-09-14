# Research Assistant — chat + canvas

> Up: [Research Surface](README.md) · Rule [W-228](../../rules/platform-infra.md)

The curator surface at `/runs/:runId/linked-data-explorer` is a **chat +
editable canvas**. The old 9-tab Linked Data Explorer is gone. Panels
(maps, network, clusters) still exist as canvas embeds; tabular SPARQL
artifacts render as a table, and the canvas has a full-screen expand view.
Analytics routers are unchanged; the agent wraps them.

**Async runs (Rule R28).** When Modal is configured the planner runs
detached: `POST /agui-async` dispatches Modal (202), Modal relays AG-UI
events to the `agui-events` webhook, and the browser reads the Heroku
SSE bridge (`agui-stream`, Redis-Stream backed, heartbeats). The local
AG-UI stub keeps the legacy request-scoped stream. Map artifacts carry
the manuscript's `cn`; the canvas preselects it in `ProvenanceMapPanel`.

Architecture name (code + docs, not a prompt-only policy):
**Architecting Secure, Domain-Restricted Agentic Systems for Bibliographic
and Semantic Web Research**.

## Trust split

```
Browser  →  Modal AG-UI (or local POST /api/research-agent/agui)
Modal    →  POST /api/research-agent/tools   Bearer tool_grant JWT
Heroku   =  trusted plane: RBAC, Postgres, SPARQL guards, wiki decrypt
```

`modal/modal_research_agent.py` is HTTPS-only (Rule W-15). FastAPI never
imports it. Empty `RESEARCH_AGENT_MODAL_URL` uses the local keyword stub.

Wiki passwords unwrap with the user KEK at session mint, then re-wrap with
`MASTER_KEY` onto `research_agent_grants`. The JWT carries **grant ids
only**. Modal never receives wiki credentials. The Modal planner default is
Qubrid `glm-5.3-flash` (`RESEARCH_AGENT_MODEL`) over the Chat
Completions API (`https://platform.qubrid.com/v1`, key `QUBRID_API_KEY`).
Qubrid does not serve the OpenAI Responses API — `modal_research_agent.py`
must build the agent with `OpenAIChatModel` + `OpenAIProvider`, never the
bare `openai:` prefix (which selects `OpenAIResponsesModel` and fails with
`Invalid request sent to the model`). `glm-5.3-flash` always thinks; do
not send `thinking: disabled`.

## Structural rails (not the system prompt)

1. **Pre-inference router** — `scope.classify_scope` ALLOW vs REJECT
   utterances + jailbreak regex, before privileged tools. Academic
   manuscript context must not over-refuse.
2. **Dual-LLM quarantine** — `sanitize.quarantine_text` types, caps, and
   strips instruction-like spans from retrieved MARC / Wikidata text.
3. **Parameterized SPARQL** — `template_id` + typed params;
   `sparql_templates.py`. Raw SPARQL still `_validate_query` + LIMIT.
4. **Output rail** — redacts `tool_grant`, `MASTER_KEY`, system-prompt leak.
5. **CSRF** — only `/api/research-agent/tools` is cookie-exempt (JWT).
   Sessions and AG-UI keep cookie + CSRF. Rate limits: sessions/agui
   `30/minute`, tools `60/minute`.
6. **Modal** — web timeout 150 s. Optional sandbox egress:
   `query.wikidata.org`, `www.wikidata.org`. Wiki reads stay on Heroku.

## Routes

| Method | Path | Auth |
|---|---|---|
| POST | `/api/research-agent/sessions` | cookie |
| GET | `/api/research-agent/threads/{id}` | cookie |
| GET | `/api/research-agent/threads?project_id=&run_id=` | cookie |
| PATCH | `/api/research-agent/threads/{id}` (title, messages) | cookie |
| POST | `/api/research-agent/threads/{id}/auto-title` | cookie |
| PUT | `/api/research-agent/threads/{id}/artifacts/{key}` | cookie |
| GET | `…/download`, `…/export` | cookie |
| GET/POST | `/api/research-agent/reply-cache` | Bearer JWT |
| POST | `/api/research-agent/tools` | Bearer JWT |
| POST | `/api/research-agent/agui` | cookie (local stub) |

Modal `/agui` MUST bind FastAPI `Request` (no postponed annotations around
that nested route) and call `AGUIAdapter.dispatch_request(request,
agent=agent)` (Rule W-229). Browser messages MUST include a string `id`
plus `tools: []` and `context: []`. The planner uses `retries=3` and
`output_type=str`. Chat maps pydantic-ai retry exceptions to a curator
sentence (Rule W-231). A Heroku-only deploy does not change the planner.

The chat MUST show bouncing wait dots (`research-chat-waiting`) while `busy`
is true and no stream token has arrived yet (frontend R22). Modal cold start
and tool calls can stay silent for tens of seconds.

Canvas artifacts version on save. User text after Save is source of truth.
Download / export are curator artifacts, not wiki writes.

**Data skills (Rule R24).** `research_sparql` always saves its raw result as
the `sparql-results` dataset artifact and returns the planner a digest when
the result exceeds 8 rows: columns, row count, and ≤8 preview rows. The
planner extracts facts with `data_info` (columns + sample), `data_select`
(eq / contains / in, ≤50 rows), `data_distinct` (top-25 value counts),
`data_search` (substring across cells), and `data_agg`
(count / min / max / sum / avg) — all reading the artifact server-side with
200-char cells, so the planner context never carries raw row dumps.

**Visualization + export skills (Rule R25).** `show_movement_map` (cn
optional — one manuscript or the whole corpus) upserts the `movement-map`
canvas artifact (kind `map`, rendered by `ProvenanceMapPanel`) and returns a
digest: ms label, stop count, place names (≤40), manuscript count. The
artifact carries the manuscript's `cn`; the canvas passes it to
`ProvenanceMapPanel` (`initialCn`) so an agent-placed single-manuscript map
opens with that manuscript's provenance chain preselected — corpus maps
(`cn: null`) open with the mode toggle and manuscript picker. It
queries the existing `get_provenance_map` / `list_manuscripts` routers — no
duplicated geo logic (R14). `show_wikidata_places` runs one read-only WDQS
query over the `wikidata-items` QIDs and places the `wikidata-places` map
artifact (kind `map`, `map: "points"`, rendered by `WikidataPlacesPanel`):
every place-valued claim with P625 coordinates, each popup linking to the
item and the place on Wikidata. `show_link_types` aggregates the `wikidata-items`
claim dataset into the `link-types` canvas artifact (kind `chart`, rendered
by the canvas `ChartView`: grouped bars per property family) and returns a
top-links digest. `export_pdf` renders any saved artifact as a
PDF server-side (`research_agent/pdf.py`, fpdf2 + DejaVu Sans with Hebrew
coverage; core-font fallback replaces non-Latin-1 chars) and returns a
`download_path` — the planner never touches PDF bytes. `create_download_link`
and the artifact download route accept `format=pdf`.

**Wikidata upload skills (Rule R26).** Questions about what links the
uploaded Wikidata items have follow the DB → API → dataset flow:
`wikidata_uploaded_items` merges (1) succeeded `publication_execution_actions`
rows — the ground truth of what reached Wikidata — with (2) both Studio cache
sources (`legacy` and `canonical`, `approved_only=False`) for items carrying
`existing_qid`, and saves the union as the `wikidata-uploads`
dataset (qid, local_id, entity_type, label, statements, source).
`wikidata_fetch_items` batches those QIDs through `wbgetentities`
(`wiki.fetch_wikidata_entities_batch`, 50 ids/call, ≤300 QIDs), extracts the
real claim datavalues (QIDs, strings, dates, amounts), and saves one row per
claim value as the `wikidata-items` dataset — with a `target_is_ours` column
that flags claims pointing at one of the project's own uploaded QIDs.
`show_link_types` turns that dataset into the `link-types` chart (grouped
bars, internal-links badge ↺). The session wiki
token comes from the curator's Settings bot password when valid, else the
server-held publication credential (Rule R18); a failed login degrades to
an anonymous read (Rule R27). Both prompts (Modal
`_SYSTEM` and `prompt.SYSTEM_PROMPT`) hard-wire this flow and forbid
guessing links from local RDF or SPARQL.
