import {describe, expect, it} from "vitest";
import {applyAguiEvent, emptyCanvas, humanizeAgentError, type AssistantUiState} from "@/lib/canvasState";
import {buildAguiRunBody, parseSseChunk} from "@/api/researchAgent";

function base(): AssistantUiState {
  return {
    messages: [],
    canvas: emptyCanvas,
    artifacts: {},
    streamingText: "",
    busy: false,
    error: null,
  };
}

describe("applyAguiEvent", () => {
  it("streams assistant text then finishes", () => {
    let state = applyAguiEvent(base(), {type: "TEXT_MESSAGE_START"});
    state = applyAguiEvent(state, {type: "TEXT_MESSAGE_CONTENT", delta: "Hello "});
    state = applyAguiEvent(state, {type: "TEXT_MESSAGE_CONTENT", delta: "corpus"});
    state = applyAguiEvent(state, {type: "TEXT_MESSAGE_END"});
    state = applyAguiEvent(state, {type: "RUN_FINISHED"});
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("Hello corpus");
    expect(state.busy).toBe(false);
  });

  it("maps pydantic-ai output retries to a curator sentence", () => {
    const state = applyAguiEvent(base(), {
      type: "RUN_ERROR",
      message: "Exceeded maximum output retries (1)",
    });
    expect(state.busy).toBe(false);
    expect(state.error).toMatch(/could not finish that answer/i);
    expect(state.error).not.toMatch(/retries/i);
  });

  it("applies canvas snapshot", () => {
    const state = applyAguiEvent(base(), {
      type: "STATE_SNAPSHOT",
      snapshot: {artifacts: [{key: "notes", kind: "markdown", title: "Notes", version: 1}], active_key: "notes"},
    });
    expect(state.canvas.active_key).toBe("notes");
    expect(state.canvas.artifacts[0].title).toBe("Notes");
  });
});

describe("humanizeAgentError", () => {
  it("leaves ordinary stream failures unchanged", () => {
    expect(humanizeAgentError("Agent stream failed (422)")).toBe(
      "Agent stream failed (422)",
    );
  });
});

describe("parseSseChunk", () => {
  it("parses a data line", () => {
    const event = parseSseChunk('data: {"type":"RUN_STARTED"}');
    expect(event?.type).toBe("RUN_STARTED");
  });
});

describe("buildAguiRunBody", () => {
  it("gives every message a string id and string content", () => {
    const body = buildAguiRunBody({
      threadId: "thread-1",
      toolGrant: "grant",
      runId: "run-1",
      state: emptyCanvas,
      messages: [
        {role: "user", content: [{type: "text", text: "how many links"}]},
      ],
    });
    expect(body.messages[0]?.id).toBeTruthy();
    expect(body.messages[0]?.content).toBe("how many links");
    expect(body.forwardedProps.tool_grant).toBe("grant");
    expect(body.tools).toEqual([]);
    expect(body.context).toEqual([]);
  });
});
