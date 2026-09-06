import {useEffect, useState} from "react";
import {PublicationAiReviewApi, type PublicationAiReviewState, type PublicationAdvanceCommand, type PublicationSummary} from "@/api/publication";
import {RunJobs} from "@/api/runJobs";
import {Tier1ModelSelect, useTier1Model} from "@/components/Tier1ModelSelect";
import {useRunJobs} from "@/stores/runJobs";
import {getPublicationReadiness} from "@/utils/publicationState";

interface Props {
  publication: PublicationSummary;
  busy: boolean;
  onAdvance: (command: PublicationAdvanceCommand) => void | Promise<void>;
  onActiveChange: (active: boolean) => void;
}

export function WikidataPublicationAiReview({publication, busy, onAdvance, onActiveChange}: Props) {
  const [state, setState] = useState<PublicationAiReviewState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [override, setOverride] = useState(false);
  const model = useTier1Model();
  const active = state?.status === "queued" || state?.status === "running";
  const {run_id: runId, publication_id: publicationId, plan, approval_set: approval} = publication;
  const current = publication.source_current && getPublicationReadiness(publication).approvalCurrent && !publication.execution;
  const report = state?.report;
  const reportCurrent = current && report?.publication_id === publicationId && report?.plan_id === plan?.plan_id
    && report?.plan_digest === plan?.plan_digest && report?.release_digest === publication.current_release.release_digest;
  const recommended = reportCurrent && state?.status === "succeeded"
    ? (report?.items ?? []).flatMap((item) => item.status === "recommended" && item.consent ? [item.consent] : []) : [];

  useEffect(() => {
    onActiveChange(active);
    return () => onActiveChange(false);
  }, [active, onActiveChange]);

  useEffect(() => {
    let stopped = false;
    void PublicationAiReviewApi.read(runId, publicationId).then((saved) => {
      if (!stopped) setState(saved);
    }).catch((caught: unknown) => {if (!stopped) setError(String(caught));})
      .finally(() => {if (!stopped) setLoading(false);});
    return () => {stopped = true;};
  }, [runId, publicationId]);

  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await PublicationAiReviewApi.read(runId, publicationId);
        if (stopped) return;
        setState(next);
        if (next.job_id) {
          const job = await RunJobs.get(runId, next.job_id);
          if (!stopped) useRunJobs.getState().upsertJob(job);
        }
      } catch (caught: unknown) {if (!stopped) setError(String(caught));}
      if (!stopped) timer = setTimeout(() => {void poll();}, 2000);
    };
    timer = setTimeout(() => {void poll();}, 1000);
    return () => {stopped = true; clearTimeout(timer);};
  }, [active, runId, publicationId]);

  const start = async () => {
    if (!plan) return;
    setLoading(true);
    setError(null);
    try {
      const next = await PublicationAiReviewApi.start(runId, publicationId, {
        plan_id: plan.plan_id, plan_digest: plan.plan_digest, tier_model: model.tierModel, force_refresh: override,
      });
      setState(next);
      if (next.job_id) useRunJobs.getState().upsertJob(await RunJobs.get(runId, next.job_id));
    } catch (caught: unknown) {setError(String(caught));}
    finally {setLoading(false);}
  };

  const approve = async () => {
    if (!approval || recommended.length === 0) return;
    await onAdvance({type: "dry_run", approval_set_id: approval.approval_set_id,
      expected_approval_digest: approval.approval_digest, force_refresh: true, foreign_qid_consents: recommended});
  };

  return <div className="rounded-lg border border-white/10 p-3 space-y-3" data-testid="publication-ai-review">
    <p className="text-sm">AI compares blocked items with Wikidata. Your approval supplies consent only for supported recommendations.</p>
    <Tier1ModelSelect tierModel={model.tierModel} onChange={model.setTierModel} list={model.list}
      loading={model.loading} disabled={busy || active || loading} />
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={override}
      disabled={busy || active || loading} onChange={(event) => setOverride(event.target.checked)} />
      Override AI cache (fresh review)</label>
    <button type="button" className="button-ghost text-sm" disabled={busy || active || loading || !current || !model.selected?.available}
      onClick={() => {void start();}}>AI review blocked items</button>
    {state?.job_id && <p className="text-xs">AI review: {state.status} · {state.processed} / {state.total} items</p>}
    {active && state?.job_id && <button type="button" className="button-ghost text-sm"
      onClick={() => {if (state.job_id) void RunJobs.cancel(runId, state.job_id).catch((caught: unknown) => setError(String(caught)));}}>Cancel AI review</button>}
    {(error || state?.error) && <p className="text-danger text-sm">{error || state?.error}</p>}
    {report && <div data-testid="publication-ai-report" className="space-y-2">
      <p className="text-xs muted">Report: {report.tier_model} · {report.created_at}</p>
      {!reportCurrent && <p className="text-warn">This report does not match the current Release and Plan.</p>}
      <ul className="space-y-3">{report.items.map((item) => <li key={item.entity_key} className="text-sm">
        <p>{item.label} · {item.status.replaceAll("_", " ")}</p><p className="muted">{item.reason}</p>
        {item.qid && <a className="text-accent underline" target="_blank" rel="noopener noreferrer"
          href={`https://${publication.target === "live" ? "www" : "test"}.wikidata.org/wiki/${item.qid}`}>Review {item.qid}</a>}
      </li>)}</ul>
      <button type="button" className="button-primary text-sm" disabled={busy || active || loading || recommended.length === 0}
        onClick={() => {void approve();}}>Approve AI recommendations ({recommended.length}) and check again</button>
      <p className="text-xs muted">Uncertain items stay blocked. The fresh dry-run checks every consent against the current Wikidata revision.</p>
    </div>}
  </div>;
}
