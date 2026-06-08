/**
 * E2E test fixtures + Playwright route mocking for the AI Extraction entity
 * review surface. Every backend endpoint the page touches is mocked
 * deterministically so the UI is exercised in isolation — no DB,
 * Modal, or Gemini dependency.
 */
import type { Page, Route } from "@playwright/test";

export const TEST_RUN_ID = "11111111-1111-1111-1111-111111111111";
export const TEST_PROJECT_ID = "22222222-2222-2222-2222-222222222222";

export interface MockEntity {
  id: string;
  control_number: string;
  text: string;
  type: string;
  role: string;
  source: string;
  start: number | null;
  end: number | null;
  confidence: number | null;
  model_confidence: number | null;
  approved: boolean;
  rejected: boolean;
  override_type: string | null;
  override_role: string | null;
  effective_type?: string | null;
  effective_role?: string | null;
  exists_in: {
    status: "grounded" | "wrong_field" | "novel" | "unknown";
    fields: string[];
    note: string;
  } | null;
  ai_verdict: {
    overall: string;
    name_ok?: boolean | null;
    type_ok?: boolean | null;
    role_ok?: boolean | null;
    reasoning?: string;
    model?: string;
    judged_at?: string;
    cache_key?: string;
    session_id?: string;
    evaluator?: string;
    /** v2 suggested_fix extension */
    suggested_fix?: {
      text: string;
      reasoning?: string | null;
      source_field?: string | null;
      confidence: "high";
    } | null;
  } | null;
}

export function makeEntity(overrides: Partial<MockEntity> = {}): MockEntity {
  return {
    id: overrides.id ?? "ent-1",
    control_number: overrides.control_number ?? "990000403370205171",
    text: overrides.text ?? "Maimonides",
    type: overrides.type ?? "PERSON",
    role: overrides.role ?? "AUTHOR",
    source: overrides.source ?? "person_ner",
    start: overrides.start ?? 0,
    end: overrides.end ?? 10,
    confidence: overrides.confidence ?? 0.85,
    model_confidence: overrides.model_confidence ?? 0.92,
    approved: overrides.approved ?? false,
    rejected: overrides.rejected ?? false,
    override_type: overrides.override_type ?? null,
    override_role: overrides.override_role ?? null,
    exists_in: overrides.exists_in ?? {
      status: "grounded",
      fields: ["contributors"],
      note: "matched in contributors",
    },
    ai_verdict: overrides.ai_verdict ?? null,
  };
}

export function makeEntitySet(): MockEntity[] {
  return [
    makeEntity({
      id: "ent-1",
      control_number: "990000403370205171",
      text: "Maimonides",
      type: "PERSON",
      role: "AUTHOR",
      source: "person_ner",
      confidence: 0.85,
      model_confidence: 0.92,
    }),
    makeEntity({
      id: "ent-2",
      control_number: "990000403370205171",
      text: "Joseph Sanger",
      type: "OWNER",
      role: "OWNER",
      source: "provenance_ner",
      confidence: 0.60,
      model_confidence: 0.56,
      exists_in: { status: "novel", fields: [], note: "not present in any catalogued MARC field" },
    }),
    makeEntity({
      id: "ent-3",
      control_number: "990000439040205171",
      text: "Beiur al ha-Torah",
      type: "WORK",
      role: "",
      source: "contents_ner",
      confidence: 0.85,
      model_confidence: 0.99,
      exists_in: { status: "grounded", fields: ["title"], note: "matched in title" },
    }),
    makeEntity({
      id: "ent-4",
      control_number: "990000439040205171",
      text: "Religious literature",
      type: "GENRE",
      role: "",
      source: "genre_ml",
      confidence: 0.85,
      model_confidence: 0.78,
      approved: true,
      exists_in: { status: "wrong_field", fields: ["subjects"], note: "matched in subjects but expected genre_form" },
    }),
    makeEntity({
      id: "ent-5",
      control_number: "990000464110205171",
      text: "Yaakov Adler",
      type: "PERSON",
      role: "TRANSCRIBER",
      source: "person_ner",
      confidence: 0.60,
      model_confidence: 0.81,
      ai_verdict: {
        overall: "pass",
        name_ok: true,
        type_ok: true,
        role_ok: true,
        reasoning: "Clear match for Yaakov Adler as transcriber based on MARC 700 ‡e",
        model: "gemini-3.5-flash",
        judged_at: "2026-06-01T08:00:00Z",
        evaluator: "person_ner",
      },
    }),
    makeEntity({
      id: "ent-6",
      control_number: "990000464110205171",
      text: "Unknown Hebrew name",
      type: "PERSON",
      role: "",
      source: "person_ner",
      confidence: 0.60,
      model_confidence: 0.42,
      ai_verdict: {
        overall: "fail",
        reasoning: "Not a real person — extraction error from MARC 500 note.",
        model: "gemini-3.5-flash",
        evaluator: "person_ner",
      },
    }),
  ];
}

export interface MockState {
  entities: MockEntity[];
  patchCalls: { entityId: string; body: Record<string, unknown> }[];
  bulkCalls: { entityIds: string[]; approved: boolean }[];
  autoApproveCalls: Record<string, unknown>[];
  verifyStreamCalls: Record<string, unknown>[];
}

export function makeMockState(): MockState {
  return {
    entities: makeEntitySet(),
    patchCalls: [],
    bulkCalls: [],
    autoApproveCalls: [],
    verifyStreamCalls: [],
  };
}

/**
 * Install Playwright route handlers that mock every backend endpoint
 * the entity-review page touches. Mutates `state` in place so test
 * assertions can inspect captured calls + emitted side-effects.
 */
export async function installExtractionMocks(page: Page, state: MockState) {
  // ── GET /runs/{id} — project id for per-row history buttons ─────
  await page.route(`**/api/runs/${TEST_RUN_ID}`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: TEST_RUN_ID,
        project_id: TEST_PROJECT_ID,
        name: "Test run",
        status: "succeeded",
        record_count: 3,
        match_count: 0,
        error: null,
        created_at: "2026-06-01T08:00:00Z",
        completed_at: "2026-06-01T09:00:00Z",
        matches: [],
      }),
    });
  });

  // ── /auth/me — the SPA's session-bootstrap check ─────────────────
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "33333333-3333-3333-3333-333333333333",
        email: "test@example.org",
        name: "Test User",
        role: "editor",
      }),
    });
  });

  // ── /runs/{id}/extraction/status — gate that controls page state ─
  await page.route(`**/api/runs/${TEST_RUN_ID}/extraction/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "complete",
        records: 3,
        entity_total: state.entities.length,
        results_path: "/tmp/ner_results.json",
      }),
    });
  });

  // ── /runs/{id}/extraction/results — for the legacy per-record body
  await page.route(`**/api/runs/${TEST_RUN_ID}/extraction/results`, async (route) => {
    const byCn: Record<string, { _control_number: string; entities: unknown[]; ml_genres: unknown[] }> = {};
    for (const e of state.entities) {
      const rec = (byCn[e.control_number] ??= {
        _control_number: e.control_number,
        entities: [],
        ml_genres: [],
      });
      if (e.source === "genre_ml") {
        rec.ml_genres.push({ label: e.text, confidence: e.model_confidence });
      } else {
        rec.entities.push({
          text: e.text, type: e.type, role: e.role, source: e.source,
          start: e.start, end: e.end,
          confidence: e.confidence, model_confidence: e.model_confidence,
        });
      }
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(Object.values(byCn)),
    });
  });

  // ── /runs/{id}/extraction/entities — the main GET ────────────────
  await page.route(`**/api/runs/${TEST_RUN_ID}/extraction/entities`, async (route) => {
    if (route.request().method() === "GET") {
      // Mirror the real endpoint shape: {entities, total, approved_count,
      // record_count, source_counts}. useApprovalStore reads these aggregates.
      const sourceCounts: Record<string, number> = {};
      for (const e of state.entities) {
        sourceCounts[e.source] = (sourceCounts[e.source] ?? 0) + 1;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          entities:       state.entities,
          total:          state.entities.length,
          approved_count: state.entities.filter((x) => x.approved).length,
          record_count:   new Set(state.entities.map((e) => e.control_number)).size,
          source_counts:  sourceCounts,
        }),
      });
    } else {
      await route.continue();
    }
  });

  // ── PATCH /entities/{id} ─────────────────────────────────────────
  await page.route(
    new RegExp(
      `/api/runs/${TEST_RUN_ID}/extraction/entities/[^/]+$`,
    ),
    async (route) => {
      if (route.request().method() !== "PATCH") {
        await route.continue();
        return;
      }
      const url = new URL(route.request().url());
      const m = url.pathname.match(/\/entities\/([^/]+)$/);
      const entityId = m ? decodeURIComponent(m[1]) : "";
      const body = route.request().postDataJSON() as Record<string, unknown>;
      state.patchCalls.push({ entityId, body });
      const idx = state.entities.findIndex((e) => e.id === entityId);
      if (idx >= 0) {
        const merged: MockEntity = { ...state.entities[idx] };
        if (typeof body.approved === "boolean") merged.approved = body.approved;
        if (typeof body.override_type === "string") {
          merged.override_type = body.override_type;
          merged.type = body.override_type;
        }
        if (typeof body.override_role === "string") {
          merged.override_role = body.override_role;
          merged.role = body.override_role;
        }
        if (typeof body.override_text === "string") {
          merged.text = body.override_text;
        }
        state.entities[idx] = merged;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(merged),
        });
      } else {
        await route.fulfill({ status: 404, body: '{"detail": "not found"}' });
      }
    },
  );

  // ── POST /entities/bulk-approve ──────────────────────────────────
  await page.route(
    `**/api/runs/${TEST_RUN_ID}/extraction/entities/bulk-approve`,
    async (route) => {
      const body = route.request().postDataJSON() as { entity_ids: string[]; approved: boolean };
      state.bulkCalls.push({ entityIds: body.entity_ids, approved: body.approved });
      let updated = 0;
      for (const id of body.entity_ids) {
        const e = state.entities.find((x) => x.id === id);
        if (e) {
          e.approved = body.approved;
          updated += 1;
        }
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ updated }),
      });
    },
  );

  // ── POST /entities/auto-approve  + /auto-approve/preview ─────────
  await page.route(
    `**/api/runs/${TEST_RUN_ID}/extraction/entities/auto-approve`,
    async (route) => {
      const body = route.request().postDataJSON();
      state.autoApproveCalls.push(body);
      const matches = countAutoApproveMatches(state.entities, body);
      // Flip the matches to approved.
      for (const e of state.entities) {
        if (autoApproveMatches(e, body)) e.approved = true;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ matched: matches, approved: matches }),
      });
    },
  );
  await page.route(
    `**/api/runs/${TEST_RUN_ID}/extraction/entities/auto-approve/preview`,
    async (route) => {
      const body = route.request().postDataJSON();
      const matches = countAutoApproveMatches(state.entities, body);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ matched: matches, approved: 0 }),
      });
    },
  );

  // ── MARC source drawer ──────────────────────────────────────────
  await page.route(
    new RegExp(
      `/api/runs/${TEST_RUN_ID}/extraction/marc-source/[^/]+$`,
    ),
    async (route) => {
      const url = new URL(route.request().url());
      const m = url.pathname.match(/marc-source\/([^/]+)$/);
      const cn = m ? decodeURIComponent(m[1]) : "";
      const entitiesForRecord = state.entities
        .filter((e) => e.control_number === cn)
        .map((e) => ({
          id: e.id, text: e.text, type: e.type, role: e.role,
          start: e.start, end: e.end, source: e.source,
        }));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          control_number: cn,
          marc: {
            leader: "00000cam a2200000 a 4500",
            fields: [
              { tag: "245", subfields: [{ code: "a", value: "Beiur al ha-Torah" }] },
              { tag: "100", subfields: [{ code: "a", value: "Maimonides, Moses" }, { code: "e", value: "author" }] },
              { tag: "500", value: "The book is a copy of a manuscript from the 16th century." },
              { tag: "561", subfields: [{ code: "a", value: "Owned by Joseph Sanger." }] },
            ],
            contributors: [{ name: "Maimonides, Moses", role: "author" }],
            title: "Beiur al ha-Torah",
            former_owners: ["Joseph Sanger"],
          },
          entities: entitiesForRecord,
        }),
      });
    },
  );

  // ── AI verify actions list ──────────────────────────────────────
  await page.route(
    `**/api/runs/${TEST_RUN_ID}/extraction/ai-verify/actions*`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "audit_ner_extraction",
            label: "Audit NER entities with AI agent",
            description: "Ask the AI judge to score each extracted NER entity.",
            scope_kinds: ["single", "selection", "all"],
            evaluators: ["person_ner", "provenance_ner", "contents_ner", "genre_classifier"],
            min_candidates: 1,
          },
        ]),
      });
    },
  );

  // ── AI verify sessions list (history) ───────────────────────────
  await page.route(
    `**/api/runs/${TEST_RUN_ID}/extraction/ai-verify/sessions`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    },
  );

  // ── AI verify SSE stream — minimal happy path ───────────────────
  await page.route(
    `**/api/runs/${TEST_RUN_ID}/extraction/ai-verify/start-stream`,
    async (route) => {
      const body = route.request().postDataJSON() as { entity_ids?: string[] };
      state.verifyStreamCalls.push(body);

      // Simulate the eval-agent resolving each scoped entity: flip its
      // verdict to a passing result and clear any suggested_fix. This lets
      // the Auto-fix → single-entity re-check flow observe an updated
      // verdict (and a vanished Fix button) once the table refetches.
      const ids =
        Array.isArray(body.entity_ids) && body.entity_ids.length
          ? body.entity_ids
          : ["ent-1"];
      for (const id of ids) {
        const e = state.entities.find((x) => x.id === id);
        if (e) {
          e.ai_verdict = {
            overall: "pass",
            name_ok: true,
            reasoning: "Re-checked after fix — entity now matches MARC.",
            model: "gemini-3.5-flash",
            evaluator: "person_ner",
            suggested_fix: null,
          };
        }
      }
      const verdictId = ids[0];
      const sseEvents = [
        `event: session.start\ndata: ${JSON.stringify({ session_id: "test-session-1", run_id: TEST_RUN_ID, action_id: "audit_ner_extraction", scope_size: ids.length, scope_cn: [], goal: "audit" })}\n\n`,
        `event: agent.stats\ndata: ${JSON.stringify({ total: ids.length, hits: 0, judged: ids.length, in_tok: 100, out_tok: 50 })}\n\n`,
        `event: agent.verdict\ndata: ${JSON.stringify({ candidate: { _entity_id: verdictId, name: "Maimonides" }, verdict: { overall: "pass", name_ok: true, reasoning: "Re-checked after fix — entity now matches MARC.", suggested_fix: null }, evaluator_id: "person_ner", judge_id: "gemini-3.5-flash", judged_at: "2026-06-01T08:00:00Z" })}\n\n`,
        `event: session.end\ndata: ${JSON.stringify({ session_id: "test-session-1", scope_size: ids.length, outcome: "complete" })}\n\n`,
      ].join("");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache", "X-Session-Id": "test-session-1" },
        body: sseEvents,
      });
    },
  );
}

function autoApproveMatches(e: MockEntity, body: Record<string, unknown>): boolean {
  const minConf = typeof body.min_confidence === "number" ? body.min_confidence : 0.85;
  const conf = e.model_confidence ?? 0;
  if (conf < minConf) return false;
  const sources = body.sources as string[] | undefined;
  if (sources && sources.length > 0 && !sources.includes(e.source)) return false;
  const types = body.types as string[] | undefined;
  if (types && types.length > 0 && !types.includes(e.type)) return false;
  const notRoles = body.not_roles as string[] | undefined;
  if (notRoles && notRoles.includes(e.role)) return false;
  if (body.require_ai_pass && (!e.ai_verdict || e.ai_verdict.overall !== "pass")) return false;
  if (body.respect_ai_fail && e.ai_verdict?.overall === "fail") return false;
  return true;
}

function countAutoApproveMatches(entities: MockEntity[], body: Record<string, unknown>): number {
  return entities.filter((e) => autoApproveMatches(e, body)).length;
}

/**
 * Drives the SPA from `/login` to the extraction page.
 * Uses an injected session cookie + mocked /auth/me so we skip the
 * actual login form.
 */
export async function gotoExtraction(page: Page) {
  // Most apps gate `/runs/...` on a session — we inject a dummy
  // cookie. The /auth/me mock returns a logged-in user regardless.
  await page.context().addCookies([
    {
      name: "session",
      value: "test-session",
      url: page.url() !== "about:blank" ? page.url() : "http://localhost:5173",
    },
  ]).catch(() => {
    // Cookie injection can fail on some Playwright versions when no
    // page has been navigated yet — fall back to navigating first.
  });

  await page.goto(`/runs/${TEST_RUN_ID}/extraction`);
}
