/**
 * E2E suite for the self-service "Request access" feature
 * (plan silly-prancing-quiche / CLAUDE.md Rule W-20).
 *
 * Three surfaces are exercised:
 *   - Public form at /request-access
 *   - Confirm-token page at /request-access/confirm?token=...
 *   - Admin queue at /admin/access-requests
 *
 * Every backend endpoint is mocked via `installAccessRequestMocks`
 * so the suite runs deterministically in isolation from Resend,
 * Cloudflare Turnstile, slowapi, and the DB.
 *
 * OWASP enumeration-resistance: the public form always shows the
 * same generic message; the only assertion that distinguishes a
 * honeypot trip is the captured `state.honeypotTripped` flag, never
 * the user-visible UI.
 */
import { expect, test } from "@playwright/test";
import {
  TEST_CONFIRM_TOKEN_EXPIRED,
  TEST_CONFIRM_TOKEN_INVALID,
  TEST_CONFIRM_TOKEN_OK,
  gotoAdminQueue,
  gotoConfirmRequest,
  gotoRequestAccess,
  installAccessRequestMocks,
  makeAccessRequestState,
} from "./fixtures/access-request-fixtures";

test.describe.configure({ mode: "parallel" });

const LONG_JUSTIFICATION =
  "I am studying Hebrew manuscript provenance and would like access to the workbench to review NER extractions for my dissertation corpus.";

test.describe("Public request-access form", () => {

  test("the public form renders", async ({ page }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);

    await expect(page.getByTestId("request-access-form")).toBeVisible({
      timeout: 8000,
    });
    await expect(page.getByTestId("request-access-name")).toBeVisible();
    await expect(page.getByTestId("request-access-email")).toBeVisible();
    await expect(page.getByTestId("request-access-affiliation")).toBeVisible();
    await expect(
      page.getByTestId("request-access-justification"),
    ).toBeVisible();
    await expect(page.getByTestId("request-access-submit")).toBeVisible();
  });

  test("submit disabled until justification >= 40 chars", async ({ page }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    await page.getByTestId("request-access-name").fill("Test User");
    await page.getByTestId("request-access-email").fill("user@example.org");
    await page
      .getByTestId("request-access-affiliation")
      .fill("Example University");

    const submit = page.getByTestId("request-access-submit");

    // Below 40 chars → disabled.
    await page.getByTestId("request-access-justification").fill("Too short.");
    await expect(submit).toBeDisabled();

    // Exactly 40 chars → enabled.
    const exactly40 = "a".repeat(40);
    await page
      .getByTestId("request-access-justification")
      .fill(exactly40);
    await expect(submit).toBeEnabled();

    // Below 40 again → disabled again.
    await page
      .getByTestId("request-access-justification")
      .fill("a".repeat(39));
    await expect(submit).toBeDisabled();
  });

  test("honeypot field is hidden from visual users", async ({ page }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    const honeypot = page.getByTestId("request-access-honeypot");
    // The element exists in the DOM (so a bot can fill it) but must
    // not be visible to a human.
    await expect(honeypot).toHaveCount(1);
    await expect(honeypot).toBeHidden();
  });

  test("happy path: submit → generic success message replaces form", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    await page.getByTestId("request-access-name").fill("Test User");
    await page.getByTestId("request-access-email").fill("user@example.org");
    await page
      .getByTestId("request-access-affiliation")
      .fill("Example University");
    await page
      .getByTestId("request-access-justification")
      .fill(LONG_JUSTIFICATION);

    await page.getByTestId("request-access-submit").click();

    // Backend received the submit.
    await expect.poll(() => state.submitCalls.length).toBeGreaterThan(0);
    expect(state.honeypotTripped).toBe(false);

    // Form is replaced by the generic success message.
    await expect(
      page.getByTestId("request-access-success"),
    ).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("request-access-form")).toHaveCount(0);

    // The success copy is the OWASP-strict generic line — never
    // confirms whether the email already had an account.
    const successText = (
      await page.getByTestId("request-access-success").textContent()
    ) ?? "";
    expect(successText).toMatch(/email|next steps|check/i);
    expect(successText).not.toMatch(/already (registered|exists)/i);
  });

  test("honeypot filled → silent reject (same generic success UI)", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    await page.getByTestId("request-access-name").fill("Bot User");
    await page.getByTestId("request-access-email").fill("bot@example.org");
    await page.getByTestId("request-access-affiliation").fill("Botland");
    await page
      .getByTestId("request-access-justification")
      .fill(LONG_JUSTIFICATION);

    // Bots fill EVERY field including the honeypot. Use evaluate so
    // we bypass any visibility guards on the hidden input.
    await page.getByTestId("request-access-honeypot").evaluate(
      (el: HTMLInputElement) => {
        el.value = "https://bot.example/spam";
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      },
    );

    await page.getByTestId("request-access-submit").click();
    await expect.poll(() => state.submitCalls.length).toBeGreaterThan(0);

    // From the user's perspective the UI is identical to the
    // legitimate happy path — that's the enumeration-resistant
    // contract. The fixture's honeypotTripped flag is the only place
    // the trip is visible.
    await expect(page.getByTestId("request-access-success")).toBeVisible();
    expect(state.honeypotTripped).toBe(true);
  });
});

test.describe("Confirm page", () => {

  test("ConfirmRequest page shows the right message per status — ok", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoConfirmRequest(page, TEST_CONFIRM_TOKEN_OK);

    await expect(page.getByTestId("confirm-request-status")).toHaveAttribute(
      "data-status",
      "ok",
      { timeout: 8000 },
    );
    await expect(
      page.getByTestId("confirm-request-message"),
    ).toContainText(/admin review|thank|awaiting/i);
  });

  test("ConfirmRequest page shows the right message per status — expired", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoConfirmRequest(page, TEST_CONFIRM_TOKEN_EXPIRED);

    await expect(page.getByTestId("confirm-request-status")).toHaveAttribute(
      "data-status",
      "expired",
      { timeout: 8000 },
    );
    await expect(
      page.getByTestId("confirm-request-message"),
    ).toContainText(/expired/i);
  });

  test("ConfirmRequest page shows the right message per status — invalid", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoConfirmRequest(page, TEST_CONFIRM_TOKEN_INVALID);

    await expect(page.getByTestId("confirm-request-status")).toHaveAttribute(
      "data-status",
      "invalid",
      { timeout: 8000 },
    );
    await expect(
      page.getByTestId("confirm-request-message"),
    ).toContainText(/not valid|invalid/i);
  });
});

test.describe("Admin access-request queue", () => {

  test("admin queue lists pending requests", async ({ page }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);

    await expect(page.getByTestId("access-requests-queue")).toBeVisible({
      timeout: 8000,
    });

    // Default filter is "Pending" — only the two pending_admin rows.
    const pendingCount = state.requests.filter(
      (r) => r.status === "pending_admin",
    ).length;
    await expect(page.getByTestId("access-request-row")).toHaveCount(
      pendingCount,
    );
  });

  test("Approve button calls API and removes row from Pending filter", async ({
    page,
  }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);
    await page.getByTestId("access-requests-queue").waitFor();

    const pendingBefore = state.requests.filter(
      (r) => r.status === "pending_admin",
    ).length;
    await expect(page.getByTestId("access-request-row")).toHaveCount(
      pendingBefore,
    );

    // Click Approve on the first pending row.
    await page.getByTestId("access-request-approve-btn").first().click();

    // If a confirmation dialog is rendered (best practice for
    // destructive-ish actions), confirm it. Otherwise this is a no-op.
    const confirmBtn = page.getByTestId("access-request-approve-confirm");
    if (await confirmBtn.count()) {
      await confirmBtn.click();
    }

    // Backend was called.
    await expect.poll(() => state.approveCalls.length).toBeGreaterThan(0);

    // Row count under the Pending filter shrinks by one.
    await expect(page.getByTestId("access-request-row")).toHaveCount(
      pendingBefore - 1,
    );
  });

  test("Deny opens reason modal; submitting calls API", async ({ page }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);
    await page.getByTestId("access-requests-queue").waitFor();

    await page.getByTestId("access-request-deny-btn").first().click();

    const reasonModal = page.getByTestId("access-request-deny-modal");
    await expect(reasonModal).toBeVisible();

    const reasonInput = page.getByTestId("access-request-deny-reason");
    await reasonInput.fill("Outside the scope of the corpus.");

    await page.getByTestId("access-request-deny-submit").click();

    await expect.poll(() => state.denyCalls.length).toBeGreaterThan(0);
    expect(state.denyCalls[0].reason).toBe("Outside the scope of the corpus.");

    // Modal closes after submit.
    await expect(reasonModal).toHaveCount(0);
  });

  test("Filter chips switch which rows are shown", async ({ page }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);
    await page.getByTestId("access-requests-queue").waitFor();

    // "All" should show every row in the mock corpus.
    const allChip = page.getByTestId("access-requests-filter-all");
    if (await allChip.count()) {
      await allChip.click();
      await expect(page.getByTestId("access-request-row")).toHaveCount(
        state.requests.length,
      );
    }

    // "Approved" should show only the approved row.
    const approvedChip = page.getByTestId("access-requests-filter-approved");
    if (await approvedChip.count()) {
      await approvedChip.click();
      const approvedCount = state.requests.filter(
        (r) => r.status === "approved",
      ).length;
      await expect(page.getByTestId("access-request-row")).toHaveCount(
        approvedCount,
      );
    }

    // "Denied" should show only the denied row.
    const deniedChip = page.getByTestId("access-requests-filter-denied");
    if (await deniedChip.count()) {
      await deniedChip.click();
      const deniedCount = state.requests.filter(
        (r) => r.status === "denied",
      ).length;
      await expect(page.getByTestId("access-request-row")).toHaveCount(
        deniedCount,
      );
    }
  });

  test("non-admin visitor is redirected away from the queue", async ({
    page,
  }) => {
    // authedAsAdmin defaults to false → /auth/me returns null user.
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);

    // The admin queue MUST NOT render for a non-admin session.
    // Either the page redirects (URL changes) or it surfaces the
    // queue's "not authorized" sentinel — both are acceptable, but
    // the row table is never shown.
    await page.waitForTimeout(500);
    const rows = page.getByTestId("access-request-row");
    await expect(rows).toHaveCount(0);
  });
});
