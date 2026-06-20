import {describe, expect, it} from "vitest";

import type {Entity} from "@/api/extractionApprovals";
import {canEntityAutoFix} from "@/utils/extractionAutofix";

function entity(overrides: Partial<Entity> = {}): Entity {
  return {
    id: "ent-1",
    control_number: "123",
    text: "Old Name",
    type: "PERSON",
    role: "AUTHOR",
    source: "person_ner",
    confidence: 0.9,
    model_confidence: 0.9,
    start: 0,
    end: 8,
    approved: false,
    rejected: false,
    exists_in: null,
    ai_verdict: {
      overall: "fail",
      suggested_fix: {
        text: "Correct Name",
        confidence: "high",
      },
    },
    ...overrides,
  };
}

describe("extractionAutofix", () => {
  it("detects high-confidence text fixes", () => {
    expect(canEntityAutoFix(entity())).toBe(true);
  });

  it("rejects genre_ml fixes", () => {
    expect(canEntityAutoFix(entity({source: "genre_ml"}))).toBe(false);
  });

  it("rejects no-op fixes", () => {
    expect(canEntityAutoFix(entity({text: "Correct Name", effective_text: "Correct Name"}))).toBe(false);
  });
});
