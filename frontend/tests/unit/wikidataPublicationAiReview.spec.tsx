import {describe, expect, it} from "vitest";

import type {PublicationAiReviewState} from "@/api/publication";
import {automaticProgressText} from "@/components/wikidata/WikidataPublicationAiReview";

function state(phase: string): PublicationAiReviewState {
  return {
    job_id: "job-1",
    status: "running",
    phase,
    processed: 16,
    total: 217,
    error: null,
    report: null,
  };
}

describe("automaticProgressText", () => {
  it("names the final Wikidata check after the AI check completes", () => {
    expect(automaticProgressText(state("dry_run"))).toBe(
      "Final Wikidata check: 16 / 217 retained records",
    );
  });

  it("names an AI check before the final Wikidata check", () => {
    expect(automaticProgressText(state("automatic_review"))).toBe("AI check: 16 / 217 records");
  });
});
