import {expect, test} from "@playwright/test";

import {
  gotoHmoBuildUploadTab,
  gotoHmoItemsTab,
  installHmoItemsMocks,
  makeHmoItemsState,
  makeHmoStudioItem,
  openBuildUploadPanel,
  TEST_PROJECT_ID,
  TEST_RUN_ID,
} from "./fixtures/hmo-items-fixtures";

test.describe("HMO Wikibase Items review UI", () => {
  test.beforeEach(async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState());
  });

  test("legacy /runs/:runId bookmark lands on HMO Studio", async ({page}) => {
    await page.context().addCookies([
      {name: "session", value: "test-session", url: "http://localhost:5173"},
    ]).catch(() => undefined);
    await page.goto(`/runs/${TEST_RUN_ID}`);
    await expect(page).toHaveURL(new RegExp(`/runs/${TEST_RUN_ID}/hmo-studio`));
    await expect(page.getByRole("heading", {name: "HMO Wikibase Studio"})).toBeVisible();
    await expect(page.getByText("Authority editor retired")).toHaveCount(0);
  });

  test("shows item table on Wikibase Items tab", async ({page}) => {
    await gotoHmoItemsTab(page);
    await expect(page.getByTestId("hmo-items-panel")).toBeVisible();
    await expect(page.getByTestId("hmo-item-table")).toBeVisible();
    await expect(page.getByTestId("hmo-item-table-scroll")).toBeVisible();
    await expect(page.getByTestId("hmo-items-verify-ai")).toBeVisible();
    await expect(page.getByTestId("hmo-items-verify-ai")).toHaveText(/Verify with AI/);
    await expect(page.getByTestId("hmo-items-autofix-ai")).toBeVisible();
    await expect(page.getByTestId("hmo-items-autofix-ai")).toHaveText(/Autofix with AI/);
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toBeVisible();
  });

  test("build and upload controls stay visible above the review table", async ({page}) => {
    await gotoHmoItemsTab(page);
    await expect(page.getByTestId("hmo-item-lifecycle-bar")).toBeVisible();
    await expect(page.getByTestId("hmo-rebuild-skip-cache")).toBeVisible();
    await expect(page.getByTestId("hmo-upload-update-existing")).toBeVisible();
    await expect(page.getByTestId("hmo-upload-submit")).toBeVisible();
    await expect(page.getByTestId("hmo-item-table")).toBeVisible();
  });

  test("data status column shows new / will update / updated", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({local_id: "QNew", status: "would_create"}),
        makeHmoStudioItem({
          local_id: "QWillUpdate",
          status: "created",
          wikibase_id: "Q9001",
          upload_outcome: "create",
        }),
        makeHmoStudioItem({
          local_id: "QUpdated",
          status: "created",
          wikibase_id: "Q9002",
          upload_outcome: "update",
        }),
      ],
    }));
    await gotoHmoItemsTab(page);
    await expect(page.getByTestId("hmo-item-data-status-QNew")).toContainText("new (not uploaded)");
    await expect(page.getByTestId("hmo-item-data-status-QWillUpdate")).toContainText("will update existing");
    await expect(page.getByTestId("hmo-item-data-status-QUpdated")).toContainText("updated");
  });

  test("global search filters rows", async ({page}) => {
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-search").fill("missing-label");
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toHaveCount(0);
    await page.getByTestId("hmo-item-search").fill("Test manuscript");
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toBeVisible();
  });

  test("clicking AI verdict pill opens full explanation popover", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({
          local_id: "QFail",
          ai_verdict: {
            overall: "fail",
            reasoning: "Label on Wikibase does not match the Hebrew title in the build.",
            model: "gemini-3.5-flash",
            judged_at: "2026-07-08T10:00:00Z",
          },
        }),
      ],
    }));
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-ai-verdict-QFail").click();
    const detail = page.getByTestId("hmo-item-ai-verdict-detail-QFail");
    await expect(detail).toBeVisible();
    await expect(detail).toContainText("Why AI flagged this as wrong");
    await expect(detail).toContainText("Label on Wikibase does not match");
  });

  test("approval checkbox triggers override PATCH", async ({page}) => {
    let patched = false;
    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/items/QDraft_MS_123/override`, (route) => {
      patched = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run_id: TEST_RUN_ID,
          local_id: "QDraft_MS_123",
          approved: true,
          labels: {en: "Test manuscript"},
          claims: [],
          status: "would_create",
        }),
      });
    });
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-approved-QDraft_MS_123").click();
    await expect.poll(() => patched).toBe(true);
  });
});

test.describe("HMO Wikibase Items — Last upload column", () => {
  test("shows never tried for an item never uploaded", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState());
    await gotoHmoItemsTab(page);
    const cell = page.getByTestId("hmo-item-upload-outcome-QDraft_MS_123");
    await expect(cell).toBeVisible();
    await expect(cell).toHaveText(/never tried/);
  });

  test("shows the adopted pill with the reconcile reason for an adopted item", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({
          status: "created",
          wikibase_id: "Q999",
          upload_outcome: "adopt",
          upload_message: "adopted via reconcile: found live",
          upload_at: "2026-07-06T12:00:00Z",
        }),
      ],
    }));
    await gotoHmoItemsTab(page);
    const cell = page.getByTestId("hmo-item-upload-outcome-QDraft_MS_123");
    await expect(cell).toBeVisible();
    await expect(cell.getByText("adopted")).toBeVisible();
  });

  test("shows the failed pill for an item whose last upload attempt failed", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({
          upload_outcome: "failed",
          upload_message: "Wikibase Cloud 500",
        }),
      ],
    }));
    await gotoHmoItemsTab(page);
    const cell = page.getByTestId("hmo-item-upload-outcome-QDraft_MS_123");
    await expect(cell.getByText("failed")).toBeVisible();
    await page.getByTestId("hmo-item-upload-badge-QDraft_MS_123").click();
    await expect(page.getByTestId("hmo-item-upload-detail-QDraft_MS_123")).toContainText("Wikibase Cloud 500");
  });

  test("clicking validation errors opens the issue detail popover", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({
          shacl_issues: [
            {code: "MISSING_LABEL", severity: "Error", message: "Item is missing an English label"},
          ],
        }),
      ],
    }));
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-shacl-badge-QDraft_MS_123").click();
    await expect(page.getByTestId("hmo-item-shacl-detail-QDraft_MS_123")).toContainText(
      "Item is missing an English label",
    );
  });

  test("filtering by Last push column narrows the table", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({local_id: "QDraft_A", upload_outcome: "create"}),
        makeHmoStudioItem({local_id: "QDraft_B", upload_outcome: "failed", upload_message: "boom"}),
      ],
    }));
    await gotoHmoItemsTab(page);
    await expect(page.getByTestId("hmo-item-row-QDraft_A")).toBeVisible();
    await expect(page.getByTestId("hmo-item-row-QDraft_B")).toBeVisible();

    await page.getByTestId("hmo-item-col-upload_outcome").click();
    const checkboxList = page.getByTestId("filter-mode-checkbox");
    await expect(checkboxList).toBeVisible();
    await expect(checkboxList.locator("label", {hasText: "create"})).toContainText("(1)");
    await expect(checkboxList.locator("label", {hasText: "failed"})).toContainText("(1)");
    await checkboxList.locator("label", {hasText: "failed"}).locator('input[type="checkbox"]').check();
    await page.getByRole("button", {name: "Apply"}).click();

    await expect(page.getByTestId("hmo-item-row-QDraft_B")).toBeVisible();
    await expect(page.getByTestId("hmo-item-row-QDraft_A")).toHaveCount(0);
  });

  test("publication-status filter shows real row counts", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({local_id: "QDraft_A", status: "would_create", wikibase_id: null}),
        makeHmoStudioItem({local_id: "QDraft_B", status: "would_create", wikibase_id: null}),
        makeHmoStudioItem({
          local_id: "QDraft_C",
          status: "created",
          wikibase_id: "Q100",
          upload_outcome: "create",
        }),
      ],
    }));
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-col-data_status").click();
    const checkboxList = page.getByTestId("filter-mode-checkbox");
    await expect(checkboxList).toBeVisible();
    // Two new items share the same data_status → count must be 2, not 1.
    await expect(checkboxList.locator("label", {hasText: "new"})).toContainText("(2)");
  });

  test("Approve all visible starts a bulk-approve job for pending filtered rows", async ({page}) => {
    let jobStartBody: Record<string, unknown> | null = null;
    await installHmoItemsMocks(page, makeHmoItemsState({
      items: [
        makeHmoStudioItem({local_id: "QDraft_A", approved: null}),
        makeHmoStudioItem({local_id: "QDraft_B", approved: true}),
        makeHmoStudioItem({local_id: "QDraft_C", approved: null}),
      ],
    }));
    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      jobStartBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-approve-1",
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          kind: "hmo_item_bulk_approve",
          status: "succeeded",
          progress: {phase: "done", processed: 2, total: 2, message: "Done"},
          params: jobStartBody?.params ?? {},
          result: {channel: "hmo", total: 2, approved: 2, unchanged: 0, failed: 0, cancelled: false},
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
    page.on("dialog", (dialog) => void dialog.accept());
    await gotoHmoItemsTab(page);
    const btn = page.getByTestId("hmo-items-approve-visible");
    await expect(btn).toContainText("Approve all visible (2)");
    await btn.click();
    await expect.poll(() => jobStartBody).not.toBeNull();
    expect((jobStartBody as {kind?: string} | null)?.kind).toBe("hmo_item_bulk_approve");
    const ids = ((jobStartBody as {params?: {local_ids?: string[]}} | null)?.params?.local_ids ?? []).slice().sort();
    expect(ids).toEqual(["QDraft_A", "QDraft_C"]);
  });
});

test.describe("HMO Wikibase Items — upload-lifecycle AI verification", () => {
  test("pre/post-upload verify checkboxes render and are toggleable", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState());
    await gotoHmoBuildUploadTab(page);

    const preCheckbox = page.getByTestId("hmo-upload-preverify-checkbox");
    const postCheckbox = page.getByTestId("hmo-upload-postverify-checkbox");
    await expect(preCheckbox).toBeVisible();
    await expect(postCheckbox).toBeVisible();
    await expect(preCheckbox).not.toBeChecked();
    await expect(postCheckbox).not.toBeChecked();

    await preCheckbox.check();
    await expect(preCheckbox).toBeChecked();
    await postCheckbox.check();
    await expect(postCheckbox).toBeChecked();
  });

  test("pre-upload verify: a clean AI pass proceeds straight to upload", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState());

    let jobStartBody: Record<string, unknown> | null = null;
    let uploadBody: Record<string, unknown> | null = null;

    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      jobStartBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-1",
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          kind: "hmo_item_verify",
          status: "succeeded",
          progress: {phase: "done", processed: 1, total: 1, session_id: "sess-1"},
          params: {action_id: "audit_hmo_wikibase_item", session_id: "sess-1"},
          result: {session_id: "sess-1", judged: 1, total: 1, outcome: "pass", uncached_skipped: 0},
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

    await page.route(
      `**/api/runs/${TEST_RUN_ID}/hmo-studio/items/ai-verify/sessions/sess-1`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            session_id: "sess-1",
            run_id: TEST_RUN_ID,
            events: [],
            verdicts: [{
              candidate: {_local_id: "QDraft_MS_123"},
              verdict: {overall: "pass", reasoning: "looks fine"},
            }],
          }),
        });
      },
    );

    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/upload-items`, async (route) => {
      uploadBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          dry_run: true, created: 1, updated: 0, linked: 0, skipped: 0,
          unresolved_links: 0, failed: 0, outcomes: [], link_outcomes: [],
        }),
      });
    });

    await gotoHmoBuildUploadTab(page);
    await page.getByTestId("hmo-upload-preverify-checkbox").check();
    await page.getByTestId("hmo-upload-submit").click();

    await expect.poll(() => jobStartBody).not.toBeNull();
    expect((jobStartBody as unknown as {kind: string} | null)?.kind).toBe("hmo_item_verify");
    expect(
      (jobStartBody as unknown as {params: {action_id: string; item_ids: string[]}} | null)
        ?.params?.action_id,
    ).toBe("audit_hmo_wikibase_item");

    // No failures -> the fail-confirm gate never shows, upload proceeds automatically.
    await expect(page.getByTestId("hmo-upload-failconfirm")).toHaveCount(0);
    await expect.poll(() => uploadBody).not.toBeNull();
  });

  test("pre-upload verify: a failed AI verdict gates the upload behind a confirm banner", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState());

    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-2",
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          kind: "hmo_item_verify",
          status: "succeeded",
          progress: {phase: "done", processed: 1, total: 1, session_id: "sess-2"},
          params: {action_id: "audit_hmo_wikibase_item", session_id: "sess-2"},
          result: {session_id: "sess-2", judged: 1, total: 1, outcome: "pass", uncached_skipped: 0},
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

    await page.route(
      `**/api/runs/${TEST_RUN_ID}/hmo-studio/items/ai-verify/sessions/sess-2`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            session_id: "sess-2",
            run_id: TEST_RUN_ID,
            events: [],
            verdicts: [{
              candidate: {_local_id: "QDraft_MS_123"},
              verdict: {overall: "fail", reasoning: "label does not match MARC"},
            }],
          }),
        });
      },
    );

    let uploadCalled = false;
    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/upload-items`, async (route) => {
      uploadCalled = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          dry_run: true, created: 1, updated: 0, linked: 0, skipped: 0,
          unresolved_links: 0, failed: 0, outcomes: [], link_outcomes: [],
        }),
      });
    });

    await gotoHmoBuildUploadTab(page);
    await page.getByTestId("hmo-upload-preverify-checkbox").check();
    await page.getByTestId("hmo-upload-submit").click();

    await expect(page.getByTestId("hmo-upload-failconfirm")).toBeVisible();
    expect(uploadCalled).toBe(false);

    await page.getByTestId("hmo-upload-failconfirm-anyway").click();
    await expect.poll(() => uploadCalled).toBe(true);
  });
});
