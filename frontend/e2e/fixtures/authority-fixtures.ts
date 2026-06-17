/**
 * E2E fixtures for the Authority Enrichment review surface (Rule W-31).
 * Mocks GET /runs/{id}, note-index, and auth — no DB dependency.
 */
import type {Page, Route} from "@playwright/test";
import {TEST_PROJECT_ID, TEST_RUN_ID} from "./extraction-fixtures";

export {TEST_RUN_ID, TEST_PROJECT_ID};

export interface MockAuthorityMatch {
  id: string;
  control_number: string;
  entity_text: string;
  entity_kind: string;
  role: string;
  matched_name: string;
  mazal_id: string;
  viaf_id: string;
  wikidata_qid: string;
  confidence: string;
  source: string;
  payload: Record<string, unknown>;
  approved: boolean;
  approved_by: string | null;
  approved_at: string | null;
  exists_in?: {status: string; fields?: string[]; note?: string} | null;
}

export interface AuthorityMockState {
  matches: MockAuthorityMatch[];
  noteIndex: Record<string, string>;
}

let matchSeq = 0;

export function makeMatch(
  overrides: Partial<MockAuthorityMatch> & Pick<MockAuthorityMatch, "control_number" | "entity_text">,
): MockAuthorityMatch {
  matchSeq += 1;
  const id = overrides.id ?? `match-${matchSeq}`;
  return {
    id,
    control_number: overrides.control_number,
    entity_text: overrides.entity_text,
    entity_kind: overrides.entity_kind ?? "person",
    role: overrides.role ?? "author",
    matched_name: overrides.matched_name ?? overrides.entity_text,
    mazal_id: overrides.mazal_id ?? "",
    viaf_id: overrides.viaf_id ?? "",
    wikidata_qid: overrides.wikidata_qid ?? "",
    confidence: overrides.confidence ?? "medium",
    source: overrides.source ?? "mazal",
    payload: overrides.payload ?? {sources: ["mazal"]},
    approved: overrides.approved ?? false,
    approved_by: overrides.approved_by ?? null,
    approved_at: overrides.approved_at ?? null,
    exists_in: overrides.exists_in ?? {status: "grounded", fields: ["100"], note: ""},
  };
}

export function makeAuthorityState(
  overrides: Partial<AuthorityMockState> = {},
): AuthorityMockState {
  const dupName = "אלוני, נחמיה";
  const cn = "MS-001";
  return {
    matches: overrides.matches ?? [
      makeMatch({
        id: "m-author",
        control_number: cn,
        entity_text: dupName,
        role: "author",
        entity_kind: "person",
        confidence: "high",
      }),
      makeMatch({
        id: "m-subject",
        control_number: cn,
        entity_text: dupName,
        role: "subject",
        entity_kind: "person",
        confidence: "low",
      }),
      makeMatch({
        id: "m-other",
        control_number: "MS-002",
        entity_text: "ירושלים",
        role: "production place",
        entity_kind: "place",
        confidence: "high",
      }),
    ],
    noteIndex: overrides.noteIndex ?? {
      [cn]: "כולל: שיר השירים. נכתב בקולופון בשנת תקנ״ה",
      "MS-002": "מקום ייצור: ירושלים",
    },
  };
}

export async function installAuthorityMocks(
  page: Page,
  state: AuthorityMockState,
) {
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "33333333-3333-3333-3333-333333333333",
        email: "test@example.org",
        name: "Test User",
        role: "editor",
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: TEST_RUN_ID,
        project_id: TEST_PROJECT_ID,
        name: "Authority test run",
        status: "succeeded",
        record_count: 2,
        match_count: state.matches.length,
        error: null,
        created_at: "2026-06-01T08:00:00Z",
        completed_at: "2026-06-01T09:00:00Z",
        matches: state.matches,
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/authority/note-index`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state.noteIndex),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/matches/**`, async (route: Route) => {
    if (route.request().method() === "PATCH") {
      const url = route.request().url();
      const id = url.split("/matches/")[1]?.split("?")[0]?.split("/")[0];
      const body = route.request().postDataJSON() as Record<string, unknown>;
      const m = state.matches.find((x) => x.id === id);
      if (m && typeof body.approved === "boolean") {
        m.approved = body.approved;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(m ?? {}),
      });
      return;
    }
    await route.continue();
  });
}

export async function gotoAuthority(page: Page) {
  await page.context().addCookies([
    {
      name: "session",
      value: "test-session",
      url: "http://localhost:5173",
    },
  ]).catch(() => {/* first navigation may be needed */});

  await page.goto(`/runs/${TEST_RUN_ID}`);
}
