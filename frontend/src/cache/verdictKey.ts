/**
 * Client-side NER verdict input fingerprint — mirrors
 * ``backend/app/pipeline/ner_verdict_cache.py``.
 */

import type {Entity} from "@/api/extractionApprovals";

export interface VerdictInputFields {
  control_number: string;
  source: string;
  start: number;
  end: number;
  text: string;
  type: string;
  role: string;
  judge_model: string;
  ai_extraction_verdict_schema: string;
  suggested_fix_policy: string;
}

export function entityVerdictQuerySummary(
  ent: Pick<
    Entity,
    | "control_number"
    | "source"
    | "start"
    | "end"
    | "text"
    | "type"
    | "role"
    | "effective_text"
    | "effective_type"
    | "effective_role"
    | "ai_verdict"
  > & {
    override_text?: string | null;
    override_type?: string | null;
    override_role?: string | null;
  },
  judgeModel?: string,
): VerdictInputFields {
  const judge = judgeModel ?? ent.ai_verdict?.model ?? "gemini-3.5-flash";
  return {
    control_number: ent.control_number ?? "",
    source:         ent.source ?? "",
    start:          ent.start ?? 0,
    end:            ent.end ?? 0,
    text:           (ent.override_text ?? ent.effective_text ?? ent.text ?? "").trim(),
    type:           (ent.override_type ?? ent.effective_type ?? ent.type ?? "").trim(),
    role:           (ent.override_role ?? ent.effective_role ?? ent.role ?? "").trim(),
    judge_model:    judge,
    ai_extraction_verdict_schema: "v2",
    suggested_fix_policy:         "text_high_confidence_v1",
  };
}

function canonicalJson(obj: Record<string, unknown>): string {
  const keys = Object.keys(obj).sort();
  const sorted: Record<string, unknown> = {};
  for (const k of keys) sorted[k] = obj[k];
  return JSON.stringify(sorted);
}

export async function entityVerdictFingerprint(
  ent: Parameters<typeof entityVerdictQuerySummary>[0],
  judgeModel?: string,
): Promise<string> {
  const summary = entityVerdictQuerySummary(ent, judgeModel);
  const body = canonicalJson(summary as unknown as Record<string, unknown>);
  const data = new TextEncoder().encode(body);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}