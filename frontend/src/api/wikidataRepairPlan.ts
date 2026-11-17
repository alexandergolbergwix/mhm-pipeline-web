import {api} from "@/api/client";

export interface RepairInventoryItem {
  local_id: string;
  label: string;
  existing_qid: string;
  validation_codes: string[];
  qid_edit_indices: string[];
  approved: boolean | null;
  override_updated_at: string | null;
}

export interface RepairInventoryPage {
  run_id: string;
  build_id: string;
  build_token: string;
  built_at: string;
  checked_at: string;
  mode: "read_only";
  public_verification: "not_checked";
  total_items: number;
  items: RepairInventoryItem[];
  next_cursor: string | null;
}

export function readRepairPlan(runId: string, cursor = "", buildToken?: string) {
  const query = new URLSearchParams({page_size: "50", cursor});
  if (buildToken) query.set("expected_build_token", buildToken);
  return api.get<RepairInventoryPage>(
    `/runs/${encodeURIComponent(runId)}/wikidata-studio/repair-plan?${query}`,
  );
}
