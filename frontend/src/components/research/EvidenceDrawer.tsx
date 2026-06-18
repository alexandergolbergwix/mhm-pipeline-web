/**
 * EvidenceDrawer — right-side drawer that resolves a Linked-Data URI to its
 * MARC source record, approval trail, and authority matches.
 *
 * Opens when the user clicks a URI cell in the SPARQL results table.
 */

import { useEffect, useRef, useState } from "react";

import {
  type EvidenceApproval,
  type EvidenceAuthorityMatch,
  type EvidenceResponse,
  linkedDataExplorerApi,
} from "@/api/linkedDataExplorer";

interface EvidenceDrawerProps {
  projectId: string;
  uri: string | null;  // null = closed
  onClose: () => void;
}

function ConfidencePill({ level }: { level: string | null }) {
  const colour =
    level === "high"
      ? "badge-success"
      : level === "medium"
      ? "badge-warn"
      : "surface-inset text-faint";
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${colour}`}>
      {level ?? "—"}
    </span>
  );
}

function ApprovalRow({ approval }: { approval: EvidenceApproval }) {
  const date = approval.approved_at
    ? new Date(approval.approved_at).toLocaleDateString()
    : null;
  return (
    <li className="flex items-start gap-2 py-1.5 border-b border-[var(--line)] last:border-0">
      <span className="mt-0.5 shrink-0 text-success">✓</span>
      <div className="min-w-0">
        <span className="block text-sm font-medium text-ink truncate">
          {approval.entity_text}
        </span>
        <span className="block text-xs muted">
          {approval.source}
          {date && <> · {date}</>}
        </span>
      </div>
    </li>
  );
}

function AuthorityMatchRow({ match }: { match: EvidenceAuthorityMatch }) {
  return (
    <li className="py-1.5 border-b border-[var(--line)] last:border-0 space-y-0.5">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-ink truncate">
          {match.entity_text ?? "—"}
        </span>
        <ConfidencePill level={match.confidence} />
      </div>
      {match.matched_name && match.matched_name !== match.entity_text && (
        <p className="text-xs muted">→ {match.matched_name}</p>
      )}
      <div className="flex flex-wrap gap-2 text-xs muted">
        {match.wikidata_qid && (
          <a
            href={`https://www.wikidata.org/wiki/${match.wikidata_qid}`}
            target="_blank"
            rel="noopener noreferrer"
            className="link-accent hover:underline"
          >
            {match.wikidata_qid}
          </a>
        )}
        {match.viaf_id && (
          <a
            href={`https://viaf.org/viaf/${match.viaf_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="link-accent hover:underline"
          >
            VIAF {match.viaf_id}
          </a>
        )}
        {match.source && <span>{match.source}</span>}
      </div>
    </li>
  );
}

function MarcSection({ marc }: { marc: Record<string, unknown> | null }) {
  const [expanded, setExpanded] = useState(false);
  if (!marc) return <p className="text-sm text-faint italic">No MARC record found.</p>;

  const fields = Object.entries(marc);
  const visible = expanded ? fields : fields.slice(0, 6);
  return (
    <div>
      <dl className="space-y-1">
        {visible.map(([key, val]) => (
          <div key={key} className="grid grid-cols-[5rem,1fr] gap-1 text-xs">
            <dt className="font-mono text-faint truncate">{key}</dt>
            <dd className="text-ink break-all">
              {typeof val === "string" ? val : JSON.stringify(val)}
            </dd>
          </div>
        ))}
      </dl>
      {fields.length > 6 && (
        <button
          onClick={() => setExpanded((e) => !e)}
          className="mt-2 text-xs link-accent hover:underline"
        >
          {expanded ? "Show less" : `Show ${fields.length - 6} more fields`}
        </button>
      )}
    </div>
  );
}

export function EvidenceDrawer({ projectId, uri, onClose }: EvidenceDrawerProps) {
  const [data, setData] = useState<EvidenceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  const open = uri !== null;

  useEffect(() => {
    if (!uri) {
      setData(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    linkedDataExplorerApi
      .getEvidence(projectId, uri)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, uri]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      {/* backdrop */}
      <div
        className="fixed inset-0 overlay-scrim z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* drawer */}
      <aside
        ref={drawerRef}
        role="dialog"
        aria-label="Evidence panel"
        className="fixed right-0 top-0 h-full w-full max-w-md glass glass-shell-drawer z-50 flex flex-col overflow-hidden"
      >
        {/* header */}
        <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--line)] surface-inset shrink-0">
          <h2 className="text-sm font-semibold text-ink truncate" title={uri ?? ""}>
            Evidence
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded text-faint hover:text-ink hover:bg-slate-200"
            aria-label="Close evidence panel"
          >
            ✕
          </button>
        </header>

        {/* body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-5 text-sm">
          {/* URI */}
          <p className="font-mono text-xs muted break-all">{uri}</p>

          {loading && (
            <p className="text-faint text-sm">Loading evidence…</p>
          )}

          {error && (
            <p className="text-danger text-sm">{error}</p>
          )}

          {data && (
            <>
              {/* control number */}
              {data.control_number && (
                <div>
                  <p className="text-xs font-semibold muted uppercase tracking-wide mb-1">
                    Control number
                  </p>
                  <p className="font-mono text-sm text-ink">{data.control_number}</p>
                </div>
              )}

              {/* MARC */}
              <div>
                <p className="text-xs font-semibold muted uppercase tracking-wide mb-2">
                  MARC source
                </p>
                <MarcSection marc={data.marc} />
              </div>

              {/* approvals */}
              <div>
                <p className="text-xs font-semibold muted uppercase tracking-wide mb-2">
                  Approved entities ({data.approvals.length})
                </p>
                {data.approvals.length === 0 ? (
                  <p className="text-faint italic text-xs">No approved entities.</p>
                ) : (
                  <ul className="divide-y divide-[var(--line)]">
                    {data.approvals.map((a, i) => (
                      <ApprovalRow key={i} approval={a} />
                    ))}
                  </ul>
                )}
              </div>

              {/* authority matches */}
              <div>
                <p className="text-xs font-semibold muted uppercase tracking-wide mb-2">
                  Authority matches ({data.authority_matches.length})
                </p>
                {data.authority_matches.length === 0 ? (
                  <p className="text-faint italic text-xs">No authority matches.</p>
                ) : (
                  <ul className="divide-y divide-[var(--line)]">
                    {data.authority_matches.map((m, i) => (
                      <AuthorityMatchRow key={i} match={m} />
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
