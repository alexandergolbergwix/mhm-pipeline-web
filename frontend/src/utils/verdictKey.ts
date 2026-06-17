import type {AgentEvent} from "@/api/aiVerify";


/** Stable map key for one authority (or NER) verdict row.

    Prefer ``candidate._match_id`` (AuthorityMatch UUID). Never fall back
    to bare ``record_id`` / control number — many entities share one MS. */
export function verdictStorageKey(ev: AgentEvent): string {
  const cand = (ev.candidate ?? {}) as Record<string, unknown>;
  const top = ev as Record<string, unknown>;

  const matchId = cand._match_id;
  if (matchId != null && String(matchId) !== "") {
    return String(matchId);
  }

  const entityId = cand._entity_id;
  if (entityId != null && String(entityId) !== "") {
    return String(entityId);
  }

  const recordId = String(top.record_id ?? cand.record_id ?? "");
  const name = String(cand.name ?? cand.person ?? cand.text ?? "").trim();
  const role = String(cand.role ?? "").trim();
  const subType = String(top.sub_type ?? cand.sub_type ?? "").trim();
  const evaluator = String(top.evaluator_id ?? cand.evaluator_id ?? "").trim();
  const idx = cand._match_index;

  if (recordId && name) {
    return `${recordId}|${name}|${role}|${subType}|${evaluator}|${idx ?? ""}`;
  }
  if (recordId) {
    return `${recordId}|${idx ?? name}`;
  }
  return `anon|${evaluator}|${subType}|${name}`;
}
