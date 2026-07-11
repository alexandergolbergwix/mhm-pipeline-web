/**
 * E2E fixtures for the Wikidata Studio surface.
 *
 * Every backend call is mocked via page.route() so the suite runs in
 * full isolation (no DB, no Wikidata, no Modal).
 *
 * The fixture carries the new server-side slicing fields introduced by
 * the 2026-06-07 server-offload refactor:
 *   total, page, page_size, approved_item_count, properties, property_labels
 */
import type {Page} from "@playwright/test";

export const TEST_RUN_ID    = "33333333-3333-3333-3333-333333333333";
export const TEST_PROJECT_ID = "44444444-4444-4444-4444-444444444444";

export interface MockStudioItem {
  entity_type: string;
  labels: Record<string, string>;
  descriptions?: Record<string, string>;
  aliases?: Record<string, string[]>;
  statements: Array<{
    property?: string;
    property_id?: string;
    property_label?: string;
    value?: unknown;
    value_id?: string;
    value_type?: string;
    value_label?: string;
    rank?: string;
    qualifiers?: unknown[];
    references?: unknown[];
  }>;
  existing_qid?: string | null;
  local_id?: string;
  validation_issues?: Array<{code: string; severity: string; message: string}>;
  approved?: boolean | null;
  upload_outcome?: string | null;
  upload_message?: string | null;
  upload_at?: string | null;
  ai_verdict?: {
    overall: string;
    reasoning?: string;
    model?: string;
    judged_at?: string;
    suggested_fixes?: Array<Record<string, unknown>>;
  } | null;
}

export function makeStudioItem(overrides: Partial<MockStudioItem> = {}): MockStudioItem {
  return {
    entity_type:  overrides.entity_type ?? "manuscript",
    labels:       overrides.labels ?? {en: "Hebrew Manuscript", he: "כתב יד עברי"},
    descriptions: overrides.descriptions ?? {en: "A Hebrew manuscript"},
    aliases:      overrides.aliases ?? {},
    statements:   overrides.statements ?? [
      {
        property: "P31",
        property_label: "instance of",
        value: "Q127045",
        value_id: "Q127045",
        value_label: "manuscript",
        rank: "normal",
        references: [{
          property: "P248",
          property_id: "P248",
          value_id: "Q118384267",
          value_label: "Ktiv",
        }],
      },
    ],
    existing_qid:      overrides.existing_qid ?? null,
    local_id:          overrides.local_id ?? "manuscript::Hebrew Manuscript",
    validation_issues: overrides.validation_issues ?? [],
    approved:          overrides.approved ?? null,
    upload_outcome:    overrides.upload_outcome ?? null,
    upload_message:    overrides.upload_message ?? "",
    upload_at:         overrides.upload_at ?? null,
    ai_verdict:        overrides.ai_verdict ?? null,
  };
}

export interface MockStudioBuild {
  items: MockStudioItem[];
  quickstatements: string;
  summary: {total_items: number; manuscripts: number; persons: number; works: number; statements: number};
  approved_match_count: number;
  pending_match_count: number;
  used_match_count: number;
  approved_only: boolean;
  record_count: number;
  total: number;
  page: number;
  page_size: number;
  approved_item_count: number;
  properties: Array<{id: string; label: string}>;
  property_labels: Record<string, string>;
}

export function makeBuildResponse(overrides: Partial<MockStudioBuild> = {}): MockStudioBuild {
  const items = overrides.items ?? [
    makeStudioItem({entity_type: "manuscript", labels: {en: "Hebrew Manuscript"}, local_id: "manuscript::Hebrew Manuscript"}),
    makeStudioItem({entity_type: "person",     labels: {en: "Moses Maimonides"},  local_id: "person::Moses Maimonides",     existing_qid: "Q127398"}),
  ];
  return {
    items,
    quickstatements: overrides.quickstatements ?? "CREATE\nLAST\tLen\t\"Hebrew Manuscript\"\n",
    summary: overrides.summary ?? {
      total_items: items.length,
      manuscripts: items.filter((i) => i.entity_type === "manuscript").length,
      persons:     items.filter((i) => i.entity_type === "person").length,
      works:       items.filter((i) => i.entity_type === "work").length,
      statements:  items.reduce((acc, i) => acc + i.statements.length, 0),
    },
    approved_match_count: overrides.approved_match_count ?? 2,
    pending_match_count:  overrides.pending_match_count  ?? 0,
    used_match_count:     overrides.used_match_count     ?? 2,
    approved_only:        overrides.approved_only        ?? true,
    record_count:         overrides.record_count         ?? 1,
    total:                overrides.total                ?? items.length,
    page:                 overrides.page                 ?? 1,
    page_size:            overrides.page_size            ?? 50,
    approved_item_count:  overrides.approved_item_count  ?? 0,
    properties:           overrides.properties           ?? [{id: "P31", label: "instance of"}],
    property_labels:      overrides.property_labels      ?? {P31: "instance of", Q127045: "manuscript"},
  };
}

/** Install all backend mocks needed for the Wikidata Studio page. */
export async function installStudioMocks(
  page: Page,
  build: MockStudioBuild,
  opts?: {
    overrideResponse?: Record<string, unknown>;
    onWikidataVerifyStart?: (body: unknown) => void;
  },
): Promise<void> {
  // Auth — current user
  await page.route("**/api/auth/me", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "user-1",
        email: "curator@example.com",
        name: "Test Curator",
        role: "editor",
      }),
    });
  });

  // Run detail (for project_id lookup)
  await page.route(`**/api/runs/${TEST_RUN_ID}`, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: TEST_RUN_ID,
        project_id: TEST_PROJECT_ID,
        name: "Test Run",
        status: "succeeded",
        record_count: 1,
        match_count: 2,
        created_at: "2026-06-01T00:00:00Z",
      }),
    });
  });

  // Wikidata Studio eval-agent verification endpoints.
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/ai-verify/**`, (route) => {
    const url = route.request().url();
    if (url.includes("/actions")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{
          id: "audit_wikidata_item",
          label: "Audit Wikidata item",
          description: "Check whether this Wikidata item draft is coherent and well sourced.",
          scope_kinds: ["single", "selection", "all"],
          evaluators: ["wikidata_item"],
          min_candidates: 1,
        }]),
      });
      return;
    }
    if (url.includes("/sessions")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
      return;
    }
    if (url.includes("/start-stream") && route.request().method() === "POST") {
      opts?.onWikidataVerifyStart?.(route.request().postDataJSON());
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          "event: session.start",
          "data: {\"action_id\":\"audit_wikidata_item\",\"scope_size\":1}",
          "",
          "event: agent.verdict",
          "data: {\"candidate\":{\"_item_id\":\"manuscript::Hebrew Manuscript\",\"label\":\"Hebrew Manuscript\"},\"verdict\":{\"overall\":\"full\",\"reasoning\":\"Looks coherent.\"},\"evaluator_id\":\"wikidata_item\",\"judge_id\":\"mock\"}",
          "",
          "event: session.end",
          "data: {\"outcome\":\"complete\",\"scope_size\":1}",
          "",
          "",
        ].join("\n"),
      });
      return;
    }
    route.continue();
  });

  // Studio build endpoint — responds to any query params
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio*`, (route) => {
    const url = route.request().url();
    if (url.includes("/ai-verify/actions")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{
          id: "audit_wikidata_item",
          label: "Audit Wikidata item",
          description: "Check whether this Wikidata item draft is coherent and well sourced.",
          scope_kinds: ["single", "selection", "all"],
          evaluators: ["wikidata_item"],
          min_candidates: 1,
        }]),
      });
      return;
    }
    if (url.includes("/ai-verify/sessions")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
      return;
    }
    if (url.includes("/ai-verify/start-stream") && route.request().method() === "POST") {
      opts?.onWikidataVerifyStart?.(route.request().postDataJSON());
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          "event: session.start",
          "data: {\"action_id\":\"audit_wikidata_item\",\"scope_size\":1}",
          "",
          "event: agent.verdict",
          "data: {\"candidate\":{\"_item_id\":\"manuscript::Hebrew Manuscript\",\"label\":\"Hebrew Manuscript\"},\"verdict\":{\"overall\":\"full\",\"reasoning\":\"Looks coherent.\"},\"evaluator_id\":\"wikidata_item\",\"judge_id\":\"mock\"}",
          "",
          "event: session.end",
          "data: {\"outcome\":\"complete\",\"scope_size\":1}",
          "",
          "",
        ].join("\n"),
      });
      return;
    }
    if (route.request().method() !== "GET") { route.continue(); return; }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({...build, ...(opts?.overrideResponse ?? {})}),
    });
  });

  // Item override + push/reconcile/ai-fix routes
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/items/**`, (route) => {
    const url = route.request().url();
    const method = route.request().method();
    const localId = decodeURIComponent(url.split("/items/")[1]?.split("?")[0]?.split("/")[0] ?? "");

    if (method === "PATCH") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run_id: TEST_RUN_ID,
          local_id: localId,
          labels: {},
          descriptions: {},
          aliases: {},
          add_statements: [],
          remove_statements: [],
          statement_edits: {},
          approved: true,
        }),
      });
      return;
    }
    if (method === "DELETE") {
      route.fulfill({status: 204, body: ""});
      return;
    }
    if (method === "POST" && url.includes("/push")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          local_id: localId,
          status: "created",
          qid: "Q99999",
          message: "ok",
          moratorium_lifted: false,
          test_mode: true,
        }),
      });
      return;
    }
    if (method === "POST" && url.includes("/reconcile")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          local_id: localId,
          status: "adopted",
          qid: "Q88888",
          method: "P8189",
          message: "found existing item",
        }),
      });
      return;
    }
    if (method === "POST" && url.includes("/ai-fixes/apply")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run_id: TEST_RUN_ID,
          local_id: localId,
          labels: {en: "Fixed label"},
          descriptions: {},
          aliases: {},
          add_statements: [],
          remove_statements: [],
          statement_edits: {},
        }),
      });
      return;
    }
    route.continue();
  });

  // Upload job
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, (route) => {
    if (route.request().method() !== "POST") {
      route.continue();
      return;
    }
    const body = route.request().postDataJSON() as {kind?: string};
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "job-wd-upload",
        project_id: TEST_PROJECT_ID,
        run_id: TEST_RUN_ID,
        kind: body.kind ?? "wikidata_upload",
        status: "succeeded",
        progress: {phase: "done"},
        params: body,
        result: {
          dry_run: true,
          moratorium_lifted: false,
          test_mode: true,
          outcomes: [{
            local_id: "manuscript::Hebrew Manuscript",
            label: "Hebrew Manuscript",
            entity_type: "manuscript",
            qid: null,
            status: "pending",
            message: "would create",
            added_properties: [],
          }],
        },
        error: null,
        created_by: null,
        started_at: null,
        finished_at: null,
        cancel_requested_at: null,
        created_at: null,
        updated_at: null,
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/*`, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "job-wd-upload",
        project_id: TEST_PROJECT_ID,
        run_id: TEST_RUN_ID,
        kind: "wikidata_upload",
        status: "succeeded",
        progress: {phase: "done"},
        params: {},
        result: {dry_run: true, moratorium_lifted: false, test_mode: true, outcomes: []},
        error: null,
        created_by: null,
        started_at: null,
        finished_at: null,
        cancel_requested_at: null,
        created_at: null,
        updated_at: null,
      }),
    });
  });

  // Wikidata label store (lazy fetches)
  await page.route("**/api/wikidata/labels*", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({P31: "instance of", Q127045: "manuscript"}),
    });
  });
}
