/**
 * AI Extraction — AI extraction client.
 *
 * Two read endpoints (status + results) and one SSE stream
 * (start-stream). The stream POST body is empty — the server reads
 * the run id from the URL and the HuggingFace token from the
 * authenticated user's encrypted store.
 *
 * The SSE shape mirrors ``backend/app/pipeline/extraction.py``:
 *
 *   event: extraction.start         { total }
 *   event: extraction.record.done   { index, total, control_number, entities, ml_genres }
 *   event: extraction.end           { records }
 *   event: extraction.error         { message }
 *
 * Consumers narrow on ``ev.type`` (string) and downstream fields.
 */

import { api, csrfHeaders } from "@/api/client";


export interface ExtractionEvent {
  type: string;
  [k: string]: unknown;
}


export interface ExtractionEntity {
  text: string;
  type: string;
  start?: number | null;
  end?: number | null;
  source?: string;
  role?: string | null;
  confidence?: number | null;
  model_confidence?: number | null;
  retyped_from?: string | null;
  [k: string]: unknown;
}


export interface ExtractionGenre {
  label: string;
  confidence: number;
}


export interface ExtractionRecord {
  _control_number: string;
  text?: string;
  entities: ExtractionEntity[];
  ml_genres: ExtractionGenre[];
  catalog_references?: string[];
  provenance_inscriptions?: string[];
  [k: string]: unknown;
}


export interface ExtractionStatus {
  state: "idle" | "running" | "complete" | "error" | string;
  records?: number;
  entity_total?: number;
  detail?: string;
  /** Backend's resolved EXTRACTION_MODE — surfaced in the page header so
   *  the user sees which inference backend will run before the first
   *  stream fires. */
  extraction_mode?: "local" | "hf-api" | "modal" | string;
}


/** Open the AI Extraction SSE stream. Cancel by calling cancel().
 *  ``mode`` picks the inference backend ("local" = on our server,
 *  "hf-api" = HuggingFace Inference Providers). The web app always
 *  uses "hf-api"; ``mode`` stays in the signature for future flexibility.
 *  ``models`` enables a subset of {person, provenance, contents, genre};
 *  omit to run every available model. */
export function streamExtraction(
  runId: string,
  mode?: "local" | "hf-api" | "modal",
  models?: string[],
  skipCache?: boolean,
): {
  events: AsyncIterableIterator<ExtractionEvent>;
  cancel: () => void;
} {
  const controller = new AbortController();
  const params: string[] = [];
  if (mode)              params.push(`mode=${encodeURIComponent(mode)}`);
  if (models?.length)    params.push(`models=${encodeURIComponent(models.join(","))}`);
  if (skipCache)         params.push(`skip_cache=true`);
  const qs = params.length > 0 ? `?${params.join("&")}` : "";
  const events = (async function* (): AsyncIterableIterator<ExtractionEvent> {
    const res = await fetch(`/api/runs/${runId}/extraction/start-stream${qs}`, {
      method:      "POST",
      credentials: "include",
      headers:     { "Content-Type": "application/json", ...csrfHeaders("POST") },
      body:        JSON.stringify({}),
      signal:      controller.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = (await res.json()) as { detail?: string };
        if (data?.detail) detail = data.detail;
      } catch { /* ignore */ }
      throw new Error(detail);
    }
    if (!res.body) throw new Error("No SSE body");
    const reader  = res.body.getReader();
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
  return { events, cancel: () => controller.abort() };
}


function parseFrame(frame: string): ExtractionEvent | null {
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
    return { type, ...(JSON.parse(data) as Record<string, unknown>) };
  } catch {
    return { type, raw: data };
  }
}


export const Extraction = {
  /** ``ner_results.json`` payload, one element per record. */
  results: (runId: string) =>
    api.get<ExtractionRecord[]>(`/runs/${runId}/extraction/results`),

  status: (runId: string) =>
    api.get<ExtractionStatus>(`/runs/${runId}/extraction/status`),
};
