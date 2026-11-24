import {useCallback, useEffect, useState} from "react";
import {useLocation} from "react-router-dom";

import {ApiError} from "@/api/client";
import {GilaRepairs, type AuditDisposition, type CaseFilter, type GilaCase, type GilaCaseReview, type GilaReport, type GilaStatus, type CorpusPreflightResult, type PreflightCase, type PreflightStatus, type RepairScope} from "@/api/gilaRepairs";
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
  linked_people: "Verify cited NLI authority links",
  compare: "Compare the evidence",
  check_report_source: "Verify the source before save",
};
const repairPhases: Record<string, string> = {
  initial_audit: "Audit all nine requests",
  audit_after_rebuild: "Audit the rebuilt source",
  rebuild: "Rebuild the canonical source",
  references_original: "Check the original reference queue",
  references_remaining: "Check the remaining reference queue",
  audit_after_references: "Check revisions after reference repairs",
  people_evidence: "Read NLI authority links for people and roles",
  people_bibframe: "Check original MARC fields against NLI BIBFRAME",
  people_viaf: "Check VIAF authority cross-references",
  people_plan: "Match people and check the edit targets",
  people_apply: "Apply and verify person repairs",
  sources_plan: "Check source proposals for requirement 8",
  corpus_ownership: "Verify the first creators",
  corpus_apply: "Apply and verify the eligible repairs",
  find_evidence: "Check source evidence with Kimi",
  prepare: "Check source evidence and item creators",
  dry_run: "Check the publication plan",
  publish: "Publish and verify supported edits",
  verify: "Audit the result on Wikidata",
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
  inscription_text_overlap: "Compare the texts that share this inscription role and folio before an edit.",
  exact_author_identity_required: "The work has a source-bound author name. Verify the person's authority record.",
  exact_scribe_identity_required: "Verify the copyist's identity from the signed note and an authority source.",
  public_provenance_and_identity_required: "Verify the named owner and the history of this copy in a public catalogue.",
  competing_public_person_matches: "More than one Wikidata item matches the authority identifier.",
  exact_public_person_match_unavailable: "The authority check found no unique verified Wikidata person.",
  exact_role_target_unavailable: "The exact work or manuscript target still needs a check.",
  exact_source_and_creator_verified: "The source, identity, and first creator passed the checks.",
  role_or_source_scope_requires_review: "The source needs a check for the role or the part of the manuscript it describes.",
  source_occurrence_changed: "The source occurrence changed since the batch review.",
  reviewed_target_changed: "The item target changed since the batch review.",
  case_absent_from_current_open_audit: "This case is absent from the current open audit. Check its latest report.",
  nli_catalogue_unavailable: "The worker could not read the NLI authority links for this record.",
  conflicting_claim: "An existing statement conflicts with the proposed edit.",
};

function failureText(error: unknown) {
  return error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "The request failed.";
}

function coverageText(counts: Partial<Record<AuditDisposition, number>>) {
  const verified = counts.verified ?? 0;
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  return total === 0 ? "No cases" : `${verified.toLocaleString()} / ${total.toLocaleString()} (${(verified / total * 100).toFixed(2)}%)`;
}

const preflightLabels: Record<PreflightStatus, string> = {
  eligible: "Eligible for repair",
  needs_evidence: "Needs evidence",
  other_creator: "Read-only item",
  needs_review: "Needs review",
};

const preflightStatuses: PreflightStatus[] = ["eligible", "needs_evidence", "other_creator", "needs_review"];

function CorpusPreflight({jobId, summary}: {jobId: string; summary: CorpusPreflightResult}) {
  const [open, setOpen] = useState(false);
  const [point, setPoint] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<{items: PreflightCase[]; total: number} | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let current = true;
    const params = new URLSearchParams({job_id: jobId, offset: String(offset), limit: String(pageSize)});
    if (point) params.set("point", point);
    if (status) params.set("status", status);
    setError("");
    setRows(null);
    void GilaRepairs.preflight(params).then(result => {
      if (current) setRows(result);
    }).catch(failure => {
      if (current) setError(failureText(failure));
    });
    return () => { current = false; };
  }, [jobId, offset, open, point, status]);
  return <div className="space-y-2">
    <h3 className="font-medium">{summary.case_count == null ? "Corpus preflight" : "Repair preflight"}</h3>
    <p className="text-sm muted">
      {summary.case_count == null ? `${summary.records.toLocaleString()} full MARC records` : `${summary.case_count.toLocaleString()} reviewed cases`}
      {" · "}{summary.eligible_items.toLocaleString()} eligible project items
    </p>
    {summary.requirements.map(row => <p className="text-sm muted" key={row.point}>
      Requirement {row.point} · {preflightStatuses.map(key =>
        `${(row.counts[key] ?? 0).toLocaleString()} ${preflightLabels[key].toLowerCase()}`).join(" · ")}
    </p>)}
    <details onToggle={event => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-sm">Review preflight cases</summary>
      {open && <div className="mt-2 space-y-2 text-sm">
        <div className="flex flex-wrap gap-3">
          {summary.requirements.length > 1 && <select aria-label="Preflight requirement" value={point} onChange={event => {
            setPoint(event.target.value);
            setOffset(0);
          }}>
            <option value="">All checked requirements</option>
            {summary.requirements.map(({point: requirement}) =>
              <option key={requirement} value={requirement}>Requirement {requirement}</option>
            )}
          </select>}
          <select aria-label="Preflight status" value={status} onChange={event => {
            setStatus(event.target.value);
            setOffset(0);
          }}>
            <option value="">All results</option>
            {Object.entries(preflightLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </div>
        {error ? <p role="alert" className="text-danger">{error}</p>
          : rows ? <>
            <div className="overflow-x-auto"><table className="w-full text-left">
              <thead><tr><th scope="col">Item</th><th scope="col">Source record</th><th scope="col">Decision</th></tr></thead>
              <tbody>{rows.items.map(row => <tr key={row.case_id} className="border-t border-glass-line">
                <td className="py-2 pr-3">{row.qid ?? "—"}</td>
                <td className="pr-3">{row.control_number ?? "—"} {row.source_field}
                  {row.person_role && <span className="block muted">{row.person_role}</span>}
                </td>
                <td>{preflightLabels[row.status]} · {explanations[row.reason] ?? row.reason.replaceAll("_", " ")}</td>
              </tr>)}</tbody>
            </table></div>
            {rows.total === 0 ? <p>No cases match these filters.</p> : <div className="flex flex-wrap items-center gap-2">
              <button className="button-ghost" disabled={offset === 0} onClick={() => setOffset(offset - pageSize)}>Previous</button>
              <span>{offset + 1}–{Math.min(offset + pageSize, rows.total)} of {rows.total}</span>
              <button className="button-ghost" disabled={offset + pageSize >= rows.total} onClick={() => setOffset(offset + pageSize)}>Next</button>
            </div>}
          </> : <p role="status">Read the preflight cases.</p>}
      </div>}
    </details>
  </div>;
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
        {report.case_count > 0 && <button className="button-ghost" disabled={preparing || auditBusy} onClick={onStartRepair}>
          {preparing ? "Request the repair…" : "Prepare eligible release"}
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
  const canReview = (item.disposition === "manual_review" || item.disposition === "repair_required")
    && Boolean(item.qid && item.control_number)
    && ["production_place_not_resolved", "named_scribe_identity_unresolved",
      "source_scribe_claim_absent", "verify_scribe_target_identity"].includes(item.reason ?? "");

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
      {item.person_role && <div><dt className="muted">Source role</dt><dd>{item.person_role}</dd></div>}
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
  const [startingAutomatic, setStartingAutomatic] = useState(false);
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
        if ([value.job, value.automatic_repair].some(job => job?.status === "queued" || job?.status === "running")) delay = 1800;
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
  const automatic = state?.automatic_repair;
  const automaticActive = automatic?.status === "queued" || automatic?.status === "running";
  const automaticResult = automatic?.result;
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
    if (!report || preparing || rebuildRequired || active || starting || automaticActive) return;
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

  async function startAutomatic(scope: RepairScope) {
    if (!state || startingAutomatic || automaticActive) return;
    setStartingAutomatic(true);
    setActionError("");
    try {
      const repair = await GilaRepairs.repairAll(scope);
      useRunJobs.getState().upsertJob(repair);
      useRunJobs.getState().ensurePolling();
      setState(value => value && {...value, automatic_repair: {
        id: repair.id, status: repair.status, progress: repair.progress, error: repair.error, result: null,
      }});
      setPoll(value => value + 1);
      focusSection("automatic-manuscript-repair");
    } catch (failure) {
      setActionError(failureText(failure));
    } finally {
      setStartingAutomatic(false);
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
        <div className="flex flex-wrap gap-2">
          <button className="button-primary"
            disabled={!state || startingAutomatic || automaticActive || starting || active || preparing || Boolean(refreshError)}
            onClick={() => void startAutomatic("people_and_sources")}>
            {automaticActive || startingAutomatic ? "Automatic repair in progress" : "Repair all eligible items"}
          </button>
          <button className="button-ghost"
            disabled={!state || startingAutomatic || automaticActive || starting || active || preparing || Boolean(refreshError)}
            onClick={() => void startAutomatic("people_authority")}>
            Repair people and current labels
          </button>
          <button className="button-ghost"
            disabled={!state || active || starting || automaticActive || startingAutomatic || Boolean(refreshError)}
            onClick={() => void start()}>
            {buildProgress ? "Rebuild in progress" : starting || active ? "Audit in progress"
              : rebuildRequired ? "Rebuild source and run audit" : needsRecheck ? "Verify repairs on Wikidata"
                : report ? "Run a fresh audit" : "Start live audit"}
          </button>
        </div>
      </div>
      <p className="text-sm muted">This repair checks every open case for people and current holdings. It applies edits that pass the evidence and creator checks.</p>
      {automatic && <section id="automatic-manuscript-repair" tabIndex={-1}
        aria-label="Automatic manuscript repair" aria-live="polite"
        className="space-y-2 rounded-xl border border-glass-line bg-[var(--surface-inset)] p-4">
        <h2 className="font-semibold">{automaticActive ? "Automatic repair in progress"
          : automatic.status === "succeeded" ? "Repair cycle complete"
            : automatic.status === "cancelled" ? "Repair cycle cancelled" : "Repair cycle stopped"}</h2>
        {automaticActive && <p className="text-sm font-medium">
          {repairPhases[(automatic.progress.phase ?? "").replace(/_\d+$/, "")] ?? "Wait for the worker"}
        </p>}
        <p className="text-sm muted">{automatic.error ?? automatic.progress.message ?? "Wait for the job service."}</p>
        {automaticActive && automatic.progress.total != null && automatic.progress.total > 0 && <>
          <progress aria-label="Automatic repair progress" className="h-2 w-full" max={automatic.progress.total}
            value={automatic.progress.processed ?? 0} />
          <p className="text-sm muted">{automatic.progress.processed ?? 0} / {automatic.progress.total} {automatic.progress.unit}</p>
        </>}
        {automaticActive && typeof automatic.progress.next_attempt_at === "string" && <p className="text-sm muted">
          Next retry at {new Date(automatic.progress.next_attempt_at).toLocaleString()}
        </p>}
        {automaticResult && <>
          {automaticResult.corpus_preflight && <CorpusPreflight key={automatic.id} jobId={automatic.id} summary={automaticResult.corpus_preflight} />}
          {automaticResult.corpus_verified?.map(row => <p key={row.point} className="text-sm">
            Requirement {row.point} · {row.cases.toLocaleString()} repaired cases verified
          </p>)}
          {!automaticResult.corpus_preflight && <p className="text-sm">{automaticResult.items_published} manuscript or work edits verified
            · {automaticResult.reference_items_corrected} items with reference repairs verified
            {automaticResult.requirements_verified != null && ` · ${automaticResult.requirements_verified} of 9 requirements verified`}
          </p>}
          {automaticResult.people_batch && !automaticResult.corpus_preflight && <p className="text-sm muted">
            {automaticResult.people_batch.cases_verified ?? 0} person cases verified by the final audit
            {" · "}{automaticResult.people_batch.edited_items ?? 0} project items edited
            {" · "}{automaticResult.people_batch.excluded_creator ?? 0} cases outside the creator scope
            {" · "}{automaticResult.people_batch.unavailable_catalogues ?? 0} catalogue records unavailable
          </p>}
          {automaticResult.people_batch?.reused_authority_reads != null && <p className="text-sm muted">
            {automaticResult.people_batch.reused_authority_reads.toLocaleString()} authority reads reused in the preflight
          </p>}
          {automaticResult.evidence_review && <p className="text-sm muted">
            {automaticResult.evidence_review.checked} source cases reviewed
            {" · "}{automaticResult.evidence_review.supported} passed the evidence checks
            {" · "}{automaticResult.evidence_review.needs_evidence} still need evidence
            {" · "}{automaticResult.evidence_review.excluded_creator} excluded by the creator check
            {" · "}{automaticResult.evidence_review.unavailable} checks unavailable
          </p>}
          {!!automaticResult.requirement_comparison?.length && <details>
            <summary className="cursor-pointer text-sm">Compare verification before and after repairs</summary>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead><tr>
                  <th className="py-2 pr-4" scope="col">Requirement</th>
                  <th className="py-2 pr-4" scope="col">Before</th>
                  <th className="py-2" scope="col">After</th>
                </tr></thead>
                <tbody>{automaticResult.requirement_comparison.filter(row =>
                  !automaticResult.corpus_preflight ||
                  automaticResult.corpus_preflight.requirements.some(summary => summary.point === row.point)).map(row =>
                  <tr className="border-t border-glass-line" key={row.point}>
                    <th className="py-2 pr-4 font-normal" scope="row">{row.title}</th>
                    <td className="whitespace-nowrap py-2 pr-4 tabular-nums">{coverageText(row.before)}</td>
                    <td className="whitespace-nowrap py-2 tabular-nums">{coverageText(row.after)}</td>
                  </tr>)}</tbody>
              </table>
            </div>
          </details>}
          {automaticResult.stages_needing_attention.map((stage, index) => <p key={index} className="text-sm text-warn">{stage.reason}</p>)}
          {automaticResult.exclusions.length > 0 && <details>
            <summary className="cursor-pointer text-sm">{automaticResult.exclusions.length} exclusions from repair plans</summary>
            <ul className="space-y-1 text-sm muted">
              {automaticResult.exclusions.map(({qid, reason}, index) => <li key={index}>
                {qid && <a className="underline" href={`https://www.wikidata.org/wiki/${qid}`}>{qid}</a>}{" "}
                {reason === "previous_write_needs_verification" ? "A previous edit needs verification." : reason.replaceAll("_", " ")}
              </li>)}
            </ul>
          </details>}
          {report && <button className="button-ghost" onClick={() => openCases(null)}>Review remaining evidence</button>}
        </>}
      </section>}
      {report && <AuditResult key={report.id} report={report} queue={queue} needsRecheck={needsRecheck}
        publishedSinceAudit={publishedSinceAudit} onOpenCases={filter => openCases(null, filter)} onStartRepair={() => void requestRepair()}
        onOpenReferences={openReferences} preparing={preparing}
        auditBusy={Boolean(rebuildRequired || active || starting || automaticActive || startingAutomatic)} />}
      {(repairNotice || repairError) && <div id="repair-request-status" tabIndex={-1}>
        {repairError ? <p role="alert" className="text-danger">{repairError}</p> : <p role="status">{repairNotice}</p>}
      </div>}
      <details className="rounded-xl border border-glass-line px-4 py-3 text-sm">
        <summary className="cursor-pointer font-medium">How repairs work</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 muted">
          <li>The job audits all nine requests and rebuilds the source if necessary.</li>
          <li>The job checks creator identity, applies reviewed reference repairs, and checks each manuscript repair before publication.</li>
          <li>The job runs a new audit after the repairs. Open each request to see the cases that need evidence.</li>
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
        {report.people_coverage && <section className="rounded-xl border border-soft p-4" aria-label="People, roles, and authority links">
          <h3 className="text-base font-semibold">People, roles, and authority links</h3>
          <p className="text-sm muted">
            {report.people_coverage.records_with_full_marc.toLocaleString()} of {report.record_count.toLocaleString()} records have full MARC fields.
          </p>
          <p className="text-sm muted">
            {report.people_coverage.roles_verified.toLocaleString()} of {report.people_coverage.source_roles.toLocaleString()} source roles match Wikidata.
            {" "}{report.people_coverage.authority_links_verified.toLocaleString()} have verified authority links.
            {" "}{report.people_coverage.uncertainty_preserved.toLocaleString()} retain uncertainty from the source.
          </p>
        </section>}
        {report.requirement_coverage.model_decision && <p className="text-sm muted">{report.requirement_coverage.model_decision}</p>}
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {report.email_points.map(row => {
            const verified = row.counts?.verified ?? 0;
            const total = Object.values(row.counts ?? {}).reduce((sum, count) => sum + count, 0);
            return <li key={row.point}>
            <button className={`w-full rounded-xl border p-3 text-left text-sm ${point === row.point ? "border-accent bg-[var(--surface-inset)]" : "border-glass-line"}`}
              aria-pressed={point === row.point} onClick={() => openCases(point === row.point ? null : row.point)}>
              <span className="block font-medium">{row.title}</span>
              <span className="mt-1 block text-xs muted">{labels[row.status]}</span>
              {row.point === 9 ? <span className="mt-1 block space-y-1 text-xs muted">
                <span className="block">
                  {report.requirement_coverage.public_reads.toLocaleString()} / {report.record_count.toLocaleString()} manuscript mappings verified
                </span>
                <span className="block">
                  {report.requirement_coverage.unchecked_passages.toLocaleString()} source passages need proof
                </span>
                {report.requirement_coverage.verified_repair_receipts != null && <span className="block">
                  {report.requirement_coverage.verified_repair_receipts.toLocaleString()} repair receipts verified in this audit
                </span>}
                {Boolean(report.requirement_coverage.repair_receipts_needing_review) && <span className="block">
                  {report.requirement_coverage.repair_receipts_needing_review?.toLocaleString()} repair receipts need a new check
                </span>}
              </span> : total > 0 && <span className="mt-1 block text-xs muted">
                {verified.toLocaleString()} / {total.toLocaleString()} cases verified ({(verified / total * 100).toFixed(1)}%)
              </span>}
              {row.ready_count != null && row.ready_count > 0 && <span className="mt-1 block text-xs muted">
                {row.ready_count.toLocaleString()} {row.ready_count === 1 ? "case" : "cases"} ready for the creator check
              </span>}
            </button>
          </li>;})}
        </ul>
      </Glass>
      <Cases key={`${report.id}:${selection}`} report={report} runId={state.run_id} point={point}
        initialFilter={initialFilter} actions={{
          preparing: Boolean(preparing || rebuildRequired || active || starting || automaticActive),
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
