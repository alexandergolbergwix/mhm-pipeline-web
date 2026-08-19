/**
 * Apply per-item upload outcomes from `run_jobs.progress` onto the review
 * table without a full list reload (avoids unmount / flicker).
 */

export type StudioUploadProgressOutcome = {
  local_id?: string;
  label?: string | null;
  entity_type?: string | null;
  status?: string;
  wikibase_id?: string | null;
  qid?: string | null;
  message?: string | null;
  source_uri?: string;
};

export type StudioUploadProgressSlice = {
  item_outcome?: StudioUploadProgressOutcome | null;
  recent_item_outcomes?: StudioUploadProgressOutcome[] | null;
  /** Local id currently under write — table shows a loading pill. */
  processing_local_id?: string | null;
} | null | undefined;

const HMO_STATUS_TO_OPERATION: Record<string, string> = {
  created: "create",
  updated: "update",
  adopted: "adopt",
  skipped: "skip",
  failed: "failed",
  blocked: "blocked",
  would_create: "create",
  would_update: "update",
  would_block: "blocked",
  processing: "processing",
  pending: "processing",
};

const WIKIDATA_STATUS_TO_OPERATION: Record<string, string> = {
  success: "create",
  updated: "update",
  adopted: "adopt",
  would_adopt: "adopt",
  exists: "unchanged",
  skipped: "skip",
  failed: "failed",
  blocked: "blocked",
  processing: "processing",
  pending: "processing",
};

/**
 * New or *changed* outcomes from a progress poll.
 *
 * ``Map`` tracks last applied status so a ``processing`` pill can be replaced
 * by the terminal create/fail/etc. Legacy ``Set`` callers keep first-seen-only.
 */
export function collectNewProgressOutcomes(
  progress: StudioUploadProgressSlice,
  seenLocalIds: Set<string> | Map<string, string>,
): StudioUploadProgressOutcome[] {
  const isMap = seenLocalIds instanceof Map;
  const lastById = isMap ? seenLocalIds : null;
  const legacySet = isMap ? null : seenLocalIds;
  const raw = [
    ...(progress?.recent_item_outcomes ?? []),
    ...(progress?.item_outcome ? [progress.item_outcome] : []),
  ];
  const out: StudioUploadProgressOutcome[] = [];
  const emitted = new Set<string>();
  for (const o of raw) {
    const id = String(o?.local_id ?? "").trim();
    if (!id || emitted.has(id)) continue;
    const status = String(o.status ?? "");
    if (lastById) {
      const prev = lastById.get(id);
      if (prev !== undefined && prev === status) continue;
      lastById.set(id, status);
    } else if (legacySet) {
      if (legacySet.has(id)) continue;
      legacySet.add(id);
    }
    emitted.add(id);
    out.push({...o, local_id: id, status});
  }
  return out;
}

function outcomeQid(o: StudioUploadProgressOutcome): string | null {
  const raw = o.wikibase_id ?? o.qid;
  if (raw == null) return null;
  const s = String(raw).trim();
  return s || null;
}

type PatchableHmoItem = {
  local_id: string;
  status: string;
  wikibase_id: string | null;
  upload_outcome?: string | null;
  upload_message?: string | null;
  upload_at?: string | null;
};

/** Patch HMO review rows from live upload progress outcomes. */
export function patchHmoItemsFromUploadOutcomes<T extends PatchableHmoItem>(
  items: T[],
  outcomes: StudioUploadProgressOutcome[],
  uploadedAt = new Date().toISOString(),
): T[] {
  if (!outcomes.length) return items;
  const byId = new Map(
    outcomes
      .filter((o) => o.local_id)
      .map((o) => [String(o.local_id), o]),
  );
  let changed = false;
  const next = items.map((item) => {
    const o = byId.get(item.local_id);
    if (!o?.status) return item;
    const op = HMO_STATUS_TO_OPERATION[o.status];
    if (!op) return item;
    const qid = outcomeQid(o) ?? item.wikibase_id;
    const mapped =
      Boolean(qid)
      && o.status !== "failed"
      && o.status !== "blocked"
      && o.status !== "would_block"
      && o.status !== "processing"
      && o.status !== "pending";
    changed = true;
    return {
      ...item,
      upload_outcome: op,
      upload_message: o.message ?? item.upload_message ?? "",
      upload_at: (op === "processing") ? item.upload_at : uploadedAt,
      wikibase_id: (op === "processing") ? item.wikibase_id : qid,
      status: mapped ? "created" : item.status,
    };
  });
  return changed ? next : items;
}

type PatchableWikidataItem = {
  local_id?: string;
  existing_qid?: string | null;
  upload_outcome?: string | null;
  upload_message?: string | null;
  upload_at?: string | null;
};

/** Patch Wikidata Studio rows from live upload progress outcomes. */
export function patchWikidataItemsFromUploadOutcomes<T extends PatchableWikidataItem>(
  items: T[],
  outcomes: StudioUploadProgressOutcome[],
  uploadedAt = new Date().toISOString(),
): T[] {
  if (!outcomes.length) return items;
  const byId = new Map(
    outcomes
      .filter((o) => o.local_id)
      .map((o) => [String(o.local_id), o]),
  );
  let changed = false;
  const next = items.map((item) => {
    const id = item.local_id;
    if (!id) return item;
    const o = byId.get(id);
    if (!o?.status) return item;
    const op = WIKIDATA_STATUS_TO_OPERATION[o.status];
    if (!op) return item;
    const qid = (op === "processing")
      ? (item.existing_qid ?? null)
      : (outcomeQid(o) ?? item.existing_qid ?? null);
    changed = true;
    return {
      ...item,
      upload_outcome: op,
      upload_message: o.message ?? item.upload_message ?? "",
      upload_at: (op === "processing") ? item.upload_at : uploadedAt,
      existing_qid: qid,
    };
  });
  return changed ? next : items;
}
