import {api, csrfHeaders} from "@/api/client";
import type {AiVerdict} from "@/api/extractionApprovals";
import type {AgentActionMeta, AgentEvent, VerifySession, VerifySessionListing} from "@/api/wikidataVerify";

export type ScopeKind = "single" | "selection" | "all";

export interface HmoItemVerifyStartRequest {
  action_id: string;
  item_ids?: string[];
  override_cache?: boolean;
  tier_model?: string;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

export function parseHmoItemSseFrame(frame: string): AgentEvent | null {
  let type = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith(": ")) continue;
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const field = line.slice(0, colon);
    const value = line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") type = value;
    else if (field === "data") data = value;
  }
  if (!data) return null;
  try {
    const parsed: unknown = JSON.parse(data);
    return isRecord(parsed) ? {type, ...parsed} : {type, raw: data};
  } catch {
    return {type, raw: data};
  }
}

export function streamHmoItemVerification(
  runId: string,
  req: HmoItemVerifyStartRequest,
): {events: AsyncIterableIterator<AgentEvent>; cancel: () => void} {
  const controller = new AbortController();
  const events = (async function* (): AsyncIterableIterator<AgentEvent> {
    const res = await fetch(`/api/runs/${runId}/hmo-studio/items/ai-verify/start-stream`, {
      method: "POST",
      credentials: "include",
      headers: {"Content-Type": "application/json", ...csrfHeaders("POST")},
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload: unknown = await res.json();
        if (isRecord(payload) && typeof payload.detail === "string") detail = payload.detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    if (!res.body) throw new Error("No SSE body");
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      let sep = buf.indexOf("\n\n");
      while (sep >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const ev = parseHmoItemSseFrame(frame);
        if (ev) yield ev;
        sep = buf.indexOf("\n\n");
      }
    }
  })();
  return {events, cancel: () => controller.abort()};
}

export function verdictFromAgentEvent(ev: AgentEvent): {localId: string; overall: string; reasoning?: string} | null {
  if (ev.type !== "agent.verdict") return null;
  const payload = isRecord(ev.payload) ? ev.payload : ev;
  const cand = isRecord(payload.candidate) ? payload.candidate : null;
  const verdict = isRecord(payload.verdict) ? payload.verdict : null;
  if (!cand || !verdict) return null;
  const localId = String(cand._local_id ?? cand.local_id ?? "");
  if (!localId) return null;
  return {
    localId,
    overall: String(verdict.overall ?? "abstain"),
    reasoning: typeof verdict.reasoning === "string" ? verdict.reasoning : undefined,
  };
}

export const HmoItemVerify = {
  actions(runId: string, scopeKind: ScopeKind = "selection"): Promise<AgentActionMeta[]> {
    return api.get(`/runs/${runId}/hmo-studio/items/ai-verify/actions?scope_kind=${scopeKind}`);
  },

  sessions(runId: string): Promise<VerifySessionListing[]> {
    return api.get(`/runs/${runId}/hmo-studio/items/ai-verify/sessions`);
  },

  session(runId: string, sessionId: string): Promise<VerifySession> {
    return api.get(`/runs/${runId}/hmo-studio/items/ai-verify/sessions/${sessionId}`);
  },

  stream: streamHmoItemVerification,
};

export type {AiVerdict};
