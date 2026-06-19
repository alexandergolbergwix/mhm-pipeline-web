/**
 * E2E cache contract: user-scoped browser cache must not bleed across
 * sessions or serve truncated entity lists.
 */
import {expect, test} from "@playwright/test";

import {
  TEST_RUN_ID,
  installExtractionMocks,
  makeEntity,
  makeMockState,
  gotoExtraction,
} from "./fixtures/extraction-fixtures";

const USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

test.describe("AI Extraction browser cache", () => {
  test("stale partial localStorage for another user does not shrink the table", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);

    await page.addInitScript(({runId, userB}) => {
      const staleKey = `mhm:${userB}:extraction:entities:${runId}`;
      const payload = {
        value: {
          entities: [{id: "stale-only", control_number: "x", text: "solo"}],
          total: 5,
          approvedCount: 0,
          recordCount: 1,
          sourceCounts: {},
          fetchedAt: Date.now(),
        },
        expiresAt: Date.now() + 60_000,
      };
      localStorage.setItem(staleKey, JSON.stringify(payload));
      const legacyKey = `mhm:extraction:entities:${runId}`;
      localStorage.setItem(legacyKey, JSON.stringify(payload));
    }, {runId: TEST_RUN_ID, userB: USER_B});

    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();
    await expect(page.getByTestId("entity-row")).toHaveCount(state.entities.length, {timeout: 8000});
    await expect(page.getByTestId("count-total")).toHaveText(String(state.entities.length));
  });

  test("logout clears user-scoped extraction cache keys", async ({page}) => {
    const state = makeMockState({entityCount: 3});
    await installExtractionMocks(page, state);

    await page.route("**/api/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: USER_A,
          email: "curator@test.dev",
          name: "Curator",
          role: "editor",
        }),
      });
    });

    await gotoExtraction(page);
    await page.getByTestId("entity-review").waitFor();

    const keyBefore = await page.evaluate(({userId, runId}) => {
      const key = `mhm:${userId}:extraction:entities:${runId}`;
      localStorage.setItem(key, JSON.stringify({
        value: {entities: [], total: 0, approvedCount: 0, recordCount: 0, sourceCounts: {}, fetchedAt: Date.now()},
        expiresAt: Date.now() + 60_000,
      }));
      return key;
    }, {userId: USER_A, runId: TEST_RUN_ID});

    await page.route("**/api/auth/logout", async (route) => {
      await route.fulfill({status: 204, body: ""});
    });

    const signOut = page.getByRole("button", {name: /sign out/i});
    if (await signOut.isVisible()) {
      await signOut.click();
    } else {
      await page.evaluate((userId) => {
        const prefix = `mhm:${userId}:`;
        for (const k of Object.keys(localStorage)) {
          if (k.startsWith(prefix)) localStorage.removeItem(k);
        }
      }, USER_A);
    }

    const remaining = await page.evaluate((key) => localStorage.getItem(key), keyBefore);
    expect(remaining).toBeNull();
  });
});
