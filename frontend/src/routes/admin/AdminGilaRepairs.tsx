import {useEffect, useState} from "react";
import {Link} from "react-router-dom";

import {ApiError} from "@/api/client";
import {GilaRepairs, type CaseFilter, type GilaCase, type GilaReport, type GilaStatus} from "@/api/gilaRepairs";
import {AdminLayout} from "@/components/admin/AdminLayout";
import {Glass} from "@/components/glass";
import {WikidataSourceReview} from "@/components/wikidata/WikidataSourceReview";
import {useDebounce} from "@/hooks/useDebounce";
import {waitForRunJob} from "@/utils/waitForRunJob";

const pageSize = 25;
const labels = {
  verified: "Verified", repair_required: "Repair needed", manual_review: "Needs review",
  not_checked: "Needs evidence", absent_in_source: "Absent from source",
};
const phases: Record<string, string> = {
  queued: "Wait for a worker",
  source: "Read the source",
  public_items: "Check Wikidata items",
  linked_items: "Check linked items",
  compare: "Compare the evidence",
  complete: "Audit complete",
};
const explanations: Record<string, string> = {
  public_record_identity_requires_review: "The project needs a confirmed manuscript mapping.",
  duplicate_public_catalog_identity: "Two or more Wikidata items have the same catalog identifier.",
  exact_historical_proposal_requires_guarded_execution: "The live item has a historical repair that needs the reference queue or specific review.",
  passage_requires_exact_scope_and_public_reference_proof: "This passage needs a check of its source, scope, and live statement.",
  live_work_author_or_source_requires_review: "The current work target needs an author or source check.",
  live_work_identity_or_scope_requires_review: "Confirm the work identity and the folios before you set the link.",
  author_authority_link_absent: "The live work lacks the expected author link. Confirm the work mapping before preparation.",
};

function failureText(error: unknown) {
  return error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "The request failed.";
}

function CaseDetails({item, report, runId}: {item: GilaCase; report: GilaReport; runId: string}) {
  const [showSource, setShowSource] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [publication, setPublication] = useState<string>();
  const sourceGap = item.classification === "absent_in_source";
  const canUseSourceReview = item.local_id && !sourceGap && !item.case_id.startsWith("work-author:");
  const links = item.qids ?? (item.qid ? [item.qid] : []);
  const reasons = [...new Set([item.reason, item.live_reason, item.completion_reason].filter(Boolean))];

  async function prepare() {
    setPreparing(true);
    setError("");
    try {
      const job = await GilaRepairs.prepare(report.id, item.case_id);
      const finished = await waitForRunJob(runId, job.id);
      const id = finished.result?.publication_id;
      if (typeof id !== "string") throw new Error("The worker did not return a Publication release.");
      setPublication(id);
      setNotice("The release is ready for review and a dry run.");
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setPreparing(false);
    }
  }

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
      {canUseSourceReview && <button className="button-ghost" aria-expanded={showSource}
        onClick={() => setShowSource(value => !value)}>{showSource ? "Close source review" : "Review source"}</button>}
      {item.can_prepare && !publication && !notice && <button className="button-primary" disabled={preparing}
        onClick={() => void prepare()}>{preparing ? "Prepare the release…" : "Prepare repair release"}</button>}
      {publication && <Link className="button-primary" to={`/runs/${runId}/wikidata-studio?publication=${publication}`}>Review the release</Link>}
    </div>
    {notice && <p role="status">{notice}</p>}
    {error && <p className="text-danger" role="alert">{error}</p>}
    {showSource && item.local_id && <WikidataSourceReview runId={runId} localId={item.local_id}
      onSaved={() => setNotice("The source decision is saved. Rebuild the canonical source and run the audit again.")} />}
  </div>;
}

function Cases({report, runId, point}: {report: GilaReport; runId: string; point: number | null}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<CaseFilter | "">("");
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<{items: GilaCase[]; total: number}>();
  const [error, setError] = useState("");
  const search = useDebounce(query, 250);

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

  return <Glass as="section" contentClassName="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <h2 className="text-lg font-semibold">{point ? report.email_points.find(row => row.point === point)?.title : "Open cases"}</h2>
      <div className="flex flex-wrap gap-2">
        <input className="input" aria-label="Find a case" type="search" value={query} maxLength={120}
          placeholder="Find a QID, source ID, or title" onChange={event => {setQuery(event.target.value); setOffset(0);}} />
        <select className="input" aria-label="Case status" value={filter}
          onChange={event => {
            const value = event.target.value;
            if (value === "" || value === "repair_required" || value === "manual_review"
              || value === "not_checked" || value === "absent_in_source") setFilter(value);
            setOffset(0);
          }}>
          <option value="">All open cases</option>
          <option value="repair_required">Repair needed</option>
          <option value="manual_review">Needs review</option>
          <option value="not_checked">Needs evidence</option>
          <option value="absent_in_source">Absent from source</option>
        </select>
      </div>
    </div>
    {!result && !error && <p role="status" className="muted">Read the case list…</p>}
    {error && <p role="alert" className="text-danger">{error}</p>}
    {result && (result.total === 0 ? <p className="muted">No cases match this filter.</p> : <>
      <ul className="divide-y divide-glass-line rounded-xl border border-glass-line">
        {result.items.map(item => <li key={item.case_id}>
          <details>
            <summary className="cursor-pointer px-4 py-3">
              <span className="ml-2 inline-flex max-w-[92%] flex-wrap items-baseline gap-x-3 gap-y-1 align-middle">
                <span className="font-mono text-xs muted">{item.qid ?? item.control_number ?? "Corpus"}</span>
                <span className="max-w-full break-words text-sm" dir="auto">{item.source_value?.slice(0, 120)
                  || explanations[item.reason ?? ""] || item.source_field || "Source evidence"}</span>
                <span className="text-xs font-medium text-[var(--text-muted)]">{labels[item.classification === "absent_in_source" ? "absent_in_source" : item.disposition]}</span>
              </span>
            </summary>
            <CaseDetails item={item} report={report} runId={runId} />
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
    </>)}
  </Glass>;
}

export default function AdminGilaRepairs() {
  const [state, setState] = useState<GilaStatus>();
  const [refreshError, setRefreshError] = useState("");
  const [actionError, setActionError] = useState("");
  const [starting, setStarting] = useState(false);
  const [point, setPoint] = useState<number | null>(null);
  const [poll, setPoll] = useState(0);
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
  const active = job?.status === "queued" || job?.status === "running";
  const processed = job?.progress.processed ?? 0;
  const total = job?.progress.total ?? 0;

  async function start() {
    setStarting(true);
    setActionError("");
    try {
      await GilaRepairs.start();
      setPoll(value => value + 1);
    } catch (failure) {
      setActionError(failureText(failure));
    } finally {
      setStarting(false);
    }
  }

  return <AdminLayout><div className="min-w-0 space-y-4">
    <Glass as="section" contentClassName="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold sm:text-3xl">Gila’s Wikidata repairs</h1>
          <p className="mt-2 text-sm muted">Check all nine requirements against the current MARC sources and live Wikidata.</p>
        </div>
        <button className="button-primary" disabled={!state || active || starting || Boolean(refreshError)}
          onClick={() => void start()}>{starting || active ? "Audit in progress" : report ? "Run a fresh audit" : "Start live audit"}</button>
      </div>
      {!state && !refreshError && <p role="status">Read the last audit…</p>}
      {refreshError && <p role="alert" className="text-danger">Status refresh failed. {refreshError}</p>}
      {actionError && <p role="alert" className="text-danger">{actionError}</p>}
      {job && <section className="rounded-xl border border-glass-line bg-[var(--surface-inset)] p-4" role="status" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-semibold">{job.status === "failed" ? "Audit stopped" : phases[job.progress.phase ?? ""] ?? job.status}</p>
          {active && total > 0 && <p className="text-sm tabular-nums muted">{processed.toLocaleString()} / {total.toLocaleString()} items</p>}
        </div>
        {active && <progress aria-label="Live audit progress" className="mt-2 h-2 w-full" max={total || 1}
          value={total ? processed : undefined} />}
        <p className="mt-1 text-sm muted">{job.error ?? job.progress.message ?? "The job remains active if you close this page."}</p>
      </section>}
      {report && <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          [report.requirement_coverage.public_reads + "/" + report.record_count, "Manuscript mappings verified"],
          [report.public_read.toLocaleString(), "Public items read"],
          [String(report.email_points.filter(row => row.status === "verified").length) + "/9", "Requirements verified"],
          [report.reference_blocks.toLocaleString(), "Repeated references"],
        ].map(([count, label]) => <div key={label} className="rounded-xl border border-glass-line px-3 py-3 sm:px-4">
          <p className="text-xl font-semibold tabular-nums">{count}</p><p className="mt-1 text-xs muted">{label}</p>
        </div>)}
      </div>}
      <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
        {state && <Link className="link-accent underline" to={`/runs/${state.run_id}/wikidata-studio`}>Open Wikidata Studio</Link>}
        <Link className="link-accent underline" to="/admin/wikidata-reference-repairs?scope=remaining">Open the remaining reference queue</Link>
      </div>
    </Glass>
    {state && report && <>
      <Glass as="section" contentClassName="space-y-3">
        <h2 className="text-lg font-semibold">The nine requirements</h2>
        <p className="text-sm muted">{active ? "Previous report" : "Last audit"} · {new Date(report.finished_at).toLocaleString()}</p>
        {report.requirement_coverage.model_decision && <p className="text-sm muted">{report.requirement_coverage.model_decision}</p>}
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {report.email_points.map(row => <li key={row.point}>
            <button className={`w-full rounded-xl border p-3 text-left text-sm ${point === row.point ? "border-accent bg-[var(--surface-inset)]" : "border-glass-line"}`}
              aria-pressed={point === row.point} onClick={() => setPoint(point === row.point ? null : row.point)}>
              <span className="block font-medium">{row.title}</span>
              <span className="mt-1 block text-xs muted">{labels[row.status]}</span>
            </button>
          </li>)}
        </ul>
      </Glass>
      <Cases key={`${report.id}:${point ?? ""}`} report={report} runId={state.run_id} point={point} />
    </>}
  </div></AdminLayout>;
}
