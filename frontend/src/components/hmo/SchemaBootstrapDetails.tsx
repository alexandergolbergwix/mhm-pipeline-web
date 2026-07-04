import {useMemo, useState} from "react";

import type {HmoSchemaBootstrapEntry, HmoSchemaBootstrapResult} from "@/api/hmoWikibaseSchema";
import {schemaEntryLocalId} from "@/api/hmoSchemaVerify";
import {AiVerdictPill} from "@/components/extraction/AiVerdictPill";
import type {AiVerdict} from "@/api/extractionApprovals";
import {GlassPill} from "@/components/glass";


type StatusFilter = "all" | "created" | "would_create" | "skipped" | "failed";


export interface SchemaBootstrapDetailsProps {
  result: HmoSchemaBootstrapResult;
  defaultExpanded?: boolean;
  verdicts?: Record<string, AiVerdict>;
}


function countByStatus(entries: HmoSchemaBootstrapEntry[]): Record<string, number> {
  const counts: Record<string, number> = {
    created: 0,
    would_create: 0,
    skipped: 0,
    failed: 0,
  };
  for (const e of entries) {
    if (e.status in counts) counts[e.status] += 1;
  }
  return counts;
}


export function SchemaBootstrapDetails({
  result,
  defaultExpanded = true,
  verdicts = {},
}: SchemaBootstrapDetailsProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");

  const counts = useMemo(() => countByStatus(result.entries), [result.entries]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return result.entries.filter((entry) => {
      if (statusFilter !== "all" && entry.status !== statusFilter) return false;
      if (!q) return true;
      return (
        entry.label.toLowerCase().includes(q)
        || entry.ontology_uri.toLowerCase().includes(q)
        || (entry.wikibase_id ?? "").toLowerCase().includes(q)
      );
    });
  }, [result.entries, statusFilter, query]);

  const headline = result.dry_run ? "Would create" : "Created";
  const headlineCount = result.dry_run ? result.would_create : result.created;

  return (
    <div className="border-t border-white/5 pt-3 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm">
          <span className="muted">{headline}:</span>{" "}
          <b className="text-biu-sky">{headlineCount}</b>
          {" · "}
          <span className="muted">skipped {result.skipped}</span>
          {result.failed > 0 && (
            <>
              {" · "}
              <span className="text-danger">failed {result.failed}</span>
            </>
          )}
        </p>
        <button onClick={() => setExpanded((v) => !v)} className="button-ghost text-xs">
          {expanded ? "Hide details" : "Show details"}
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        <SummaryChip
          label="would create"
          count={counts.would_create}
          active={statusFilter === "would_create"}
          onClick={() => setStatusFilter((f) => (f === "would_create" ? "all" : "would_create"))}
          tone="text-warn"
        />
        <SummaryChip
          label="created"
          count={counts.created}
          active={statusFilter === "created"}
          onClick={() => setStatusFilter((f) => (f === "created" ? "all" : "created"))}
          tone="text-biu-sky"
        />
        <SummaryChip
          label="skipped"
          count={counts.skipped}
          active={statusFilter === "skipped"}
          onClick={() => setStatusFilter((f) => (f === "skipped" ? "all" : "skipped"))}
          tone="muted"
        />
        <SummaryChip
          label="failed"
          count={counts.failed}
          active={statusFilter === "failed"}
          onClick={() => setStatusFilter((f) => (f === "failed" ? "all" : "failed"))}
          tone="text-danger"
        />
      </div>

      {expanded && (
        <>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by label, URI, or Wikibase id…"
            className="input-glass w-full text-sm"
          />
          <div className="overflow-x-auto border border-white/5 rounded-lg max-h-96 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0">
                <tr>
                  <th className="text-left px-3 py-2">Kind</th>
                  <th className="text-left px-3 py-2">Label</th>
                  <th className="text-left px-3 py-2">Ontology URI</th>
                  <th className="text-left px-3 py-2">Status</th>
                  <th className="text-left px-3 py-2">Wikibase id</th>
                  <th className="text-left px-3 py-2">AI verdict</th>
                  <th className="text-left px-3 py-2">Message</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((entry) => {
                  const localId = schemaEntryLocalId(entry);
                  const verdict = verdicts[localId] ?? verdicts[entry.ontology_uri] ?? null;
                  return (
                    <tr key={entry.ontology_uri} className="border-t border-white/5 align-top">
                      <td className="px-3 py-2 text-xs muted">{entry.entity_kind}</td>
                      <td className="px-3 py-2 text-xs">{entry.label}</td>
                      <td className="px-3 py-2 text-xs font-mono max-w-[14rem] truncate" title={entry.ontology_uri}>
                        {entry.ontology_uri}
                      </td>
                      <td className="px-3 py-2">
                        <EntryStatusPill status={entry.status} />
                      </td>
                      <td className="px-3 py-2 text-xs font-mono">{entry.wikibase_id ?? "—"}</td>
                      <td className="px-3 py-2">
                        <AiVerdictPill verdict={verdict} size="sm" />
                      </td>
                      <td className="px-3 py-2 text-xs muted max-w-[12rem]">{entry.message || "—"}</td>
                    </tr>
                  );
                })}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center muted text-sm">
                      No entries match the current filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-xs muted">
            Showing {rows.length} of {result.entries.length} entries
          </p>
        </>
      )}
    </div>
  );
}


function SummaryChip({
  label,
  count,
  active,
  onClick,
  tone,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
  tone: string;
}) {
  if (count === 0) return null;
  return (
    <button type="button" onClick={onClick}>
      <GlassPill
        className={`px-2 py-0.5 text-[10px] kicker ${tone} ${active ? "ring-1 ring-biu-sky/60" : ""}`}
      >
        {label} {count}
      </GlassPill>
    </button>
  );
}


function EntryStatusPill({status}: {status: string}) {
  const tone =
    status === "created"
      ? "text-biu-sky"
      : status === "would_create"
        ? "text-warn"
        : status === "skipped"
          ? "muted"
          : "text-danger";
  return <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`}>{status}</GlassPill>;
}
