import type {HmoStudioItem} from "@/api/hmoStudioItems";

export type HmoItemDataStatus = "new" | "will_update" | "updated" | "failed";

export const DATA_STATUS_LABEL: Record<HmoItemDataStatus, string> = {
  new: "new (not uploaded)",
  will_update: "will update existing",
  updated: "updated",
  failed: "failed",
};

/** Curator-facing upload posture derived from mapping + last push audit row. */
export function resolveHmoItemDataStatus(item: HmoStudioItem): HmoItemDataStatus {
  if (item.upload_outcome === "failed") return "failed";
  if (item.status === "would_create") return "new";
  if (item.upload_outcome === "update") return "updated";
  return "will_update";
}
