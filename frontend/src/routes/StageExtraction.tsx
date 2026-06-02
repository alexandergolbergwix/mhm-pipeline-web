/**
 * AI Extraction — per-run page.
 *
 * Three states:
 *  - ``idle``     — no ``ner_results.json`` on disk. Show big "Start"
 *                   button; the SSE stream populates the table live.
 *  - ``running``  — SSE stream open. Show progress bar (processed / total)
 *                   + live event log + Cancel button.
 *  - ``complete`` — fetch the persisted results and render the table.
 *
 * The SSE event shape comes from ``backend/app/pipeline/extraction.py``:
 *   extraction.start         { total }
 *   extraction.record.done   { index, total, control_number, entities, ml_genres }
 *   extraction.end           { records }   ← records is a list[dict]
 *   extraction.error         { message }
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import {
  Extraction,
  streamExtraction,
  type ExtractionEvent,
  type ExtractionRecord,
} from "@/api/extraction";
import {
  ExtractionApprovals,
  type Entity,
} from "@/api/extractionApprovals";
import { Runs } from "@/api/runs";
import { useApprovalStore } from "@/hooks/useApprovalStore";
import { EntityTable } from "@/components/extraction/EntityTable";
import { EntityFilterChips } from "@/components/extraction/EntityFilterChips";
import { EntityActionsBar } from "@/components/extraction/EntityActionsBar";
import { EntityEditModal } from "@/components/extraction/EntityEditModal";
import { AutoApproveRuleBuilder } from "@/components/extraction/AutoApproveRuleBuilder";
import { EntityDetailDrawer } from "@/components/extraction/EntityDetailDrawer";
import { NerVerificationModal } from "@/components/extraction/NerVerificationModal";


type Phase = "idle" | "running" | "complete" | "error";


type ModelRole   = "person" | "provenance" | "contents" | "genre";
type ModelStatus = "idle" | "ready" | "unavailable" | "warming" | "error";


interface ModelState {
  status: ModelStatus;
  note:   string;
}


const _ROLE_LABELS: Record<ModelRole, string> = {
  person:     "Person NER",
  provenance: "Provenance NER",
  contents:   "Contents NER",
  genre:      "Genre classifier",
};


function _initialModels(): Record<ModelRole, ModelState> {
  return {
    person:     { status: "idle", note: "" },
    provenance: { status: "idle", note: "" },
    contents:   { status: "idle", note: "" },
    genre:      { status: "idle", note: "" },
  };
}


type ExtractionMode = "local" | "hf-api" | "modal";


export default function StageExtraction() {
  const { runId } = useParams<{ runId: string }>();

  // Project id is needed for the per-row "📜 View edit history"
  // affordance on EntityTable / EntityDetailDrawer (HistoryTimeline is
  // keyed by (project, entity_type, entity_id)). Fetched once on mount;
  // empty string while in-flight or unresolved — the history button is
  // hidden in that state.
  const [projectId, setProjectId] = useState<string>("");
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    Runs.get(runId)
      .then((r) => { if (!cancelled) setProjectId(r.project_id); })
      .catch(() => { /* non-fatal: history button stays hidden */ });
    return () => { cancelled = true; };
  }, [runId]);

  const [phase, setPhase] = useState<Phase>("idle");
  // Live operational status — the latest `extraction.step` message
  // from the backend. Surfaces "Contacting Modal…", "Models ready
  // (4/4) in 12.3s", "Processing 68 records" etc. so the curator
  // never sees a silent 0% bar during the warm-up window.
  const [statusMessage, setStatusMessage] = useState<string>("");
  const [statusPhase,   setStatusPhase]   = useState<"warming" | "warmed" | "processing" | "">("");
  // When did the current status begin? Lets the banner show a live
  // "(12s)" counter so the user knows nothing is actually stuck.
  const [statusStartedAt, setStatusStartedAt] = useState<number | null>(null);
  const [statusTick, setStatusTick] = useState(0);
  useEffect(() => {
    if (statusStartedAt === null) return;
    const id = window.setInterval(() => setStatusTick((t) => t + 1), 500);
    return () => window.clearInterval(id);
  }, [statusStartedAt]);
  void statusTick; // dependency only — value isn't read directly
  const [records, setRecords] = useState<ExtractionRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [processed, setProcessed] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [models, setModels] = useState<Record<ModelRole, ModelState>>(_initialModels);

  // ─── AI Extraction review UI state (Rule W-16) ─────────────────────────
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [columnFilters, setColumnFilters] = useState<Record<string, Set<string>>>({});
  const [filteredEntities, setFilteredEntities] = useState<Entity[]>([]);
  const [editEntity, setEditEntity] = useState<Entity | null>(null);
  // The new EntityDetailDrawer takes the full Entity row; clicking a
  // table row, the 👁 button, OR the AI verdict pill opens it.
  const [detailEntity, setDetailEntity] = useState<Entity | null>(null);
  const [autoApproveOpen, setAutoApproveOpen] = useState(false);
  const [verifyScope, setVerifyScope] = useState<
    null | { scopeKind: "selection" | "all"; entityIds: string[]; label: string }
  >(null);
  const approvalStore = useApprovalStore(runId ?? "", {
    active: verifyScope !== null,
  });
  const refreshEntities = approvalStore.refresh;
  // Mode is undefined → backend picks from its own EXTRACTION_MODE
  // env var (resolve_mode in extraction_backend.py). Used to be
  // hard-coded "hf-api" here, which overrode the backend's preference
  // — fine when HF was the only option, broken once Modal landed
  // (CLAUDE.md Rule W-11). Drop the override; let the env decide.
  const mode: ExtractionMode | undefined = undefined;
  // The actual mode the backend chose, filled in from the
  // `extraction.start` event payload. The UI label reads this.
  const [resolvedMode, setResolvedMode] = useState<string>("");

  // Cross-user shared cache. Default OFF (= use cache for efficiency);
  // tick to force a fresh call against HF when you want to test a
  // newly-tuned model. Either way, the fresh result lands in the cache
  // so the NEXT user warm-hits.
  const [skipCache, setSkipCache] = useState(false);

  // Per-role enable/disable. Persisted to localStorage so a curator's
  // "I only want Person NER" choice survives reloads. Default = all 4.
  // Provenance + Contents are archival-only on HF Inference Providers
  // (custom-code repos), so they default OFF in Mode B.
  const [enabledRoles, setEnabledRoles] = useState<Record<ModelRole, boolean>>(() => {
    try {
      const stored = localStorage.getItem("mhm.extraction.enabledRoles");
      if (stored) return JSON.parse(stored);
    } catch { /* ignore */ }
    return {
      person:     true,
      provenance: false,    // not on HF Inference Providers (custom-code)
      contents:   false,    // not on HF Inference Providers (custom-code)
      genre:      true,
    };
  });
  useEffect(() => {
    localStorage.setItem("mhm.extraction.enabledRoles", JSON.stringify(enabledRoles));
  }, [enabledRoles]);
  function toggleRole(role: ModelRole) {
    setEnabledRoles((prev) => ({ ...prev, [role]: !prev[role] }));
  }

  const cancelRef = useRef<(() => void) | null>(null);

  // On mount: pull status. If results exist, fetch them.
  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      if (!runId) return;
      try {
        const st = await Extraction.status(runId);
        if (cancelled) return;
        if (st.state === "complete") {
          const rows = await Extraction.results(runId);
          if (cancelled) return;
          setRecords(rows);
          setProcessed(rows.length);
          setTotal(rows.length);
          setPhase("complete");
        } else if (st.state === "error") {
          setError(st.detail ?? "Extraction error on disk");
          setPhase("error");
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.detail : String(e));
        setPhase("error");
      }
    }
    void bootstrap();
    return () => { cancelled = true; };
  }, [runId]);

  function pushLog(line: string) {
    setLog((prev) => {
      const next = [...prev, line];
      return next.length > 40 ? next.slice(next.length - 40) : next;
    });
  }

  async function start() {
    if (!runId || phase === "running") return;
    setError(null);
    setRecords([]);
    setProcessed(0);
    setTotal(0);
    setLog([]);
    setModels(_initialModels());
    setStatusMessage("Starting…");
    setStatusPhase("warming");
    setStatusStartedAt(Date.now());
    setPhase("running");
    const selectedRoles = (Object.keys(enabledRoles) as ModelRole[])
      .filter((r) => enabledRoles[r]);
    if (selectedRoles.length === 0) {
      setError("Select at least one model in the Models panel.");
      setPhase("idle");
      return;
    }
    const { events, cancel } = streamExtraction(runId, mode, selectedRoles, skipCache);
    cancelRef.current = cancel;
    try {
      for await (const ev of events) {
        handleEvent(ev);
      }
      // Stream closed cleanly — pull persisted results to be safe.
      const rows = await Extraction.results(runId);
      setRecords(rows);
      setProcessed(rows.length);
      if (rows.length > 0 && total === 0) setTotal(rows.length);
      setPhase("complete");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // Cancellation from the user is not an error.
      if (msg.includes("aborted")) {
        setPhase("idle");
        pushLog("Cancelled by user");
        return;
      }
      setError(msg);
      setPhase("error");
    } finally {
      cancelRef.current = null;
    }
  }

  function handleEvent(ev: ExtractionEvent) {
    if (ev.type === "extraction.start") {
      const t = Number(ev.total ?? 0);
      setTotal(t);
      const m = String(ev.mode ?? mode ?? "");
      if (m) setResolvedMode(m);
      pushLog(`Starting extraction over ${t} record${t === 1 ? "" : "s"} · mode=${m}`);
      return;
    }
    if (ev.type === "extraction.model.ready" ||
        ev.type === "extraction.model.unavailable") {
      const role  = String(ev.model ?? "") as ModelRole;
      const ready = ev.type === "extraction.model.ready";
      const note  = String(ev.note ?? "");
      setModels((prev) => ({
        ...prev,
        [role]: { status: ready ? "ready" : "unavailable", note },
      }));
      pushLog(`${role}: ${ready ? "ready" : "unavailable"}${note ? ` (${note})` : ""}`);
      return;
    }
    if (ev.type === "extraction.model.warming") {
      const role = String(ev.model ?? "") as ModelRole;
      const est  = Number(ev.estimated_seconds ?? 0);
      setModels((prev) => ({
        ...prev,
        [role]: { status: "warming", note: est ? `~${est}s cold start` : "warming" },
      }));
      pushLog(`${role}: warming (~${est}s)`);
      return;
    }
    if (ev.type === "extraction.record.done") {
      const idx = Number(ev.index ?? 0);
      const cn = String(ev.control_number ?? "");
      setProcessed(idx);
      pushLog(`#${idx} — ${cn}`);
      return;
    }
    if (ev.type === "extraction.step") {
      const msg   = String(ev.message ?? "");
      const phase = String(ev.phase ?? "") as typeof statusPhase;
      if (msg) {
        setStatusMessage(msg);
        setStatusPhase(phase || "");
        setStatusStartedAt(Date.now());
        pushLog(msg);
      }
      return;
    }
    if (ev.type === "extraction.end") {
      setStatusMessage("");
      setStatusPhase("");
      setStatusStartedAt(null);
      pushLog("Extraction finished. Loading results…");
      return;
    }
    if (ev.type === "extraction.error") {
      pushLog(`ERROR — ${String(ev.message ?? "unknown")}`);
      return;
    }
    // Anything else — log raw type only.
    pushLog(ev.type);
  }

  function cancelStream() {
    cancelRef.current?.();
  }

  function toggleRow(cn: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(cn)) next.delete(cn); else next.add(cn);
      return next;
    });
  }

  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;

  const entityTotals = useMemo(() => {
    let persons = 0, prov = 0, contents = 0, genres = 0;
    for (const r of records) {
      for (const e of r.entities ?? []) {
        const src = String(e.source ?? "");
        if (src === "person_ner")     persons++;
        else if (src === "provenance_ner") prov++;
        else if (src === "contents_ner")   contents++;
      }
      genres += (r.ml_genres ?? []).length;
    }
    return { persons, prov, contents, genres };
  }, [records]);

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-2">
          <div className="kicker">
            <Link to={`/runs/${runId}/overview`} className="hover:text-ink underline">
              ← back to run
            </Link>
            {" · "}AI Extraction
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-2xl font-semibold">Named Entity Extraction</h2>
            <PhasePill phase={phase} />
          </div>
          <p className="muted text-sm">
            Hebrew Person NER · Provenance NER · Contents NER · Genre classifier
          </p>
        </section>

        <section className="glass p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3 flex-wrap">
              {phase !== "running" && (
                <button onClick={start} className="button-primary text-sm">
                  {phase === "complete" ? "Re-run extraction" : "Start extraction"}
                </button>
              )}
              {phase === "running" && (
                <button onClick={cancelStream} className="button-ghost text-sm">
                  Cancel
                </button>
              )}
              {/* Reads the resolved EXTRACTION_MODE the backend picked
                  out of the `extraction.start` SSE event. Until the
                  first run fires, this stays blank. */}
              <span className="muted text-xs ml-2"
                    title="Inference backend currently configured on the server (EXTRACTION_MODE env var)">
                {resolvedMode
                  ? `Inference: ${resolvedMode === "modal" ? "Modal"
                                : resolvedMode === "hf-api" ? "HuggingFace"
                                : resolvedMode === "local"  ? "Local (in-process)"
                                : resolvedMode}`
                  : "Inference: (auto)"}
              </span>
              <label className="muted text-xs flex items-center gap-2"
                     title="Inference results are cached in the DB and shared across every user. Tick to skip the cache and force a fresh call — the new result still lands in the cache for the next caller.">
                <input type="checkbox" checked={skipCache}
                       onChange={(e) => setSkipCache(e.target.checked)}
                       disabled={phase === "running"} />
                <span>Skip cache</span>
              </label>
              {phase === "running" && total > 0 && (
                <span className="muted text-sm">{processed} / {total} ({pct}%)</span>
              )}
              {phase === "complete" && (
                <span className="muted text-sm">
                  {records.length} record{records.length === 1 ? "" : "s"} ·{" "}
                  {entityTotals.persons} persons ·{" "}
                  {entityTotals.prov} provenance ·{" "}
                  {entityTotals.contents} contents ·{" "}
                  {entityTotals.genres} ML genre predictions
                </span>
              )}
            </div>
          </div>

          {phase === "running" && (
            <div role="status" aria-live="polite" aria-atomic="true" className="space-y-4">
              {statusMessage && (
                <div className="flex items-start gap-3 px-3 py-2 rounded-lg"
                     style={{
                       background: "rgba(127,196,255,0.08)",
                       border: "1px solid rgba(127,196,255,0.25)",
                     }}>
                  {/* Spinner — animates while any operation is in flight. */}
                  <span
                    className="inline-block h-3 w-3 rounded-full shrink-0 mt-1 animate-spin"
                    style={{
                      border: "2px solid rgba(127,196,255,0.3)",
                      borderTopColor: "rgba(127,196,255,0.95)",
                    }}
                  />
                  <div className="flex-1 text-sm leading-tight">
                    <div className="text-ink">{statusMessage}</div>
                    {statusStartedAt !== null && (
                      <div className="muted text-[11px] mt-0.5">
                        {((Date.now() - statusStartedAt) / 1000).toFixed(1)}s elapsed
                        {statusPhase === "warming" && " · containers cold-start in ~30–60s"}
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div
                role="progressbar"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
                className="h-2 w-full rounded-full bg-white/5 overflow-hidden"
              >
                <div
                  className="h-full transition-all"
                  style={{
                    width: `${pct}%`,
                    background: "linear-gradient(90deg, #77cce5 0%, #004027 100%)",
                  }}
                />
              </div>
            </div>
          )}

          {/* Models settings — enable/disable per role + live status.
              Persisted to localStorage. Disabled while running so a
              user can't yank a model mid-stream. */}
          <div>
            <div className="kicker mb-2 flex items-center gap-2">
              <span>Models</span>
              <span className="muted text-[10px] normal-case font-normal">
                · tick a model to include it; untick to skip
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {(["person", "provenance", "contents", "genre"] as ModelRole[]).map((role) => (
                <ModelPill key={role}
                           role={role}
                           label={_ROLE_LABELS[role]}
                           state={models[role]}
                           enabled={enabledRoles[role]}
                           onToggle={() => toggleRole(role)}
                           disabled={phase === "running"} />
              ))}
            </div>
            {!enabledRoles.provenance && !enabledRoles.contents && (
              <p className="muted text-[10px] mt-1.5">
                Provenance + Contents NER aren't on HuggingFace Inference Providers
                (their custom 2-layer head doesn't fit HF's serverless tier).
                They're archival-only on the Hub.
              </p>
            )}
          </div>

          {error && (
            <div className="glass-pill px-3 py-2 text-sm text-red-300">
              {error}
            </div>
          )}

          {(phase === "running" || log.length > 0) && (
            <details className="text-xs" open={phase === "running"}>
              <summary className="cursor-pointer muted">
                Step log ({log.length})
              </summary>
              <pre className="mt-2 bg-black/30 rounded-lg p-3 max-h-48 overflow-y-auto font-mono text-[11px] whitespace-pre-wrap">
                {log.join("\n") || "—"}
              </pre>
            </details>
          )}
        </section>

        {/* Rich AI Extraction review UI (Rule W-16). Replaces the legacy
            per-record collapsible list with an entity-level table +
            filters + bulk actions + AI verification entry points. */}
        {runId && approvalStore.entities.length > 0 && (
          <section className="glass p-6 space-y-4" data-testid="entity-review">
            <div>
              <div className="kicker">Entity review</div>
              <h3 className="text-lg font-medium">
                Validate, edit, and approve every extracted entity
              </h3>
            </div>

            <EntityActionsBar
              totalCount={approvalStore.total || approvalStore.entities.length}
              visibleCount={filteredEntities.length}
              selectedIds={selectedIds}
              visibleEntities={filteredEntities}
              onSelectAllVisible={() => {
                const next = new Set(selectedIds);
                for (const e of filteredEntities) next.add(e.id);
                setSelectedIds(next);
              }}
              onApproveSelected={async () => {
                if (selectedIds.size === 0) return;
                await ExtractionApprovals.bulkApprove(
                  runId, Array.from(selectedIds), true,
                );
                await refreshEntities();
              }}
              onRejectSelected={async () => {
                if (selectedIds.size === 0) return;
                await ExtractionApprovals.bulkApprove(
                  runId, Array.from(selectedIds), false,
                );
                await refreshEntities();
              }}
              onOpenAutoApprove={() => setAutoApproveOpen(true)}
              onVerifyScope={(scopeKind, entityIds) => {
                const label = scopeKind === "selection"
                  ? `${entityIds.length} selected`
                  : `${entityIds.length} visible`;
                setVerifyScope({ scopeKind, entityIds, label });
              }}
            />

            <EntityFilterChips
              entities={approvalStore.entities}
              onFilteredChange={setFilteredEntities}
            />

            <EntityTable
              runId={runId}
              projectId={projectId}
              entities={filteredEntities}
              selectedIds={selectedIds}
              onSelectionChange={setSelectedIds}
              onEntityUpdated={() => { void refreshEntities(); }}
              onOpenEdit={setEditEntity}
              onViewSource={setDetailEntity}
              onOpenVerdict={setDetailEntity}
              onOpenMarc={(cn) => {
                // Open detail with the first entity from that record.
                const first = approvalStore.entities.find((e) => e.control_number === cn);
                if (first) setDetailEntity(first);
              }}
              columnFilters={columnFilters}
              onColumnFiltersChange={setColumnFilters}
            />
          </section>
        )}

        {/* Legacy per-record results table — kept as a secondary view
            for users who prefer the by-MS grouping while the new
            entity-level table is the primary review surface. */}
        {records.length > 0 && (
          <details className="glass p-6 space-y-4">
            <summary className="kicker cursor-pointer">
              Per-record overview ({records.length} records)
            </summary>
            <div className="overflow-x-auto mt-3">
              <table className="w-full text-sm border-collapse">
                <thead className="muted text-left">
                  <tr className="border-b border-white/5">
                    <th className="py-2 pr-3 w-6"></th>
                    <th className="py-2 pr-3">Control number</th>
                    <th className="py-2 pr-3">Persons</th>
                    <th className="py-2 pr-3">Provenance</th>
                    <th className="py-2 pr-3">Contents</th>
                    <th className="py-2 pr-3">ML genres</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((r) => {
                    const open = expanded.has(r._control_number);
                    const counts = countByType(r);
                    return (
                      <RecordRow
                        key={r._control_number}
                        record={r}
                        open={open}
                        counts={counts}
                        onToggle={() => toggleRow(r._control_number)}
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </div>

      {/* ─── Modals + drawer ──────────────────────────────────────── */}
      {editEntity && runId && (
        <EntityEditModal
          runId={runId}
          entity={editEntity}
          onClose={() => setEditEntity(null)}
          onSaved={() => { void refreshEntities(); setEditEntity(null); }}
        />
      )}
      {runId && (
        <EntityDetailDrawer
          runId={runId}
          projectId={projectId}
          entity={detailEntity}
          onClose={() => setDetailEntity(null)}
          onEntityChanged={() => { void refreshEntities(); }}
          onOpenEdit={(e) => setEditEntity(e)}
        />
      )}
      {autoApproveOpen && runId && (
        <AutoApproveRuleBuilder
          runId={runId}
          onClose={() => setAutoApproveOpen(false)}
          onComplete={() => { void refreshEntities(); setAutoApproveOpen(false); }}
        />
      )}
      {verifyScope && runId && (
        <NerVerificationModal
          runId={runId}
          scopeKind={verifyScope.scopeKind}
          entityIds={verifyScope.entityIds}
          scopeLabel={verifyScope.label}
          onClose={() => setVerifyScope(null)}
          onVerdictsLanded={() => { void refreshEntities(); }}
        />
      )}
    </Layout>
  );
}


// ── Row + expanded detail ───────────────────────────────────────────────


interface Counts {
  persons:  number;
  prov:     number;
  contents: number;
  genres:   number;
}


function countByType(r: ExtractionRecord): Counts {
  let persons = 0, prov = 0, contents = 0;
  for (const e of r.entities ?? []) {
    const src = String(e.source ?? "");
    if      (src === "person_ner")     persons++;
    else if (src === "provenance_ner") prov++;
    else if (src === "contents_ner")   contents++;
  }
  return { persons, prov, contents, genres: (r.ml_genres ?? []).length };
}


function RecordRow({
  record, open, counts, onToggle,
}: {
  record:   ExtractionRecord;
  open:     boolean;
  counts:   Counts;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        className="border-b border-white/5 hover:bg-white/[0.03] transition cursor-pointer"
        onClick={onToggle}
      >
        <td className="py-2 pr-3 text-center muted">
          <button
            type="button"
            aria-expanded={open}
            aria-label={"Toggle record " + record._control_number}
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            className="bg-transparent border-0 p-0 cursor-pointer text-inherit"
          >
            {open ? "▾" : "▸"}
          </button>
        </td>
        <td className="py-2 pr-3 font-mono text-xs">{record._control_number}</td>
        <td className="py-2 pr-3">{counts.persons || <span className="muted">—</span>}</td>
        <td className="py-2 pr-3">{counts.prov || <span className="muted">—</span>}</td>
        <td className="py-2 pr-3">{counts.contents || <span className="muted">—</span>}</td>
        <td className="py-2 pr-3">{counts.genres || <span className="muted">—</span>}</td>
      </tr>
      {open && (
        <tr className="border-b border-white/5 bg-black/15">
          <td colSpan={6} className="py-4 px-6">
            <RecordDetail record={record} />
          </td>
        </tr>
      )}
    </>
  );
}


function RecordDetail({ record }: { record: ExtractionRecord }) {
  const ents = record.entities ?? [];
  const persons  = ents.filter((e) => e.source === "person_ner");
  const prov     = ents.filter((e) => e.source === "provenance_ner");
  const contents = ents.filter((e) => e.source === "contents_ner");
  const genres   = (record.ml_genres ?? []).slice().sort((a, b) => b.confidence - a.confidence);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <EntityList title="Persons"     entities={persons}  showRole  />
      <EntityList title="Provenance"  entities={prov}     showType  />
      <EntityList title="Contents"    entities={contents} showType  />
      <div>
        <div className="kicker mb-2">ML genre predictions</div>
        {genres.length === 0 ? (
          <p className="muted text-sm">No genre predictions.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {genres.map((g) => (
              <li key={g.label} className="flex items-center justify-between gap-3">
                <span>{g.label}</span>
                <span className="glass-pill px-2 py-[1px] text-[10px] font-mono">
                  {g.confidence.toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}


function EntityList({
  title, entities, showRole, showType,
}: {
  title:     string;
  entities:  Array<{
    text: string; type: string; role?: string | null;
    confidence?: number | null; model_confidence?: number | null;
  }>;
  showRole?: boolean;
  showType?: boolean;
}) {
  return (
    <div>
      <div className="kicker mb-2">{title}</div>
      {entities.length === 0 ? (
        <p className="muted text-sm">No entities.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {entities.map((e, i) => (
            <li key={`${e.text}-${i}`} className="flex items-center justify-between gap-3">
              <span className="truncate">{e.text}</span>
              <span className="flex items-center gap-1 shrink-0">
                {showRole && e.role && (
                  <span className="glass-pill px-2 py-[1px] text-[10px] kicker">{e.role}</span>
                )}
                {showType && (
                  <span className="glass-pill px-2 py-[1px] text-[10px] kicker">{e.type}</span>
                )}
                {typeof e.model_confidence === "number" && (
                  <span className="muted text-[10px] font-mono">
                    {e.model_confidence.toFixed(2)}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function PhasePill({ phase }: { phase: Phase }) {
  const tone =
    phase === "complete" ? "text-biu-sky"
    : phase === "running"  ? "text-yellow-300"
    : phase === "error"    ? "text-red-300"
    : "muted";
  return (
    <span className={`glass-pill px-3 py-1 text-[10px] kicker ${tone}`}>
      {phase}
    </span>
  );
}


/** One pill summarising a model's availability + state, with a
 *  checkbox to enable/disable it for the next run.
 *
 *  Status drives the colour of the live-state line:
 *   idle         — muted, "—"
 *   warming      — yellow-300, "warming (~Ns)"
 *   ready        — biu-sky, "ready"
 *   unavailable  — red-300, "unavailable" (hover for reason)
 *   error        — red-300, "error" (hover for message)
 *
 *  When the role is disabled (checkbox unticked), the whole tile is
 *  visually dimmed.
 */
function ModelPill({
  role, label, state, enabled, onToggle, disabled,
}: {
  role:     ModelRole;
  label:    string;
  state:    ModelState;
  enabled:  boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  const tone =
    state.status === "ready"        ? "text-biu-sky"
    : state.status === "warming"     ? "text-yellow-300"
    : state.status === "unavailable" ? "text-red-300"
    : state.status === "error"       ? "text-red-300"
    : "muted";
  const glyph =
    state.status === "ready"        ? "✓"
    : state.status === "warming"     ? "⏱"
    : state.status === "unavailable" ? "✗"
    : state.status === "error"       ? "⚠"
    : "—";
  return (
    <label
      className={`glass-pill px-3 py-2 flex items-start gap-2 transition cursor-pointer
                  ${enabled ? "" : "opacity-50"} ${disabled ? "cursor-not-allowed" : "hover:bg-white/[0.06]"}`}
      title={state.note || `Toggle ${label}`}>
      <input type="checkbox"
             checked={enabled}
             onChange={onToggle}
             disabled={disabled}
             className="mt-0.5 shrink-0"
             aria-label={`Enable ${label}`} />
      <div className="min-w-0 flex-1">
        <div className="kicker truncate">{label}</div>
        <div className={`text-xs font-medium ${enabled ? tone : "muted"}`}>
          {enabled ? `${glyph} ${state.status}` : "off"}
        </div>
        {enabled && state.note && (
          <div className="muted text-[10px] leading-tight mt-0.5 truncate"
               title={state.note}>
            {state.note}
          </div>
        )}
      </div>
      {/* Hidden var so TS doesn't strip ``role`` as unused —
          surfaces in DOM as a data attribute for e2e tests. */}
      <span hidden data-role={role} />
    </label>
  );
}
