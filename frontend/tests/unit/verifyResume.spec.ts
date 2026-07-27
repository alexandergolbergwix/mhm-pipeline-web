import {describe, expect, it} from "vitest";

import type {RunJobSnapshot} from "@/api/runJobs";
import {continueVerifyLabel, resumeOfferFromJob} from "@/utils/verifyResume";

function job(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "j1",
    project_id: "p1",
    run_id: "r1",
    kind: "wikidata_verify",
    status: "failed",
    progress: {processed: 61, total: 313, session_id: "sess-1"},
    params: {session_id: "sess-1", action_id: "audit_wikidata_item", item_ids: ["a"]},
    result: {
      resumable: true,
      judged: 61,
      total: 313,
      outcome: "partial",
      session_id: "sess-1",
    },
    error: "Verification interrupted after 61 of 313.",
    created_by: null,
    started_at: null,
    finished_at: null,
    cancel_requested_at: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

describe("verifyResume", () => {
  it("offers continue for interrupted partial verify", () => {
    const offer = resumeOfferFromJob(job());
    expect(offer).not.toBeNull();
    expect(offer?.judged).toBe(61);
    expect(offer?.remaining).toBe(252);
    expect(continueVerifyLabel(offer!)).toContain("61/313");
  });

  it("returns null for active jobs", () => {
    expect(resumeOfferFromJob(job({status: "running"}))).toBeNull();
  });

  it("returns null when fully complete", () => {
    expect(resumeOfferFromJob(job({
      status: "succeeded",
      result: {judged: 10, total: 10, outcome: "complete", resumable: false},
      progress: {processed: 10, total: 10},
    }))).toBeNull();
  });
});
