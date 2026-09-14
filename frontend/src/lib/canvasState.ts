import type {AguiMessage, CanvasState, ResearchArtifact} from "@/api/researchAgent";

export interface ToolActivity {
  id: string;
  name: string;
  args: string;
  done: boolean;
}

export interface ThinkingTrack {
  id: string;
  text: string;
  done: boolean;
}

export interface AssistantUiState {
  messages: AguiMessage[];
  canvas: CanvasState;
  artifacts: Record<string, ResearchArtifact>;
  streamingText: string;
  busy: boolean;
  error: string | null;
  activity: ToolActivity[];
  thinking: ThinkingTrack[];
}

const ACTIVITY_CAP = 20;
const THINKING_TEXT_CAP = 2000;

export const emptyCanvas: CanvasState = {artifacts: [], active_key: null};

export function humanizeAgentError(message: string): string {
  const raw = message.trim() || "The assistant could not finish that answer.";
  if (/maximum output retries/i.test(raw) || /exceeded maximum retries/i.test(raw)) {
    return (
      "The assistant could not finish that answer. " +
      "Send the question again, or name a manuscript, work, or URI."
    );
  }
  return raw;
}

export function applyAguiEvent(
  state: AssistantUiState,
  event: Record<string, unknown>,
): AssistantUiState {
  const type = String(event.type || "");
  if (type === "RUN_STARTED") {
    return {...state, activity: [], thinking: [], busy: true};
  }
  if (type === "TOOL_CALL_START") {
    const entry: ToolActivity = {
      id: String(event.toolCallId || event.tool_call_id || `t${state.activity.length}`),
      name: String(event.toolCallName || event.tool_call_name || "tool"),
      args: "",
      done: false,
    };
    const activity = [...state.activity, entry].slice(-ACTIVITY_CAP);
    return {...state, activity, busy: true};
  }
  if (type === "TOOL_CALL_ARGS") {
    const id = String(event.toolCallId || event.tool_call_id || "");
    const delta = String(event.delta || "");
    const activity = state.activity.map((a) =>
      a.id === id ? {...a, args: (a.args + delta).slice(-400)} : a,
    );
    return {...state, activity};
  }
  if (type === "TOOL_CALL_END" || type === "TOOL_CALL_RESULT") {
    const id = String(event.toolCallId || event.tool_call_id || "");
    const activity = state.activity.map((a) =>
      a.id === id ? {...a, done: true} : a,
    );
    return {...state, activity};
  }
  if (
    type === "THINKING_START" ||
    type === "THINKING_CONTENT" ||
    type === "THINKING_DELTA" ||
    type === "THINKING_END"
  ) {
    const id = String(event.id || event.messageId || event.message_id || "thinking");
    const delta =
      type === "THINKING_END"
        ? ""
        : String(event.delta || event.content || "");
    const existing = state.thinking.find((t) => t.id === id);
    const entry: ThinkingTrack = existing
      ? {...existing, text: (existing.text + delta).slice(-THINKING_TEXT_CAP), done: type === "THINKING_END"}
      : {id, text: delta.slice(-THINKING_TEXT_CAP), done: type === "THINKING_END"};
    const thinking = [...state.thinking.filter((t) => t.id !== id), entry].slice(-3);
    return {...state, thinking, busy: true};
  }
  if (type === "TEXT_MESSAGE_START") {
    return {...state, streamingText: "", busy: true, error: null};
  }
  if (type === "TEXT_MESSAGE_CONTENT") {
    const delta = String(event.delta || "");
    return {...state, streamingText: state.streamingText + delta, busy: true};
  }
  if (type === "TEXT_MESSAGE_END") {
    const content = state.streamingText;
    const messages = content
      ? [...state.messages, {role: "assistant", content}]
      : state.messages;
    return {...state, messages, streamingText: "", busy: true};
  }
  if (type === "STATE_SNAPSHOT") {
    const snapshot = (event.snapshot || {}) as CanvasState;
    return {
      ...state,
      canvas: {
        artifacts: Array.isArray(snapshot.artifacts) ? snapshot.artifacts : state.canvas.artifacts,
        active_key: snapshot.active_key ?? state.canvas.active_key,
      },
    };
  }
  if (type === "RUN_FINISHED") {
    return {...state, busy: false};
  }
  if (type === "RUN_ERROR") {
    return {
      ...state,
      busy: false,
      error: humanizeAgentError(String(event.message || "Agent run failed.")),
    };
  }
  return state;
}

export function setActiveArtifact(canvas: CanvasState, key: string): CanvasState {
  return {...canvas, active_key: key};
}

export function upsertLocalArtifact(
  canvas: CanvasState,
  meta: CanvasState["artifacts"][number],
): CanvasState {
  const rest = canvas.artifacts.filter((a) => a.key !== meta.key);
  return {artifacts: [...rest, meta], active_key: meta.key};
}
