import {api, csrfHeaders} from "./client";

export type AgentMode = "modal" | "local";

export interface CanvasArtifactMeta {
  key: string;
  kind: string;
  title: string;
  version: number;
  updated_by?: string;
}

export interface CanvasState {
  artifacts: CanvasArtifactMeta[];
  active_key: string | null;
}

export interface ResearchAgentSession {
  thread_id: string;
  tool_grant: string;
  grant_expires_at: string;
  agent_url: string;
  agent_mode: AgentMode;
  canvas_state: CanvasState;
  messages: AguiMessage[];
  tools: {name: string; description: string}[];
}

export interface AguiMessage {
  id?: string;
  role: string;
  content: string | Array<{type: string; text?: string}>;
}

export interface ResearchArtifact {
  id: string;
  artifact_key: string;
  kind: string;
  title: string;
  version: number;
  content: Record<string, unknown>;
  created_by: string;
}

export interface ThreadDetail {
  id: string;
  project_id: string;
  run_id: string | null;
  title: string;
  messages: AguiMessage[];
  canvas_state: CanvasState;
  artifacts: ResearchArtifact[];
}

export interface ThreadSummary {
  id: string;
  run_id: string | null;
  title: string;
  message_count: number;
  updated_at: string | null;
}

export interface ThreadPatch {
  title?: string;
  messages?: AguiMessage[];
}

export const ResearchAgent = {
  startSession: (runId: string, options?: {threadId?: string; newThread?: boolean}) =>
    api.post<ResearchAgentSession>("/research-agent/sessions", {
      run_id: runId,
      thread_id: options?.threadId ?? null,
      new_thread: options?.newThread ?? false,
    }),

  getThread: (threadId: string) =>
    api.get<ThreadDetail>(`/research-agent/threads/${threadId}`),

  listThreads: (projectId: string, runId?: string) =>
    api.get<ThreadSummary[]>(
      `/research-agent/threads?project_id=${projectId}${runId ? `&run_id=${runId}` : ""}`,
    ),

  patchThread: (threadId: string, body: ThreadPatch) =>
    api.patch<{id: string; title: string; message_count: number}>(
      `/research-agent/threads/${threadId}`,
      body,
    ),

  autoTitle: (threadId: string) =>
    api.post<{id: string; title: string}>(`/research-agent/threads/${threadId}/auto-title`, {}),

  saveArtifact: (
    threadId: string,
    artifactKey: string,
    body: {title?: string; kind?: string; content: Record<string, unknown>; created_by?: string},
  ) =>
    api.put<ResearchArtifact & {canvas_state: CanvasState}>(
      `/research-agent/threads/${threadId}/artifacts/${encodeURIComponent(artifactKey)}`,
      body,
    ),

  downloadUrl: (threadId: string, artifactKey: string, format: string) =>
    `/api/research-agent/threads/${threadId}/artifacts/${encodeURIComponent(artifactKey)}/download?format=${format}`,

  exportUrl: (threadId: string) =>
    `/api/research-agent/threads/${threadId}/export`,
};

export function aguiMessageText(msg: AguiMessage): string {
  if (typeof msg.content === "string") return msg.content;
  return msg.content.map((part) => part.text || "").join("");
}

export function buildAguiRunBody(options: {
  threadId: string;
  messages: AguiMessage[];
  state: CanvasState;
  toolGrant: string;
  runId?: string;
}): {
  threadId: string;
  runId: string;
  messages: {id: string; role: string; content: string}[];
  state: CanvasState;
  tools: [];
  context: [];
  forwardedProps: {tool_grant: string};
} {
  return {
    threadId: options.threadId,
    runId: options.runId ?? crypto.randomUUID(),
    messages: options.messages.map((msg, index) => ({
      id: msg.id || `user-${options.threadId}-${index}`,
      role: msg.role,
      content: aguiMessageText(msg),
    })),
    state: options.state,
    tools: [],
    context: [],
    forwardedProps: {tool_grant: options.toolGrant},
  };
}

export async function aguiHttpError(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  let detail = text.slice(0, 240).trim();
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const raw = parsed.detail;
      if (typeof raw === "string") {
        detail = raw;
      } else if (Array.isArray(raw) && raw[0] && typeof raw[0] === "object" && "msg" in raw[0]) {
        const msg = raw[0].msg;
        if (typeof msg === "string") detail = msg;
      }
    }
  } catch {
    /* keep the raw body */
  }
  return detail
    ? `Agent stream failed (${res.status}): ${detail}`
    : `Agent stream failed (${res.status})`;
}

export async function streamAgui(options: {
  agentUrl: string;
  toolGrant: string;
  threadId: string;
  messages: AguiMessage[];
  state: CanvasState;
  onEvent: (event: Record<string, unknown>) => void;
  signal?: AbortSignal;
}): Promise<void> {
  const isAbsolute = options.agentUrl.startsWith("http");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    ...csrfHeaders("POST"),
  };
  if (isAbsolute) {
    headers.Authorization = `Bearer ${options.toolGrant}`;
  }
  const res = await fetch(options.agentUrl, {
    method: "POST",
    credentials: isAbsolute ? "omit" : "include",
    cache: "no-store",
    headers,
    signal: options.signal,
    body: JSON.stringify(buildAguiRunBody(options)),
  });
  if (!res.ok || !res.body) {
    throw new Error(await aguiHttpError(res));
  }
  await readSseBody(res, options.onEvent);
}

/**
 * Async AG-UI run (Rule R28): start a detached planner run on Heroku,
 * then read the run's Redis-Stream-backed SSE bridge. Long turns survive
 * Heroku's router idle timeout and Modal's HTTP timeout.
 */
export async function startAguiAsync(options: {
  toolGrant: string;
  threadId: string;
  messages: AguiMessage[];
  state: CanvasState;
  onEvent: (event: Record<string, unknown>) => void;
  signal?: AbortSignal;
}): Promise<void> {
  const start = await fetch("/api/research-agent/agui-async", {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...csrfHeaders("POST"),
    },
    signal: options.signal,
    body: JSON.stringify(buildAguiRunBody(options)),
  });
  if (!start.ok) {
    throw new Error(await aguiHttpError(start));
  }
  const started = (await start.json()) as {stream_url?: string};
  const streamUrl = started.stream_url || "/api/research-agent/agui-stream";
  const res = await fetch(streamUrl, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: {Accept: "text/event-stream"},
    signal: options.signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(await aguiHttpError(res));
  }
  const finished = await readSseBody(res, options.onEvent);
  if (!finished) {
    throw new Error(
      "The agent stream closed before finishing. Send the question again.",
    );
  }
}

/** Reads an SSE body; returns true when a RUN_FINISHED/RUN_ERROR arrived. */
async function readSseBody(
  res: Response,
  onEvent: (event: Record<string, unknown>) => void,
): Promise<boolean> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = false;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (event) {
        if (event.type === "RUN_FINISHED" || event.type === "RUN_ERROR") {
          terminal = true;
        }
        onEvent(event);
      }
    }
  }
  if (buffer.trim()) {
    const event = parseSseChunk(buffer);
    if (event) {
      if (event.type === "RUN_FINISHED" || event.type === "RUN_ERROR") {
        terminal = true;
      }
      onEvent(event);
    }
  }
  return terminal;
}

export function parseSseChunk(chunk: string): Record<string, unknown> | null {
  const lines = chunk.split("\n");
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  if (!raw || raw === "[DONE]") return null;
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}
