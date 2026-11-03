import type {StudioItem} from "@/api/wikidataStudio";

export type WikidataItemDataStatus = "new" | "will_update" | "updated" | "local_only";

export const WIKIDATA_DATA_STATUS_LABEL: Record<WikidataItemDataStatus, string> = {
  new: "new (not uploaded)",
  will_update: "will update existing",
  updated: "updated",
  local_only: "local only (no ID)",
};

/** Curator-facing upload posture from existing QID + last upload audit row. */
export function resolveWikidataItemDataStatus(item: StudioItem): WikidataItemDataStatus {
  if (!item.existing_qid) {
    if (
      item.publishable === false ||
      (item.upload_outcome === "skip" && (item.upload_message ?? "").includes("no VIAF/NLI/QID"))
    ) {
      return "local_only";
    }
    return "new";
  }
  if (item.upload_outcome === "update" || item.upload_outcome === "create") return "updated";
  return "will_update";
}
