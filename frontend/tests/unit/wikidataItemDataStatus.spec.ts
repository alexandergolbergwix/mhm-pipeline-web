import {describe, expect, it} from "vitest";

import {countWikidataUploadStates, resolveWikidataItemDataStatus} from "@/utils/wikidataItemDataStatus";

describe("Wikidata Studio data status", () => {
  it("recognizes the durable receipt for an adopted live update", () => {
    expect(resolveWikidataItemDataStatus({
      existing_qid: "Q141747098",
      upload_outcome: "adopt",
      upload_message: "Updated 12 properties",
    })).toBe("updated");
  });

  it("recognizes a successful unchanged receipt", () => {
    expect(resolveWikidataItemDataStatus({
      existing_qid: "Q141747098",
      upload_outcome: "exists",
      upload_message: "No changes needed",
    })).toBe("updated");
  });

  it("does not certify source changes after the last upload", () => {
    expect(resolveWikidataItemDataStatus({
      existing_qid: "Q141747098",
      upload_outcome: "update",
      rebuild_required: true,
    })).toBe("will_update");
  });

  it("does not call a blocked item without a QID new", () => {
    expect(resolveWikidataItemDataStatus({upload_outcome: "blocked"})).toBe("needs_review");
  });

  it("keeps an existing work available for references", () => {
    expect(resolveWikidataItemDataStatus({
      upload_outcome: "skip",
      upload_message: "Manuscripts may P1574 that QID.",
    })).toBe("reference_only");
  });

  it("does not call a reconciliation-only adoption a successful upload", () => {
    expect(resolveWikidataItemDataStatus({
      existing_qid: "Q42", upload_outcome: "adopt", upload_message: "Matched by VIAF",
    })).toBe("will_update");
  });

  it("exposes a failed upload", () => {
    expect(resolveWikidataItemDataStatus({
      existing_qid: "Q42", upload_outcome: "failed",
    })).toBe("failed");
  });

  it("counts each record once across successful and blocked states", () => {
    const counts = countWikidataUploadStates([
      {existing_qid: "Q42", upload_outcome: "adopt", upload_message: "Updated Q42: +1 claims"},
      {upload_outcome: "blocked"},
      {existing_qid: "Q43", upload_outcome: "skip"},
      {existing_qid: "Q44", upload_outcome: "failed"},
    ]);
    expect(counts.updated).toBe(1);
    expect(counts.needs_review).toBe(1);
    expect(counts.reference_only).toBe(1);
    expect(counts.failed).toBe(1);
    expect(Object.values(counts).reduce((total, value) => total + value, 0)).toBe(4);
  });
});
