import {describe, expect, it} from "vitest";

import type {RunJobSnapshot} from "@/api/runJobs";
import {WikidataReviewRefreshTracker} from "@/utils/wikidataReviewRefresh";

type Job = Pick<RunJobSnapshot, "id" | "run_id" | "kind" | "status" | "params">;
const completed: Job = {
  id: "upload-1", run_id: "run-1", kind: "wikidata_upload", status: "succeeded",
  params: {source: "canonical", upload_target: "live", dry_run: false},
};

describe("Wikidata review refresh", () => {
  it("refreshes once when an external live upload finishes", () => {
    const tracker = new WikidataReviewRefreshTracker("run-1", "canonical");
    expect(tracker.observe([{...completed, status: "running"}])).toBe(false);
    expect(tracker.observe([completed])).toBe(true);
    expect(tracker.observe([completed])).toBe(false);
  });

  it("does not reload historical completions at mount", () => {
    const tracker = new WikidataReviewRefreshTracker("run-1", "canonical");
    expect(tracker.observe([completed])).toBe(false);
    expect(tracker.observe([completed])).toBe(false);
  });

  it.each(["failed", "cancelled"] satisfies RunJobSnapshot["status"][])(
    "refreshes partial live receipts after %s", (status) => {
      const tracker = new WikidataReviewRefreshTracker("run-1", "canonical");
      tracker.observe([]);
      expect(tracker.observe([{...completed, status}])).toBe(true);
    },
  );

  it("ignores dry-run, other runs, and other source scopes", () => {
    const tracker = new WikidataReviewRefreshTracker("run-1", "canonical");
    tracker.observe([]);
    expect(tracker.observe([
      {...completed, params: {...completed.params, upload_target: "dry_run"}},
      {...completed, id: "other-run", run_id: "run-2"},
      {...completed, id: "other-source", params: {...completed.params, source: "legacy"}},
    ])).toBe(false);
  });

  it("uses the explicit target when the legacy dry-run flag differs", () => {
    const tracker = new WikidataReviewRefreshTracker("run-1", "canonical");
    tracker.observe([]);
    expect(tracker.observe([{
      ...completed, params: {...completed.params, dry_run: true},
    }])).toBe(true);
  });

  it("refreshes the AI counts after verification finishes", () => {
    const tracker = new WikidataReviewRefreshTracker("run-1", "canonical");
    tracker.observe([]);
    expect(tracker.observe([{
      ...completed, id: "verify-1", kind: "wikidata_verify", params: {source: "canonical"},
    }])).toBe(true);
  });
});
