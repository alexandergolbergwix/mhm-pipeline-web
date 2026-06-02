/**
 * History API client.
 *
 * Two surfaces share this module:
 *
 * 1. **Project-level event log** (legacy, Phase 6) — append-only stream of
 *    project events (member changes, run uploads, match approvals,
 *    snapshot tags). Exposed as ``History`` for the Project History page
 *    at ``/routes/ProjectHistory.tsx``.
 *
 * 2. **Per-entity event-sourced history** (new) — typed wrapper around
 *    the per-(entity_type, entity_id) audit log used by MARC records,
 *    extraction entities, authority matches, Wikidata overrides, and
 *    Wikibase items. Exposed as ``EntityHistory`` with ``Entity``-
 *    prefixed types to avoid collisions with the legacy surface.
 *
 * State-changing calls go through ``api.post`` which handles the CSRF
 * double-submit header. Read-only calls use ``api.get``.
 */

import { api } from "@/api/client";

// ---------------------------------------------------------------------------
// Legacy project-level event log (used by /routes/ProjectHistory.tsx)
// ---------------------------------------------------------------------------

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
  events: (projectId: string): Promise<ProjectEvent[]> =>
    api.get<ProjectEvent[]>(`/projects/${projectId}/events`),
  snapshots: (projectId: string): Promise<Snapshot[]> =>
    api.get<Snapshot[]>(`/projects/${projectId}/snapshots`),
  tagSnapshot: (projectId: string, eventId: string, name: string): Promise<Snapshot> =>
    api.post<Snapshot>(`/projects/${projectId}/snapshots`, { event_id: eventId, name }),
  restoreTo: (projectId: string, eventId: string): Promise<{ ok: boolean; matches_changed: number }> =>
    api.post<{ ok: boolean; matches_changed: number }>(
      `/projects/${projectId}/restore/${eventId}`,
      {},
    ),
};

// ---------------------------------------------------------------------------
// Per-entity event-sourced history (timeline / diff / point-in-time / revert)
// ---------------------------------------------------------------------------

export type EntityType =
  | "marc_record"
  | "extraction_entity"
  | "authority_match"
  | "wikidata_override"
  | "wikibase_item";

export type EntityEventOp = "create" | "patch" | "revert" | "snapshot";

export interface EntityEventRow {
  id: string;
  rev_no: number;
  parent_event_id: string | null;
  op: EntityEventOp;
  actor_email: string | null;
  message: string;
  created_at: string;
}

export interface EntityPatchOp {
  op: "add" | "remove" | "replace" | "move" | "copy" | "test";
  path: string;
  value?: unknown;
  from?: string;
}

export interface EntityDiffPayload {
  patch: ReadonlyArray<EntityPatchOp>;
  before: unknown;
  after: unknown;
}

export interface EntitySnapshotRow {
  bucket: string;     // YYYY-MM-DD UTC
  slot: 0 | 1 | 2;    // 00:00 / 08:00 / 16:00
  rev_no: number;
  created_at: string;
}

export interface EntityRevertRequest {
  entity_type: EntityType;
  entity_id: string;
  target_rev: number;
  message?: string;
}

export interface EntityRevertResponse {
  ok: boolean;
  new_event_id: string;
  new_rev_no: number;
}

const entityHistoryBase = (projectId: string): string =>
  `/projects/${encodeURIComponent(projectId)}/history`;

function buildQuery(parts: ReadonlyArray<[string, string | number | undefined]>): string {
  const segments: string[] = [];
  for (const [key, value] of parts) {
    if (value === undefined) continue;
    segments.push(`${key}=${encodeURIComponent(String(value))}`);
  }
  return segments.length > 0 ? `?${segments.join("&")}` : "";
}

export const EntityHistory = {
  timeline: (
    projectId: string,
    entity_type: EntityType,
    entity_id: string,
    limit?: number,
    beforeRev?: number,
  ): Promise<EntityEventRow[]> =>
    api.get<EntityEventRow[]>(
      `${entityHistoryBase(projectId)}${buildQuery([
        ["entity_type", entity_type],
        ["entity_id", entity_id],
        ["limit", limit],
        ["before_rev", beforeRev],
      ])}`,
    ),

  diff: (
    projectId: string,
    entity_type: EntityType,
    entity_id: string,
    from_rev: number,
    to_rev: number,
  ): Promise<EntityDiffPayload> =>
    api.get<EntityDiffPayload>(
      `${entityHistoryBase(projectId)}/diff${buildQuery([
        ["entity_type", entity_type],
        ["entity_id", entity_id],
        ["from", from_rev],
        ["to", to_rev],
      ])}`,
    ),

  at: (
    projectId: string,
    entity_type: EntityType,
    entity_id: string,
    rev: number,
  ): Promise<unknown> =>
    api.get<unknown>(
      `${entityHistoryBase(projectId)}/at${buildQuery([
        ["entity_type", entity_type],
        ["entity_id", entity_id],
        ["rev", rev],
      ])}`,
    ),

  revert: (projectId: string, body: EntityRevertRequest): Promise<EntityRevertResponse> =>
    api.post<EntityRevertResponse>(`${entityHistoryBase(projectId)}/revert`, body),

  snapshots: (
    projectId: string,
    entity_type: EntityType,
    entity_id: string,
    sinceISODate?: string,
  ): Promise<EntitySnapshotRow[]> =>
    api.get<EntitySnapshotRow[]>(
      `${entityHistoryBase(projectId)}/snapshots${buildQuery([
        ["entity_type", entity_type],
        ["entity_id", entity_id],
        ["since", sinceISODate],
      ])}`,
    ),
};
