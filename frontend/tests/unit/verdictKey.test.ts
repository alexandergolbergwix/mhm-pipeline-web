import {describe, expect, it} from "vitest";

import type {AgentEvent} from "@/api/aiVerify";
import {verdictStorageKey} from "@/utils/verdictKey";


describe("verdictStorageKey", () => {
  it("uses candidate._match_id when present", () => {
    const ev: AgentEvent = {
      type: "agent.verdict",
      record_id: "990001",
      candidate: {_match_id: "uuid-1", name: "Foo"},
    };
    expect(verdictStorageKey(ev)).toBe("uuid-1");
  });

  it("does not collapse multiple entities on the same control number", () => {
    const base = {
      type: "agent.verdict",
      record_id: "990001",
      evaluator_id: "authority",
      sub_type: "person",
    };
    const a: AgentEvent = {
      ...base,
      candidate: {name: "Author A", role: "author"},
    };
    const b: AgentEvent = {
      ...base,
      candidate: {name: "Scribe B", role: "scribe"},
    };
    expect(verdictStorageKey(a)).not.toBe(verdictStorageKey(b));
  });

  it("uses _entity_id for extraction verdicts", () => {
    const ev: AgentEvent = {
      type: "agent.verdict",
      candidate: {_entity_id: "ent-42", name: "span"},
    };
    expect(verdictStorageKey(ev)).toBe("ent-42");
  });
});
