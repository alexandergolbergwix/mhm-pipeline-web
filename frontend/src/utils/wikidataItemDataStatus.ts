import type {StudioItem} from "@/api/wikidataStudio";

export type WikidataItemDataStatus =
  | "new" | "will_update" | "updated" | "local_only"
  | "needs_review" | "reference_only" | "failed" | "outcome_unknown";

export const WIKIDATA_DATA_STATUS_LABEL: Record<WikidataItemDataStatus, string> = {
  new: "not uploaded",
  will_update: "update not confirmed",
  updated: "upload succeeded",
  local_only: "local only (no ID)",
  needs_review: "needs source review",
  reference_only: "reference only",
  failed: "upload failed",
  outcome_unknown: "upload outcome unknown",
};

export const WIKIDATA_DATA_STATUS_ORDER: WikidataItemDataStatus[] = [
  "updated", "reference_only", "needs_review", "outcome_unknown", "failed", "will_update", "new", "local_only",
];

export function countWikidataUploadStates(items: StudioItem[]): Record<WikidataItemDataStatus, number> {
  const counts: Record<WikidataItemDataStatus, number> = {
    new: 0, will_update: 0, updated: 0, local_only: 0,
    needs_review: 0, reference_only: 0, failed: 0, outcome_unknown: 0,
  };
  for (const item of items) counts[resolveWikidataItemDataStatus(item)] += 1;
  return counts;
}

/** Curator-facing upload posture from existing QID + last upload audit row. */
export function resolveWikidataItemDataStatus(item: StudioItem): WikidataItemDataStatus {
  const outcome = item.upload_outcome ?? "";
  const message = item.upload_message ?? "";
  if (outcome === "failed") {
    return /(?:write|CREATE) outcome unknown/i.test(message) ? "outcome_unknown" : "failed";
  }
  if (!item.rebuild_required && item.authority_evidence?.some((entry) =>
    entry.source === "wikidata_source_identity_choice" && entry.accepted === true &&
    entry.reference_only === true && entry.identifier === item.existing_qid
  )) return "reference_only";
  if (outcome === "blocked" || outcome === "would_block") return "needs_review";
  if ((outcome === "skip" || outcome === "skipped") && item.publishable !== false) {
    if (item.existing_qid || message.includes("Manuscripts may P1574 that QID")) {
      return "reference_only";
    }
    if (!message.includes("no VIAF/NLI/QID")) return "needs_review";
  }
  if (!item.existing_qid) {
    if (
      item.publishable === false ||
      (item.upload_outcome === "skip" && (item.upload_message ?? "").includes("no VIAF/NLI/QID"))
    ) {
      return "local_only";
    }
    return "new";
  }
  if (item.rebuild_required) return "will_update";
  if (["create", "created", "success", "update", "updated", "adopted"].includes(outcome)) {
    return "updated";
  }
  if (
    (outcome === "adopt" && /^Updated\s/i.test(message)) ||
    (["exists", "unchanged"].includes(outcome) && /no changes|already up to date/i.test(message))
  ) return "updated";
  return "will_update";
}
