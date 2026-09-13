import type {AguiMessage, CanvasState, ResearchArtifact} from "@/api/researchAgent";

export interface AssistantUiState {
  messages: AguiMessage[];
  canvas: CanvasState;
  artifacts: Record<string, ResearchArtifact>;
  streamingText: string;
  busy: boolean;
  error: string | null;
}

export const emptyCanvas: CanvasState = {artifacts: [], active_key: null};

export function applyAguiEvent(
  state: AssistantUiState,
  event: Record<string, unknown>,
): AssistantUiState {
  const type = String(event.type || "");
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
    return {...state, busy: false, error: String(event.message || "Agent run failed.")};
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
