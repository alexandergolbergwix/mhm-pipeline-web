import {describe, expect, it} from "vitest";

import {parseHmoSchemaSseFrame, verdictFromAgentEvent} from "@/api/hmoSchemaVerify";


describe("hmoSchemaVerify SSE parsing", () => {
  it("merges event type with data payload like other verify clients", () => {
    const frame = [
      "event: agent.verdict",
      'data: {"candidate":{"_local_id":"property::http://example.org#p","label":"test"},"verdict":{"overall":"pass","reasoning":"ok"}}',
    ].join("\n");

    const ev = parseHmoSchemaSseFrame(frame);
    expect(ev).not.toBeNull();
    expect(ev?.type).toBe("agent.verdict");

    const parsed = verdictFromAgentEvent(ev!);
    expect(parsed?.localId).toBe("property::http://example.org#p");
    expect(parsed?.overall).toBe("pass");
  });

  it("parses session.start frames", () => {
    const frame = [
      "event: session.start",
      'data: {"session_id":"abc","scope_size":3}',
    ].join("\n");
    const ev = parseHmoSchemaSseFrame(frame);
    expect(ev?.type).toBe("session.start");
    expect(ev?.scope_size).toBe(3);
  });
});
