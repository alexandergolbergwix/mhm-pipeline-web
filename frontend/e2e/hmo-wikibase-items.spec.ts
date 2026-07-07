import {expect, test} from "@playwright/test";

import {
  gotoHmoBuildUploadTab,
  gotoHmoItemsTab,
  installHmoItemsMocks,
  makeHmoItemsState,
  makeHmoStudioItem,
  TEST_PROJECT_ID,
  TEST_RUN_ID,
} from "./fixtures/hmo-items-fixtures";

test.describe("HMO Wikibase Items review UI", () => {
  test.beforeEach(async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState());
  });

  test("shows item table on Wikibase Items tab", async ({page}) => {
    await gotoHmoItemsTab(page);
    await expect(page.getByTestId("hmo-items-panel")).toBeVisible();
    await expect(page.getByTestId("hmo-item-table")).toBeVisible();
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toBeVisible();
  });

  test("global search filters rows", async ({page}) => {
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-search").fill("missing-label");
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toHaveCount(0);
    await page.getByTestId("hmo-item-search").fill("Test manuscript");
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toBeVisible();
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
    await expect(cell.getByText("Wikibase Cloud 500")).toBeVisible();
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

    await page.getByRole("button", {name: "Last push"}).click();
    const checkboxList = page.getByTestId("filter-mode-checkbox");
    await expect(checkboxList).toBeVisible();
    await checkboxList.locator("label", {hasText: "failed"}).locator('input[type="checkbox"]').check();
    await page.getByRole("button", {name: "Apply"}).click();

    await expect(page.getByTestId("hmo-item-row-QDraft_B")).toBeVisible();
    await expect(page.getByTestId("hmo-item-row-QDraft_A")).toHaveCount(0);
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
