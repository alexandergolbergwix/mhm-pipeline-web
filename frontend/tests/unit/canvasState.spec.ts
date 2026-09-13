import {describe, expect, it} from "vitest";
import {applyAguiEvent, emptyCanvas, type AssistantUiState} from "@/lib/canvasState";
import {parseSseChunk} from "@/api/researchAgent";

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

  it("applies canvas snapshot", () => {
    const state = applyAguiEvent(base(), {
      type: "STATE_SNAPSHOT",
      snapshot: {artifacts: [{key: "notes", kind: "markdown", title: "Notes", version: 1}], active_key: "notes"},
    });
    expect(state.canvas.active_key).toBe("notes");
    expect(state.canvas.artifacts[0].title).toBe("Notes");
  });
});

describe("parseSseChunk", () => {
  it("parses a data line", () => {
    const event = parseSseChunk('data: {"type":"RUN_STARTED"}');
    expect(event?.type).toBe("RUN_STARTED");
  });
});
