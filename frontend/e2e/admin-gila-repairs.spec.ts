import {expect, test} from "@playwright/test";

import {summary} from "./fixtures/publication-fixtures";
import {installStudioMocks, makeBuildResponse, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

test.beforeEach(async ({page}) => {
  await page.route("**/api/admin/wikidata-gila-repairs/preparations?*", route =>
    route.fulfill({json: {more: false, items: []}}));
});

test("the administrator sees live audit progress and can inspect source gaps", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  let status = "idle";
  const report = {
    id: "audit-1", finished_at: "2026-11-20T08:17:21Z", record_count: 897, public_read: 3537,
    reference_blocks: 472, case_count: 1, requirement_coverage: {public_reads: 896},
    email_points: [{point: 8, title: "Sources, bibliography, and inscriptions", status: "manual_review"}],
  };
  await page.route("**/api/admin/wikidata-gila-repairs", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, job: status === "idle" ? null : {id: "audit-1", status, progress: {
      phase: status === "running" ? "public_items" : "complete",
      processed: 200, total: 3538, message: "Read live Wikidata revisions and references.",
    }}, report: status === "succeeded" ? report : null,
  }}));
  await page.route("**/api/admin/wikidata-gila-repairs/start", route => {
    status = "running";
    return route.fulfill({json: {id: "audit-1", status, progress: {phase: "queued"}, error: null}});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route => route.fulfill({json: {
    total: 1,
    items: [{
      case_id: "source:1", qid: "Q141175895", source_value: "Weisz catalogue", points: [8],
      disposition: "manual_review", reason: "bibliographic_source_identity_unresolved",
    }],
  }}));
  await page.goto("/admin/wikidata-gila-repairs");
  await expect(page.getByRole("heading", {name: "Wikidata Manuscript Audit and Repair"})).toBeVisible();
  await page.getByText("How repairs work", {exact: true}).click();
  await expect(page.getByText("The job runs a new audit after the repairs.", {exact: false})).toBeVisible();
  await page.getByRole("button", {name: "Start live audit"}).click();
  await expect(page.getByRole("progressbar", {name: "Live audit progress"})).toBeVisible();
  await expect(page.getByText("Check Wikidata items")).toBeVisible();
  status = "succeeded";
  await page.getByRole("button", {name: /Sources, bibliography/}).click();
  await page.locator("summary").getByText("Weisz catalogue", {exact: true}).click();
  await expect(page.getByText("bibliographic source identity unresolved")).toBeVisible();
  await expect(page.getByRole("button", {name: "Prepare repair release"})).toHaveCount(0);
});

test("one click starts an automatic repair and the page restores its result", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  const job = {id: "auto-1", run_id: TEST_RUN_ID, kind: "wikidata_gila_repair", params: {}, error: null};
  let launched = 0;
  let finished = false;
  const progress = {phase: "publish_0", message: "Verify public edits.", processed: 1, total: 2, unit: "items"};
  await page.route("**/api/admin/wikidata-gila-repairs", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, report: null, job: null,
    automatic_repair: launched ? {...job, progress, status: finished ? "succeeded" : "running",
      result: finished ? {
        items_published: 1, reference_items_corrected: 0, requirements_verified: 6,
        stages_needing_attention: [],
        exclusions: [{qid: "Q42", reason: "other_creator"}],
        requirement_comparison: [{
          point: 5, title: "People and roles",
          before: {repair_required: 1, manual_review: 1325},
          after: {verified: 1, manual_review: 1325},
        }],
      } : null,
    } : null,
  }}));
  await page.route("**/api/admin/wikidata-gila-repairs/repair-all", route => {
    expect(route.request().method()).toBe("POST");
    launched += 1;
    return route.fulfill({json: {...job, status: "queued", progress}});
  });
  await page.goto("/admin/wikidata-gila-repairs");
  await page.getByRole("button", {name: "Repair all eligible items"}).click();
  await expect(page.getByRole("progressbar", {name: "Automatic repair progress"})).toBeVisible();
  finished = true;
  await page.reload();
  await expect(page.getByRole("heading", {name: "Repair cycle complete"})).toBeVisible();
  await expect(page.getByText(/6 of 9 requirements verified/)).toBeVisible();
  await page.getByText("Compare verification before and after repairs").click();
  await expect(page.getByRole("row", {name: /People and roles.*0.08%/})).toBeVisible();
  await page.getByText("1 exclusions from repair plans").click();
  await expect(page.getByRole("link", {name: "Q42"})).toBeVisible();
  expect(launched).toBe(1);
});

test("the administrator can rebuild the source and retry an audit that stopped", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  const previousAudit = {
    id: "audit-1", status: "failed", progress: {phase: "check_source"},
    error: "Build the canonical Studio source and start the audit again.",
  };
  const nextAudit = {
    id: "audit-2", status: "running", progress: {phase: "public_items"},
    error: null,
  };
  let audit: typeof previousAudit | typeof nextAudit = previousAudit;
  await page.route("**/api/admin/wikidata-gila-repairs", route =>
    route.fulfill({json: {run_id: TEST_RUN_ID, job: audit, report: null}}));

  const params = {
    source: "canonical", approved_only: true, force_rebuild: true, extract_provenance_prose: false,
  };
  const build = {id: "source-build", run_id: TEST_RUN_ID, kind: "wikidata_studio_build", params};
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({kind: build.kind, params});
    return route.fulfill({json: {...build, status: "queued", progress: {phase: "queued"}}});
  });
  let completeBuild = false;
  let checkedSuccessfulBuild = false;
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/${build.id}`, route => {
    checkedSuccessfulBuild = completeBuild;
    return route.fulfill({json: {
      ...build, status: completeBuild ? "succeeded" : "running",
      progress: {message: "Read the approved MARC records."},
    }});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/start", route => {
    expect(checkedSuccessfulBuild).toBe(true);
    audit = nextAudit;
    return route.fulfill({json: nextAudit});
  });
  await page.goto("/admin/wikidata-gila-repairs");

  await page.getByRole("button", {name: "Rebuild source and run audit"}).click({timeout: 5000});
  await expect(page.getByRole("status").getByText("Read the approved MARC records.")).toBeVisible();
  completeBuild = true;
  await expect(page.getByText("Check Wikidata items")).toBeVisible();
});

test("the audit shows work left and opens the scoped repair controls", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  const finishedAt = "2026-11-20T08:17:21Z";
  await page.route("**/api/admin/wikidata-gila-repairs", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, job: {id: "audit-1", status: "succeeded", progress: {phase: "complete"}},
    report: {
      id: "audit-1", finished_at: finishedAt, record_count: 897, public_read: 2161,
      reference_blocks: 14, case_count: 2, all_requirements_verified: false,
      requirement_coverage: {public_reads: 896, verified_claims: 8399},
      email_points: [{point: 8, title: "Sources, bibliography, and inscriptions", status: "manual_review"}],
    },
  }}));
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route =>
    route.fulfill({json: {total: 0, items: []}}));
  const scopeDigest = "b".repeat(64);
  let starts = 0;
  const repairJob = {id: "reference-job", status: "queued", progress: {}};
  await page.route("**/api/admin/wikidata-reference-repairs?scope=remaining", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, total: 1, enabled: true, scope_digest: scopeDigest, maxlag: 100,
    job: starts > 0 ? repairJob : null, verified: 0, halted_status: null,
    reference_inventory: {
      audited_at: finishedAt, live_checked_at: finishedAt,
      items: [
        {qid: "Q141175569", baseline_blocks: 4, observed_blocks: 4, creator_matches: true},
        {qid: "Q134926101", baseline_blocks: 10, observed_blocks: 10, creator_matches: false},
      ],
    },
  }}));
  await page.route("**/api/admin/wikidata-reference-repairs/start", route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({
      confirmed: true, scope_digest: scopeDigest, scope: "remaining",
    });
    starts++;
    return route.fulfill({json: repairJob});
  });
  await page.goto("/admin/wikidata-gila-repairs");
  await expect(page.getByRole("heading", {name: "Work remains after the audit"})).toBeVisible();
  await expect(page.getByText("0 of 9", {exact: true})).toBeVisible();
  await expect(page.getByText("4 repeated references on project-created items.", {exact: false})).toBeVisible();
  await expect(page.getByText("10 on items that other accounts created.", {exact: false})).toBeVisible();
  await page.getByRole("button", {name: "Review reference queue"}).click();
  await expect(page).toHaveURL(/wikidata-gila-repairs$/);
  await page.getByText("View the audited items").click();
  await expect(page.getByText("10 at latest audit · Other creator")).toBeVisible();
  const start = page.getByRole("button", {name: "Start backend reference repairs"});
  await expect(start).toBeDisabled();
  expect(starts).toBe(0);
  await page.getByRole("checkbox", {name: "I authorize the reviewed reference repairs only."}).check();
  await start.click();
  await expect(page.getByRole("heading", {name: "In queue", exact: true})).toBeVisible();
  expect(starts).toBe(1);
  await expect(page).toHaveURL(/wikidata-gila-repairs$/);
});

test("the audit asks for verification after reference repairs and exports evidence", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  const report = {
    id: "audit-1", finished_at: "2026-11-20T08:17:21Z", record_count: 897, public_read: 2161,
    reference_blocks: 472, case_count: 1, all_requirements_verified: false,
    requirement_coverage: {public_reads: 896, verified_claims: 8399},
    email_points: [{point: 8, title: "Sources, bibliography, and inscriptions", status: "manual_review"}],
  };
  let audit = {id: "audit-1", status: "succeeded", progress: {phase: "complete"}};
  await page.route("**/api/admin/wikidata-gila-repairs", route =>
    route.fulfill({json: {run_id: TEST_RUN_ID, job: audit, report}}));
  await page.route("**/api/admin/wikidata-gila-repairs/start", route => {
    audit = {id: "audit-2", status: "running", progress: {phase: "public_items"}};
    return route.fulfill({json: audit});
  });
  let readyCase = false;
  let preparations = 0;
  let preparationComplete = false;
  const preparation = {id: "prepare-1", qids: ["Q42"], publication_id: null,
    status: "running", progress: {message: "Verify the audited source."}};
  let stage: Parameters<typeof summary>[0] = "prepare";
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route => {
    const {searchParams} = new URL(route.request().url());
    const items = readyCase && searchParams.get("disposition") === "ready_to_prepare"
      ? [{case_id: "repair:1", qid: "Q42", points: [8], disposition: "repair_required", can_prepare: true}]
      : [];
    return route.fulfill({json: {items, total: items.length}});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/prepare-batch", route => {
    expect(route.request().postDataJSON()).toEqual({audit_id: "audit-1"});
    if (readyCase) preparations++;
    return route.fulfill({contentType: "application/json", body: JSON.stringify(readyCase ? preparation : null)});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/preparations?*", route => route.fulfill({json: {
    more: false,
    items: preparations === 0 ? [] : [preparationComplete ? {
      ...preparation, publication_id: "publication-7", status: "succeeded",
      progress: {message: "The repair release is ready."},
      qids: ["Q42", "Q43"], batch_counts: {eligible: 3, prepared: 2},
      open_case_counts: {manual_review: 1, not_checked: 4},
      exclusions: [{qid: "Q44", reason: "other_creator"}],
    } : preparation],
  }}));
  await page.route("**/jobs?kind=wikidata_publication_dry_run&limit=20", route =>
    route.fulfill({json: {jobs: []}}));
  await page.route("**/wikidata-publications/publication-7/read", route => {
    const {type} = route.request().postDataJSON().query;
    if (type === "audit") return route.fulfill({json: {items: [], total: 0, next_cursor: null}});
    if (type === "entities") return route.fulfill({json: {items: [], total: 0, next_cursor: null}});
    expect(type).toBe("summary");
    return route.fulfill({json: {publication: summary(stage)}});
  });
  await page.route("**/wikidata-publications/publication-7/advance", route => {
    const {type} = route.request().postDataJSON().command;
    expect(type).toBe(stage === "prepare" ? "review" : stage === "review" ? "dry_run" : "publish");
    stage = type;
    return route.fulfill({json: {publication: summary(stage)}});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/evidence?audit_id=audit-1", route =>
    route.fulfill({json: {audit_id: "audit-1", reference_repairs: [{qid: "Q42", after_revision: 22}]}}));
  await page.route("**/api/admin/wikidata-reference-repairs?scope=remaining", route =>
    route.fulfill({json: {
      enabled: true, verified: 9,
      job: {id: "repairs", status: "succeeded", finished_at: "2026-11-20T09:15:00Z"},
    }}));
  await page.goto("/admin/wikidata-gila-repairs");
  await expect(page.getByRole("heading", {name: "Reference repairs complete. A fresh audit is due."})).toBeVisible();
  await expect(page.getByText("Repeated references at last audit")).toBeVisible();
  await page.getByRole("button", {name: "Prepare eligible release"}).click();
  await expect(page.getByRole("heading", {name: "Repairs ready across all nine requirements"})).toBeVisible();
  await expect(page.locator("#audit-cases")).toBeFocused();
  await expect(page.getByRole("combobox", {name: "Case status"})).toHaveValue("ready_to_prepare");
  await expect(page.getByText("No source-supported cases are ready", {exact: false})).toBeVisible();
  await page.getByRole("button", {name: "Review cases that need a decision"}).click();
  await expect(page.getByRole("combobox", {name: "Case status"})).toHaveValue("manual_review");
  readyCase = true;
  await page.getByRole("button", {name: "Prepare eligible release"}).click();
  await expect(page.getByText("Verify the audited source.")).toBeVisible();
  expect(preparations).toBe(1);
  preparationComplete = true;
  await expect(page.getByText("2 of 3 eligible items prepared")).toBeVisible();
  await page.getByText("Items that did not pass the repair checks").click();
  await expect(page.getByText("Q44 · Another account created the item.")).toBeVisible();
  await expect(page.getByRole("heading", {name: "Repair release", exact: true})).toBeVisible();
  await page.getByRole("button", {name: "Approve Release for checks"}).click();
  await page.getByRole("button", {name: "Check before publish", exact: true}).click();
  await page.getByText("Publication details and manual actions").click();
  await page.getByRole("button", {name: "Publication history"}).click();
  await expect(page.getByRole("heading", {name: "Durable publication events"})).toBeVisible();
  await page.getByRole("button", {name: "Publish to Wikidata", exact: true}).click();
  await expect(page.getByTestId("publication-execution-progress")).toBeVisible();
  await expect(page).toHaveURL(/wikidata-gila-repairs$/);
  await page.reload();
  await expect(page.getByTestId("publication-execution-progress")).toBeVisible();
  await expect(page).toHaveURL(/wikidata-gila-repairs$/);
  const download = page.waitForEvent("download");
  await page.getByRole("button", {name: "Download audit evidence"}).click();
  expect((await download).suggestedFilename()).toBe("wikidata-audit-audit-1.json");
  await page.getByRole("button", {name: "Verify repairs on Wikidata"}).click();
  await expect(page.getByText("Check Wikidata items")).toBeVisible();
});

test("Kimi reviews an unresolved scribe and displays the source evidence", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  const caseId = "source:scribe:1";
  const job = {id: "scribe-review", run_id: TEST_RUN_ID, kind: "wikidata_gila_case_review"};
  await page.route("**/api/admin/wikidata-gila-repairs", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, job: null,
    report: {
      id: "audit-1", finished_at: "2026-11-20T08:17:21Z",
      record_count: 897, public_read: 2161, reference_blocks: 0, case_count: 1,
      requirement_coverage: {public_reads: 896},
      email_points: [{point: 5, title: "People and roles", status: "manual_review"}],
    },
  }}));
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route => route.fulfill({json: {
    items: [{case_id: caseId, points: [5], disposition: "manual_review",
      qid: "Q141746831", control_number: "990000403660205171", reason: "named_scribe_identity_unresolved"}],
    total: 1,
  }}));
  let complete = false;
  const result = {decision: "candidate", explanation: "The source names a scribe but gives no authority ID.",
    candidate_qid: "Q42", candidate_revision: 123, source_excerpts: ["700:1 $e scribe"]};
  await page.route("**/api/admin/wikidata-gila-repairs/case-review?*", route =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(complete ? {...job, status: "succeeded", result} : null),
    }));
  await page.route("**/api/admin/wikidata-gila-repairs/case-review", route => {
    expect(route.request().postDataJSON()).toEqual({audit_id: "audit-1", case_id: caseId});
    return route.fulfill({json: {...job, status: "queued"}});
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/${job.id}`, route => route.fulfill({json: {
    ...job, status: complete ? "succeeded" : "running", result,
    progress: {message: "Kimi checks the MARC evidence and Wikidata candidates."},
  }}));
  await page.goto("/admin/wikidata-gila-repairs");
  await page.getByText("The named scribe needs an identity check.", {exact: true}).first().click();
  await page.getByRole("button", {name: "Review with Kimi"}).click();
  await expect(page.getByRole("status").getByText("Kimi checks the MARC evidence and Wikidata candidates.")).toBeVisible();
  complete = true;
  await expect(page.getByRole("heading", {name: "Possible match. More evidence is necessary."})).toBeVisible();
  await expect(page.getByRole("link", {name: "Q42"})).toHaveAttribute("href", /oldid=123/);
  await page.getByText("MARC evidence", {exact: true}).click();
  await expect(page.getByText("700:1 $e scribe")).toBeVisible();
  await expect(page.getByRole("button", {name: "Prepare repair release"})).toHaveCount(0);

  await page.reload();
  await page.getByText("The named scribe needs an identity check.", {exact: true}).first().click();
  await expect(page.getByRole("heading", {name: "Possible match. More evidence is necessary."})).toBeVisible();
});
