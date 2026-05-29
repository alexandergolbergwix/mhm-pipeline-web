import { api } from "@/api/client";

export interface ProjectEvent {
  id: string;
  project_id: string;
  actor_id: string | null;
  actor_name: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Snapshot {
  id: string;
  project_id: string;
  event_id: string;
  name: string;
  created_by: string | null;
  created_at: string;
}

export const History = {
  events:    (projectId: string) =>
    api.get<ProjectEvent[]>(`/projects/${projectId}/events`),
  snapshots: (projectId: string) =>
    api.get<Snapshot[]>(`/projects/${projectId}/snapshots`),
  tagSnapshot: (projectId: string, eventId: string, name: string) =>
    api.post<Snapshot>(`/projects/${projectId}/snapshots`, { event_id: eventId, name }),
  restoreTo: (projectId: string, eventId: string) =>
    api.post<{ ok: boolean; matches_changed: number }>(
      `/projects/${projectId}/restore/${eventId}`, {},
    ),
};
