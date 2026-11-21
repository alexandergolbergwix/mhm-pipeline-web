import {useCallback, useEffect, useState} from "react";
import {useLocation} from "react-router-dom";

import {ApiError} from "@/api/client";
import {GilaRepairs, type CaseFilter, type GilaCase, type GilaCaseReview, type GilaReport, type GilaStatus} from "@/api/gilaRepairs";
import type {ReferenceRepairStatus, ReferenceScope} from "@/api/referenceRepairs";
import {AdminLayout} from "@/components/admin/AdminLayout";
import {Glass} from "@/components/glass";
import {ManuscriptRepairReleases} from "@/components/wikidata/ManuscriptRepairReleases";
import {ReferenceRepairQueue} from "@/components/wikidata/ReferenceRepairQueue";
import {WikidataRepairInventory} from "@/components/wikidata/WikidataRepairInventory";
import {WikidataSourceReview} from "@/components/wikidata/WikidataSourceReview";
import {useDebounce} from "@/hooks/useDebounce";
import {useReferenceRepairStatus} from "@/hooks/useReferenceRepairStatus";
import {useRunJobs} from "@/stores/runJobs";
import {ensureRunJob, studioBuildProgressMessage, waitForRunJob} from "@/utils/waitForRunJob";

const pageSize = 25;
const caseReviewLabels = {
  verified_match: "Source confirms the match",
  candidate: "Possible match. More evidence is necessary.",
  needs_evidence: "The evidence did not establish a match.",
};

function readCaseReview(result: Record<string, unknown> | null): GilaCaseReview {
  const decision = result?.decision;
  const explanation = result?.explanation;
  if ((decision !== "verified_match" && decision !== "candidate" && decision !== "needs_evidence")
      || typeof explanation !== "string") {
    throw new Error("The worker did not return a case review.");
  }
  const qid = result?.candidate_qid;
  const excerpts = result?.source_excerpts;
  return {
    decision, explanation,
    candidate_qid: typeof qid === "string" && /^Q[1-9][0-9]*$/.test(qid) ? qid : null,
    candidate_revision: typeof result?.candidate_revision === "number" ? result.candidate_revision : null,
    source_excerpts: Array.isArray(excerpts) ? excerpts.filter(value => typeof value === "string") : [],
  };
}
const labels = {
  verified: "Verified", repair_required: "Repair needed", manual_review: "Needs review",
  not_checked: "Needs evidence", absent_in_source: "Absent from source",
};
const phases: Record<string, string> = {
  queued: "Wait for a worker",
  check_source: "Verify the Studio build",
  source: "Read the source",
  public_items: "Check Wikidata items",
  linked_items: "Check linked items",
  compare: "Compare the evidence",
  check_report_source: "Verify the source before save",
};
const explanations: Record<string, string> = {
  public_record_identity_requires_review: "The project needs a confirmed manuscript mapping.",
  duplicate_public_catalog_identity: "Two or more Wikidata items have the same catalog identifier.",
  exact_historical_proposal_requires_guarded_execution: "The live item has a historical repair that needs the reference queue or specific review.",
  passage_requires_exact_scope_and_public_reference_proof: "This passage needs a check of its source, scope, and live statement.",
  live_work_author_or_source_requires_review: "The current work target needs an author or source check.",
  live_work_identity_or_scope_requires_review: "Confirm the work identity and the folios before you set the link.",
  author_authority_link_absent: "The live work lacks the expected author link. Confirm the work mapping before preparation.",
  troki_author_link_verified: "The source supports the author link for Ḥizzuq Emunah. This release adds only the sourced author statement.",
  production_place_not_resolved: "The production place needs a source check.",
  named_scribe_identity_unresolved: "The named scribe needs an identity check.",
  stale_catalogue_edit: "The build skipped an old edit that could overwrite a catalogue code. Review the proposed work link against the MARC record.",
  manuscript_work_model_requires_decision: "Review the request to use both classes on one item. The project keeps the physical manuscript and its work in separate items.",
  item_has_other_creator: "Another account created this item. The manuscript repair system keeps it read-only.",
};

function failureText(error: unknown) {
  return error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "The request failed.";
}

function AuditResult({
  report, queue, needsRecheck, publishedSinceAudit, onOpenCases, onStartRepair, onOpenReferences,
  preparing, auditBusy,
}: {
  report: GilaReport; queue: ReferenceRepairStatus | null; needsRecheck: boolean;
  publishedSinceAudit: boolean;
  onOpenCases: (filter: CaseFilter | "") => void;
  onStartRepair: () => void;
  onOpenReferences: () => void;
  preparing: boolean;
  auditBusy: boolean;
}) {
  const [exportError, setExportError] = useState("");
  const [exporting, setExporting] = useState(false);
  const hasRepeatedReferences = report.reference_blocks > 0;

  const verified = report.email_points.filter(row => row.status === "verified").length;
  const complete = report.all_requirements_verified && !hasRepeatedReferences && !needsRecheck;
  const inventory = queue?.reference_inventory;
  const referenceCounts = inventory?.live_checked_at
    && Date.parse(inventory.live_checked_at) === Date.parse(report.finished_at)
    && inventory.items.every(item => item.observed_blocks != null)
    ? inventory.items.reduce((counts, item) => {
      const blocks = item.observed_blocks ?? 0;
      if (item.creator_matches) counts.project += blocks;
      else counts.other += blocks;
      return counts;
    }, {project: 0, other: 0})
    : null;
  const canStart = queue?.enabled !== false
    && !queue?.other_scope_active
    && queue?.resume_action !== "review"
    && (referenceCounts?.project ?? 1) > 0
    && queue?.job?.status !== "queued"
    && queue?.job?.status !== "running"
    && queue?.job?.status !== "succeeded";

  async function exportEvidence() {
    setExporting(true);
    setExportError("");
    try {
      const evidence = await GilaRepairs.evidence(report.id);
      const blob = new Blob([JSON.stringify(evidence, null, 2)], {type: "application/json"});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `wikidata-audit-${report.id}.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setExportError(failureText(error));
    } finally {
      setExporting(false);
    }
  }

  return <section className="space-y-4 rounded-xl border border-glass-line bg-[var(--surface-inset)] p-4"
    aria-label="Audit result">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold">{publishedSinceAudit ? "Manuscript edits published. A fresh audit is due."
          : needsRecheck ? "Reference repairs complete. A fresh audit is due." : complete ? "All nine requirements verified" : "Work remains after the audit"}</h2>
        <p className="mt-1 text-sm muted">The audit checks Wikidata and makes no edits.</p>
        <p className="mt-1 text-xs muted">Last completed audit · {new Date(report.finished_at).toLocaleString()}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {report.case_count > 0 && <button className="button-primary" disabled={preparing || auditBusy} onClick={onStartRepair}>
          {preparing ? "Request the repair…" : "Start manuscript repairs"}
        </button>}
        {hasRepeatedReferences && <button className="button-ghost" onClick={onOpenReferences}>
          {canStart ? "Review reference queue" : "View reference repairs"}
        </button>}
        {report.case_count > 0 && <button className="button-ghost"
          onClick={() => onOpenCases("")}>Review open cases</button>}
        <button className="button-ghost" disabled={exporting}
          onClick={() => void exportEvidence()}>{exporting ? "Prepare evidence…" : "Download audit evidence"}</button>
      </div>
    </div>
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {[
        [`${verified} of 9`, "Requirements fully verified"],
        [report.requirement_coverage.verified_claims?.toLocaleString() ?? "—", "Individual claims verified"],
        [`${report.requirement_coverage.public_reads} of ${report.record_count}`, "Manuscript mappings verified"],
        [report.reference_blocks.toLocaleString(), "Repeated references at last audit"],
      ].map(([count, label]) => <div key={label} className="flex flex-col-reverse rounded-xl border border-glass-line px-3 py-3">
        <dt className="mt-1 text-xs muted">{label}</dt><dd className="text-xl font-semibold tabular-nums">{count}</dd>
      </div>)}
    </dl>
    {needsRecheck && <p className="text-sm muted">
      {publishedSinceAudit ? "A repair release updated Wikidata after this audit."
        : queue?.verified ? `The reference queue verified ${queue.verified.toLocaleString()} corrections after this audit.`
          : "The reference queue finished after this audit."}{" "}
      Run a fresh audit to check the current Wikidata revisions and all nine requirements.
    </p>}
    {referenceCounts && <p className="text-sm muted">
      In the reviewed scope, the audit found <strong>{referenceCounts.project.toLocaleString()}</strong> repeated references on project-created items.
      {" "}It found <strong>{referenceCounts.other.toLocaleString()}</strong> on items that other accounts created. Those items are read-only.
    </p>}
    {hasRepeatedReferences && canStart && <p className="text-sm muted">Review and confirm the reference scope before the worker starts.</p>}
    {exportError && <p role="alert" className="text-sm text-danger">{exportError}</p>}
  </section>;
}

interface RepairCaseActions {
  preparing: boolean;
  onPrepare: (caseId: string) => void;
  onSourceSaved: () => void;
}

function CaseDetails({item, report, runId, expanded, actions}: {
  item: GilaCase; report: GilaReport; runId: string; expanded: boolean; actions: RepairCaseActions;
}) {
  const [showSource, setShowSource] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [caseReview, setCaseReview] = useState<GilaCaseReview>();
  const [reviewProgress, setReviewProgress] = useState("");
  const canReview = item.disposition === "manual_review" && Boolean(item.qid && item.control_number)
    && (item.reason === "production_place_not_resolved" || item.reason === "named_scribe_identity_unresolved");

  useEffect(() => {
    if (!canReview || !expanded) return;
    let active = true;
    setReviewProgress("Check for a saved case review.");
    void GilaRepairs.latestCaseReview(report.id, item.case_id).then(async job => {
      if (!active || !job) return;
      if (job.status === "failed" || job.status === "cancelled") {
        setError(job.error ?? "The prior case review stopped.");
        return;
      }
      const finished = job.status === "succeeded" ? job : await waitForRunJob(runId, job.id, {
        onUpdate: snapshot => {
          if (active) setReviewProgress(snapshot.progress.message ?? "Wait for the review worker.");
        },
      });
      if (active) setCaseReview(readCaseReview(finished.result));
    }).catch(failure => {
      if (active) setError(failureText(failure));
    }).finally(() => {
      if (active) setReviewProgress("");
    });
    return () => {active = false;};
  }, [canReview, expanded, report.id, item.case_id, runId]);

  async function reviewWithKimi() {
    setCaseReview(undefined);
    setReviewProgress("Request a case review.");
    setError("");
    try {
      const job = await GilaRepairs.reviewCase(report.id, item.case_id);
      const finished = await waitForRunJob(runId, job.id, {
        onUpdate: snapshot => setReviewProgress(snapshot.progress.message ?? "Wait for the review worker."),
      });
      setCaseReview(readCaseReview(finished.result));
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setReviewProgress("");
    }
  }
  const sourceGap = item.classification === "absent_in_source";
  const canUseSourceReview = item.local_id && !sourceGap && !item.case_id.startsWith("work-author:");
  const links = item.qids ?? (item.qid ? [item.qid] : []);
  const reasons = [...new Set([item.reason, item.live_reason, item.completion_reason].filter(Boolean))];

  return <div className="space-y-4 border-t border-glass-line px-4 py-4 text-sm">
    {item.source_value && <blockquote className="whitespace-pre-wrap rounded-lg bg-[var(--surface-inset)] p-3" dir="auto">{item.source_value}</blockquote>}
    <dl className="grid gap-x-5 gap-y-2 sm:grid-cols-2">
      {item.source_field && <div><dt className="muted">MARC field</dt><dd>{item.source_field}</dd></div>}
      {item.control_number && <div><dt className="muted">Ktiv record</dt>
        <dd><a className="link-accent underline" href={`https://www.nli.org.il/en/manuscripts/NNL_ALEPH${encodeURIComponent(item.control_number)}/NLI`} target="_blank" rel="noreferrer">{item.control_number}</a></dd>
      </div>}
      {item.expected_author && <div><dt className="muted">Source author</dt><dd dir="auto">{item.expected_author}</dd></div>}
      {item.expected_value && <div><dt className="muted">Expected value</dt><dd dir="auto">{item.expected_value}</dd></div>}
      {Object.entries(item.expected_qualifiers ?? {}).map(([property, value]) => <div key={property}>
        <dt className="muted">{property === "P958" || property === "P7416" ? "Folio or section" : property}</dt><dd dir="auto">{value}</dd>
      </div>)}
    </dl>
    {reasons.map(reason => reason && <p className="muted" key={reason}>{explanations[reason] ?? reason.replaceAll("_", " ")}</p>)}
    <div className="flex flex-wrap items-center gap-3">
      {links.map(qid => <a key={qid} className="link-accent underline" target="_blank" rel="noreferrer"
        href={item.live_revision && item.qid === qid
          ? `https://www.wikidata.org/w/index.php?title=${qid}&oldid=${item.live_revision}`
          : `https://www.wikidata.org/wiki/${qid}`}>{qid}</a>)}
      {canReview && <button className="button-primary" disabled={Boolean(reviewProgress)}
        onClick={() => void reviewWithKimi()}>{caseReview ? "Run Kimi review again" : "Review with Kimi"}</button>}
      {canUseSourceReview && <button className="button-ghost" aria-expanded={showSource}
        onClick={() => setShowSource(value => !value)}>{showSource ? "Close source review" : "Review source"}</button>}
      {item.can_prepare && !notice && <button className="button-primary" disabled={actions.preparing}
        onClick={() => actions.onPrepare(item.case_id)}>Prepare repair release</button>}
    </div>
    {notice && <p role="status">{notice}</p>}
    {reviewProgress && <p role="status">{reviewProgress}</p>}
    {caseReview && <section className="space-y-2 rounded-lg border border-glass-line p-3" aria-label="Kimi case review">
      <h3 className="font-semibold">{caseReviewLabels[caseReview.decision]}</h3>
      <p dir="auto">{caseReview.explanation}</p>
      {caseReview.candidate_qid && <a className="link-accent underline" target="_blank" rel="noreferrer"
        href={caseReview.candidate_revision
          ? `https://www.wikidata.org/w/index.php?title=${caseReview.candidate_qid}&oldid=${caseReview.candidate_revision}`
          : `https://www.wikidata.org/wiki/${caseReview.candidate_qid}`}>
        {caseReview.candidate_qid}
      </a>}
      <details>
        <summary className="cursor-pointer">MARC evidence</summary>
        {caseReview.source_excerpts.map((excerpt, index) => <p key={index} dir="auto" className="whitespace-pre-wrap break-words">{excerpt}</p>)}
      </details>
      <p className="muted">The review checks evidence. A repair release requires separate proof and approval.</p>
    </section>}
    {error && <p className="text-danger" role="alert">{error}</p>}
    {showSource && item.local_id && <WikidataSourceReview runId={runId} localId={item.local_id}
      onSaved={() => {
        setNotice("The source decision is saved. Use Rebuild source and run audit above.");
        actions.onSourceSaved();
      }} />}
  </div>;
}

function Cases({report, runId, point, initialFilter, actions}: {
  report: GilaReport; runId: string; point: number | null; initialFilter: CaseFilter | "";
  actions: RepairCaseActions;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<CaseFilter | "">(initialFilter);
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<{items: GilaCase[]; total: number}>();
  const [error, setError] = useState("");
  const search = useDebounce(query, 250);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let stopped = false;
    const params = new URLSearchParams({audit_id: report.id, offset: String(offset), limit: String(pageSize)});
    if (point) params.set("point", String(point));
    if (filter) params.set("disposition", filter);
    if (search) params.set("query", search);
    setResult(undefined);
    setError("");
    void GilaRepairs.cases(params).then(rows => { if (!stopped) setResult(rows); })
      .catch(failure => { if (!stopped) setError(failureText(failure)); });
    return () => { stopped = true; };
  }, [report.id, point, filter, offset, search]);

  return <Glass as="section" contentClassName="space-y-4" id="audit-cases" tabIndex={-1}>
    <div className="flex flex-wrap items-end justify-between gap-3">
      <h2 className="text-lg font-semibold">{point
        ? report.email_points.find(row => row.point === point)?.title
        : filter === "ready_to_prepare" ? "Repairs ready across all nine requirements" : "Open cases"}</h2>
      <div className="flex flex-wrap gap-2">
        <input className="input" aria-label="Find a case" type="search" value={query} maxLength={120}
          placeholder="Find a QID, source ID, or title" onChange={event => {setQuery(event.target.value); setOffset(0);}} />
        <select className="input" aria-label="Case status" value={filter}
          onChange={event => {
            const value = event.target.value;
            if (value === "" || value === "ready_to_prepare" || value === "repair_required" || value === "manual_review"
              || value === "not_checked" || value === "absent_in_source") setFilter(value);
            setOffset(0);
          }}>
          <option value="">All open cases</option>
          <option value="ready_to_prepare">Ready to prepare</option>
          <option value="repair_required">Repair needed</option>
          <option value="manual_review">Needs review</option>
          <option value="not_checked">Needs evidence</option>
          <option value="absent_in_source">Absent from source</option>
        </select>
      </div>
    </div>
    {!result && !error && <p role="status" className="muted">Read the case list…</p>}
    {error && <p role="alert" className="text-danger">{error}</p>}
    {result?.total === 0 && (filter === "ready_to_prepare"
      ? <div className="space-y-3">
        <p className="muted">No source-supported cases are ready for a repair release.</p>
        <button className="button-ghost" onClick={() => {setFilter("manual_review"); setOffset(0);}}>
          Review cases that need a decision
        </button>
      </div>
      : <p className="muted">No cases match this filter.</p>)}
    {result && result.total > 0 && <>
      <ul className="divide-y divide-glass-line rounded-xl border border-glass-line">
        {result.items.map((item, index) => <li key={item.case_id}>
          <details open={filter === "ready_to_prepare" && index === 0 ? true : undefined}
            onToggle={event => {
              const open = event.currentTarget.open;
              setExpanded(value => ({...value, [item.case_id]: open}));
            }}>
            <summary className="cursor-pointer px-4 py-3">
              <span className="ml-2 inline-flex max-w-[92%] flex-wrap items-baseline gap-x-3 gap-y-1 align-middle">
                <span className="font-mono text-xs muted">{item.qid ?? item.control_number ?? "Corpus"}</span>
                <span className="max-w-full break-words text-sm" dir="auto">{item.source_value?.slice(0, 120)
                  || explanations[item.reason ?? ""] || item.source_field || "Source evidence"}</span>
                <span className="text-xs font-medium text-[var(--text-muted)]">{labels[item.classification === "absent_in_source" ? "absent_in_source" : item.disposition]}</span>
              </span>
            </summary>
            <CaseDetails item={item} report={report} runId={runId} expanded={Boolean(expanded[item.case_id])}
              actions={actions} />
          </details>
        </li>)}
      </ul>
      <footer className="flex items-center justify-between gap-2 text-sm">
        <span className="muted">{offset + 1}–{offset + result.items.length} of {result.total.toLocaleString()}</span>
        <div className="flex gap-2">
          <button className="button-ghost" disabled={!offset} onClick={() => setOffset(offset - pageSize)}>Previous</button>
          <button className="button-ghost" disabled={offset + pageSize >= result.total} onClick={() => setOffset(offset + pageSize)}>Next</button>
        </div>
      </footer>
    </>}
  </Glass>;
}

export default function AdminGilaRepairs() {
  const {hash} = useLocation();
  const [state, setState] = useState<GilaStatus>();
  const [refreshError, setRefreshError] = useState("");
  const [actionError, setActionError] = useState("");
  const [starting, setStarting] = useState(false);
  const [buildProgress, setBuildProgress] = useState("");
  const [caseView, setCaseView] = useState<{
    point: number | null; initialFilter: CaseFilter | ""; selection: number;
  }>({point: null, initialFilter: "", selection: 0});
  const {point, initialFilter, selection} = caseView;
  const [poll, setPoll] = useState(0);
  const [referenceScope, setReferenceScope] = useState<ReferenceScope>("remaining");
  const [referencesOpen, setReferencesOpen] = useState(false);
  useEffect(() => {
    if (hash !== "#repair-references" && hash !== "#repair-references-original") return;
    setReferencesOpen(true);
    setReferenceScope(hash === "#repair-references-original" ? "original" : "remaining");
    const frame = window.requestAnimationFrame(() => document.getElementById("repair-references")?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [hash]);
  const [inventoryOpen, setInventoryOpen] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [repairNotice, setRepairNotice] = useState("");
  const [repairError, setRepairError] = useState("");
  const [requestedRepairJob, setRequestedRepairJob] = useState<{id: string; request: number}>();
  const [sourceEdited, setSourceEdited] = useState(false);
  const remainingQueue = useReferenceRepairStatus("remaining", Boolean(state));
  const originalQueue = useReferenceRepairStatus("original", referencesOpen && referenceScope === "original");
  const queue = remainingQueue.state;
  const [lastPublication, setLastPublication] = useState(0);
  const onLiveChanges = useCallback((finishedAt: string) => {
    setLastPublication(current => Math.max(current, Date.parse(finishedAt) || 0));
  }, []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      let delay = 5000;
      try {
        const value = await GilaRepairs.status();
        if (stopped) return;
        setState(value);
        setRefreshError("");
        if (value.job?.status === "queued" || value.job?.status === "running") delay = 1800;
      } catch (failure) {
        if (stopped) return;
        if (failure instanceof ApiError && (failure.status === 401 || failure.status === 403)) setState(undefined);
        setRefreshError(failureText(failure));
      } finally {
        if (!stopped) timer = setTimeout(() => void read(), delay);
      }
    }
    void read();
    return () => { stopped = true; clearTimeout(timer); };
  }, [poll]);

  const job = state?.job;
  const report = state?.report;
  const publishedSinceAudit = Boolean(report && lastPublication > Date.parse(report.finished_at));
  const needsRecheck = publishedSinceAudit || Boolean(report && [queue, originalQueue.state].some(
    status => status?.job?.status === "succeeded" && status.job.finished_at
      && Date.parse(status.job.finished_at) > Date.parse(report.finished_at),
  ));
  const active = job?.status === "queued" || job?.status === "running";
  const rebuildRequired = sourceEdited || (job?.status === "failed" && (
    job.progress.phase === "source_rebuild_required"
    || job.error === "Build the canonical Studio source and start the audit again."
  ));
  const processed = job?.progress.processed ?? 0;
  const total = job?.progress.total ?? 0;

  function focusSection(id: string) {
    window.requestAnimationFrame(() => document.getElementById(id)?.focus());
  }

  function openReferences() {
    setReferencesOpen(true);
    setReferenceScope("remaining");
    focusSection("repair-references");
  }

  async function requestRepair(caseId?: string) {
    if (!report || preparing || rebuildRequired || active || starting) return;
    setPreparing(true);
    setRepairNotice("");
    setRepairError("");
    try {
      const next = caseId ? await GilaRepairs.prepare(report.id, caseId) : await GilaRepairs.prepareBatch(report.id);
      if (next) {
        setRequestedRepairJob(current => ({id: next.id, request: (current?.request ?? 0) + 1}));
        focusSection("repair-releases");
      } else {
        setRepairNotice("This audit has no cases ready for a repair release.");
        openCases(null, "ready_to_prepare");
      }
    } catch (failure) {
      setRepairError(failureText(failure));
      focusSection("repair-request-status");
    } finally {
      setPreparing(false);
    }
  }

  function openCases(point: number | null, filter: CaseFilter | "" = "") {
    setCaseView(current => ({point, initialFilter: filter, selection: current.selection + 1}));
    window.requestAnimationFrame(() => document.getElementById("audit-cases")?.focus());
  }

  useEffect(() => {
    if (active) useRunJobs.getState().ensurePolling();
  }, [active]);

  async function start() {
    if (!state) return;
    setStarting(true);
    setActionError("");
    try {
      if (rebuildRequired) {
        setBuildProgress("Request the canonical Studio build for approved records.");
        const build = await ensureRunJob(state.run_id, "wikidata_studio_build", {
          source: "canonical",
          approved_only: true,
          force_rebuild: true,
          extract_provenance_prose: false,
        });
        if (build.params.source !== "canonical" || build.params.approved_only !== true
          || build.params.force_rebuild !== true) {
          throw new Error("Another Studio build is active. Wait for it to finish and try again.");
        }
        useRunJobs.getState().upsertJob(build);
        setBuildProgress(studioBuildProgressMessage(build));
        await waitForRunJob(state.run_id, build.id, {
          onUpdate: snapshot => {
            useRunJobs.getState().upsertJob(snapshot);
            setBuildProgress(studioBuildProgressMessage(snapshot));
          },
        });
        setSourceEdited(false);
        setBuildProgress("The Studio build is ready. The page will start the live audit.");
      }
      const nextJob = await GilaRepairs.start();
      setState(current => current && {...current, job: nextJob});
      setPoll(value => value + 1);
    } catch (failure) {
      setActionError(failureText(failure));
    } finally {
      setBuildProgress("");
      setStarting(false);
    }
  }

  return <AdminLayout><div className="min-w-0 space-y-4">
    <Glass as="section" contentClassName="space-y-5" id="repair-audit" tabIndex={-1}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold sm:text-3xl">Wikidata Manuscript Audit and Repair</h1>
          <p className="mt-2 max-w-prose text-sm muted">Compare the MARC records with live Wikidata to find missing manuscript data, incorrect links, and references that need review.</p>
        </div>
        <button className="button-primary" disabled={!state || active || starting || Boolean(refreshError)}
          onClick={() => void start()}>
          {buildProgress ? "Rebuild in progress" : starting || active ? "Audit in progress"
            : rebuildRequired ? "Rebuild source and run audit" : needsRecheck ? "Verify repairs on Wikidata"
              : report ? "Run a fresh audit" : "Start live audit"}
        </button>
      </div>
      {report && <AuditResult key={report.id} report={report} queue={queue} needsRecheck={needsRecheck}
        publishedSinceAudit={publishedSinceAudit} onOpenCases={filter => openCases(null, filter)} onStartRepair={() => void requestRepair()}
        onOpenReferences={openReferences} preparing={preparing}
        auditBusy={Boolean(rebuildRequired || active || starting)} />}
      {(repairNotice || repairError) && <div id="repair-request-status" tabIndex={-1}>
        {repairError ? <p role="alert" className="text-danger">{repairError}</p> : <p role="status">{repairNotice}</p>}
      </div>}
      <details className="rounded-xl border border-glass-line px-4 py-3 text-sm">
        <summary className="cursor-pointer font-medium">How repairs work</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 muted">
          <li>Run the audit to find cases. The audit reads Wikidata.</li>
          <li>Start manuscript repairs to prepare all eligible items in one release. Open a case to review its source or run a Kimi review.</li>
          <li>Review the release on this page. Run a dry run before you publish.</li>
          <li>Open the reference queue below to confirm its repair scope and monitor progress.</li>
        </ol>
        <p className="mt-2 muted">The system checks the creator before each edit. It can edit only items that the project account created.</p>
      </details>
      {!state && !refreshError && <p role="status">Read the last audit…</p>}
      {refreshError && <p role="alert" className="text-danger">Status refresh failed. {refreshError}</p>}
      {actionError && <p role="alert" className="text-danger">{actionError}</p>}
      {buildProgress ? <section className="rounded-xl border border-glass-line bg-[var(--surface-inset)] p-4" role="status">
        <p className="font-semibold">Rebuild the canonical Studio source</p>
        <p className="mt-1 text-sm muted">{buildProgress}</p>
        <p className="mt-1 text-sm muted">Keep this tab open so the page can start the audit after the rebuild.</p>
      </section> : job && job.status !== "succeeded" && <section className="rounded-xl border border-glass-line bg-[var(--surface-inset)] p-4" role="status" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-semibold">{job.status === "failed" ? "Audit stopped" : phases[job.progress.phase ?? ""] ?? job.status}</p>
          {active && total > 0 && <p className="text-sm tabular-nums muted">{processed.toLocaleString()} / {total.toLocaleString()} items</p>}
        </div>
        {active && <progress aria-label="Live audit progress" className="mt-2 h-2 w-full" max={total || 1}
          value={total ? processed : undefined} />}
        <p className="mt-1 text-sm muted">{rebuildRequired
          ? "The audit needs a current canonical Studio build. Rebuild the source to run the audit."
          : job.error ?? job.progress.message ?? "The job remains active if you close this page."}</p>
      </section>}
      {state && <nav className="flex flex-wrap gap-2" aria-label="Repair sections">
        {!report?.reference_blocks && <button className="button-ghost" onClick={openReferences}>Open reference queues</button>}
        {report && <button className="button-ghost" onClick={() => focusSection("repair-releases")}>Repair releases</button>}
        <button className="button-ghost" aria-expanded={inventoryOpen} onClick={() => {
          setInventoryOpen(value => !value);
          if (!inventoryOpen) focusSection("repair-inventory");
        }}>Saved repair inventory</button>
      </nav>}
    </Glass>
    {state && report && <>
      <Glass as="section" contentClassName="space-y-3">
        <h2 className="text-lg font-semibold">Manuscript audit checks</h2>
        <p className="text-sm muted">{report.public_read.toLocaleString()} public items read</p>
        {report.requirement_coverage.model_decision && <p className="text-sm muted">{report.requirement_coverage.model_decision}</p>}
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {report.email_points.map(row => <li key={row.point}>
            <button className={`w-full rounded-xl border p-3 text-left text-sm ${point === row.point ? "border-accent bg-[var(--surface-inset)]" : "border-glass-line"}`}
              aria-pressed={point === row.point} onClick={() => openCases(point === row.point ? null : row.point)}>
              <span className="block font-medium">{row.title}</span>
              <span className="mt-1 block text-xs muted">{labels[row.status]}</span>
            </button>
          </li>)}
        </ul>
      </Glass>
      <Cases key={`${report.id}:${selection}`} report={report} runId={state.run_id} point={point}
        initialFilter={initialFilter} actions={{
          preparing: Boolean(preparing || rebuildRequired || active || starting),
          onPrepare: caseId => void requestRepair(caseId),
          onSourceSaved: () => setSourceEdited(true),
        }} />
      <ManuscriptRepairReleases key={requestedRepairJob?.request ?? 0} runId={state.run_id}
        requestedJobId={requestedRepairJob?.id} onLiveChanges={onLiveChanges} />
    </>}
    {referencesOpen && <section id="repair-references" tabIndex={-1} className="space-y-3">
      <nav className="flex flex-wrap gap-2" aria-label="Reference repair scope">
        <button className={referenceScope === "remaining" ? "button-primary" : "button-ghost"}
          aria-pressed={referenceScope === "remaining"} onClick={() => setReferenceScope("remaining")}>Remaining references</button>
        <button className={referenceScope === "original" ? "button-primary" : "button-ghost"}
          aria-pressed={referenceScope === "original"} onClick={() => setReferenceScope("original")}>Original queue</button>
      </nav>
      <ReferenceRepairQueue key={referenceScope} scope={referenceScope}
        status={referenceScope === "remaining" ? remainingQueue : originalQueue}
        onVerify={() => focusSection("repair-audit")} />
    </section>}
    {state && inventoryOpen && <div id="repair-inventory" tabIndex={-1}>
      <WikidataRepairInventory runId={state.run_id} embedded />
    </div>}
  </div></AdminLayout>;
}
