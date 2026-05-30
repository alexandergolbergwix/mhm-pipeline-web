/**
 * NodeDetailPanel — side panel that surfaces everything about a clicked
 * RDF graph node:
 *
 *   • all rdf:type values (classes)
 *   • every datatype property (literal values: labels, dates, IDs, …)
 *   • every outgoing edge (with the target node's label + colour)
 *   • every incoming edge (symmetric)
 *
 * Click on a target/source label inside an edge → fire ``onNavigate(id)``
 * so the parent re-fetches that node and refocuses the graph on it.
 */

import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { Rdf, type NodeDetail } from "@/api/rdf";


export interface NodeDetailPanelProps {
  runId:       string;
  nodeId:      string;
  onClose:     () => void;
  onNavigate?: (nodeId: string) => void;
}


export function NodeDetailPanel(props: NodeDetailPanelProps) {
  const { runId, nodeId, onClose, onNavigate } = props;
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error,  setError]  = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null); setDetail(null);
    Rdf.node(runId, nodeId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.detail : String(e));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, nodeId]);

  return (
    <aside className="glass max-h-[78vh] overflow-auto p-4 space-y-4 w-full">
      <header className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="kicker">Node detail</div>
            {detail
              ? <h3 className="text-lg font-semibold truncate" title={detail.label}>
                  {detail.label}
                </h3>
              : <h3 className="text-lg font-semibold muted">
                  {loading ? "Loading…" : "(no detail)"}
                </h3>}
            <p className="muted text-[10px] font-mono break-all">{nodeId}</p>
          </div>
          <button onClick={onClose}
                  className="button-ghost !py-1 !px-2 text-xs shrink-0"
                  title="Close">
            ✕
          </button>
        </div>
        {detail && (
          <div className="flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-full"
                  style={{ background: detail.color }} />
            <span className="kicker">{detail.type}</span>
          </div>
        )}
      </header>

      {error && <p className="text-red-300 text-sm">{error}</p>}

      {detail && (
        <>
          {/* rdf:type values */}
          {detail.types.length > 0 && (
            <Section title={`Classes (${detail.types.length})`}>
              <ul className="space-y-1 text-xs">
                {detail.types.map((t, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="muted shrink-0">rdf:type</span>
                    <span className="font-mono break-all">
                      <span className="text-ink">{t.label}</span>
                      <span className="muted ml-1">({t.uri})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {/* Datatype properties */}
          {detail.properties.length > 0 && (
            <Section title={`Properties (${detail.properties.length})`}>
              <dl className="text-xs space-y-1.5">
                {detail.properties.map((p, i) => (
                  <div key={i} className="grid grid-cols-[140px_1fr] gap-x-3 items-baseline">
                    <dt className="muted truncate" title={p.predicate}>
                      {p.predicate_label}
                    </dt>
                    <dd className="break-words">
                      <span className="text-ink">{p.value}</span>
                      {p.datatype && (
                        <span className="muted text-[10px] ml-2">({_shortenDatatype(p.datatype)})</span>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </Section>
          )}

          {/* Outgoing edges */}
          {detail.outgoing.length > 0 && (
            <Section title={`Outgoing (${detail.outgoing.length})`}>
              <ul className="space-y-1 text-xs">
                {detail.outgoing.map((e, i) => (
                  <EdgeRow key={i} edge={e} dir="out" onNavigate={onNavigate} />
                ))}
              </ul>
            </Section>
          )}

          {/* Incoming edges */}
          {detail.incoming.length > 0 && (
            <Section title={`Incoming (${detail.incoming.length})`}>
              <ul className="space-y-1 text-xs">
                {detail.incoming.map((e, i) => (
                  <EdgeRow key={i} edge={e} dir="in" onNavigate={onNavigate} />
                ))}
              </ul>
            </Section>
          )}

          {/* Empty state */}
          {detail.types.length === 0 && detail.properties.length === 0 &&
            detail.outgoing.length === 0 && detail.incoming.length === 0 && (
            <p className="muted text-sm italic">
              No metadata attached to this node.
            </p>
          )}
        </>
      )}
    </aside>
  );
}


// ── Internals ──────────────────────────────────────────────────────────


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="kicker mb-1.5">{title}</div>
      {children}
    </section>
  );
}


function EdgeRow({
  edge, dir, onNavigate,
}: {
  edge: import("@/api/rdf").NodeEdgeRef;
  dir:  "in" | "out";
  onNavigate?: (id: string) => void;
}) {
  const id    = dir === "out" ? edge.target_id    : edge.source_id;
  const label = dir === "out" ? edge.target_label : edge.source_label;
  const color = dir === "out" ? edge.target_color : edge.source_color;
  const arrow = dir === "out" ? "→" : "←";
  return (
    <li className="flex items-center gap-2 flex-wrap">
      <span className="muted shrink-0 font-mono" title={edge.predicate}>
        {edge.predicate_label}
      </span>
      <span className="muted shrink-0">{arrow}</span>
      {color && (
        <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
              style={{ background: color }} />
      )}
      {id && onNavigate
        ? <button onClick={() => onNavigate(id)}
                  className="text-biu-sky hover:underline text-left flex-1 truncate"
                  title={id}>
            {label || id}
          </button>
        : <span className="text-ink flex-1 truncate" title={id || ""}>
            {label || id || "(unknown)"}
          </span>}
    </li>
  );
}


function _shortenDatatype(dt: string): string {
  const idx = Math.max(dt.lastIndexOf("#"), dt.lastIndexOf("/"));
  return idx > 0 ? dt.slice(idx + 1) : dt;
}
