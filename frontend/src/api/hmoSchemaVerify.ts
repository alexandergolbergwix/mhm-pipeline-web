import {api, csrfHeaders} from "@/api/client";
import type {AgentActionMeta, AgentEvent, VerifySession, VerifySessionListing} from "@/api/wikidataVerify";


export type ScopeKind = "single" | "selection" | "all";


export interface SchemaVerifyStartRequest {
  run_id: string;
  action_id: string;
  ontology_uris?: string[];
  override_cache?: boolean;
  tier_model?: string;
}


function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}


function parseFrame(frame: string): AgentEvent | null {
  for (const line of frame.split("\n")) {
    if (line.startsWith("data: ")) {
      try {
        return JSON.parse(line.slice(6)) as AgentEvent;
      } catch {
        return null;
      }
    }
  }
  return null;
}


export function streamSchemaVerification(
  req: SchemaVerifyStartRequest,
): {events: AsyncIterableIterator<AgentEvent>; cancel: () => void} {
  const controller = new AbortController();
  const events = (async function* (): AsyncIterableIterator<AgentEvent> {
    const res = await fetch("/api/hmo-wikibase-schema/ai-verify/start-stream", {
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
        const ev = parseFrame(frame);
        if (ev) yield ev;
        sep = buf.indexOf("\n\n");
      }
    }
  })();
  return {events, cancel: () => controller.abort()};
}


export const HmoSchemaVerify = {
  actions: (scopeKind: ScopeKind = "selection") =>
    api.get<AgentActionMeta[]>(
      `/hmo-wikibase-schema/ai-verify/actions?scope_kind=${scopeKind}`,
    ),

  sessions: (runId: string) =>
    api.get<VerifySessionListing[]>(
      `/hmo-wikibase-schema/ai-verify/sessions?run_id=${runId}`,
    ),

  session: (runId: string, sessionId: string) =>
    api.get<VerifySession>(
      `/hmo-wikibase-schema/ai-verify/sessions/${sessionId}?run_id=${runId}`,
    ),

  stream: streamSchemaVerification,
};

export function schemaEntryLocalId(entry: {
  ontology_uri: string;
  entity_kind: string;
  label: string;
}): string {
  const uri = entry.ontology_uri.trim();
  const kind = entry.entity_kind.trim() || "entity";
  return uri ? `${kind}::${uri}` : `${kind}::${entry.label}`;
}

export function verdictFromAgentEvent(
  ev: AgentEvent,
): {localId: string; overall: string; reasoning: string} | null {
  if (ev.type !== "agent.verdict") return null;
  const payload = isRecord(ev.payload) ? ev.payload : ev;
  const candidate = isRecord(payload.candidate) ? payload.candidate : null;
  const verdict = isRecord(payload.verdict) ? payload.verdict : null;
  if (!candidate || !verdict) return null;
  const localId = String(
    candidate._local_id ?? candidate.local_id ?? candidate.ontology_uri ?? "",
  );
  return {
    localId,
    overall: String(verdict.overall ?? "unknown"),
    reasoning: String(verdict.reasoning ?? verdict.summary ?? ""),
  };
}
