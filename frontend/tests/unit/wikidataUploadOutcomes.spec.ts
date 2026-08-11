import {describe, expect, it} from "vitest";

import {
  liveUploadOutcomeRows,
  tallyWikidataUploadOutcomeCounts,
} from "@/utils/wikidataUploadOutcomes";

describe("wikidataUploadOutcomes", () => {
  it("tallies upload statuses into count buckets", () => {
    const counts = tallyWikidataUploadOutcomeCounts([
      {status: "success"},
      {status: "updated"},
      {status: "failed"},
    ]);
    expect(counts.created).toBe(1);
    expect(counts.updated).toBe(1);
    expect(counts.failed).toBe(1);
  });

  it("prefers terminal outcomes when the job finished", () => {
    const rows = liveUploadOutcomeRows(
      {
        recent_item_outcomes: [{local_id: "a", status: "pending"}],
      },
      [{local_id: "b", label: "B", entity_type: "work", status: "success", qid: "Q1"}],
      true,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]?.local_id).toBe("b");
    expect(rows[0]?.label).toBe("B");
  });
});
