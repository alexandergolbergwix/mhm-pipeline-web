import {describe, expect, it} from "vitest";

import type {AiVerdictOverall} from "@/api/extractionApprovals";

/**
 * Rule W-110 — the review table patches rows as verdicts land rather than
 * waiting for the run to finish. This mirrors `applyStreamedVerdicts` so the
 * patch semantics are pinned independently of React rendering.
 */
function applyStreamedVerdicts(
  items: Array<{local_id: string; ai_verdict?: {overall?: AiVerdictOverall} | null}>,
  overallByItemId: Record<string, string>,
): {items: typeof items; changed: boolean} {
  const known = new Set<AiVerdictOverall>([
    "pass", "full", "partial", "fail", "abstain", "unknown",
  ]);
  let changed = false;
  const next = items.map((item) => {
    const streamed = overallByItemId[item.local_id]?.toLowerCase() as AiVerdictOverall;
    if (!streamed || !known.has(streamed)) return item;
    if (item.ai_verdict?.overall === streamed) return item;
    changed = true;
    return {...item, ai_verdict: {...(item.ai_verdict ?? {}), overall: streamed}};
  });
  return {items: changed ? next : items, changed};
}

describe("live verdict patching", () => {
  const items = [
    {local_id: "ms:1"},
    {local_id: "ms:2", ai_verdict: {overall: "full" as AiVerdictOverall}},
    {local_id: "ms:3"},
  ];

  it("fills in the verdict for the judged row only", () => {
    const {items: next, changed} = applyStreamedVerdicts(items, {"ms:1": "partial"});
    expect(changed).toBe(true);
    expect(next[0].ai_verdict?.overall).toBe("partial");
    expect(next[2].ai_verdict).toBeUndefined();
    // Untouched rows keep their identity so React does not re-render them.
    expect(next[1]).toBe(items[1]);
    expect(next[2]).toBe(items[2]);
  });

  it("is a no-op when nothing changed, so the table does not flicker", () => {
    const {items: next, changed} = applyStreamedVerdicts(items, {"ms:2": "full"});
    expect(changed).toBe(false);
    expect(next).toBe(items);
  });

  it("ignores a verdict the table cannot render", () => {
    const {changed} = applyStreamedVerdicts(items, {"ms:1": "banana"});
    expect(changed).toBe(false);
  });

  it("normalises case from the stream", () => {
    const {items: next} = applyStreamedVerdicts(items, {"ms:3": "FAIL"});
    expect(next[2].ai_verdict?.overall).toBe("fail");
  });

  it("ignores ids that are not in the table", () => {
    const {changed} = applyStreamedVerdicts(items, {"ms:999": "fail"});
    expect(changed).toBe(false);
  });
});
