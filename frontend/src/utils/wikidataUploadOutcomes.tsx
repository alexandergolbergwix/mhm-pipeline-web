import {useMemo, useState} from "react";

import type {UploadOutcome, UploadResponse} from "@/api/wikidataStudio";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {UploadOutcomeBadge} from "@/components/shared/UploadOutcomeBadge";
import type {RunJobProgress} from "@/api/runJobs";

export type WikidataUploadOutcomeRow = {
  local_id: string;
  label?: string | null;
  entity_type?: string | null;
  status?: string;
  qid?: string | null;
  wikibase_id?: string | null;
  message?: string | null;
};

export type WikidataUploadOutcomeCounts = {
  created: number;
  updated: number;
  adopted: number;
  blocked: number;
  skipped: number;
  failed: number;
  pending: number;
};

const EMPTY_COUNTS: WikidataUploadOutcomeCounts = {
  created: 0,
  updated: 0,
  adopted: 0,
  blocked: 0,
  skipped: 0,
  failed: 0,
  pending: 0,
};

const STATUS_TO_COUNT_KEY: Record<string, keyof WikidataUploadOutcomeCounts> = {
  success: "created",
  created: "created",
  would_create: "created",
  updated: "updated",
  exists: "updated",
  would_update: "updated",
  adopted: "adopted",
  would_adopt: "adopted",
  blocked: "blocked",
  would_block: "blocked",
  skipped: "skipped",
  failed: "failed",
  pending: "pending",
  processing: "pending",
};

export function tallyWikidataUploadOutcomeCounts(
  outcomes: Array<{status?: string | null}>,
): WikidataUploadOutcomeCounts {
  const counts = {...EMPTY_COUNTS};
  for (const o of outcomes) {
    const key = STATUS_TO_COUNT_KEY[String(o.status ?? "").toLowerCase()];
    if (key) counts[key] += 1;
  }
  return counts;
}

export function progressOutcomeCounts(
  progress: RunJobProgress | null | undefined,
): WikidataUploadOutcomeCounts | null {
  const raw = progress?.outcome_counts;
  if (!raw || typeof raw !== "object") return null;
  return {
    created: Number(raw.created ?? 0),
    updated: Number(raw.updated ?? 0),
    adopted: Number(raw.adopted ?? 0),
    blocked: Number(raw.blocked ?? 0),
    skipped: Number(raw.skipped ?? 0),
    failed: Number(raw.failed ?? 0),
    pending: Number(raw.pending ?? 0),
  };
}

export function liveUploadOutcomeRows(
  progress: RunJobProgress | null | undefined,
  terminalOutcomes: UploadOutcome[] | null | undefined,
  jobDone: boolean,
): WikidataUploadOutcomeRow[] {
  if (jobDone && terminalOutcomes?.length) {
    return terminalOutcomes.map((o) => ({
      local_id: o.local_id,
      label: o.label,
      entity_type: o.entity_type,
      status: o.status,
      qid: o.qid,
      wikibase_id: o.qid,
      message: o.message,
    }));
  }
  const recent = progress?.recent_item_outcomes ?? [];
  const byId = new Map<string, WikidataUploadOutcomeRow>();
  for (const o of recent) {
    const id = String(o.local_id ?? "").trim();
    if (!id) continue;
    byId.set(id, {...o, local_id: id});
  }
  const item = progress?.item_outcome;
  if (item?.local_id) {
    byId.set(String(item.local_id), {...item, local_id: String(item.local_id)});
  }
  return Array.from(byId.values()).reverse();
}

export function UploadOutcomeCountStrip({
  counts,
  dryRun = false,
}: {
  counts: WikidataUploadOutcomeCounts;
  dryRun?: boolean;
}) {
  const createLabel = dryRun ? "Would create" : "Created";
  const updateLabel = dryRun ? "Would update" : "Updated";
  return (
    <p className="text-sm flex flex-wrap gap-x-2 gap-y-1">
      <span>
        <span className="muted">{createLabel}:</span>{" "}
        <b className="text-biu-sky">{counts.created}</b>
      </span>
      <span>·</span>
      <span>
        <span className="muted">{updateLabel}:</span>{" "}
        <b className="text-biu-sky">{counts.updated}</b>
      </span>
      {counts.adopted > 0 && (
        <>
          <span>·</span>
          <span>
            <span className="muted">adopted</span>{" "}
            <b className="text-biu-sky">{counts.adopted}</b>
          </span>
        </>
      )}
      {counts.blocked + counts.skipped > 0 && (
        <>
          <span>·</span>
          <span className="text-warn">
            blocked/skipped {counts.blocked + counts.skipped}
          </span>
        </>
      )}
      {counts.failed > 0 && (
        <>
          <span>·</span>
          <span className="text-danger">failed {counts.failed}</span>
        </>
      )}
    </p>
  );
}

export function UploadOutcomeTable({
  rows,
  testIdPrefix = "wikidata-upload-outcome",
}: {
  rows: WikidataUploadOutcomeRow[];
  testIdPrefix?: string;
}) {
  if (!rows.length) {
    return <p className="muted text-sm">No item outcomes yet.</p>;
  }
  return (
    <CuratorTableScroll className="max-h-[min(50vh,28rem)]">
      <table className="w-full text-sm" data-testid={`${testIdPrefix}-table`}>
        <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
          <tr>
            {["Local id", "Label", "Type", "Status", "QID", "Message"].map((h) => (
              <th key={h} className="text-left px-3 py-2">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.local_id} className="border-t border-white/5">
              <td className="px-3 py-2 font-mono text-xs">{o.local_id}</td>
              <td className="px-3 py-2 text-xs max-w-[12rem] truncate" title={o.label ?? ""}>
                {o.label ?? "—"}
              </td>
              <td className="px-3 py-2 text-xs">{o.entity_type ?? "—"}</td>
              <td className="px-3 py-2">
                <UploadOutcomeBadge
                  outcome={o.status}
                  message={o.message}
                  localId={o.local_id}
                  testIdPrefix={testIdPrefix}
                />
              </td>
              <td className="px-3 py-2 font-mono text-xs">{o.qid ?? o.wikibase_id ?? "—"}</td>
              <td className="px-3 py-2 text-xs max-w-[20rem] truncate" title={o.message ?? ""}>
                {o.message ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </CuratorTableScroll>
  );
}

export function UploadResultSummary({result}: {result: UploadResponse}) {
  const [expand, setExpand] = useState(false);
  const counts = useMemo(
    () => tallyWikidataUploadOutcomeCounts(result.outcomes),
    [result.outcomes],
  );

  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <UploadOutcomeCountStrip counts={counts} dryRun={result.dry_run} />
        <button onClick={() => setExpand((v) => !v)} className="button-ghost text-xs">
          {expand ? "Hide details" : "Show details"}
        </button>
      </div>
      {expand && (
        <UploadOutcomeTable
          rows={result.outcomes.map((o) => ({
            local_id: o.local_id,
            label: o.label,
            entity_type: o.entity_type,
            status: o.status,
            qid: o.qid,
            wikibase_id: o.qid,
            message: o.message,
          }))}
        />
      )}
    </div>
  );
}
