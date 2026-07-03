import { api } from "@/api/client";

export interface HmoSchemaStatus {
  total_classes: number;
  total_properties: number;
  mapped_classes: number;
  mapped_properties: number;
  missing_sample: string[];
  bot_username_set: boolean;
  bot_password_set: boolean;
}

export interface HmoSchemaBootstrapEntry {
  ontology_uri: string;
  entity_kind: string;
  label: string;
  wikibase_id: string | null;
  status: "created" | "skipped" | "would_create" | "failed" | string;
  message: string;
}

export interface HmoSchemaBootstrapResult {
  dry_run: boolean;
  created: number;
  skipped: number;
  failed: number;
  entries: HmoSchemaBootstrapEntry[];
}

export const HmoWikibaseSchema = {
  status: () => api.get<HmoSchemaStatus>("/hmo-wikibase-schema/status"),

  bootstrap: (dryRun: boolean) =>
    api.post<HmoSchemaBootstrapResult>(
      "/hmo-wikibase-schema/bootstrap", { dry_run: dryRun },
    ),
};
