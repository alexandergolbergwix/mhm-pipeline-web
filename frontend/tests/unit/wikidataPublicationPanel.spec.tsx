import {render, screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach, expect, it, vi} from "vitest";

import {WikidataPublicationPanel} from "@/components/wikidata/WikidataPublicationPanel";
import {makeBuildResponse} from "../../e2e/fixtures/wikidata-fixtures";

afterEach(() => vi.unstubAllGlobals());

it("lets the curator explicitly defer connections to a later Release", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    if (typeof options.body === "string") bodies.push(JSON.parse(options.body));
    return new Response(JSON.stringify({publication: null, operation: null}), {
      headers: {"Content-Type": "application/json"},
    });
  }));
  const user = userEvent.setup();
  render(<WikidataPublicationPanel runId="run-1" source="canonical" approvedOnly
    build={{...makeBuildResponse(), source: "canonical"}} />);

  const defer = screen.getByRole("checkbox", {name: "Defer connections that need new QIDs"});
  expect(defer).not.toBeChecked();
  await user.click(defer);
  await user.click(screen.getByRole("button", {name: "Prepare Release"}));

  expect(bodies).toContainEqual(expect.objectContaining({profile_version: "1-nodes"}));
  expect(screen.getByText(/A later Release must add the deferred connections/)).toBeVisible();
});

it("shows the retained deferred claim before the curator approves the Release", async () => {
  const release = {
    release_id: "release-1", release_digest: "digest-1", revision: 1,
    created_at: "2026-09-05T10:00:00Z", entity_count: 1,
    finding_counts: {error: 0, warning: 0, info: 0},
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const body = url.endsWith("/prepare") ? {publication: {
      publication_id: "publication-1", run_id: "run-1", profile_id: "mhm-wikidata",
      profile_version: "1-nodes", target: "live", status: "ready_for_review",
      source_current: true, current_release: release,
      approval_set: null, plan: null, dry_run_receipt: null, execution: null,
    }} : {
      release_id: "release-1", release_digest: "digest-1", next_cursor: null, total: 1,
      items: [{
        entity_id: "work:1", entity_digest: "item-digest", entity_kind: "work",
        label: "Work 1", statement_count: 1, review_status: "pending", findings: [],
        deferred_statements: [{property: "P50", value: "__LOCAL:person:new"}],
      }],
    };
    return new Response(JSON.stringify(body), {headers: {"Content-Type": "application/json"}});
  }));
  const user = userEvent.setup();
  render(<WikidataPublicationPanel runId="run-1" source="canonical" approvedOnly
    build={{...makeBuildResponse(), source: "canonical"}} />);

  await user.click(screen.getByRole("button", {name: "Prepare Release"}));
  await user.click(await screen.findByText("1 deferred connection"));

  expect(screen.getByText(/__LOCAL:person:new/)).toBeVisible();
});
