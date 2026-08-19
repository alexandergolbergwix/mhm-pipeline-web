import {describe, expect, it} from "vitest";

import type {RunJobSnapshot} from "@/api/runJobs";
import {resolveUploadTarget} from "@/components/wikidata/WikidataUploadProgressModal";

function job(overrides: Partial<RunJobSnapshot> = {}): RunJobSnapshot {
  return {
    id: "j1",
    project_id: "p1",
    run_id: "r1",
    kind: "wikidata_upload",
    status: "cancelled",
    progress: {},
    params: {},
    result: null,
    error: null,
    created_by: null,
    started_at: null,
    finished_at: null,
    cancel_requested_at: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

describe("resolveUploadTarget", () => {
  it("prefers params.upload_target over a cancelled progress blob without it", () => {
    expect(resolveUploadTarget(job({
      params: {upload_target: "test"},
      progress: {phase: "cancelled", processed: 201, total: 233, message: "Cancelled by user"},
    }))).toBe("test");
  });

  it("uses sticky target when the job snapshot is briefly missing", () => {
    expect(resolveUploadTarget(null, "test")).toBe("test");
  });

  it("does not invent live when evidence is absent", () => {
    expect(resolveUploadTarget(null)).toBe("dry_run");
    expect(resolveUploadTarget(job())).toBe("dry_run");
  });
});
