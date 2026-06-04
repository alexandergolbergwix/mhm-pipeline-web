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
  test.describe.configure({timeout: 60000});

  test("the public form renders", async ({ page }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);

    await expect(page.getByTestId("request-access-form")).toBeVisible({
      timeout: 8000,
    });
    await expect(page.getByTestId("input-name")).toBeVisible();
    await expect(page.getByTestId("input-email")).toBeVisible();
    await expect(page.getByTestId("input-affiliation")).toBeVisible();
    await expect(
      page.getByTestId("input-justification"),
    ).toBeVisible();
    await expect(page.getByTestId("submit-button")).toBeVisible();
  });

  test("submit disabled until justification >= 40 chars", async ({ page }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    await page.getByTestId("input-name").fill("Test User");
    await page.getByTestId("input-email").fill("user@example.org");
    await page
      .getByTestId("input-affiliation")
      .fill("Example University");

    const submit = page.getByTestId("submit-button");

    // Below 40 chars → disabled (justification too short).
    await page.getByTestId("input-justification").fill("Too short.");
    await expect(submit).toBeDisabled();

    // Simulate Turnstile success so only the char-count gate is tested.
    await page.evaluate(() => {
      window.onTurnstileSuccess?.("e2e-turnstile-token");
    });

    // Exactly 40 chars AND Turnstile passed → enabled.
    const exactly40 = "a".repeat(40);
    await page
      .getByTestId("input-justification")
      .fill(exactly40);
    await expect(submit).toBeEnabled();

    // Below 40 again → disabled again (Turnstile still satisfied).
    await page
      .getByTestId("input-justification")
      .fill("a".repeat(39));
    await expect(submit).toBeDisabled();
  });

  test("submit stays disabled until Turnstile fires even with valid justification", async ({ page }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    await page.getByTestId("input-name").fill("Test User");
    await page.getByTestId("input-email").fill("user@example.org");
    await page.getByTestId("input-affiliation").fill("Example University");
    await page.getByTestId("input-justification").fill("a".repeat(40));

    const submit = page.getByTestId("submit-button");
    // Turnstile not yet fired → disabled.
    await expect(submit).toBeDisabled();

    // Fire the Turnstile callback → enabled.
    await page.evaluate(() => {
      window.onTurnstileSuccess?.("e2e-turnstile-token");
    });
    await expect(submit).toBeEnabled();
  });

  test("honeypot field is hidden from visual users", async ({ page }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    const honeypot = page.getByTestId("input-website");
    // The element exists in the DOM (so a bot can fill it) but must
    // not be visible to a human.
    await expect(honeypot).toHaveCount(1);
    await expect(honeypot).not.toBeInViewport();
  });

  test("happy path: submit → generic success message replaces form", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoRequestAccess(page);
    await page.getByTestId("request-access-form").waitFor();

    await page.getByTestId("input-name").fill("Test User");
    await page.getByTestId("input-email").fill("user@example.org");
    await page
      .getByTestId("input-affiliation")
      .fill("Example University");
    await page
      .getByTestId("input-justification")
      .fill(LONG_JUSTIFICATION);

    await page.evaluate(() => {
      window.onTurnstileSuccess?.("e2e-turnstile-token");
    });
    const submit = page.getByTestId("submit-button");
    await expect(submit).toBeEnabled();
    await submit.click();

    // Backend received the submit.
    await expect.poll(() => state.submitCalls.length).toBeGreaterThan(0);
    expect(state.honeypotTripped).toBe(false);

    // Form is replaced by the generic success message.
    await expect(
      page.getByTestId("success-message"),
    ).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("request-access-form")).toHaveCount(0);

    // The success copy is the OWASP-strict generic line — never
    // confirms whether the email already had an account.
    const successText = (
      await page.getByTestId("success-message").textContent()
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

    await page.getByTestId("input-name").fill("Bot User");
    await page.getByTestId("input-email").fill("bot@example.org");
    await page.getByTestId("input-affiliation").fill("Botland");
    await page
      .getByTestId("input-justification")
      .fill(LONG_JUSTIFICATION);

    await page
      .getByTestId("input-website")
      .fill("https://bot.example/spam", { force: true });

    await page.evaluate(() => {
      window.onTurnstileSuccess?.("e2e-turnstile-token");
    });
    await page.getByTestId("submit-button").click();
    await expect.poll(() => state.submitCalls.length).toBeGreaterThan(0);

    // From the user's perspective the UI is identical to the
    // legitimate happy path — that's the enumeration-resistant
    // contract. The fixture's honeypotTripped flag is the only place
    // the trip is visible.
    await expect(page.getByTestId("success-message")).toBeVisible();
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

    await expect(page.getByTestId("confirm-status-confirmed")).toBeVisible({
      timeout: 8000,
    });
    await expect(page.getByTestId("confirm-message")).toContainText(
      /admin review|thank|awaiting/i,
    );
  });

  test("ConfirmRequest page shows the right message per status — expired", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoConfirmRequest(page, TEST_CONFIRM_TOKEN_EXPIRED);

    await expect(page.getByTestId("confirm-status-expired")).toBeVisible({
      timeout: 8000,
    });
    await expect(page.getByTestId("confirm-message")).toContainText(/expired/i);
  });

  test("ConfirmRequest page shows the right message per status — invalid", async ({
    page,
  }) => {
    const state = makeAccessRequestState();
    await installAccessRequestMocks(page, state);
    await gotoConfirmRequest(page, TEST_CONFIRM_TOKEN_INVALID);

    await expect(page.getByTestId("confirm-error")).toBeVisible({
      timeout: 8000,
    });
    await expect(page.getByTestId("confirm-error")).toContainText(
      /not valid|invalid|expired/i,
    );
  });
});

test.describe("Admin access-request queue", () => {

  test("admin queue lists pending requests", async ({ page }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);

    await expect(page.getByTestId("access-requests-page")).toBeVisible({
      timeout: 8000,
    });
    await page.getByTestId("filter-chip-pending_admin").click();

    const pendingCount = state.requests.filter(
      (r) => r.status === "pending_admin",
    ).length;
    await expect(page.locator('[data-testid^="request-row-"]')).toHaveCount(
      pendingCount,
    );
  });

  test("Approve button calls API and removes row from Pending filter", async ({
    page,
  }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);
    await page.getByTestId("access-requests-page").waitFor();
    await page.getByTestId("filter-chip-pending_admin").click();

    const pendingBefore = state.requests.filter(
      (r) => r.status === "pending_admin",
    ).length;
    await expect(page.locator('[data-testid^="request-row-"]')).toHaveCount(
      pendingBefore,
    );

    const firstPending = state.requests.find((r) => r.status === "pending_admin");
    expect(firstPending).toBeTruthy();
    await page.getByTestId(`approve-button-${firstPending!.id}`).click();

    await expect.poll(() => state.approveCalls.length).toBeGreaterThan(0);

    await expect(page.locator('[data-testid^="request-row-"]')).toHaveCount(
      pendingBefore - 1,
    );
  });

  test("Deny opens reason modal; submitting calls API", async ({ page }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);
    await page.getByTestId("access-requests-page").waitFor();

    const firstPending = state.requests.find((r) => r.status === "pending_admin");
    expect(firstPending).toBeTruthy();
    await page.getByTestId(`deny-button-${firstPending!.id}`).click();

    const reasonModal = page.getByTestId("deny-modal");
    await expect(reasonModal).toBeVisible();

    const reasonInput = page.getByTestId("deny-reason-input");
    await reasonInput.fill("Outside the scope of the corpus.");

    await page.getByTestId("deny-confirm-button").click();

    await expect.poll(() => state.denyCalls.length).toBeGreaterThan(0);
    expect(state.denyCalls[0].reason).toBe("Outside the scope of the corpus.");

    // Modal closes after submit.
    await expect(reasonModal).toHaveCount(0);
  });

  test("Filter chips switch which rows are shown", async ({ page }) => {
    const state = makeAccessRequestState({ authedAsAdmin: true });
    await installAccessRequestMocks(page, state);
    await gotoAdminQueue(page);
    await page.getByTestId("access-requests-page").waitFor();

    await page.getByTestId("filter-chip-all").click();
    await expect(page.locator('[data-testid^="request-row-"]')).toHaveCount(
      state.requests.length,
    );

    await page.getByTestId("filter-chip-approved").click();
    const approvedCount = state.requests.filter(
      (r) => r.status === "approved",
    ).length;
    await expect(page.locator('[data-testid^="request-row-"]')).toHaveCount(
      approvedCount,
    );

    await page.getByTestId("filter-chip-denied").click();
    const deniedCount = state.requests.filter(
      (r) => r.status === "denied",
    ).length;
    await expect(page.locator('[data-testid^="request-row-"]')).toHaveCount(
      deniedCount,
    );
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
    const rows = page.locator('[data-testid^="request-row-"]');
    await expect(rows).toHaveCount(0);
  });
});
