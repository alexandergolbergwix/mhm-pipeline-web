/**
 * SchemaEntryDetailDrawer — pinnable right-side drawer surfacing every
 * signal for one HMO ontology schema entry (a class or property from
 * ``bootstrap-schema``'s dry-run/live report):
 *
 *  1. Ontology — full URI, description, datatype (for properties).
 *  2. Wikibase — mapped id + link to the live Item/Property on
 *     ``mhm-hmo.wikibase.cloud``.
 *  3. AI verdict — full reasoning (not just the truncated pill tooltip).
 *  4. RDF graph usage — how many times this class/property actually
 *     appears in the *current run's* RDF graph, with example nodes/
 *     triples. This is the "deep dive" the flat table can't show:
 *     "would_create"/"skipped" only reflects the Wikibase mapping, not
 *     whether the ontology term is used at all in the data.
 *
 * Pattern matches AuthorityDetailDrawer / EntityDetailDrawer (pin +
 * Esc-to-close fixed drawer), scoped down to this panel's needs.
 */

import {useEffect, useRef, useState} from "react";

import type {AiVerdict} from "@/api/extractionApprovals";
import type {HmoSchemaBootstrapEntry} from "@/api/hmoWikibaseSchema";
import {Rdf, type OntologyUsageResponse} from "@/api/rdf";
import {AiVerdictPill} from "@/components/extraction/AiVerdictPill";
import {Glass, GlassPill} from "@/components/glass";
import {useFocusTrap} from "@/hooks/useFocusTrap";

export interface SchemaEntryDetailDrawerProps {
  entry: HmoSchemaBootstrapEntry | null;   // null = drawer closed
  runId?: string;
  verdict?: AiVerdict | null;
  wikibaseBaseUrl?: string;
  onClose: () => void;
}

const DEFAULT_WIKIBASE_BASE_URL = "https://mhm-hmo.wikibase.cloud";

export function SchemaEntryDetailDrawer({
  entry, runId, verdict, wikibaseBaseUrl, onClose,
}: SchemaEntryDetailDrawerProps) {
  const [pinned, setPinned] = useState(false);
  const [usage, setUsage] = useState<OntologyUsageResponse | null>(null);
  const [usageBusy, setUsageBusy] = useState(false);
  const [usageError, setUsageError] = useState<string | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  const open = entry !== null;
  useFocusTrap(open, drawerRef);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !pinned) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, pinned, onClose]);

  useEffect(() => {
    setUsage(null);
    setUsageError(null);
    if (!entry || !runId) return;
    const kind = entry.entity_kind === "class" ? "class" : "property";
    setUsageBusy(true);
    let cancelled = false;
    Rdf.ontologyUsage(runId, entry.ontology_uri, kind)
      .then((r) => { if (!cancelled) setUsage(r); })
      .catch(() => { if (!cancelled) setUsageError("Could not load RDF graph usage for this entry."); })
      .finally(() => { if (!cancelled) setUsageBusy(false); });
    return () => { cancelled = true; };
  }, [entry, runId]);

  const base = wikibaseBaseUrl || DEFAULT_WIKIBASE_BASE_URL;
  const wikibaseHref = entry?.wikibase_id
    ? `${base.replace(/\/$/, "")}/wiki/${entry.entity_kind === "property" ? "Property" : "Item"}:${entry.wikibase_id}`
    : null;

  return (
    <>
      <Glass as="aside" className="flex flex-col p-0" ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="schema-entry-detail-drawer-title"
        data-testid="schema-entry-detail-drawer"
        style={{
          position:      "fixed",
          top:           0,
          right:         0,
          bottom:        0,
          width:         "min(640px, 95vw)",
          zIndex:        40,
          transform:     open ? "translateX(0)" : "translateX(110%)",
          transition:    "transform 220ms ease-out",
          pointerEvents: open ? "auto" : "none",
        }}>
        <header className="flex items-start justify-between gap-3 px-4 pt-4 pb-2 border-b border-white/5">
          <div className="min-w-0">
            <div className="kicker">{entry?.entity_kind ?? "schema entry"}</div>
            <h3 id="schema-entry-detail-drawer-title"
                className="text-base font-semibold truncate">
              {entry?.label ?? ""}
            </h3>
            {entry && <EntryStatusPill status={entry.status} />}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <label className="muted flex items-center gap-1 cursor-pointer select-none text-[11px]"
                   title="Keep this drawer open while browsing the table.">
              <input type="checkbox" checked={pinned}
                     onChange={(e) => setPinned(e.target.checked)} />
              Pin
            </label>
            <button onClick={onClose}
                    className="button-ghost !py-0.5 !px-2 text-xs"
                    title="Close (Esc)">✕</button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-xs">
          {entry && (
            <>
              <OntologyCard entry={entry} />
              <WikibaseCard entry={entry} href={wikibaseHref} />
              <AiVerdictCard verdict={verdict} />
              <RdfUsageCard
                entry={entry}
                runId={runId}
                usage={usage}
                busy={usageBusy}
                error={usageError}
              />
            </>
          )}
        </div>
      </Glass>
      {open && (
        <button
          aria-hidden
          tabIndex={-1}
          onClick={() => { if (!pinned) onClose(); }}
          style={{
            position: "fixed", inset: 0, zIndex: 30,
            background: "transparent", cursor: "default",
          }}
        />
      )}
    </>
  );
}

function Card({title, children}: {title: string; children: React.ReactNode}) {
  return (
    <section className="rounded-lg border border-white/5 bg-white/2 p-3 space-y-2">
      <h4 className="kicker">{title}</h4>
      {children}
    </section>
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

function OntologyCard({entry}: {entry: HmoSchemaBootstrapEntry}) {
  return (
    <Card title="Ontology">
      <p className="font-mono text-[11px] break-all select-all">{entry.ontology_uri}</p>
      {entry.description && <p className="text-ink leading-relaxed">{entry.description}</p>}
      {entry.datatype && (
        <p className="muted">Datatype: <span className="text-ink">{entry.datatype}</span></p>
      )}
      {entry.message && (
        <p className="text-danger">{entry.message}</p>
      )}
    </Card>
  );
}

function WikibaseCard({entry, href}: {entry: HmoSchemaBootstrapEntry; href: string | null}) {
  return (
    <Card title="Wikibase">
      {entry.wikibase_id ? (
        <p>
          <span className="font-mono text-ink">{entry.wikibase_id}</span>
          {href && (
            <>
              {" · "}
              <a href={href} target="_blank" rel="noreferrer" className="text-biu-sky underline">
                Open on wikibase.cloud ↗
              </a>
            </>
          )}
        </p>
      ) : (
        <p className="muted">Not created on Wikibase yet.</p>
      )}
    </Card>
  );
}

function AiVerdictCard({verdict}: {verdict?: AiVerdict | null}) {
  return (
    <Card title="AI verdict">
      <AiVerdictPill verdict={verdict} size="md" />
      {verdict?.reasoning && (
        <p className="text-ink leading-relaxed">{verdict.reasoning}</p>
      )}
      {(verdict?.model || verdict?.judged_at) && (
        <p className="muted text-[10px]">
          {verdict.model && <>Model: {verdict.model}</>}
          {verdict.model && verdict.judged_at && " · "}
          {verdict.judged_at && <>Judged {new Date(verdict.judged_at).toLocaleString()}</>}
        </p>
      )}
      {!verdict && (
        <p className="muted">Not verified with AI yet.</p>
      )}
    </Card>
  );
}

function RdfUsageCard({
  entry, runId, usage, busy, error,
}: {
  entry: HmoSchemaBootstrapEntry;
  runId?: string;
  usage: OntologyUsageResponse | null;
  busy: boolean;
  error: string | null;
}) {
  if (!runId) {
    return (
      <Card title="RDF graph usage">
        <p className="muted">
          Open the schema panel from inside a run to see whether this{" "}
          {entry.entity_kind} is actually used in that run's RDF graph.
        </p>
      </Card>
    );
  }

  return (
    <Card title="RDF graph usage">
      {busy && <p className="muted">Checking the run's RDF graph…</p>}
      {error && <p className="text-danger">{error}</p>}
      {!busy && !error && usage && !usage.built && (
        <p className="muted">
          No RDF graph has been built for this run yet — build it in the{" "}
          <a href={`/runs/${runId}/rdf`} className="text-biu-sky underline">RDF Graph</a> tab
          to see real usage data for this {entry.entity_kind}.
        </p>
      )}
      {!busy && !error && usage && usage.built && (
        <>
          <p>
            {usage.count > 0 ? (
              <>
                Used <b className="text-biu-sky">{usage.count}</b>{" "}
                {entry.entity_kind === "class" ? "time(s) as a node type" : "time(s) as a predicate"}{" "}
                in this run's graph ({usage.total_triples.toLocaleString()} triples total).
              </>
            ) : (
              <>
                Not used anywhere in this run's RDF graph ({usage.total_triples.toLocaleString()}{" "}
                triples total) — this ontology term has no live data yet.
              </>
            )}
          </p>
          {usage.examples.length > 0 && (
            <ul className="space-y-1 border-t border-white/5 pt-2">
              {usage.examples.map((ex, i) => (
                <li key={i} className="text-ink font-mono text-[10px] truncate">
                  {entry.entity_kind === "class"
                    ? (ex.label ?? ex.node_id)
                    : `${ex.subject_label ?? ex.subject_id} → ${ex.object_label ?? ex.object_id}`}
                </li>
              ))}
            </ul>
          )}
          {usage.count > 0 && (
            <a href={`/runs/${runId}/rdf`} className="text-biu-sky underline text-[11px]">
              Open the RDF Graph tab and search “{entry.label}” to explore it visually →
            </a>
          )}
        </>
      )}
    </Card>
  );
}
