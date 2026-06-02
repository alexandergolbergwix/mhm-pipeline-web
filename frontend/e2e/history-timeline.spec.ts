/**
 * E2E suite for `<HistoryTimeline>` + `<HistoryDiffModal>`
 * (CLAUDE.md plan `silly-prancing-quiche.md`, Wave 5 — frontend
 * surface for the per-entity event-sourced audit log).
 *
 * Every backend endpoint is mocked through `installHistoryMocks` so
 * the suite is deterministic and runs in isolation from the DB or
 * the backend's versioning core.
 *
 * Scenarios covered (7):
 *   1. timeline renders newest-first with all event metadata
 *   2. selecting two rows enables the Diff button
 *   3. Diff modal renders patch + before + after
 *   4. Revert button opens confirm dialog
 *   5. Revert API call appends a new revert event to the timeline
 *   6. "Older than 1000 events" loads archive-tier snapshots
 *   7. Diff modal closes on Escape
 */
import { expect, test } from "@playwright/test";

import {
  TEST_ENTITY_ID,
  gotoTimeline,
  installHistoryMocks,
  makeHistoryMockState,
} from "./fixtures/history-fixtures";

test.describe.configure({ mode: "parallel" });

test.describe("HistoryTimeline + HistoryDiffModal", () => {
  test("timeline renders newest-first with all event metadata", async ({
    page,
  }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    // Three rows visible.
    const rows = page.getByTestId(/^event-row-\d+$/);
    await expect(rows).toHaveCount(3, { timeout: 8000 });

    // Newest-first: the first DOM row should be rev #3.
    const firstRow = rows.first();
    await expect(firstRow).toHaveAttribute("data-rev-no", "3");
    await expect(firstRow).toHaveAttribute("data-op", "patch");

    // Every row carries op pill, actor email, and message text.
    await expect(firstRow).toContainText("rev #3");
    await expect(firstRow).toContainText("patch");
    await expect(firstRow).toContainText("admin@example.org");
    await expect(firstRow).toContainText("override role to CENSOR");

    // The create row (rev #1) sits at the bottom.
    const createRow = page.getByTestId("event-row-1");
    await expect(createRow).toHaveAttribute("data-op", "create");
    await expect(createRow).toContainText("create");
    await expect(createRow).toContainText("created from MARC upload");
  });

  test("selecting two rows enables the Diff button", async ({ page }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    const diffButton = page.getByTestId("diff-button");
    await expect(diffButton).toBeDisabled();

    await page.getByTestId("event-checkbox-2").check();
    await expect(diffButton).toBeDisabled();

    await page.getByTestId("event-checkbox-3").check();
    await expect(diffButton).toBeEnabled();
  });

  test("Diff modal renders patch + before + after", async ({ page }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    await page.getByTestId("event-checkbox-2").check();
    await page.getByTestId("event-checkbox-3").check();
    await page.getByTestId("diff-button").click();

    const modal = page.getByTestId("diff-modal");
    await expect(modal).toBeVisible({ timeout: 8000 });

    // Diff endpoint was called with from=2, to=3 (sorted ascending).
    await expect
      .poll(() => state.diffCalls.length, { timeout: 5000 })
      .toBeGreaterThan(0);
    const call = state.diffCalls[state.diffCalls.length - 1];
    expect(call.from_rev).toBe(2);
    expect(call.to_rev).toBe(3);

    // Patch list visible + carries the replace op.
    const patchList = page.getByTestId("diff-patch-list");
    await expect(patchList).toBeVisible();
    await expect(patchList).toContainText("replace");
    await expect(patchList).toContainText("/approved");

    // before + after sections both rendered.
    await expect(page.getByTestId("diff-before")).toBeVisible();
    await expect(page.getByTestId("diff-after")).toBeVisible();
  });

  test("Revert button opens confirm dialog", async ({ page }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    // Rev #2 is a patch op → has a revert button.
    await page.getByTestId("revert-button-2").click();

    const dialog = page.getByTestId("revert-confirm-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("data-target-rev", "2");
    await expect(dialog).toContainText("Revert to rev #2");
  });

  test("Revert API call appends a new revert event to the timeline", async ({
    page,
  }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    // Sanity: 3 rows before the revert.
    await expect(page.getByTestId(/^event-row-\d+$/)).toHaveCount(3);

    await page.getByTestId("revert-button-2").click();
    await page.getByTestId("revert-confirm-dialog").waitFor();

    await page
      .getByTestId("revert-message")
      .fill("undo accidental approval");
    await page.getByTestId("revert-submit").click();

    // The revert call landed on the mock.
    await expect
      .poll(() => state.revertCalls.length, { timeout: 5000 })
      .toBeGreaterThan(0);
    const call = state.revertCalls[state.revertCalls.length - 1];
    expect(call.target_rev).toBe(2);
    expect(call.message).toBe("undo accidental approval");

    // Dialog closes after a successful revert.
    await expect(page.getByTestId("revert-confirm-dialog")).toHaveCount(0);

    // The timeline refreshes — 4 rows now, with the new revert row
    // (rev #4) at the top.
    const rows = page.getByTestId(/^event-row-\d+$/);
    await expect(rows).toHaveCount(4, { timeout: 8000 });
    const top = rows.first();
    await expect(top).toHaveAttribute("data-rev-no", "4");
    await expect(top).toHaveAttribute("data-op", "revert");
    await expect(top).toContainText("revert");
  });

  test("'Older than 1000 events' link loads archive-tier snapshots", async ({
    page,
  }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    await page.getByTestId("snapshot-link").click();

    // Both snapshot rows from the canonical set appear with reduced
    // opacity (HistoryTimeline applies `opacity-60`).
    const row42 = page.getByTestId("snapshot-row-42");
    const row78 = page.getByTestId("snapshot-row-78");
    await expect(row42).toBeVisible({ timeout: 5000 });
    await expect(row78).toBeVisible();

    // The link is replaced by the "Archive loaded" marker.
    await expect(page.getByTestId("snapshot-loaded")).toBeVisible();
  });

  test("Diff modal closes on Escape", async ({ page }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    await page.getByTestId("event-checkbox-2").check();
    await page.getByTestId("event-checkbox-3").check();
    await page.getByTestId("diff-button").click();

    await page.getByTestId("diff-modal").waitFor({ timeout: 8000 });

    // Use the body so the keypress isn't swallowed by a focused
    // child element with its own handler.
    await page.keyboard.press("Escape");

    await expect(page.getByTestId("diff-modal")).toHaveCount(0);
  });

  // Sanity check kept alongside the 7 scenarios: TEST_ENTITY_ID is
  // the contract between the extraction page and the history mocks.
  test("history button on the entity row routes to TEST_ENTITY_ID", async ({
    page,
  }) => {
    const state = makeHistoryMockState();
    await installHistoryMocks(page, state);
    await gotoTimeline(page);

    const timeline = page.getByTestId("history-timeline");
    await expect(timeline).toHaveAttribute("data-entity-id", TEST_ENTITY_ID);
    await expect(timeline).toHaveAttribute(
      "data-entity-type",
      "extraction_entity",
    );
  });
});
