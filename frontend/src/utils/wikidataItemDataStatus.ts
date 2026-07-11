import type {StudioItem} from "@/api/wikidataStudio";

export type WikidataItemDataStatus = "new" | "will_update" | "updated";

export const WIKIDATA_DATA_STATUS_LABEL: Record<WikidataItemDataStatus, string> = {
  new: "new (not uploaded)",
  will_update: "will update existing",
  updated: "updated",
};

/** Curator-facing upload posture from existing QID + last upload audit row. */
export function resolveWikidataItemDataStatus(item: StudioItem): WikidataItemDataStatus {
  if (!item.existing_qid) return "new";
  if (item.upload_outcome === "update") return "updated";
  return "will_update";
}
