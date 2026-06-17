/**
 * NodeDetailPanel — floating overlay that surfaces everything about a
 * clicked RDF graph node:
 *
 *   • all rdf:type values (classes)
 *   • every datatype property (literal values: labels, dates, IDs, …)
 *   • every outgoing edge (with the target node's label + colour)
 *   • every incoming edge (symmetric)
 *
 * Click on a target/source label inside an edge → fire ``onNavigate(id)``
 * so the parent re-fetches that node and refocuses the graph on it.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";

import {ApiError} from "@/api/client";
import {Rdf, type NodeDetail} from "@/api/rdf";
import {Glass} from "@/components/glass";


export interface NodeDetailPanelProps {
  runId:       string;
  nodeId:      string;
  onClose:     () => void;
  onNavigate?: (nodeId: string) => void;
}


export function NodeDetailPanel(props: NodeDetailPanelProps) {
  const {runId, nodeId, onClose, onNavigate} = props;
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState({x: 0, y: 0});
  const dragRef = useRef<{
    startX: number;
    startY: number;
    origX: number;
    origY: number;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);
    Rdf.node(runId, nodeId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.detail : String(e));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, nodeId]);

  const onDragStart = useCallback((e: ReactMouseEvent<HTMLElement>) => {
    if ((e.target as HTMLElement).closest("button, a, input, select, textarea")) return;
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      origX: offset.x,
      origY: offset.y,
    };
    e.preventDefault();
  }, [offset]);

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!dragRef.current) return;
      const {startX, startY, origX, origY} = dragRef.current;
      setOffset({
        x: origX + e.clientX - startX,
        y: origY + e.clientY - startY,
      });
    }
    function onUp() {
      dragRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  return (
    <Glass
      variant="panel"
      as="aside"
      data-testid="node-detail-panel"
      className="absolute z-50 top-4 right-4 w-[min(360px,calc(100%-2rem))] max-h-[calc(100%-2rem)] overflow-auto p-4 space-y-4 shadow-2xl pointer-events-auto"
      style={{transform: `translate(${offset.x}px, ${offset.y}px)`}}
    >
      <header className="space-y-1 cursor-move select-none" onMouseDown={onDragStart}>
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
                  className="button-ghost !py-1 !px-2 text-xs shrink-0 cursor-pointer"
                  title="Close">
            ✕
          </button>
        </div>
        {detail && (
          <div className="flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-full"
                  style={{background: detail.color}} />
            <span className="kicker">{detail.type}</span>
          </div>
        )}
      </header>

      {error && <p className="text-red-300 text-sm">{error}</p>}

      {detail && (
        <>
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

          {detail.outgoing.length > 0 && (
            <Section title={`Outgoing (${detail.outgoing.length})`}>
              <ul className="space-y-1 text-xs">
                {detail.outgoing.map((e, i) => (
                  <EdgeRow key={i} edge={e} dir="out" onNavigate={onNavigate} />
                ))}
              </ul>
            </Section>
          )}

          {detail.incoming.length > 0 && (
            <Section title={`Incoming (${detail.incoming.length})`}>
              <ul className="space-y-1 text-xs">
                {detail.incoming.map((e, i) => (
                  <EdgeRow key={i} edge={e} dir="in" onNavigate={onNavigate} />
                ))}
              </ul>
            </Section>
          )}

          {detail.types.length === 0 && detail.properties.length === 0 &&
            detail.outgoing.length === 0 && detail.incoming.length === 0 && (
            <p className="muted text-sm italic">
              No metadata attached to this node.
            </p>
          )}
        </>
      )}
    </Glass>
  );
}


function Section({title, children}: {title: string; children: React.ReactNode}) {
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
              style={{background: color}} />
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
