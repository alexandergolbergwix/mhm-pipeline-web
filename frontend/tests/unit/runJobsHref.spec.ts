import {describe, expect, it} from "vitest";

import {jobRunHref, type RunJobSnapshot} from "@/api/runJobs";

function job(kind: string): RunJobSnapshot {
  return {
    id: "job-1",
    run_id: "run-9",
    kind,
    status: "running",
    progress: {},
    params: {},
    result: {},
  } as unknown as RunJobSnapshot;
}

describe("jobRunHref", () => {
  it("carries the job id for jobs whose progress lives in a modal", () => {
    // The curator clicked View to watch *this* run, so the page must reopen it.
    expect(jobRunHref(job("wikidata_verify"))).toBe(
      "/runs/run-9/wikidata-studio?job=job-1",
    );
    expect(jobRunHref(job("hmo_item_verify"))).toBe(
      "/runs/run-9/hmo-studio?job=job-1",
    );
  });

  it("leaves page-level jobs as a plain route", () => {
    expect(jobRunHref(job("wikidata_studio_build"))).toBe("/runs/run-9/wikidata-studio");
    expect(jobRunHref(job("rdf_build"))).toBe("/runs/run-9/rdf");
  });

  it("falls back to the overview for an unknown kind", () => {
    expect(jobRunHref(job("something_new"))).toBe("/runs/run-9/overview");
  });
});
