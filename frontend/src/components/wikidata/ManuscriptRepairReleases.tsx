import {useEffect, useState} from "react";

import {GilaRepairs, type GilaPreparation} from "@/api/gilaRepairs";
import {Glass} from "@/components/glass";
import {WikidataPublicationPanel} from "@/components/wikidata/WikidataPublicationPanel";

const exclusionReasons: Record<string, string> = {
  other_creator: "Another account created the item.",
  creator_unavailable: "Wikidata could not confirm the creator.",
  target_conflict: "The audited item mappings conflict.",
  source_changed: "The source or target changed after the audit.",
  evidence_unavailable: "The worker could not confirm the current Wikidata evidence.",
};

function pending(job: GilaPreparation) {
  return job.status === "queued" || job.status === "running";
}

export function ManuscriptRepairReleases({runId, requestedJobId, onLiveChanges}: {
  runId: string;
  requestedJobId?: string;
  onLiveChanges: (finishedAt: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [request, setRequest] = useState(0);
  const [page, setPage] = useState<{items: GilaPreparation[]; more: boolean}>();
  const [selected, setSelected] = useState<string>();
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    async function refresh() {
      let delay = 0;
      try {
        const result = await GilaRepairs.preparations(offset);
        if (stopped) return;
        setPage(result);
        setError("");
        const job = requestedJobId
          ? result.items.find(item => item.id === requestedJobId)
          : result.items.find(pending) ?? result.items.find(item => item.publication_id);
        if (job?.publication_id) setSelected(current => current ?? job.publication_id ?? undefined);
        if (result.items.some(pending)) delay = 2000;
      } catch (failure) {
        if (!stopped) setError(failure instanceof Error ? failure.message : "The release list failed.");
        delay = 5000;
      }
      if (!stopped && delay) timer = window.setTimeout(() => void refresh(), delay);
    }
    void refresh();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [offset, request, requestedJobId]);

  function changePage(next: number) {
    setPage(undefined);
    setOffset(next);
  }

  return <Glass as="section" contentClassName="space-y-4" id="repair-releases" tabIndex={-1}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-lg font-semibold">Repair releases</h2>
      <button className="button-ghost text-sm" onClick={() => setRequest(value => value + 1)}>Refresh releases</button>
    </div>
    {error && <p className="text-sm text-danger" role="alert">{error}</p>}
    {!page && !error && <p role="status" className="text-sm muted">Read the repair releases…</p>}
    {page?.items.length === 0 && <p className="text-sm muted">Prepare a case to create its repair release.</p>}
    {page && page.items.length > 0 && <ul className="divide-y divide-glass-line rounded-xl border border-glass-line">
      {page.items.map(job => <li key={job.id} className="flex flex-wrap items-center justify-between gap-3 p-3 text-sm">
        <div>
          <p className="font-medium">{job.open_case_counts ? "Manuscript repair batch"
            : job.qids.join(", ") || "Manuscript repair"}</p>
          <p className={job.error ? "text-danger" : "muted"} role={pending(job) ? "status" : undefined}>
            {job.error || job.progress.message || job.status}
          </p>
          {pending(job) && Boolean(job.progress.total) && <p className="mt-1 text-sm muted">
            {job.progress.processed ?? 0} of {job.progress.total} items checked
          </p>}
          {job.batch_counts && <p className="mt-1 text-sm">
            {job.batch_counts.prepared} of {job.batch_counts.eligible} eligible items prepared
          </p>}
          {job.open_case_counts && <p className="mt-1 text-sm muted">
            {job.open_case_counts.manual_review.toLocaleString()} audit cases require review.{" "}
            {job.open_case_counts.not_checked.toLocaleString()} audit cases require evidence or a check.
          </p>}
          {Boolean(job.exclusions?.length) && <details className="mt-2">
            <summary className="cursor-pointer">Items that did not pass the repair checks</summary>
            <ul className="mt-1 space-y-1">
              {job.exclusions?.map(item => <li key={item.qid}>
                {item.qid} · {exclusionReasons[item.reason] ?? item.reason}
              </li>)}
            </ul>
          </details>}
        </div>
        {job.publication_id && <button className="button-ghost"
          aria-pressed={selected === job.publication_id}
          onClick={() => setSelected(job.publication_id ?? undefined)}>Review the release</button>}
      </li>)}
    </ul>}
    {page && (offset > 0 || page.more) && <div className="flex flex-wrap gap-2">
      <button className="button-ghost" disabled={offset === 0} onClick={() => changePage(offset - 20)}>Previous releases</button>
      <button className="button-ghost" disabled={!page.more} onClick={() => changePage(offset + 20)}>Next releases</button>
    </div>}
    {selected && <WikidataPublicationPanel key={selected} runId={runId} source="canonical"
      approvedOnly repairPublicationId={selected} onLiveChanges={onLiveChanges} />}
  </Glass>;
}
