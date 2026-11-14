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

  it("classifies StudioItem data_status including local_only and newly created items", async () => {
    const {resolveWikidataItemDataStatus} = await import("@/utils/wikidataItemDataStatus");
    expect(resolveWikidataItemDataStatus({entity_type: "work", existing_qid: null})).toBe("new");
    expect(
      resolveWikidataItemDataStatus({
        entity_type: "person",
        existing_qid: null,
        publishable: false,
      }),
    ).toBe("local_only");
    expect(
      resolveWikidataItemDataStatus({
        entity_type: "person",
        existing_qid: null,
        upload_outcome: "skip",
        upload_message: "Not publishable — no VIAF/NLI/QID (Rule W-154/W-185).",
      }),
    ).toBe("local_only");
    expect(
      resolveWikidataItemDataStatus({
        entity_type: "work",
        existing_qid: "Q141950143",
        upload_outcome: "create",
      }),
    ).toBe("updated");
    expect(
      resolveWikidataItemDataStatus({
        entity_type: "manuscript",
        existing_qid: "Q141175735",
        upload_outcome: "skip",
      }),
    ).toBe("reference_only");
  });
});
