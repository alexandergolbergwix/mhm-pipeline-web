/**
 * E2E test fixtures + Playwright route mocking for the per-entity
 * history timeline (CLAUDE.md Wave 5 of the entity-versioning plan
 * at `plans/silly-prancing-quiche.md`).
 *
 * The HistoryTimeline component is mounted by the extraction-review
 * page (Round 4 wired the per-row `history-button-{entity_id}`).
 * These fixtures mock:
 *
 *   - GET  /api/auth/me
 *   - GET  /api/projects/{id}/history
 *   - GET  /api/projects/{id}/history/diff
 *   - POST /api/projects/{id}/history/revert
 *   - GET  /api/projects/{id}/history/snapshots
 *
 * Plus the minimum extraction-page mocks needed for the entity row
 * carrying `TEST_ENTITY_ID` to render so the history button is
 * clickable.
 *
 * Mutates `state.events` in place so spec assertions can verify
 * timeline refreshes after a revert call.
 */
import type { Page, Route } from "@playwright/test";

import {
  TEST_PROJECT_ID,
  TEST_RUN_ID as EXTRACTION_TEST_RUN_ID,
  installExtractionMocks,
  makeMockState as makeExtractionMockState,
  type MockEntity,
  type MockState as ExtractionMockState,
} from "./extraction-fixtures";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export {TEST_PROJECT_ID};

// Reuse the extraction-fixtures run id so the routes the SPA hits for
// the StageExtraction page resolve against the same mocks regardless
// of which fixture module installed them first.
export const TEST_RUN_ID = EXTRACTION_TEST_RUN_ID;

// The entity whose history we exercise. The extraction-page entity
// row carries `data-testid="history-button-{id}"`; the history API
// is hit with `entity_id={id}`. The row id MUST match across both.
export const TEST_ENTITY_ID = "extraction-entity-abc";

// ---------------------------------------------------------------------------
// EventRow + canonical timeline
// ---------------------------------------------------------------------------

export type EventOp = "create" | "patch" | "revert" | "snapshot";

export interface EventRow {
  id: string;
  rev_no: number;
  parent_event_id: string | null;
  op: EventOp;
  actor_email: string | null;
  message: string;
  created_at: string;
}

export interface SnapshotRow {
  bucket: string;
  slot: 0 | 1 | 2;
  rev_no: number;
  created_at: string;
}

let _eventCounter = 0;

function nextEventId(): string {
  _eventCounter += 1;
  return `event-${_eventCounter.toString().padStart(4, "0")}`;
}

export function makeEventRow(overrides: Partial<EventRow> = {}): EventRow {
  return {
    id: overrides.id ?? nextEventId(),
    rev_no: overrides.rev_no ?? 1,
    parent_event_id: overrides.parent_event_id ?? null,
    op: overrides.op ?? "patch",
    actor_email: overrides.actor_email ?? "admin@example.org",
    message: overrides.message ?? "patched approved",
    created_at: overrides.created_at ?? "2026-06-01T08:00:00Z",
  };
}

/**
 * Canonical 3-event timeline:
 *   rev 3 — patch (override_role="CENSOR")   ← newest first (server contract)
 *   rev 2 — patch (approved=true)
 *   rev 1 — create
 *
 * The server guarantees ORDER BY rev_no DESC; HistoryTimeline no
 * longer client-sorts, so the mock must mirror that contract.
 */
export function makeEventSet(): EventRow[] {
  return [
    makeEventRow({
      id: "event-rev-3",
      rev_no: 3,
      parent_event_id: "event-rev-2",
      op: "patch",
      actor_email: "admin@example.org",
      message: "override role to CENSOR",
      created_at: "2026-06-01T09:00:00Z",
    }),
    makeEventRow({
      id: "event-rev-2",
      rev_no: 2,
      parent_event_id: "event-rev-1",
      op: "patch",
      actor_email: "admin@example.org",
      message: "marked approved by curator",
      created_at: "2026-05-31T14:00:00Z",
    }),
    makeEventRow({
      id: "event-rev-1",
      rev_no: 1,
      parent_event_id: null,
      op: "create",
      actor_email: "admin@example.org",
      message: "created from MARC upload",
      created_at: "2026-05-30T10:00:00Z",
    }),
  ];
}

export function makeSnapshotSet(): SnapshotRow[] {
  return [
    {
      bucket: "2026-05-15",
      slot: 0,
      rev_no: 42,
      created_at: "2026-05-15T00:05:00Z",
    },
    {
      bucket: "2026-05-15",
      slot: 2,
      rev_no: 78,
      created_at: "2026-05-15T16:05:00Z",
    },
  ];
}

// ---------------------------------------------------------------------------
// Mock state — mutated by the revert endpoint
// ---------------------------------------------------------------------------

export interface HistoryMockState {
  events: EventRow[];
  snapshots: SnapshotRow[];
  diffCalls: { from_rev: number; to_rev: number }[];
  revertCalls: { target_rev: number; message: string | undefined }[];
  extraction: ExtractionMockState;
}

export function makeHistoryMockState(): HistoryMockState {
  const extraction = makeExtractionMockState();
  // Re-id the first entity to match TEST_ENTITY_ID so the
  // history button on its row resolves to the entity we're driving
  // the API mocks for. We mutate in place because the extraction
  // fixtures' route closures capture the array by reference.
  if (extraction.entities.length > 0) {
    extraction.entities[0] = {
      ...extraction.entities[0],
      id: TEST_ENTITY_ID,
    };
  }
  return {
    events: makeEventSet(),
    snapshots: makeSnapshotSet(),
    diffCalls: [],
    revertCalls: [],
    extraction,
  };
}

// ---------------------------------------------------------------------------
// Route installation
// ---------------------------------------------------------------------------

const HISTORY_BASE = `**/api/projects/${TEST_PROJECT_ID}/history`;

export async function installHistoryMocks(
  page: Page,
  state: HistoryMockState,
): Promise<void> {
  // The extraction page is the host for the history button; install
  // its full mock surface first so the entity row renders.
  await installExtractionMocks(page, state.extraction);

  // ── /auth/me — guarantee an admin-shaped session ───────────────
  // The extraction fixtures already install /auth/me with a
  // generic user. Re-register so admin scenarios that check actor
  // labels see the right email. Playwright resolves the LAST
  // matching route first, so this overrides the extraction one.
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "44444444-4444-4444-4444-444444444444",
        email: "admin@example.org",
        name: "Admin User",
        role: "admin",
      }),
    });
  });

  // ── GET /projects/{id}/history?entity_type=…&entity_id=… ────────
  // Playwright matches routes in reverse registration order, so the
  // generic timeline handler is registered FIRST and the specific
  // /diff, /revert, /snapshots handlers LAST (checked first).

  // ── GET /history (the timeline) — register before /diff ─────────
  await page.route(`${HISTORY_BASE}*`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const url = new URL(route.request().url());
    const pathTail = url.pathname.slice(url.pathname.indexOf("/history"));
    if (
      pathTail.startsWith("/history/diff") ||
      pathTail.startsWith("/history/revert") ||
      pathTail.startsWith("/history/snapshots") ||
      pathTail.startsWith("/history/at")
    ) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state.events),
    });
  });

  // ── GET /history/diff ──────────────────────────────────────────
  await page.route(`${HISTORY_BASE}/diff*`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const url = new URL(route.request().url());
    const fromRev = Number(url.searchParams.get("from") ?? "0");
    const toRev = Number(url.searchParams.get("to") ?? "0");
    state.diffCalls.push({ from_rev: fromRev, to_rev: toRev });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        patch: [
          { op: "replace", path: "/approved", value: true },
        ],
        before: { approved: false },
        after: { approved: true },
      }),
    });
  });

  // ── POST /history/revert ───────────────────────────────────────
  await page.route(`${HISTORY_BASE}/revert`, async (route: Route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    const body = route.request().postDataJSON() as {
      entity_type: string;
      entity_id: string;
      target_rev: number;
      message?: string;
    };
    state.revertCalls.push({
      target_rev: body.target_rev,
      message: body.message,
    });
    const maxRev = state.events.reduce(
      (acc, e) => (e.rev_no > acc ? e.rev_no : acc),
      0,
    );
    const newRevNo = maxRev + 1;
    const newEventId = `event-revert-${newRevNo}`;
    // Prepend a synthetic revert event so a subsequent timeline
    // GET reflects the new row at the front (newest-first contract).
    state.events.unshift(
      makeEventRow({
        id: newEventId,
        rev_no: newRevNo,
        parent_event_id: `event-rev-${maxRev}`,
        op: "revert",
        actor_email: "admin@example.org",
        message:
          body.message && body.message.trim().length > 0
            ? body.message
            : `Restoring rev #${body.target_rev}`,
        created_at: new Date().toISOString(),
      }),
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        new_event_id: newEventId,
        new_rev_no: newRevNo,
      }),
    });
  });

  // ── GET /history/snapshots ─────────────────────────────────────
  await page.route(`${HISTORY_BASE}/snapshots*`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state.snapshots),
    });
  });

}

// ---------------------------------------------------------------------------
// Navigation helper
// ---------------------------------------------------------------------------

/**
 * Drive the SPA to the extraction-review page where the
 * per-entity `history-button-{id}` lives, then click the button so
 * `<HistoryTimeline>` is mounted in the side drawer.
 *
 * Callers are responsible for asserting on `data-testid="history-timeline"`
 * visibility after this returns.
 */
export async function gotoTimeline(page: Page): Promise<void> {
  await page.context()
    .addCookies([
      {
        name: "session",
        value: "test-session",
        url: "http://localhost:5173",
      },
    ])
    .catch(() => {
      // Cookie injection can fail before a navigation; the
      // /auth/me mock returns a logged-in user regardless.
    });

  await page.goto(`/runs/${TEST_RUN_ID}/extraction`);
  // Wait for the entity-review table to render before clicking
  // the history button so the row is in the DOM.
  await page.getByTestId("entity-review").waitFor({ timeout: 8000 });
  await page.getByTestId(`history-button-${TEST_ENTITY_ID}`).click();
  await page.getByTestId("history-timeline").waitFor({ timeout: 8000 });
}

// Re-export so specs only need one import.
export type { MockEntity };
