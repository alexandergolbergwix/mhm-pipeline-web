import type {AuthorityMatch} from "@/api/runs";

export type AuthorityEditTarget =
  | "matched_name"
  | "entity_text"
  | "mazal_id"
  | "viaf_id"
  | "wikidata_qid"
  | "role";

export interface AuthoritySuggestedFix {
  target?: string;
  value?: string;
  text?: string;
  confidence?: string;
  reasoning?: string | null;
  source_field?: string | null;
}

export interface AuthorityAiVerdict {
  overall?: string;
  reasoning?: string;
  suggested_fix?: AuthoritySuggestedFix | null;
}

const EDIT_TARGETS: ReadonlySet<string> = new Set([
  "matched_name",
  "entity_text",
  "mazal_id",
  "viaf_id",
  "wikidata_qid",
  "role",
]);

function fieldValue(match: AuthorityMatch, target: AuthorityEditTarget): string {
  switch (target) {
    case "matched_name": return (match.matched_name ?? "").trim();
    case "entity_text": return (match.entity_text ?? "").trim();
    case "mazal_id": return (match.mazal_id ?? "").trim();
    case "viaf_id": return (match.viaf_id ?? "").trim();
    case "wikidata_qid": return (match.wikidata_qid ?? "").trim();
    case "role": return (match.role ?? "").trim();
  }
}

export function getAuthoritySuggestedFix(
  match: AuthorityMatch,
): AuthoritySuggestedFix | null {
  const av = (match.payload?.ai_verdict ?? null) as AuthorityAiVerdict | null;
  const fix = av?.suggested_fix;
  return fix && typeof fix === "object" ? fix : null;
}

export function resolveAuthorityFixPatch(
  match: AuthorityMatch,
  fix: AuthoritySuggestedFix,
): Partial<Record<AuthorityEditTarget, string>> | null {
  if (fix.confidence !== "high") return null;

  let target = fix.target?.trim() as AuthorityEditTarget | undefined;
  let value = (fix.value ?? fix.text ?? "").trim();
  if (!value) return null;

  if (!target && fix.text) {
    const text = fix.text.trim();
    const sourceField = fix.source_field?.trim() as AuthorityEditTarget | undefined;
    if (sourceField && EDIT_TARGETS.has(sourceField)) {
      target = sourceField;
      value = text;
    } else if (text !== fieldValue(match, "entity_text")) target = "entity_text";
    else if (text !== fieldValue(match, "matched_name")) target = "matched_name";
    else return null;
    value = text;
  }

  if (!target || !EDIT_TARGETS.has(target)) return null;
  if (value === fieldValue(match, target)) return null;

  return {[target]: value};
}

export function canAuthorityAutoFix(match: AuthorityMatch): boolean {
  const fix = getAuthoritySuggestedFix(match);
  if (!fix) return false;
  return resolveAuthorityFixPatch(match, fix) !== null;
}

export function authorityFixTitle(match: AuthorityMatch): string {
  const fix = getAuthoritySuggestedFix(match);
  if (!fix) return "";
  const patch = resolveAuthorityFixPatch(match, fix);
  if (!patch) return "";
  const [target, value] = Object.entries(patch)[0] ?? [];
  return fix.reasoning
    ?? `Auto-fix: set ${target} → ${value}`;
}
