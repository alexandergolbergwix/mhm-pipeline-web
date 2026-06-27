/** Apply AI-suggested Wikidata Studio fixes from eval-agent verdicts. */

import {api} from "@/api/client";

export interface WikidataSuggestedFix {
  target: string;
  value?: string;
  studio_statement_index?: number;
  property_id?: string;
  value_type?: string;
  confidence?: string;
  reasoning?: string;
  source?: string;
}

export function collectWikidataFixes(
  candidate: Record<string, unknown>,
  verdict: Record<string, unknown>,
): WikidataSuggestedFix[] {
  const multi = candidate.suggested_fixes ?? verdict.suggested_fixes;
  if (Array.isArray(multi)) {
    return multi.filter(
      (f): f is WikidataSuggestedFix =>
        typeof f === "object"
        && f !== null
        && String((f as WikidataSuggestedFix).confidence ?? "") === "high",
    );
  }
  const single = (candidate.suggested_fix ?? verdict.suggested_fix) as WikidataSuggestedFix | null;
  if (
    single
    && String(single.confidence ?? "") === "high"
    && (single.value || single.target === "statement.remove" || single.target === "statement.add")
  ) {
    return [single];
  }
  return [];
}

export async function applyWikidataFixes(
  runId: string,
  localId: string,
  fixes: WikidataSuggestedFix[],
): Promise<void> {
  await api.post(
    `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}/ai-fixes/apply`,
    {fixes},
  );
}
