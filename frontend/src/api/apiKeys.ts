import { api } from "@/api/client";

export type ApiKeyName =
  | "gemini"
  | "wikidata"
  | "wikibase_cloud_bot_name"
  | "wikibase_cloud_bot_username"
  | "wikibase_cloud_bot_password"
  | "huggingface";

export interface ApiKeyStatus {
  name: ApiKeyName;
  set: boolean;
  last_used_at: string | null;
  created_at: string | null;
}

export interface WikibaseCloudVerifyResult {
  ok: boolean;
  message: string;
  login_name: string | null;
}

export const WIKIBASE_CLOUD_KEY_NAMES: ApiKeyName[] = [
  "wikibase_cloud_bot_username",
  "wikibase_cloud_bot_name",
  "wikibase_cloud_bot_password",
];

export const ApiKeys = {
  list:   () => api.get<ApiKeyStatus[]>("/me/api-keys"),
  set:    (name: ApiKeyName, value: string) =>
    api.put<ApiKeyStatus>(`/me/api-keys/${name}`, { value }),
  delete: (name: ApiKeyName) => api.del<void>(`/me/api-keys/${name}`),
  verifyWikibaseCloud: (overrides?: {
    username?: string;
    bot_name?: string;
    password?: string;
  }) =>
    api.post<WikibaseCloudVerifyResult>(
      "/me/api-keys/wikibase-cloud/verify",
      overrides ?? {},
    ),
};
