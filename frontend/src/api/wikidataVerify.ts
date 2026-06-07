/**
 * Wikidata Studio AI verification client.
 *
 * Targets the Wikidata Studio eval-agent endpoint family. The request
 * body carries `item_ids`, and the UI joins verdicts back to rows by
 * Studio item `local_id`.
 */

import { api, csrfHeaders } from "@/api/client";


export type ScopeKind = "single" | "selection" | "all";


export interface AgentActionMeta {
  id:             string;
  label:          string;
  description:    string;
  scope_kinds:    ScopeKind[];
  evaluators:     string[];
  min_candidates: number;
}


export interface AgentEvent {
  type: string;
  [k: string]: unknown;
}


export interface VerifySessionListing {
  session_id: string;
  started_at: string | null;
  ended_at:   string | null;
  action_id:  string | null;
  scope_size: number;
  outcome:    string | null;
}


export interface VerifySession {
  session_id: string;
  run_id:     string;
  events:     AgentEvent[];
  verdicts:   Array<Record<string, unknown>>;
}


export interface StartRequest {
  action_id: string;
  item_ids?: string[];
  override_cache?: boolean;
  tier_model?: string;
}


export function streamWikidataVerification(
  runId: string,
  req: StartRequest,
): {
  events: AsyncIterableIterator<AgentEvent>;
  cancel: () => void;
} {
  const controller = new AbortController();
  const events = (async function* (): AsyncIterableIterator<AgentEvent> {
    const res = await fetch(
      `/api/runs/${runId}/wikidata-studio/ai-verify/start-stream`,
      {
        method:      "POST",
        credentials: "include",
        headers:     { "Content-Type": "application/json", ...csrfHeaders("POST") },
        body:        JSON.stringify(req),
        signal:      controller.signal,
      },
    );
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
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep = buf.indexOf("\n\n");
      while (sep >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const ev = parseFrame(frame);
        if (ev) yield ev;
        sep = buf.indexOf("\n\n");
      }
    }
  })();
  return {
    events,
    cancel: () => controller.abort(),
  };
}


function parseFrame(frame: string): AgentEvent | null {
  let type = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith(": ")) continue;
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const field = line.slice(0, colon);
    const value = line.slice(colon + 1).replace(/^ /, "");
    if      (field === "event") type = value;
    else if (field === "data")  data = value;
  }
  if (!data) return null;
  try {
    const parsed: unknown = JSON.parse(data);
    return isRecord(parsed) ? { type, ...parsed } : { type, raw: data };
  } catch {
    return { type, raw: data };
  }
}


function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


export const WikidataVerify = {
  listActions: (runId: string, scopeKind: ScopeKind) =>
    api.get<AgentActionMeta[]>(
      `/runs/${runId}/wikidata-studio/ai-verify/actions?scope_kind=${scopeKind}`,
    ),
  listSessions: (runId: string) =>
    api.get<VerifySessionListing[]>(
      `/runs/${runId}/wikidata-studio/ai-verify/sessions`,
    ),
  session: (runId: string, sessionId: string) =>
    api.get<VerifySession>(
      `/runs/${runId}/wikidata-studio/ai-verify/sessions/${sessionId}`,
    ),
};
