# Research Assistant — chat + canvas

> Up: [Research Surface](README.md) · Rule [W-228](../../rules/platform-infra.md)

The curator surface at `/runs/:runId/linked-data-explorer` is a **chat +
editable canvas**. The old 9-tab Linked Data Explorer is gone. Panels
(maps, SPARQL, network, clusters) still exist as canvas embeds. Analytics
routers are unchanged; the agent wraps them.

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
| PUT | `/api/research-agent/threads/{id}/artifacts/{key}` | cookie |
| GET | `…/download`, `…/export` | cookie |
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
