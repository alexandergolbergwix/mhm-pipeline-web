import { api } from "@/api/client";

export type AccessRequestStatus =
  | "pending_email_confirm"
  | "pending_admin"
  | "approved"
  | "denied";

export interface AccessRequestListItem {
  id:                 string;
  email:              string;
  name:               string;
  affiliation:        string;
  status:             AccessRequestStatus;
  created_at:         string;
  confirmed_at:       string | null;
  reviewed_at:        string | null;
  reviewed_by_email:  string | null;
  denial_reason:      string | null;
}

export interface AccessRequestDetail extends AccessRequestListItem {
  justification: string;
  client_ip:     string;
  user_agent:    string;
}

export interface AccessRequestSubmitBody {
  name:            string;
  email:           string;
  affiliation:     string;
  justification:   string;
  website:         string;          // honeypot — must remain empty
  turnstile_token: string;
}

export interface AccessRequestSubmitResponse {
  message: string;
}

export interface ConfirmResponse {
  message: string;
  status:  "confirmed" | "expired" | "already_used";
}

export interface AccessRequestActionResponse {
  ok: boolean;
}

export const AccessRequests = {
  submit: (body: AccessRequestSubmitBody) =>
    api.post<AccessRequestSubmitResponse>("/access-request", body),
  confirm: (token: string) =>
    api.get<ConfirmResponse>(
      `/access-request/confirm/${encodeURIComponent(token)}`,
    ),
  list: (statusFilter?: string) =>
    api.get<AccessRequestListItem[]>(
      `/admin/access-requests${
        statusFilter ? `?status_filter=${encodeURIComponent(statusFilter)}` : ""
      }`,
    ),
  get: (id: string) =>
    api.get<AccessRequestDetail>(`/admin/access-requests/${id}`),
  approve: (id: string) =>
    api.post<AccessRequestActionResponse>(
      `/admin/access-requests/${id}/approve`,
    ),
  deny: (id: string, reason: string) =>
    api.post<AccessRequestActionResponse>(
      `/admin/access-requests/${id}/deny`,
      { reason },
    ),
};
