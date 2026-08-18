import {expect, test} from "@playwright/test";

import {
  installStudioMocks,
  makeBuildResponse,
  TEST_PROJECT_ID,
  TEST_RUN_ID,
} from "./fixtures/wikidata-fixtures";

async function gotoModernStudio(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("mhm.studio.reviewMode", "modern");
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.waitForLoadState("networkidle");
}

test.describe("Wikidata upload panel", () => {
  test.beforeEach(async ({page}) => {
    await installStudioMocks(page, makeBuildResponse());
    await gotoModernStudio(page);
  });

  test("upload target radios default to dry-run", async ({page}) => {
    await expect(page.getByTestId("wikidata-item-upload-actions")).toBeVisible();
    await expect(page.getByTestId("wikidata-upload-target-dry_run")).toBeChecked();
    await expect(page.getByTestId("wikidata-upload-update-existing")).toBeVisible();
    await expect(page.getByTestId("wikidata-upload-submit")).toBeVisible();
    await expect(page.getByTestId("wikidata-upload-target-pill")).toContainText("moratorium active");
  });

  test("pre/post-upload verify checkboxes are toggleable", async ({page}) => {
    const pre = page.getByTestId("wikidata-upload-preverify-checkbox");
    const post = page.getByTestId("wikidata-upload-postverify-checkbox");
    await pre.check();
    await expect(pre).toBeChecked();
    // post-verify stays disabled while dry-run is selected
    await expect(post).toBeDisabled();
    await page.getByTestId("wikidata-upload-target-test").check();
    await post.check();
    await expect(post).toBeChecked();
  });

  test("pill follows selected upload target", async ({page}) => {
    await page.getByTestId("wikidata-upload-target-test").check();
    await expect(page.getByTestId("wikidata-upload-target-pill")).toContainText("TEST MODE");
    await page.getByTestId("wikidata-upload-target-live").check();
    await expect(page.getByTestId("wikidata-upload-target-pill")).toContainText("LIVE");
  });

  test("pre-upload verify fail shows confirm gate", async ({page}) => {
    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      const body = route.request().postDataJSON() as {kind?: string};
      if (body.kind === "wikidata_verify") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "job-wd-verify",
            project_id: TEST_PROJECT_ID,
            run_id: TEST_RUN_ID,
            kind: "wikidata_verify",
            status: "succeeded",
            progress: {phase: "done", session_id: "sess-wd-1"},
            params: {action_id: "audit_wikidata_item", session_id: "sess-wd-1"},
            result: {session_id: "sess-wd-1", judged: 1, total: 1},
            error: null,
            created_by: null,
            started_at: null,
            finished_at: null,
            cancel_requested_at: null,
            created_at: null,
            updated_at: null,
          }),
        });
        return;
      }
      if (body.kind === "wikidata_upload") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "job-wd-upload-2",
            project_id: TEST_PROJECT_ID,
            run_id: TEST_RUN_ID,
            kind: "wikidata_upload",
            status: "succeeded",
            progress: {phase: "done"},
            params: body,
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
        return;
      }
      await route.fallback();
    });

    await page.route(
      `**/api/runs/${TEST_RUN_ID}/wikidata-studio/ai-verify/sessions/sess-wd-1`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            session_id: "sess-wd-1",
            run_id: TEST_RUN_ID,
            events: [],
            verdicts: [{
              candidate: {_item_id: "manuscript::Hebrew Manuscript"},
              verdict: {overall: "fail", reasoning: "label mismatch"},
            }],
          }),
        });
      },
    );

    await page.getByTestId("wikidata-upload-preverify-checkbox").check();
    await page.getByTestId("wikidata-upload-submit").click();

    await expect(page.getByTestId("wikidata-upload-failconfirm")).toBeVisible({timeout: 10000});

    await page.getByTestId("wikidata-upload-failconfirm-review").click();
    await expect(page.getByText(/AI verification.*Wikidata Studio/i)).toBeVisible();
  });

  test("test target hint describes remap-before-drop", async ({page}) => {
    await expect(page.getByTestId("wikidata-upload-target-hint-test")).toContainText("remaps live P/Q");
    await expect(page.getByTestId("wikidata-upload-target-hint-test")).toContainText("leftover snaks");
  });

  test("choosing test target posts upload_target=test and never live", async ({page}) => {
    let jobBody: Record<string, unknown> | null = null;
    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      jobBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-wd-upload-test",
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          kind: "wikidata_upload",
          status: "succeeded",
          progress: {phase: "done"},
          params: jobBody,
          result: {
            dry_run: false,
            upload_target: "test",
            moratorium_lifted: false,
            test_mode: true,
            outcomes: [],
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

    await page.getByTestId("wikidata-upload-target-test").check();
    await expect(page.getByTestId("wikidata-upload-target-pill")).toContainText("TEST MODE");
    await page.getByTestId("wikidata-upload-submit").click();
    await expect.poll(() => jobBody).not.toBeNull();
    expect(jobBody?.kind).toBe("wikidata_upload");
    const params = jobBody?.params as Record<string, unknown>;
    expect(params.upload_target).toBe("test");
    expect(params.dry_run).toBe(false);
  });
});
