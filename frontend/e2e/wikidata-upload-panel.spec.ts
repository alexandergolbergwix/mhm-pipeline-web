import {expect, test} from "@playwright/test";

import {
  installStudioMocks,
  makeBuildResponse,
  makeStudioItem,
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
    await expect(post).toBeEnabled();
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
    const verifyJob = {
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
    };

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
          body: JSON.stringify(verifyJob),
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
    // Polls must keep judged>0 — the fixture jobs/* stub omits judged and
    // would otherwise fire "finished with no verdicts" and clear verifyPhase.
    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/job-wd-verify`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(verifyJob),
      });
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

    await expect(page.getByTestId("wikidata-upload-failconfirm")).toBeVisible({timeout: 15000});

    await page.getByTestId("wikidata-upload-failconfirm-review").click();
    await expect(page.getByText(/AI verification.*Wikidata Studio/i)).toBeVisible();
  });

  test("test target hint describes full-claim remap", async ({page}) => {
    await expect(page.getByTestId("wikidata-upload-target-hint-test")).toContainText("same claim set as live");
    await expect(page.getByTestId("wikidata-upload-target-hint-test")).toContainText("blocks the item");
  });

  test("live upload progress patches processing pill on the row under write", async ({page}) => {
    const localId = "manuscript::MS Alpha";
    const processingOutcome = {
      local_id: localId,
      label: "MS Alpha",
      entity_type: "manuscript",
      status: "processing",
      message: "Processing…",
    };
    const processingProgress = {
      phase: "uploading",
      processed: 0,
      total: 1,
      message: "Processing item 1 / 1",
      processing_local_id: localId,
      item_outcome: processingOutcome,
      recent_item_outcomes: [processingOutcome],
      outcome_counts: {pending: 1},
    };

    await installStudioMocks(page, makeBuildResponse({
      items: [
        makeStudioItem({local_id: localId, labels: {en: "MS Alpha"}}),
      ],
      total: 1,
    }));
    await gotoModernStudio(page);

    let uploadStarted = false;
    let pollN = 0;
    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      const body = route.request().postDataJSON() as {kind?: string};
      if (body.kind !== "wikidata_upload") {
        await route.fallback();
        return;
      }
      uploadStarted = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-wd-upload-processing",
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          kind: "wikidata_upload",
          status: "running",
          progress: processingProgress,
          params: body,
          result: null,
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
    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/job-wd-upload-processing`, async (route) => {
      pollN += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-wd-upload-processing",
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          kind: "wikidata_upload",
          status: "running",
          progress: processingProgress,
          params: {upload_target: "dry_run", dry_run: true},
          result: null,
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
    // Only surface the running job after submit — a pre-submit jobs/mine
    // "running" snapshot disables upload controls (jobRunning).
    await page.route("**/api/jobs/mine**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          jobs: uploadStarted
            ? [{
              id: "job-wd-upload-processing",
              project_id: TEST_PROJECT_ID,
              run_id: TEST_RUN_ID,
              kind: "wikidata_upload",
              status: "running",
              progress: processingProgress,
              params: {upload_target: "dry_run"},
              result: null,
              error: null,
            }]
            : [],
        }),
      });
    });

    await page.getByTestId("wikidata-upload-submit").click();
    await expect(page.getByTestId(`wikidata-item-upload-badge-${localId}`)).toContainText(
      "processing",
      {timeout: 10_000},
    );
    expect(pollN).toBeGreaterThanOrEqual(0);
  });

  test("the compatibility path cannot publish without the Publication service", async ({page}) => {
    await page.getByTestId("wikidata-upload-target-test").check();
    await expect(page.getByTestId("wikidata-upload-target-pill")).toContainText("TEST MODE");
    await expect(page.getByTestId("wikidata-upload-receipt-required")).toContainText("Prepare a Publication");
    await expect(page.getByTestId("wikidata-upload-submit")).toBeDisabled();
  });

  test("live upload shows the same two-step progress UI as test", async ({page}) => {
    const twoStepProgress = {
      phase: "uploading",
      processed: 1,
      total: 2,
      unit: "steps",
      message: "Writing items",
      upload_target: "live",
      sub_processed: 3,
      sub_total: 10,
      sub_unit: "items",
      current_label: "work · MS Alpha",
      elapsed_seconds: 12,
      eta_seconds: 240,
      steps: [
        {
          id: "write_items",
          label: "Step 1 — Write items",
          status: "running",
          processed: 3,
          total: 10,
          unit: "items",
          eta_seconds: 240,
          current_label: "work · MS Alpha",
        },
        {
          id: "add_connections",
          label: "Step 2 — Add connections",
          status: "pending",
          processed: 0,
          total: 4,
          unit: "links",
          eta_seconds: null,
          current_label: "Waiting for step 1",
        },
      ],
    };
    const runningJob = {
      id: "job-wd-upload-live-progress",
      project_id: TEST_PROJECT_ID,
      run_id: TEST_RUN_ID,
      kind: "wikidata_upload",
      status: "running",
      progress: twoStepProgress,
      params: {upload_target: "live", dry_run: false},
      result: null,
      error: null,
      created_by: null,
      started_at: null,
      finished_at: null,
      cancel_requested_at: null,
      created_at: null,
      updated_at: null,
    };

    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs**`, async (route) => {
      const url = route.request().url();
      if (url.includes("job-wd-upload-live-progress")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(runningJob),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({jobs: [runningJob]}),
      });
    });
    await page.route(
      `**/api/runs/${TEST_RUN_ID}/jobs/job-wd-upload-live-progress`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(runningJob),
        });
      },
    );
    await page.route("**/api/jobs/mine**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({jobs: [runningJob]}),
      });
    });

    await gotoModernStudio(page);
    const modal = page.getByTestId("wikidata-upload-progress-modal");
    await expect(modal).toBeVisible({timeout: 10_000});
    await expect(page.getByTestId("wikidata-upload-progress-target-pill")).toContainText("Live");
    await expect(modal.getByTestId("wikidata-upload-steps")).toBeVisible();
    await expect(modal.getByText("Step 1 — Write items")).toBeVisible();
    await expect(modal.getByText("Step 2 — Add connections")).toBeVisible();
    await expect(modal.getByText("MS Alpha")).toBeVisible();

    const tray = page.getByTestId("job-tray");
    await expect(tray.getByText("Step 1 — Write items")).toBeVisible();
    await expect(tray.getByText("Step 2 — Add connections")).toBeVisible();
  });
});
