import {applyAguiEvent, emptyCanvas, type AssistantUiState} from "@/lib/canvasState";
import {expect, it} from "vitest";

function base(): AssistantUiState {
  return {
    messages: [],
    canvas: emptyCanvas,
    artifacts: {},
    streamingText: "",
    busy: false,
    error: null,
    activity: [],
    thinking: [],
  };
}

it("tracks tool calls from camelCase AG-UI events", () => {
  let s = applyAguiEvent(base(), {type: "RUN_STARTED"});
  s = applyAguiEvent(s, {type: "TOOL_CALL_START", toolCallId: "t1", toolCallName: "show_wikidata_places"});
  s = applyAguiEvent(s, {type: "TOOL_CALL_ARGS", toolCallId: "t1", delta: '{"artifact_key":"wikidata-items"}'});
  s = applyAguiEvent(s, {type: "TOOL_CALL_END", toolCallId: "t1"});
  expect(s.activity).toHaveLength(1);
  expect(s.activity[0]).toMatchObject({name: "show_wikidata_places", done: true});
  expect(s.activity[0].args).toContain("wikidata-items");
});

it("tracks tool calls from snake_case events too", () => {
  let s = base();
  s = applyAguiEvent(s, {type: "TOOL_CALL_START", tool_call_id: "t2", tool_call_name: "research_sparql"});
  s = applyAguiEvent(s, {type: "TOOL_CALL_END", tool_call_id: "t2"});
  expect(s.activity[0]).toMatchObject({name: "research_sparql", done: true});
});

it("resets the tracks when a new run starts", () => {
  let s = base();
  s = applyAguiEvent(s, {type: "TOOL_CALL_START", toolCallId: "t1", toolCallName: "x"});
  s = applyAguiEvent(s, {type: "RUN_STARTED"});
  expect(s.activity).toHaveLength(0);
});

it("accumulates thinking text and marks it done", () => {
  let s = base();
  s = applyAguiEvent(s, {type: "THINKING_START", id: "th1"});
  s = applyAguiEvent(s, {type: "THINKING_DELTA", id: "th1", delta: "plan: "});
  s = applyAguiEvent(s, {type: "THINKING_DELTA", id: "th1", delta: "fetch claims"});
  s = applyAguiEvent(s, {type: "THINKING_END", id: "th1"});
  expect(s.thinking[0]).toMatchObject({text: "plan: fetch claims", done: true});
});
