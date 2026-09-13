/**
 * E2E suite for the Research Assistant (chat + canvas).
 * Backend is mocked — no running server needed.
 */
import {expect, test} from "@playwright/test";
import type {Page, Route} from "@playwright/test";

const TEST_RUN_ID = "22222222-2222-2222-2222-222222222222";
const TEST_PROJECT_ID = "44444444-4444-4444-4444-444444444444";
const THREAD_ID = "55555555-5555-5555-5555-555555555555";

async function installMocks(page: Page) {
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
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: TEST_RUN_ID,
        project_id: TEST_PROJECT_ID,
        name: "Test run",
        status: "succeeded",
        record_count: 68,
        match_count: 270,
        created_at: "2026-06-06T10:02:06Z",
        matches: [],
        error: null,
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/extraction/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({state: "complete", records: 68, entity_total: 248}),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({status: "built", triples_count: 6898, manuscripts_count: 68}),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/status`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "built",
        rdf_present: true,
        manifest_count: 68,
        coverage_present: false,
        last_upload_at: null,
        last_upload: null,
        wikibase_configured: true,
        canonical_live_count: 0,
        canonical_ready: false,
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/build*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [],
        summary: {total_items: 360, manuscripts: 68, persons: 223, works: 69, statements: 2600},
      }),
    });
  });

  await page.route(`**/api/projects/${TEST_PROJECT_ID}/research/summary`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total_manuscripts: 68,
        total_works: 69,
        total_persons: 223,
        total_places: 12,
        triples: 6898,
      }),
    });
  });

  await page.route("**/api/jobs/mine**", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({jobs: []}),
    });
  });

  await page.route("**/api/research-agent/sessions", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        thread_id: THREAD_ID,
        tool_grant: "test.grant.token",
        grant_expires_at: "2099-01-01T00:00:00Z",
        agent_url: "/api/research-agent/agui",
        agent_mode: "local",
        canvas_state: {artifacts: [], active_key: null},
        messages: [],
        tools: [],
      }),
    });
  });

  await page.route(`**/api/research-agent/threads/${THREAD_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: THREAD_ID,
        project_id: TEST_PROJECT_ID,
        run_id: TEST_RUN_ID,
        title: "Research session",
        messages: [],
        canvas_state: {artifacts: [], active_key: null},
        artifacts: [],
      }),
    });
  });

  await page.route("**/api/research-agent/agui", async (route) => {
    const body = `
data: {"type":"RUN_STARTED","threadId":"${THREAD_ID}","runId":"r1"}

data: {"type":"TEXT_MESSAGE_START","messageId":"m1","role":"assistant"}

data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"I ran research_summary and placed Corpus overview on the canvas."}

data: {"type":"TEXT_MESSAGE_END","messageId":"m1"}

data: {"type":"STATE_SNAPSHOT","snapshot":{"artifacts":[{"key":"research-summary","kind":"markdown","title":"Corpus overview","version":1}],"active_key":"research-summary"}}

data: {"type":"RUN_FINISHED","threadId":"${THREAD_ID}","runId":"r1"}

`;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body,
    });
  });

  await page.route(/\/api\/projects\/[^/]+\/research\/sparql/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({columns: ["s", "p", "o"], rows: [], truncated: false}),
    });
  });
}

async function setSession(page: Page) {
  await page.context().addCookies([
    {name: "session", value: "test-session", url: "http://localhost:5173"},
  ]).catch(() => {});
}

test.describe.configure({mode: "parallel"});

test.describe("Research Assistant", () => {
  test("tile appears on RunOverview", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/overview`);
    await expect(page.getByRole("link", {name: /research assistant/i})).toBeVisible({timeout: 8000});
  });

  test("tile links to /runs/:id/linked-data-explorer", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/overview`);
    const tile = page.getByRole("link", {name: /research assistant/i});
    await expect(tile).toHaveAttribute("href", new RegExp(`/runs/${TEST_RUN_ID}/linked-data-explorer`));
  });

  test("chat and canvas render", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await expect(page.getByRole("button", {name: /^overview$/i})).toBeVisible({timeout: 8000});
    await expect(page.getByLabel(/message/i)).toBeVisible();
    await expect(page.getByTestId("research-canvas")).toBeVisible();
  });

  test("SPARQL action opens the SPARQL console in the canvas", async ({page}) => {
    await installMocks(page);
    await page.route("**/api/research-agent/agui", async (route) => {
      const body = `
data: {"type":"RUN_STARTED"}

data: {"type":"STATE_SNAPSHOT","snapshot":{"artifacts":[{"key":"research-sparql","kind":"sparql","title":"SPARQL results","version":1}],"active_key":"research-sparql"}}

data: {"type":"TEXT_MESSAGE_START","messageId":"m1","role":"assistant"}

data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"Opened SPARQL."}

data: {"type":"TEXT_MESSAGE_END","messageId":"m1"}

data: {"type":"RUN_FINISHED"}

`;
      await route.fulfill({status: 200, contentType: "text/event-stream", body});
    });
    await page.route(`**/api/research-agent/threads/${THREAD_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: THREAD_ID,
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          title: "Research session",
          messages: [],
          canvas_state: {
            artifacts: [{key: "research-sparql", kind: "sparql", title: "SPARQL results", version: 1}],
            active_key: "research-sparql",
          },
          artifacts: [{
            id: "a1",
            artifact_key: "research-sparql",
            kind: "sparql",
            title: "SPARQL results",
            version: 1,
            content: {columns: ["work", "manuscript"], rows: [["urn:work:1", "urn:ms:1"]]},
            created_by: "agent",
          }],
        }),
      });
    });
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await page.getByRole("button", {name: /^sparql$/i}).click();
    await expect(page.getByRole("button", {name: /hmo graph/i})).toBeVisible({timeout: 8000});
  });

  test("Overview posts AG-UI messages that include an id", async ({page}) => {
    await installMocks(page);
    let firstId = "";
    let firstContent = "";
    await page.route("**/api/research-agent/agui", async (route) => {
      const posted: unknown = route.request().postDataJSON();
      if (posted && typeof posted === "object" && "messages" in posted && Array.isArray(posted.messages)) {
        const first = posted.messages[0];
        if (first && typeof first === "object" && "id" in first && "content" in first) {
          firstId = typeof first.id === "string" ? first.id : "";
          firstContent = typeof first.content === "string" ? first.content : "";
        }
      }
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `
data: {"type":"RUN_STARTED"}

data: {"type":"TEXT_MESSAGE_START","messageId":"m1","role":"assistant"}

data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"Overview ready."}

data: {"type":"TEXT_MESSAGE_END","messageId":"m1"}

data: {"type":"RUN_FINISHED"}

`,
      });
    });
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await page.getByRole("button", {name: /^overview$/i}).click();
    await expect(page.getByText("Overview ready.")).toBeVisible({timeout: 8000});
    expect(firstId).toBeTruthy();
    expect(firstContent).toMatch(/corpus overview/i);
  });
});
