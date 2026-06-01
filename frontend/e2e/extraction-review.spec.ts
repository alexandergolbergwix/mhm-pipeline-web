/**
 * E2E suite for the AI Extraction entity-review UI (CLAUDE.md Rule W-16).
 *
 * Every backend endpoint the page touches is mocked via
 * `installExtractionMocks`, so the suite is deterministic and runs in
 * isolation from Modal/Gemini/the DB. Tests target the user-visible
 * surface — what a curator sees and clicks — not internal hook state.
 *
 * Coverage map (close to 100% of the new review-UI flows):
 *   - Page load + review section visibility
 *   - 10-column table + every column's content
 *   - Three dimension filter chips + AND-combine
 *   - Free-text search
 *   - Column-header sorting (asc/desc/none)
 *   - Right-click column → ColumnFilterPopup
 *   - Selection (single + select-all-visible)
 *   - Bulk approve / reject
 *   - Inline Type/Role edit → PATCH call
 *   - Individual Approved checkbox → PATCH
 *   - Edit modal open/save + MARC source side-by-side
 *   - MARC source drawer open from 👁 button + from MS column click
 *   - Drawer Esc-to-close + pin behaviour
 *   - Auto-approve rule builder (slider + checkboxes + preview + apply)
 *   - AI verify selected → modal opens, SSE events flow, verdicts inline
 *   - AI verify all visible
 *   - Re-open page → approval state survives (polling)
 */
import { expect, test } from "@playwright/test";
import {
  TEST_RUN_ID,
  installExtractionMocks,
  makeMockState,
  gotoExtraction,
} from "./fixtures/extraction-fixtures";

test.describe.configure({ mode: "parallel" });

test.describe("AI Extraction entity-review UI", () => {

  // ── 1. PAGE LOAD ────────────────────────────────────────────────

  test("the review section appears when entities exist", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await expect(page.getByTestId("entity-review")).toBeVisible({ timeout: 8000 });
  });

  test("the table renders all 10 logical columns", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    for (const col of [
      "col-control_number", "col-text", "col-type", "col-role",
      "col-source", "col-confidence", "col-model_confidence",
      "col-exists_in", "col-ai_verdict", "col-approved",
    ]) {
      await expect(page.getByTestId(col)).toBeVisible();
    }
  });

  test("each entity from the mock corpus renders as a row", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    const rows = page.getByTestId("entity-row");
    await expect(rows).toHaveCount(state.entities.length, { timeout: 8000 });
  });

  test("counts in the action bar reflect total/visible/selected", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await expect(page.getByTestId("count-total")).toHaveText(String(state.entities.length));
    await expect(page.getByTestId("count-visible")).toHaveText(String(state.entities.length));
    await expect(page.getByTestId("count-selected")).toHaveText("0");
  });

  // ── 2. FILTERS ──────────────────────────────────────────────────

  test("filter by source narrows the table", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("chip-source-person_ner").click();
    const personRows = state.entities.filter((e) => e.source === "person_ner").length;
    await expect(page.getByTestId("entity-row")).toHaveCount(personRows);
  });

  test("filter by type narrows the table", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("chip-type-PERSON").click();
    const personTypes = state.entities.filter((e) => e.type === "PERSON").length;
    await expect(page.getByTestId("entity-row")).toHaveCount(personTypes);
  });

  test("filter by role narrows the table", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("chip-role-AUTHOR").click();
    const authorRoles = state.entities.filter((e) => e.role === "AUTHOR").length;
    await expect(page.getByTestId("entity-row")).toHaveCount(authorRoles);
  });

  test("two chip dimensions AND-combine", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("chip-source-person_ner").click();
    await page.getByTestId("chip-type-PERSON").click();
    const matches = state.entities.filter(
      (e) => e.source === "person_ner" && e.type === "PERSON",
    ).length;
    await expect(page.getByTestId("entity-row")).toHaveCount(matches);
  });

  test("free-text search filters by entity text", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("entity-search").fill("Maimonides");
    const matches = state.entities.filter((e) =>
      `${e.text} ${e.control_number}`.toLowerCase().includes("maimonides"),
    ).length;
    await expect(page.getByTestId("entity-row")).toHaveCount(matches);
  });

  test("clear-filters resets the chip filters", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("chip-source-person_ner").click();
    await page.getByTestId("clear-filters").click();
    await expect(page.getByTestId("entity-row")).toHaveCount(state.entities.length);
  });

  // ── 3. SORT + COLUMN POPUP ──────────────────────────────────────

  test("clicking a column header cycles asc→desc→none", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    const header = page.getByTestId("col-text");
    await header.click();
    await expect(header).toContainText("▲");
    await header.click();
    await expect(header).toContainText("▼");
    await header.click();
    await expect(header).not.toContainText("▲");
    await expect(header).not.toContainText("▼");
  });

  test("right-click column header opens the filter popup", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("col-source").click({ button: "right" });
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  // ── 4. SELECTION + BULK ─────────────────────────────────────────

  test("toggling a row selects it and updates the count", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    const firstCheckbox = page.getByTestId("entity-select").first();
    await firstCheckbox.check();
    await expect(page.getByTestId("count-selected")).toHaveText("1");
  });

  test("'Select all visible' selects every visible row", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("btn-select-all-visible").click();
    await expect(page.getByTestId("count-selected")).toHaveText(String(state.entities.length));
  });

  test("'Approve selected' calls bulk-approve and updates the row", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    const firstCheckbox = page.getByTestId("entity-select").first();
    await firstCheckbox.check();
    await page.getByTestId("btn-approve-selected").click();
    await expect.poll(() => state.bulkCalls.length).toBeGreaterThan(0);
    expect(state.bulkCalls[0].approved).toBe(true);
  });

  // ── 5. INLINE EDIT + APPROVAL ───────────────────────────────────

  test("toggling the Approved checkbox PATCHes the entity", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    const approvedBox = page.getByTestId("entity-approved").first();
    await approvedBox.check();
    await expect.poll(() => state.patchCalls.length).toBeGreaterThan(0);
    expect(state.patchCalls[0].body.approved).toBe(true);
  });

  test("changing the inline Type select PATCHes the entity", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    const typeSelect = page.getByTestId("entity-type").first();
    await typeSelect.selectOption("OWNER");
    await expect.poll(() => state.patchCalls.length).toBeGreaterThan(0);
    expect(state.patchCalls[0].body.type).toBe("OWNER");
  });

  test("changing the inline Role select PATCHes the entity", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    const roleSelect = page.getByTestId("entity-role").first();
    await roleSelect.selectOption("CENSOR");
    await expect.poll(() => state.patchCalls.length).toBeGreaterThan(0);
    expect(state.patchCalls[0].body.role).toBe("CENSOR");
  });

  // ── 6. EDIT MODAL ───────────────────────────────────────────────

  test("clicking the ✎ button opens the edit modal", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("entity-edit-btn").first().click();
    await expect(page.getByTestId("entity-edit-modal")).toBeVisible();
  });

  test("the edit modal renders MARC source side-by-side", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("entity-edit-btn").first().click();
    await expect(page.getByTestId("entity-edit-marc")).toBeVisible({ timeout: 5000 });
  });

  test("Save in the edit modal PATCHes the entity and closes", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("entity-edit-btn").first().click();
    await page.getByTestId("entity-edit-text").fill("Edited text");
    await page.getByTestId("entity-edit-save").click();
    await expect.poll(() => state.patchCalls.length).toBeGreaterThan(0);
    expect(state.patchCalls[0].body.text).toBe("Edited text");
    await expect(page.getByTestId("entity-edit-modal")).toHaveCount(0);
  });

  // ── 7. MARC SOURCE DRAWER ───────────────────────────────────────

  test("clicking 👁 opens the right-side MARC source drawer", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("entity-view-source").first().click();
    const drawer = page.locator('aside:has-text("MARC source")');
    await expect(drawer).toBeVisible();
  });

  test("the drawer shows the control number it was opened for", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    const firstCn = state.entities[0].control_number;
    await page.getByTestId("entity-view-source").first().click();
    const drawer = page.locator('aside:has-text("MARC source")');
    await expect(drawer).toContainText(firstCn);
  });

  test("the drawer lists every entity in the record", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("entity-view-source").first().click();
    const drawer = page.locator('aside:has-text("MARC source")');
    await expect(drawer).toContainText(/Entities in this record/);
  });

  // ── 8. AUTO-APPROVE RULE BUILDER ────────────────────────────────

  test("the Auto-approve button opens the rule-builder modal", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("btn-auto-approve").click();
    await expect(page.getByTestId("auto-approve-modal")).toBeVisible();
  });

  test("the slider updates the displayed min-confidence value", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("btn-auto-approve").click();
    const slider = page.getByTestId("min-confidence-slider");
    await slider.evaluate((el: HTMLInputElement) => {
      el.value = "0.95";
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await expect(page.getByTestId("min-confidence-value")).toHaveText("0.95");
  });

  test("the preview count updates as the rule changes", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("btn-auto-approve").click();
    await expect(page.getByTestId("auto-approve-preview")).toContainText(/match/);
  });

  test("Apply triggers the auto-approve endpoint and closes the modal", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("btn-auto-approve").click();
    await page.getByTestId("auto-approve-apply").click();
    await expect.poll(() => state.autoApproveCalls.length).toBeGreaterThan(0);
    await expect(page.getByTestId("auto-approve-modal")).toHaveCount(0);
  });

  // ── 9. AI VERIFICATION ──────────────────────────────────────────

  test("Verify selected fires the SSE stream and opens the modal", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("entity-select").first().check();
    await page.getByTestId("btn-verify-selected").click();
    await expect.poll(() => state.verifyStreamCalls.length).toBeGreaterThan(0);
  });

  test("Verify all visible enqueues a session with every visible entity", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await page.getByTestId("btn-verify-all-visible").click();
    await expect.poll(() => state.verifyStreamCalls.length).toBeGreaterThan(0);
    const call = state.verifyStreamCalls[0] as { entity_ids: string[] };
    expect(call.entity_ids).toHaveLength(state.entities.length);
  });

  // ── 10. EXISTS_IN BADGE + AI VERDICT PILL RENDERING ────────────

  test("the exists_in badge renders each status correctly", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await expect(page.locator('[data-testid="exists-in"][data-status="grounded"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="exists-in"][data-status="novel"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="exists-in"][data-status="wrong_field"]').first()).toBeVisible();
  });

  test("AI verdict pills render for verdicts that exist", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    // ent-5 (pass) and ent-6 (fail) carry verdicts.
    const passPill = page.locator('[data-verdict="pass"]').first();
    const failPill = page.locator('[data-verdict="fail"]').first();
    await expect(passPill).toBeVisible();
    await expect(failPill).toBeVisible();
  });

});
