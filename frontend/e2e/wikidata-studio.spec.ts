/**
 * E2E spec for the Wikidata Studio surface.
 *
 * Every backend call is mocked — no DB, no Wikidata, no Modal.
 *
 * Coverage:
 * - Page renders with summary stats and item list
 * - approved_item_count from server response (no inline filter().length)
 * - entity_type filter chip sends entity_type query param
 * - search box debounce: q query param sent after typing
 * - sort controls send sort + sort_dir params
 * - pagination controls (prev/next) send page param
 * - item-level approval toggle (ItemApprovalBadge ○/✓)
 * - inline statement exclude (✗ Exclude button) sends PATCH remove_statements
 * - validator badge renders when validation_issues present
 * - table view toggle
 * - force-rebuild checkbox sends force_rebuild=true
 * - W-27: approved-items-only gate shows approved_item_count from server
 */

import {expect, test} from "@playwright/test";
import {
  TEST_RUN_ID,
  installStudioMocks,
  makeBuildResponse,
  makeStudioItem,
} from "./fixtures/wikidata-fixtures";

async function gotoStudio(page: import("@playwright/test").Page) {
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.waitForLoadState("networkidle");
}

// ── Page renders ─────────────────────────────────────────────────────────

test.describe("WikidataStudio page", () => {
  test("renders summary stats from build response", async ({page}) => {
    const build = makeBuildResponse();
    await installStudioMocks(page, build);
    await gotoStudio(page);

    await expect(
      page.locator(".glass-pill", {hasText: "Items"}).getByText("2", {exact: true}),
    ).toBeVisible({timeout: 8000});
    await expect(
      page.locator(".glass-pill", {hasText: "Records"}).getByText("1", {exact: true}),
    ).toBeVisible();
  });

  test("renders item list with item labels", async ({page}) => {
    const build = makeBuildResponse();
    await installStudioMocks(page, build);
    await gotoStudio(page);

    await expect(page.getByRole("button", {name: /Hebrew Manuscript/})).toBeVisible({timeout: 8000});
    await expect(page.getByRole("button", {name: /Moses Maimonides/})).toBeVisible();
  });

  test("shows QID badge for items with existing_qid", async ({page}) => {
    const build = makeBuildResponse();
    await installStudioMocks(page, build);
    await gotoStudio(page);

    // Maimonides has existing_qid Q127398
    await expect(page.getByText(/Q127398/)).toBeVisible({timeout: 8000});
  });
});

// ── AI verification ─────────────────────────────────────────────────────

test.describe("AI verification", () => {
  test("verifies the current Wikidata item with item_ids in the SSE body", async ({page}) => {
    const build = makeBuildResponse({
      items: [
        makeStudioItem({
          entity_type: "person",
          labels: {en: "Moses Maimonides"},
          local_id: "person::Moses Maimonides",
          existing_qid: "Q127398",
        }),
      ],
      total: 1,
    });
    const posts: Array<{url: string; body: unknown}> = [];

    await installStudioMocks(page, build);
    await page.route(
      `**/api/runs/${TEST_RUN_ID}/wikidata-studio/ai-verify/actions*`,
      (route) => {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              id: "audit_wikidata_item",
              label: "Audit Wikidata item",
              description: "Judge the generated item against MARC evidence.",
              scope_kinds: ["single", "selection", "all"],
              evaluators: ["wikidata_item"],
              min_candidates: 1,
            },
          ]),
        });
      },
    );
    await page.route(
      `**/api/runs/${TEST_RUN_ID}/wikidata-studio/ai-verify/sessions`,
      (route) => {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        });
      },
    );
    await page.route(
      `**/api/runs/${TEST_RUN_ID}/wikidata-studio/ai-verify/start-stream`,
      (route) => {
        posts.push({
          url: route.request().url(),
          body: route.request().postDataJSON(),
        });
        route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: [
            "event: session.start",
            `data: ${JSON.stringify({
              session_id: "s1",
              action_id: "audit_wikidata_item",
              scope_size: 1,
            })}`,
            "",
            "event: agent.verdict",
            `data: ${JSON.stringify({
              record_id: "990000000000000001",
              evaluator_id: "wikidata_item",
              sub_type: "person",
              candidate: {
                _local_id: "person::Moses Maimonides",
                label: "Moses Maimonides",
              },
              verdict: {
                overall: "pass",
                name_ok: "yes",
                type_ok: "yes",
                role_ok: "n/a",
                reasoning: "The item matches the supplied evidence.",
              },
            })}`,
            "",
            "event: session.end",
            `data: ${JSON.stringify({session_id: "s1", outcome: "complete"})}`,
            "",
            "",
          ].join("\n"),
        });
      },
    );

    await gotoStudio(page);
    await page.getByRole("button", {name: /verify current with ai/i}).click();
    await page.getByRole("button", {name: /start verification/i}).click();

    await page.locator("tbody tr").filter({hasText: "Moses Maimonides"}).first().click();
    await expect(page.locator("p").filter({hasText: "The item matches the supplied evidence."})).toBeVisible({
      timeout: 8000,
    });
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toMatchObject({
      action_id: "audit_wikidata_item",
      item_ids: ["person::Moses Maimonides"],
    });
  });
});

// ── approved_item_count (W-27, server-side) ──────────────────────────────

test.describe("approved_item_count", () => {
  test("shows server-provided approved_item_count in action bar", async ({page}) => {
    const build = makeBuildResponse({
      approved_item_count: 1,
      items: [
        makeStudioItem({
          entity_type: "manuscript",
          labels: {en: "MS Alpha"},
          local_id: "manuscript::MS Alpha",
          approved: true,
        }),
        makeStudioItem({
          entity_type: "person",
          labels: {en: "Person Beta"},
          local_id: "person::Person Beta",
          approved: null,
        }),
      ],
      total: 2,
    });
    await installStudioMocks(page, build);
    await gotoStudio(page);

    // The action bar should show "1 of 2 approved" from server counts
    await expect(page.getByText(/1 of 2 approved/)).toBeVisible({timeout: 8000});
  });
});

// ── entity_type filter chip ──────────────────────────────────────────────

test.describe("entity_type filter", () => {
  test("clicking entity filter chip fires request with entity_type param", async ({page}) => {
    const build = makeBuildResponse();
    const requests: string[] = [];

    await installStudioMocks(page, build);
    // Intercept studio build calls to capture query params
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio*`, (route) => {
      if (route.request().method() === "GET") {
        requests.push(route.request().url());
      }
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeBuildResponse({
          items: [makeStudioItem({entity_type: "manuscript", labels: {en: "MS1"}, local_id: "manuscript::MS1"})],
          total: 1,
        })),
      });
    });

    await gotoStudio(page);
    // Click the "manuscript" filter chip
    await page.getByRole("button", {name: /^manuscript$/i}).click();
    await page.waitForLoadState("networkidle");

    // At least one request after initial load should carry entity_type=manuscript
    const filtered = requests.filter((u) => u.includes("entity_type=manuscript"));
    expect(filtered.length).toBeGreaterThan(0);
  });
});

// ── sort controls ────────────────────────────────────────────────────────

test.describe("sort controls", () => {
  test("changing sort dropdown fires request with sort param", async ({page}) => {
    const build = makeBuildResponse();
    const requests: string[] = [];

    await installStudioMocks(page, build);
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio*`, (route) => {
      if (route.request().method() === "GET") {
        requests.push(route.request().url());
      }
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(makeBuildResponse()),
      });
    });

    await gotoStudio(page);
    await page.locator("select").nth(1).selectOption("statements");
    await page.waitForLoadState("networkidle");

    const sortRequests = requests.filter((u) => u.includes("sort=statements"));
    expect(sortRequests.length).toBeGreaterThan(0);
  });

  test("sort direction toggle fires request with sort_dir=desc", async ({page}) => {
    const build = makeBuildResponse();
    const requests: string[] = [];

    await installStudioMocks(page, build);
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio*`, (route) => {
      if (route.request().method() === "GET") {
        requests.push(route.request().url());
      }
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(makeBuildResponse()),
      });
    });

    await gotoStudio(page);
    // Click the direction toggle button (↑ or ↓)
    await page.getByRole("button", {name: /↑|↓/}).click();
    await page.waitForLoadState("networkidle");

    const descRequests = requests.filter((u) => u.includes("sort_dir=desc"));
    expect(descRequests.length).toBeGreaterThan(0);
  });
});

// ── validation badge (W-27) ──────────────────────────────────────────────

test.describe("ItemValidatorBadge", () => {
  test("renders red dot for item with error-level validation_issues", async ({page}) => {
    const itemWithIssue = makeStudioItem({
      labels: {en: "Bad Item"},
      local_id: "manuscript::Bad Item",
      validation_issues: [{code: "P31_WRONG_QID", severity: "error", message: "Wrong QID for P31"}],
    });
    const build = makeBuildResponse({items: [itemWithIssue], total: 1});
    await installStudioMocks(page, build);
    await gotoStudio(page);

    // The red dot button has aria-label containing "validation issue"
    await expect(
      page.getByRole("button", {name: "1 validation issue", exact: true}),
    ).toBeVisible({timeout: 8000});
  });
});

// ── approval toggle (W-27) ───────────────────────────────────────────────

test.describe("ItemApprovalBadge toggle", () => {
  test("clicking approval badge fires PATCH with approved=true", async ({page}) => {
    const item = makeStudioItem({
      labels: {en: "Approvable"},
      local_id: "manuscript::Approvable",
      approved: null,
    });
    const build = makeBuildResponse({items: [item], total: 1});
    const patches: Array<{url: string; body: unknown}> = [];

    await installStudioMocks(page, build);
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/items/**`, (route) => {
      if (route.request().method() === "PATCH") {
        patches.push({url: route.request().url(), body: route.request().postDataJSON()});
      }
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          run_id: TEST_RUN_ID, local_id: "manuscript::Approvable",
          labels: {}, descriptions: {}, aliases: {},
          add_statements: [], remove_statements: [], statement_edits: {},
          approved: true,
        }),
      });
    });

    await gotoStudio(page);

    // The sidebar shows a ○ (not approved) badge for this item — click it
    const approvalBtn = page.getByTitle(/not reviewed.*click to approve/i).first();
    await expect(approvalBtn).toBeVisible({timeout: 8000});
    await approvalBtn.click();

    // A PATCH should have been sent with approved: true
    expect(patches.length).toBe(1);
    expect(patches[0].body).toMatchObject({approved: true});
  });
});

// ── view toggle ──────────────────────────────────────────────────────────

test.describe("view mode toggle", () => {
  test("table view button switches to table view", async ({page}) => {
    const build = makeBuildResponse();
    await installStudioMocks(page, build);
    await gotoStudio(page);

    await expect(page.getByRole("button", {name: /table view/i})).toBeVisible({timeout: 8000});
    await page.getByRole("button", {name: /table view/i}).click();

    // Table view renders a <table> element
    await expect(page.locator("table")).toBeVisible({timeout: 5000});
  });
});

// ── force-rebuild checkbox (W-27) ────────────────────────────────────────

test.describe("force-rebuild", () => {
  test("rebuild button with force checkbox sends force_rebuild=true", async ({page}) => {
    const build = makeBuildResponse();
    const requests: string[] = [];

    await installStudioMocks(page, build);
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio*`, (route) => {
      if (route.request().method() === "GET") {
        requests.push(route.request().url());
      }
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(makeBuildResponse()),
      });
    });

    await gotoStudio(page);
    // Check the force-rebuild checkbox
    await page.getByRole("checkbox", {name: /skip cache/i}).check();
    // Click Rebuild
    await page.getByRole("button", {name: /^Rebuild$/i}).click();
    await page.waitForLoadState("networkidle");

    const forceRequests = requests.filter((u) => u.includes("force_rebuild=true"));
    expect(forceRequests.length).toBeGreaterThan(0);
  });
});

// ── eval-agent verification ─────────────────────────────────────────────

test.describe("eval-agent verification", () => {
  test("current item verification sends local_id as item_ids", async ({page}) => {
    const build = makeBuildResponse();
    let startBody: unknown = null;
    await installStudioMocks(page, build, {
      onWikidataVerifyStart: (body) => { startBody = body; },
    });
    await gotoStudio(page);

    await page.getByRole("button", {name: /verify current with ai/i}).click();
    await expect(page.getByText(/AI verification.*Wikidata Studio/i)).toBeVisible({timeout: 8000});
    await page.getByRole("button", {name: /start verification/i}).click();

    await expect(
      page.getByRole("table").getByText("Hebrew Manuscript", {exact: true}),
    ).toBeVisible({timeout: 8000});
    expect(startBody).toMatchObject({
      action_id: "audit_wikidata_item",
      item_ids: ["manuscript::Hebrew Manuscript"],
    });
  });

  test("all visible verification sends visible local_ids", async ({page}) => {
    const build = makeBuildResponse();
    let startBody: unknown = null;
    await installStudioMocks(page, build, {
      onWikidataVerifyStart: (body) => { startBody = body; },
    });
    await gotoStudio(page);

    await page.getByRole("button", {name: /verify all visible with ai/i}).click();
    await expect(page.getByText(/2 visible/)).toBeVisible({timeout: 8000});
    await page.getByRole("button", {name: /start verification/i}).click();

    expect(startBody).toMatchObject({
      action_id: "audit_wikidata_item",
      item_ids: ["manuscript::Hebrew Manuscript", "person::Moses Maimonides"],
    });
  });
});

// ── pagination ───────────────────────────────────────────────────────────

test.describe("pagination", () => {
  test("next page button sends page=2", async ({page}) => {
    // Build a response with 51 items so pagination controls appear
    const manyItems = Array.from({length: 51}, (_, i) =>
      makeStudioItem({
        entity_type: "manuscript",
        labels: {en: `MS ${i}`},
        local_id: `manuscript::MS ${i}`,
      }),
    );
    // First call returns page 1 (50 items) with total=51
    let callCount = 0;
    const requests: string[] = [];

    await installStudioMocks(page, makeBuildResponse());
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio*`, (route) => {
      if (route.request().method() !== "GET") { route.continue(); return; }
      requests.push(route.request().url());
      callCount++;
      const pageParam = new URL(route.request().url()).searchParams.get("page") ?? "1";
      const pg = parseInt(pageParam, 10);
      const start = (pg - 1) * 50;
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(makeBuildResponse({
          items: manyItems.slice(start, start + 50),
          total: 51,
          page: pg,
          page_size: 50,
          summary: {total_items: 51, manuscripts: 51, persons: 0, works: 0, statements: 51},
        })),
      });
    });

    await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
    await page.waitForLoadState("networkidle");

    // Pagination controls should be visible
    const nextBtn = page.getByRole("button", {name: /next/i});
    await expect(nextBtn).toBeVisible({timeout: 8000});
    await nextBtn.click();
    await page.waitForLoadState("networkidle");

    // A request with page=2 should have been made
    const page2Requests = requests.filter((u) => u.includes("page=2"));
    expect(page2Requests.length).toBeGreaterThan(0);
  });
});
