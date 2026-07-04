import { api } from "@/api/client";

export type ApiKeyName =
  | "gemini"
  | "wikidata"
  | "huggingface";

export interface ApiKeyStatus {
  name: ApiKeyName;
  set: boolean;
  last_used_at: string | null;
  created_at: string | null;
}

export const ApiKeys = {
  list:   () => api.get<ApiKeyStatus[]>("/me/api-keys"),
  set:    (name: ApiKeyName, value: string) =>
    api.put<ApiKeyStatus>(`/me/api-keys/${name}`, { value }),
  delete: (name: ApiKeyName) => api.del<void>(`/me/api-keys/${name}`),
};
