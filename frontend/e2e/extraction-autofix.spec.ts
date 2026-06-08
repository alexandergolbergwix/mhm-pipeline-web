/**
 * E2E tests for the Auto-fix button (AI-suggested extraction fixes).
 *
 * Coverage (per CLAUDE.md Rule W-19 "every click is a test"):
 *  1. No Auto-fix button when entity has no ai_verdict.suggested_fix.
 *  2. No Auto-fix button when fix text equals entity's current text.
 *  3. Auto-fix button visible in table row when fix differs from current text.
 *  4. Clicking Auto-fix sends PATCH with {override_text: fix.text}.
 *  5. Row updates to show the new text after PATCH returns.
 *  6. Auto-fix does NOT change the approved checkbox state.
 *  7. Auto-fix button visible in EntityDetailDrawer when fix present.
 *  8. Drawer shows "Current:" and "Proposed:" preview before clicking.
 *  9. Clicking drawer Auto-fix sends PATCH and updates drawer header.
 * 10. Drawer Auto-fix does NOT change approval state.
 * 11. No Auto-fix button for genre entities (genre_classifier never fixes).
 */

import { expect, test } from "@playwright/test";
import {
  TEST_RUN_ID,
  installExtractionMocks,
  makeMockState,
  makeEntity,
  gotoExtraction,
} from "./fixtures/extraction-fixtures";

test.describe.configure({ mode: "parallel" });

test.describe("Auto-fix button", () => {

  // ── Shared setup: add a fixable entity (ent-fix) to the state ───

  function makeStateWithFix() {
    const state = makeMockState();
    // entity with a high-confidence fix from the AI
    state.entities.push(
      makeEntity({
        id: "ent-fix",
        control_number: "990000403370205171",
        text: "יוסף",               // current (truncated)
        type: "PERSON",
        role: "TRANSCRIBER",
        source: "person_ner",
        confidence: 0.82,
        model_confidence: 0.91,
        approved: false,
        ai_verdict: {
          overall: "partial",
          name_ok: false,
          type_ok: true,
          role_ok: true,
          reasoning: "Name is truncated; patronymic visible in colophon_text.",
          model: "gemini-3.1-pro-preview",
          judged_at: "2026-06-07T08:00:00Z",
          evaluator: "person_ner",
          suggested_fix: {
            text: "יוסף בן יעקב",
            reasoning: "Full name with patronymic visible in colophon_text.",
            source_field: "colophon_text",
            confidence: "high",
          },
        },
      }),
    );
    return state;
  }

  function makeStateFixEqualsText() {
    const state = makeMockState();
    state.entities.push(
      makeEntity({
        id: "ent-fix-same",
        text: "יוסף בן יעקב",          // same as suggested fix
        type: "PERSON",
        role: "TRANSCRIBER",
        source: "person_ner",
        confidence: 0.82,
        ai_verdict: {
          overall: "partial",
          name_ok: false,
          type_ok: true,
          role_ok: true,
          reasoning: "Name matches but check role.",
          suggested_fix: {
            text: "יוסף בן יעקב",       // same — should NOT show button
            confidence: "high",
          },
        },
      }),
    );
    return state;
  }

  // ── 1. No button when no suggested_fix ────────────────────────────

  test("no auto-fix button when entity has no suggested_fix", async ({ page }) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    // ent-5 has ai_verdict with overall=pass but no suggested_fix
    const row = page.getByTestId("entity-row").filter({ hasText: "Yaakov Adler" });
    await expect(row).toBeVisible();
    await expect(row.getByTestId("entity-autofix-btn")).not.toBeVisible();
  });

  // ── 2. No button when fix equals current text ─────────────────────

  test("no auto-fix button when fix text equals current entity text", async ({ page }) => {
    const state = makeStateFixEqualsText();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף בן יעקב" }).last();
    await expect(row.getByTestId("entity-autofix-btn")).not.toBeVisible();
  });

  // ── 3. Button visible when fix differs ───────────────────────────

  test("auto-fix button is visible in table row when fix differs from current text", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await expect(row.getByTestId("entity-autofix-btn")).toBeVisible();
  });

  // ── 4. Clicking sends PATCH with override_text ────────────────────

  test("clicking auto-fix calls PATCH with override_text", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-autofix-btn").click();

    // The PATCH body must include override_text and nothing else that changes approval
    const call = state.patchCalls.find((c) => c.entityId === "ent-fix");
    expect(call).toBeDefined();
    expect(call!.body.override_text).toBe("יוסף בן יעקב");
    // approved must NOT be set to true by the auto-fix call
    expect(call!.body.approved).toBeUndefined();
  });

  // ── 5. Row updates to new text ────────────────────────────────────

  test("row text updates to the fixed value after PATCH", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-autofix-btn").click();

    // After PATCH, the mock returns the entity with text="יוסף בן יעקב"
    // The row should update (re-render via onEntityUpdated).
    await expect(
      page.getByTestId("entity-row").filter({ hasText: "יוסף בן יעקב" }),
    ).toBeVisible({ timeout: 3000 });
  });

  // ── 6. Auto-fix does NOT approve ─────────────────────────────────

  test("auto-fix does not change approved state", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();

    // Verify entity is not approved before click
    const checkbox = row.getByTestId("entity-approved");
    await expect(checkbox).not.toBeChecked();

    await row.getByTestId("entity-autofix-btn").click();
    // Wait for patch to complete
    await page.waitForTimeout(200);

    // Checkbox should still not be checked
    await expect(checkbox).not.toBeChecked();
  });

  // ── 7. Auto-fix button visible in EntityDetailDrawer ─────────────

  test("auto-fix button visible in EntityDetailDrawer for fixable entity", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    // Open drawer by clicking on the 👁 view-source button (which calls onViewSource → setDetailEntity)
    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-view-source").click();

    const drawer = page.getByTestId("entity-detail-drawer");
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await expect(drawer.getByTestId("detail-autofix-btn")).toBeVisible();
  });

  // ── 8. Drawer shows current + proposed preview ────────────────────

  test("drawer shows current and proposed text in auto-fix preview", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-view-source").click();

    const drawer = page.getByTestId("entity-detail-drawer");
    await expect(drawer).toBeVisible({ timeout: 3000 });

    // The AI verdict card in the drawer should show both current and proposed
    await expect(drawer.getByTestId("autofix-current-text")).toBeVisible();
    await expect(drawer.getByTestId("autofix-proposed-text")).toBeVisible();
    await expect(drawer.getByTestId("autofix-proposed-text")).toContainText("יוסף בן יעקב");
  });

  // ── 9. Drawer auto-fix sends PATCH + updates header ──────────────

  test("drawer auto-fix button calls PATCH and updates entity text", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-view-source").click();

    const drawer = page.getByTestId("entity-detail-drawer");
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await drawer.getByTestId("detail-autofix-btn").click();

    const call = state.patchCalls.find((c) => c.entityId === "ent-fix");
    expect(call).toBeDefined();
    expect(call!.body.override_text).toBe("יוסף בן יעקב");
  });

  // ── 10. Drawer auto-fix does not change approval ──────────────────

  test("drawer auto-fix does not change approval state", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-view-source").click();

    const drawer = page.getByTestId("entity-detail-drawer");
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await drawer.getByTestId("detail-autofix-btn").click();

    // Check that no approved=true was sent
    const call = state.patchCalls.find((c) => c.entityId === "ent-fix");
    expect(call).toBeDefined();
    expect(call!.body.approved).toBeUndefined();
  });

  // ── 11. No button for genre entities ─────────────────────────────

  test("no auto-fix button for genre_ml entities even with a verdict", async ({ page }) => {
    const state = makeMockState();
    // Give ent-4 (genre_ml) a fake suggested_fix — should still show no button
    const genreEntity = state.entities.find((e) => e.id === "ent-4")!;
    genreEntity.ai_verdict = {
      overall: "partial",
      name_ok: false,
      type_ok: true,
      role_ok: "n/a" as unknown as boolean,
      reasoning: "Genre label doesn't match.",
      suggested_fix: {
        text: "Religious texts",
        confidence: "high",
      },
    };

    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "Religious literature" });
    await expect(row).toBeVisible();
    // Genre entities must NEVER show an auto-fix button
    await expect(row.getByTestId("entity-autofix-btn")).not.toBeVisible();
  });

  // ── 12. Auto-fix re-runs the AI check for the entity ──────────────

  test("auto-fix re-runs AI verification for that entity (override_cache) and updates the verdict", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-autofix-btn").click();

    // A single-entity re-check fires with override_cache=true.
    await expect.poll(() => state.verifyStreamCalls.length).toBeGreaterThan(0);
    const call = state.verifyStreamCalls.find((c) => {
      const ids = (c as { entity_ids?: string[] }).entity_ids;
      return Array.isArray(ids) && ids.includes("ent-fix");
    }) as { entity_ids: string[]; override_cache?: boolean } | undefined;
    expect(call).toBeDefined();
    expect(call!.entity_ids).toEqual(["ent-fix"]);
    expect(call!.override_cache).toBe(true);

    // After the re-check resolves and the table refetches, the entity's
    // verdict pill flips from "partly" to "pass".
    const fixedRow = page.getByTestId("entity-row").filter({ hasText: "יוסף בן יעקב" });
    await expect(fixedRow.locator('[data-verdict="pass"]')).toBeVisible({ timeout: 5000 });
  });

  test("drawer auto-fix also re-runs AI verification for the entity", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();
    await row.getByTestId("entity-view-source").click();
    const drawer = page.getByTestId("entity-detail-drawer");
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await drawer.getByTestId("detail-autofix-btn").click();

    await expect.poll(() => state.verifyStreamCalls.length).toBeGreaterThan(0);
    const call = state.verifyStreamCalls.find((c) => {
      const ids = (c as { entity_ids?: string[] }).entity_ids;
      return Array.isArray(ids) && ids.includes("ent-fix");
    }) as { entity_ids: string[]; override_cache?: boolean } | undefined;
    expect(call).toBeDefined();
    expect(call!.override_cache).toBe(true);
  });

  // ── LAYOUT REGRESSIONS (2026-06-08) ─────────────────────────────
  //
  // Before the fix the auto-fix button shared the actions cell (edit / view /
  // history) which had a fixed pixel width. Adding the button overflowed the
  // cell and misaligned the entire row grid.
  //
  // These two tests pin the contract: fix button lives in entity-fix-cell
  // (a dedicated grid column), NOT as a sibling of the edit button.

  test("auto-fix button lives in entity-fix-cell, not in the actions div", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const row = page.getByTestId("entity-row").filter({ hasText: "יוסף" }).last();

    // The button must be reachable through entity-fix-cell.
    await expect(row.getByTestId("entity-fix-cell").getByTestId("entity-autofix-btn")).toBeVisible();

    // The button must NOT share a parent element with entity-edit-btn
    // (they used to both be children of the same "flex gap-1" actions div).
    const hasSameParent = await page.evaluate(() => {
      const fixBtn  = document.querySelector('[data-testid="entity-autofix-btn"]');
      const editBtn = document.querySelector('[data-testid="entity-edit-btn"]');
      if (!fixBtn || !editBtn) return null;
      return fixBtn.parentElement === editBtn.parentElement;
    });
    expect(hasSameParent).toBe(false);
  });

  test("row gridTemplateColumns matches the header when the fix button is visible", async ({ page }) => {
    const state = makeStateWithFix();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    // Wait for the fixable row to actually render its fix button so we know
    // it's the layout variant we care about.
    await expect(page.getByTestId("entity-autofix-btn")).toBeVisible();

    const sameGrid = await page.evaluate(() => {
      // Walk up from a header cell to its grid container.
      const headerCell = document.querySelector('[data-testid="col-fix"]') as HTMLElement | null;
      const header = headerCell?.closest<HTMLElement>("[style]") ?? null;
      // The fixable row is the one that contains the fix button.
      const fixBtn = document.querySelector('[data-testid="entity-autofix-btn"]');
      const dataRow = fixBtn?.closest<HTMLElement>('[data-testid="entity-row"]') ?? null;
      if (!header || !dataRow) return null;
      return (
        getComputedStyle(header).gridTemplateColumns ===
        getComputedStyle(dataRow).gridTemplateColumns
      );
    });

    expect(sameGrid).toBe(true);
  });
});
