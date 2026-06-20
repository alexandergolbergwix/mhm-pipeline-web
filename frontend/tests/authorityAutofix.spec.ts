import {describe, expect, it} from "vitest";

import type {AuthorityMatch} from "@/api/runs";
import {
  canAuthorityAutoFix,
  resolveAuthorityFixPatch,
} from "@/utils/authorityAutofix";

function match(overrides: Partial<AuthorityMatch> = {}): AuthorityMatch {
  return {
    id: "m1",
    control_number: "123",
    entity_text: "Old Name",
    entity_kind: "person",
    role: "author",
    matched_name: "Wrong Name",
    mazal_id: "",
    viaf_id: "",
    wikidata_qid: "",
    confidence: "medium",
    source: "mazal",
    payload: {
      ai_verdict: {
        overall: "fail",
        suggested_fix: {
          text: "Correct Name",
          source_field: "matched_name",
          confidence: "high",
        },
      },
    },
    approved: false,
    approved_by: null,
    approved_at: null,
    ...overrides,
  };
}

describe("authorityAutofix", () => {
  it("detects high-confidence matched_name fixes", () => {
    const m = match();
    expect(canAuthorityAutoFix(m)).toBe(true);
    expect(resolveAuthorityFixPatch(m, m.payload!.ai_verdict!.suggested_fix!)).toEqual({
      matched_name: "Correct Name",
    });
  });

  it("rejects fixes that match current value", () => {
    const m = match({matched_name: "Correct Name"});
    expect(canAuthorityAutoFix(m)).toBe(false);
  });
});
