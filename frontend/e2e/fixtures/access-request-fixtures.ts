/**
 * E2E test fixtures + Playwright route mocking for the public
 * "Request access" + admin queue surface (CLAUDE.md Rule W-20 /
 * plan silly-prancing-quiche).
 *
 * Every backend endpoint the pages touch is mocked deterministically
 * so the suite runs in isolation from Resend, Turnstile, and the DB.
 * State mutates in place so tests can assert on captured side-effects
 * (honeypot tripping, status flips, denial reasons).
 */
import type { Page, Route } from "@playwright/test";

export const TEST_ADMIN_ID = "44444444-4444-4444-4444-444444444444";
export const TEST_CONFIRM_TOKEN_OK = "tok-confirm-ok";
export const TEST_CONFIRM_TOKEN_EXPIRED = "tok-confirm-expired";
export const TEST_CONFIRM_TOKEN_INVALID = "tok-confirm-invalid";

/** A row in the admin queue. Mirrors AccessRequestListItem on the wire. */
export interface MockAccessRequest {
  id: string;
  email: string;
  name: string;
  affiliation: string;
  justification: string;
  status: "pending_email_confirm" | "pending_admin" | "approved" | "denied";
  created_at: string;
  confirmed_at: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
  denial_reason: string | null;
}

export function makeRequestRow(
  overrides: Partial<MockAccessRequest> = {},
): MockAccessRequest {
  return {
    id: overrides.id ?? "req-1",
    email: overrides.email ?? "researcher@example.org",
    name: overrides.name ?? "Researcher One",
    affiliation: overrides.affiliation ?? "Example University",
    justification:
      overrides.justification ??
      "I am studying Hebrew manuscript provenance and would like access to the workbench to review and approve NER extractions for my dissertation corpus.",
    status: overrides.status ?? "pending_admin",
    created_at: overrides.created_at ?? "2026-06-01T08:00:00Z",
    confirmed_at: overrides.confirmed_at ?? "2026-06-01T08:05:00Z",
    reviewed_at: overrides.reviewed_at ?? null,
    reviewed_by: overrides.reviewed_by ?? null,
    denial_reason: overrides.denial_reason ?? null,
  };
}

export function makeRequestSet(): MockAccessRequest[] {
  return [
    makeRequestRow({
      id: "req-1",
      email: "alice@example.org",
      name: "Alice Researcher",
      affiliation: "University A",
      status: "pending_admin",
    }),
    makeRequestRow({
      id: "req-2",
      email: "bob@example.org",
      name: "Bob Scholar",
      affiliation: "Institute B",
      status: "pending_admin",
    }),
    makeRequestRow({
      id: "req-3",
      email: "carol@example.org",
      name: "Carol Curator",
      affiliation: "Library C",
      status: "approved",
      reviewed_at: "2026-05-30T10:00:00Z",
      reviewed_by: TEST_ADMIN_ID,
    }),
    makeRequestRow({
      id: "req-4",
      email: "dave@example.org",
      name: "Dave Doctoral",
      affiliation: "School D",
      status: "denied",
      reviewed_at: "2026-05-29T14:00:00Z",
      reviewed_by: TEST_ADMIN_ID,
      denial_reason: "Outside the scope of the corpus.",
    }),
  ];
}

/** Outcome the mock returns from the confirm endpoint. */
export type ConfirmOutcome =
  | { status: "ok"; message?: string }
  | { status: "expired"; message?: string }
  | { status: "invalid"; message?: string }
  | { status: "already_confirmed"; message?: string };

export interface MockAccessRequestState {
  /** When true, /api/auth/me returns a logged-in admin. */
  authedAsAdmin: boolean;
  /** Rows visible from the admin queue. */
  requests: MockAccessRequest[];
  /** Per-token outcome returned by GET /confirm/{token}. */
  confirmOutcomes: Record<string, ConfirmOutcome>;
  /** Captured side effects for assertions. */
  submitCalls: Record<string, unknown>[];
  honeypotTripped: boolean;
  approveCalls: { id: string }[];
  denyCalls: { id: string; reason: string }[];
}

export function makeAccessRequestState(
  overrides: Partial<MockAccessRequestState> = {},
): MockAccessRequestState {
  return {
    authedAsAdmin: overrides.authedAsAdmin ?? false,
    requests: overrides.requests ?? makeRequestSet(),
    confirmOutcomes:
      overrides.confirmOutcomes ?? {
        [TEST_CONFIRM_TOKEN_OK]: {
          status: "ok",
          message: "Thank you. Your request is now awaiting admin review.",
        },
        [TEST_CONFIRM_TOKEN_EXPIRED]: {
          status: "expired",
          message: "This confirmation link has expired. Please submit a new request.",
        },
        [TEST_CONFIRM_TOKEN_INVALID]: {
          status: "invalid",
          message: "This confirmation link is not valid.",
        },
      },
    submitCalls: overrides.submitCalls ?? [],
    honeypotTripped: overrides.honeypotTripped ?? false,
    approveCalls: overrides.approveCalls ?? [],
    denyCalls: overrides.denyCalls ?? [],
  };
}

const GENERIC_SUBMIT_BODY = {
  message:
    "If your email is eligible, you'll receive next steps shortly.",
};

/**
 * Install Playwright route handlers that mock every backend endpoint
 * the public form, the confirm page, and the admin queue touch.
 * Mutates `state` in place so test assertions can inspect captured
 * calls + emitted side-effects.
 */
export async function installAccessRequestMocks(
  page: Page,
  state: MockAccessRequestState,
): Promise<void> {
  // ── /auth/me — drives the SPA's "am I an admin?" check ──────────
  await page.route("**/api/auth/me", async (route: Route) => {
    if (state.authedAsAdmin) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user: {
            id: TEST_ADMIN_ID,
            email: "admin@example.org",
            name: "Test Admin",
            role: "admin",
          },
        }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: null }),
      });
    }
  });

  // ── POST /api/access-request — public submit ────────────────────
  // OWASP-strict: ALWAYS returns 202 with the same generic body,
  // regardless of whether the email already has an account, the
  // honeypot is tripped, or anything else. The honeypot trip is
  // captured on `state.honeypotTripped` so tests can assert on it,
  // but the HTTP response is indistinguishable.
  await page.route("**/api/access-request", async (route: Route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    state.submitCalls.push(body);
    const honeypot = typeof body.website === "string" ? body.website : "";
    if (honeypot.trim() !== "") {
      state.honeypotTripped = true;
    }
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify(GENERIC_SUBMIT_BODY),
    });
  });

  // ── GET /api/access-request/confirm/{token} ─────────────────────
  await page.route(
    /\/api\/access-request\/confirm\/[^/?#]+/,
    async (route: Route) => {
      const url = new URL(route.request().url());
      const m = url.pathname.match(/\/confirm\/([^/]+)$/);
      const token = m ? decodeURIComponent(m[1]) : "";
      const outcome: ConfirmOutcome =
        state.confirmOutcomes[token] ?? {
          status: "invalid",
          message: "This confirmation link is not valid.",
        };
      const httpStatus =
        outcome.status === "ok" || outcome.status === "already_confirmed"
          ? 200
          : outcome.status === "expired"
          ? 410
          : 404;
      await route.fulfill({
        status: httpStatus,
        contentType: "application/json",
        body: JSON.stringify({
          status: outcome.status,
          message: outcome.message ?? "",
        }),
      });
    },
  );

  // ── GET /api/admin/access-requests — list ───────────────────────
  await page.route(
    "**/api/admin/access-requests",
    async (route: Route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      if (!state.authedAsAdmin) {
        await route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({ detail: "not authenticated" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ requests: state.requests }),
      });
    },
  );

  // ── GET /api/admin/access-requests/{id} — detail (optional) ─────
  await page.route(
    /\/api\/admin\/access-requests\/[^/?#]+(?:\?.*)?$/,
    async (route: Route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      const url = new URL(route.request().url());
      const m = url.pathname.match(/\/access-requests\/([^/]+)$/);
      if (!m) {
        await route.continue();
        return;
      }
      const id = decodeURIComponent(m[1]);
      const row = state.requests.find((r) => r.id === id);
      if (!row) {
        await route.fulfill({ status: 404, body: '{"detail":"not found"}' });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(row),
      });
    },
  );

  // ── POST /api/admin/access-requests/{id}/approve ────────────────
  await page.route(
    /\/api\/admin\/access-requests\/[^/?#]+\/approve(?:\?.*)?$/,
    async (route: Route) => {
      const url = new URL(route.request().url());
      const m = url.pathname.match(/\/access-requests\/([^/]+)\/approve$/);
      if (!m) {
        await route.continue();
        return;
      }
      const id = decodeURIComponent(m[1]);
      state.approveCalls.push({ id });
      const row = state.requests.find((r) => r.id === id);
      if (row) {
        row.status = "approved";
        row.reviewed_at = new Date().toISOString();
        row.reviewed_by = TEST_ADMIN_ID;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    },
  );

  // ── POST /api/admin/access-requests/{id}/deny ───────────────────
  await page.route(
    /\/api\/admin\/access-requests\/[^/?#]+\/deny(?:\?.*)?$/,
    async (route: Route) => {
      const url = new URL(route.request().url());
      const m = url.pathname.match(/\/access-requests\/([^/]+)\/deny$/);
      if (!m) {
        await route.continue();
        return;
      }
      const id = decodeURIComponent(m[1]);
      const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
      const reason = typeof body.reason === "string" ? body.reason : "";
      state.denyCalls.push({ id, reason });
      const row = state.requests.find((r) => r.id === id);
      if (row) {
        row.status = "denied";
        row.reviewed_at = new Date().toISOString();
        row.reviewed_by = TEST_ADMIN_ID;
        row.denial_reason = reason;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    },
  );

  // ── /api/config — Turnstile site key (public) ───────────────────
  // The public form reads the Turnstile site key from the app's
  // public config endpoint. Returning an empty key tells the widget
  // to short-circuit / render a stub — the actual Turnstile script
  // is never loaded under test.
  await page.route("**/api/config", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        turnstile_site_key: "",
        request_access_enabled: true,
      }),
    });
  });

  // ── Block the real Turnstile script from loading ────────────────
  await page.route(
    /challenges\.cloudflare\.com\/turnstile\/.*/,
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: "/* turnstile stubbed for e2e */",
      });
    },
  );
}

/**
 * Drives the SPA to the public "Request access" form.
 * The form is unauthenticated, so no session cookie is injected
 * unless the test explicitly opted in via `state.authedAsAdmin`.
 */
export async function gotoRequestAccess(page: Page): Promise<void> {
  await page.goto("/request-access");
}

/**
 * Drives the SPA to the admin queue. Injects a session cookie so
 * the SPA's gate is satisfied; the /auth/me mock decides whether the
 * cookie maps to an admin or not (driven by `state.authedAsAdmin`).
 */
export async function gotoAdminQueue(page: Page): Promise<void> {
  await page.context().addCookies([
    {
      name: "session",
      value: "test-admin-session",
      url: "http://localhost:5173",
    },
  ]).catch(() => {
    // Cookie injection can fail on some Playwright versions when no
    // page has been navigated yet — the /auth/me mock still drives
    // the gate so this is best-effort.
  });
  await page.goto("/admin/access-requests");
}

/** Navigate to the confirm page with a specific token. */
export async function gotoConfirmRequest(
  page: Page,
  token: string,
): Promise<void> {
  await page.goto(`/request-access/confirm?token=${encodeURIComponent(token)}`);
}
