/**
 * E2E fixtures for the HMO Wikibase Items review surface.
 * All backend calls are mocked via page.route() for full isolation.
 */
import {expect, type Page, type Route} from "@playwright/test";

import type {AiVerdict} from "@/api/extractionApprovals";

export const TEST_RUN_ID = "55555555-5555-5555-5555-555555555555";
export const TEST_PROJECT_ID = "66666666-6666-6666-6666-666666666666";

export interface MockHmoStudioItem {
  local_id: string;
  labels: Record<string, string>;
  descriptions?: Record<string, string>;
  class_qid: string;
  source_uri: string;
  claims: Array<{property_id: string; datatype: string; value: string}>;
  status: string;
  wikibase_id: string | null;
  approved: boolean | null;
  shacl_issues: Array<{code: string; severity: string; message: string}>;
  ai_verdict: AiVerdict | null;
  override_present: boolean;
  upload_outcome?: string | null;
  upload_message?: string;
  upload_at?: string | null;
}

export function makeHmoStudioItem(overrides: Partial<MockHmoStudioItem> = {}): MockHmoStudioItem {
  return {
    local_id: overrides.local_id ?? "QDraft_MS_123",
    labels: overrides.labels ?? {en: "Test manuscript"},
    descriptions: overrides.descriptions ?? {en: "A test item"},
    class_qid: overrides.class_qid ?? "Q1",
    source_uri: overrides.source_uri ?? "http://example.org/ms/123",
    claims: overrides.claims ?? [{property_id: "P31", datatype: "wikibase-item", value: "Q1"}],
    status: overrides.status ?? "would_create",
    wikibase_id: overrides.wikibase_id ?? null,
    approved: overrides.approved ?? null,
    shacl_issues: overrides.shacl_issues ?? [],
    ai_verdict: overrides.ai_verdict ?? null,
    override_present: overrides.override_present ?? false,
    upload_outcome: overrides.upload_outcome ?? null,
    upload_message: overrides.upload_message ?? "",
    upload_at: overrides.upload_at ?? null,
  };
}

export interface HmoItemsMockState {
  items: MockHmoStudioItem[];
  buildPresent: boolean;
}

export function makeHmoItemsState(overrides: Partial<HmoItemsMockState> = {}): HmoItemsMockState {
  return {
    items: overrides.items ?? [makeHmoStudioItem()],
    buildPresent: overrides.buildPresent ?? true,
  };
}

export async function installHmoItemsMocks(
  page: Page,
  state: HmoItemsMockState = makeHmoItemsState(),
): Promise<void> {
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "user-hmo-1",
        email: "curator@example.com",
        name: "Test Curator",
        role: "editor",
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: TEST_RUN_ID,
        project_id: TEST_PROJECT_ID,
        name: "Test Run",
        status: "succeeded",
        record_count: 1,
        match_count: 0,
        created_at: "2026-07-05T00:00:00Z",
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/records`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });

  await page.route("**/api/jobs/mine*", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({jobs: []}),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs**`, async (route: Route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "GET" && url.pathname.endsWith("/jobs")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({jobs: []}),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({jobs: []}),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/status`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({status: "built", triples_count: 10, manuscripts_count: 1}),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/catalog`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total_nodes: 5,
        total_edges: 4,
        node_types: {Manuscript: 1, Person: 2},
        edge_predicates: {"has author": 1},
        manuscript_count: 1,
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/status`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "built",
        rdf_present: true,
        wikibase_configured: true,
        coverage_present: false,
        manifest_present: false,
        upload_present: false,
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/item-status`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        build_present: state.buildPresent,
        entity_count: state.items.length,
        deferred_link_count: 0,
        uploaded_count: 0,
        built_at: "2026-07-05T00:00:00Z",
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/authority-conflicts**`, async (route: Route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ready: true,
          conflict_count: 0,
          invalid_count: 0,
          conflicts: [],
          invalid: [],
          unapproved_match_ids: [],
          message: "Unapproved colliding matches (mock).",
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ready: true,
        conflict_count: 0,
        invalid_count: 0,
        conflicts: [],
        invalid: [],
        unapproved_match_ids: [],
        message: "",
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/coverage`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        report_version: 1,
        ttl_path: "/tmp/coverage.ttl",
        strategy_source: "mock",
        rdf_class_count: 1,
        wikidata_item_count: 1,
        wikidata_item_counts_by_type: {manuscript: 1},
        classes: [],
      }),
    });
  });

  await page.route("**/api/hmo-wikibase-schema/**", async (route: Route) => {
    const url = route.request().url();
    if (url.includes("/status")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_classes: 10,
          total_properties: 20,
          mapped_classes: 10,
          mapped_properties: 20,
          missing_sample: [],
          wikibase_configured: true,
          wikibase_base_url: "https://wikibase.example.org",
          wikibase_write_user: "bot",
        }),
      });
      return;
    }
    if (url.includes("/bootstrap") && route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          dry_run: true,
          created: 0,
          skipped: 10,
          failed: 0,
          would_create: 0,
          entries: [],
        }),
      });
      return;
    }
    if (url.includes("/last-report")) {
      await route.fulfill({status: 404, contentType: "application/json", body: "{}"});
      return;
    }
    if (url.includes("/ai-verify/cached-verdicts")) {
      await route.fulfill({status: 200, contentType: "application/json", body: "[]"});
      return;
    }
    await route.continue();
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/items`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({run_id: TEST_RUN_ID, items: state.items}),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/items/**`, async (route: Route) => {
    const url = route.request().url();
    if (url.includes("/ai-verify/")) {
      if (url.includes("/actions")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([{
            id: "audit_hmo_wikibase_item",
            label: "Audit HMO Wikibase item",
            description: "Check item draft quality.",
            scope_kinds: ["single", "selection", "all"],
            evaluators: ["hmo_wikibase_item"],
            min_candidates: 1,
          }]),
        });
        return;
      }
      if (url.includes("/sessions")) {
        await route.fulfill({status: 200, contentType: "application/json", body: "[]"});
        return;
      }
      await route.continue();
      return;
    }
    if (route.request().method() === "PATCH" && url.includes("/override")) {
      await route.continue();
      return;
    }
    await route.continue();
  });
}

export async function openBuildUploadPanel(page: Page): Promise<void> {
  await expect(page.getByTestId("hmo-item-lifecycle-bar")).toBeVisible();
}

export async function gotoHmoItemsTab(page: Page): Promise<void> {
  const statusWait = page.waitForResponse(`**/api/runs/${TEST_RUN_ID}/hmo-studio/status`);
  const itemStatusWait = page.waitForResponse(`**/api/runs/${TEST_RUN_ID}/hmo-studio/item-status`);
  await page.goto(`/runs/${TEST_RUN_ID}/hmo-studio`);
  await Promise.all([statusWait, itemStatusWait]);
  await expect(page.getByRole("heading", {name: "HMO Wikibase Studio"})).toBeVisible();
  await expect(page.getByTestId("hmo-items-panel")).toBeVisible();
  await expect(page.getByTestId("hmo-item-table")).toBeVisible();
}

/** Opens the Wikibase Items lifecycle bar (build + upload controls above the table). */
export async function gotoHmoBuildUploadTab(page: Page): Promise<void> {
  const statusWait = page.waitForResponse(`**/api/runs/${TEST_RUN_ID}/hmo-studio/status`);
  const itemStatusWait = page.waitForResponse(`**/api/runs/${TEST_RUN_ID}/hmo-studio/item-status`);
  await page.goto(`/runs/${TEST_RUN_ID}/hmo-studio`);
  await Promise.all([statusWait, itemStatusWait]);
  await expect(page.getByRole("heading", {name: "HMO Wikibase Studio"})).toBeVisible();
  await openBuildUploadPanel(page);
  await expect(page.getByTestId("hmo-upload-submit")).toBeVisible();
}
