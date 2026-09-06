import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {
  PublicationApi,
  type PublicationAdvanceCommand,
  type PublicationEntity,
  type PublicationOperation,
  type PublicationSummary,
  type PublicationTarget,
} from "@/api/publication";
import type {StudioBuild, StudioItem} from "@/api/wikidataStudio";
import {WikidataPublicationControls} from "@/components/wikidata/WikidataPublicationControls";
import {usePublicationEntityPage} from "@/hooks/usePublicationEntityPage";

import {useRunJobs} from "@/stores/runJobs";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";

const PROFILE_ID = "mhm-wikidata";

export interface WikidataPublicationPanelProps {
  runId: string;
  source: "legacy" | "canonical";
  approvedOnly: boolean;
  build: StudioBuild;
  onPublicationActiveChange?: (active: boolean) => void;
}

function labelOf(item: StudioItem): string {
  return item.labels?.en
    ?? item.labels?.he
    ?? Object.values(item.labels ?? {})[0]
    ?? item.local_id
    ?? "Untitled entity";
}

function compatibilityEntity(item: StudioItem, index: number): PublicationEntity {
  const entityId = item.local_id ?? `${item.entity_type ?? "entity"}:${index}`;
  return {
    entity_id: entityId,
    entity_digest: item.source_fingerprint ?? `compatibility:${entityId}:${item.statement_count ?? item.statements?.length ?? 0}`,
    entity_kind: item.entity_type ?? "entity",
    label: labelOf(item),
    description: item.descriptions?.en ?? item.descriptions?.he ?? null,
    target_qid: item.existing_qid ?? null,
    statement_count: item.statement_count ?? item.statements?.length ?? 0,
    review_status: item.approved === true ? "approved" : "pending",
    proposed_action: item.validation_issues?.some((issue) => issue.severity === "error")
      ? "blocked"
      : item.existing_qid
        ? "update"
        : "create",
    findings: (item.validation_issues ?? []).map((issue, issueIndex) => ({
      finding_id: `${entityId}:${issue.code}:${issueIndex}`,
      code: issue.code,
      severity: issue.severity,
      message: issue.message,
      gate: issue.severity === "error",
    })),
  };
}

function operationActive(operation: PublicationOperation | null): boolean {
  return operation?.status === "queued" || operation?.status === "running";
}

export function WikidataPublicationPanel({
  runId,
  source,
  approvedOnly,
  build,
  onPublicationActiveChange,
}: WikidataPublicationPanelProps) {
  const [restoring, setRestoring] = useState(true);
  const [target, setTarget] = useState<PublicationTarget>("test");
  const [deferConnections, setDeferConnections] = useState(false);
  const [publication, setPublication] = useState<PublicationSummary | null>(null);
  const [operation, setOperation] = useState<PublicationOperation | null>(null);
  const [jobSnapshot, setJobSnapshot] = useState<RunJobSnapshot | null>(null);
  const [prepareJobId, setPrepareJobId] = useState<string | null>(null);
  const [busyCommand, setBusyCommand] = useState<PublicationAdvanceCommand["type"] | "prepare" | null>(null);
  const [routeUnavailable, setRouteUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const compatibilityItems = useMemo(
    () => build.items.map(compatibilityEntity),
    [build.items],
  );
  const release = publication?.current_release;
  const entityPage = usePublicationEntityPage({
    runId,
    publicationId: publication?.publication_id ?? null,
    releaseId: release?.release_id ?? "current-studio-data",
    releaseDigest: release?.release_digest ?? "compatibility-current-data",
    limit: 50,
    compatibilityItems,
  });

  useEffect(() => {
    onPublicationActiveChange?.(!routeUnavailable);
  }, [onPublicationActiveChange, routeUnavailable]);

  useEffect(() => {
    if (source !== "canonical" && target === "live") setTarget("test");
  }, [source, target]);

  useEffect(() => {
    let stopped = false;
    setRestoring(true);
    void PublicationApi.latest(runId).then(async ({publication: saved}) => {
        if (stopped || !saved) return;
        setPublication(saved);
        setTarget(saved.target);
        setDeferConnections(saved.profile_version === "1-nodes");
        const {jobs} = await RunJobs.listForRun(runId, false, {kind: "wikidata_publication_dry_run", limit: 1});
        const job = jobs[0];
        if (stopped || !job || job.params.publication_id !== saved.publication_id) return;
        setJobSnapshot(job);
        useRunJobs.getState().upsertJob(job);
        if (job.status === "queued" || job.status === "running") setPrepareJobId(job.id);
        else if (job.error) setError(job.error);
      }).catch((caught: unknown) => {
        if (!stopped) setError(caught instanceof ApiError ? caught.detail : String(caught));
      }).finally(() => { if (!stopped) setRestoring(false); });
    return () => { stopped = true; };
  }, [runId]);

  const prepare = useCallback(async () => {
    setBusyCommand("prepare");
    setError(null);
    setRouteUnavailable(false);
    try {
      const response = await PublicationApi.prepare(runId, {
        profile_id: PROFILE_ID,
        profile_version: deferConnections ? "1-nodes" : "1",
        target,
        source: {
          kind: "run",
          projection_source: source,
          approved_only: approvedOnly,
        },
      });
      setPublication(response.publication);
      setOperation(response.operation ?? null);
      setPrepareJobId(response.publication ? null : response.operation?.operation_id ?? null);
    } catch (caught) {
      if (caught instanceof ApiError && [404, 405, 410].includes(caught.status)) {
        setRouteUnavailable(true);
      } else {
        setError(caught instanceof ApiError ? caught.detail : String(caught));
      }
    } finally {
      setBusyCommand(null);
    }
  }, [approvedOnly, deferConnections, runId, source, target]);

  useEffect(() => {
    if (!prepareJobId) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const job = await RunJobs.get(runId, prepareJobId);
        if (stopped) return;
        setJobSnapshot(job);
        useRunJobs.getState().upsertJob(job);
        if (job.status === "queued" || job.status === "running") {
          timer = window.setTimeout(() => { void poll(); }, 1000);
          return;
        }
        const publicationId = typeof job.result?.publication_id === "string"
          ? job.result.publication_id : null;
        if (publicationId) {
          const response = await PublicationApi.read(runId, publicationId, {type: "summary"});
          if (stopped) return;
          setPublication(response.publication);
        }
        if (job.status !== "succeeded") setError(job.error ?? "The Publication job did not complete.");
        setOperation(null);
        setBusyCommand(null);
        setPrepareJobId(null);
      } catch (caught: unknown) {
        if (stopped) return;
        setError(caught instanceof ApiError ? caught.detail : String(caught));
        setOperation(null);
        setBusyCommand(null);
        setPrepareJobId(null);
      }
    };
    void poll();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [prepareJobId, runId]);

  const advance = useCallback(async (command: PublicationAdvanceCommand) => {
    if (!publication) return;
    setBusyCommand(command.type);
    setError(null);
    try {
      const response = await PublicationApi.advance(runId, publication.publication_id, command);
      if (response.publication) {
        setPublication(response.publication);
        if (command.type === "dry_run" && response.publication.dry_run_receipt?.status === "failed") {
          setError("The saved dry-run has blocked actions. Review the plan below. Use Override cache for fresh checks.");
        }
      }
      setOperation(response.operation ?? null);
      if (command.type === "dry_run" && response.operation?.status === "queued") {
        setJobSnapshot(null);
        setPrepareJobId(response.operation.operation_id);
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setBusyCommand(null);
    }
  }, [publication, runId]);

  useEffect(() => {
    if (prepareJobId || !publication || !operationActive(operation)) return;
    const timer = window.setTimeout(() => {
      void PublicationApi.read(runId, publication.publication_id, {
        type: "operation",
        operation_id: operation?.operation_id ?? "",
      }).then((response) => {
        setPublication(response.publication);
        setOperation(response.operation);
      }).catch((caught: unknown) => {
        setError(caught instanceof ApiError ? caught.detail : String(caught));
      });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [operation, publication, prepareJobId, runId]);

  return (
    <section className="rounded-xl border border-biu-sky/20 bg-biu-sky/[0.03] p-4 space-y-4" data-testid="wikidata-publication-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="kicker">Publication</div>
          <h3 className="text-lg font-medium">Versioned Wikidata publication</h3>
          <p className="mt-1 max-w-3xl text-sm muted">
            Prepare an immutable Release. Review entity digests. Create a Dry-run Receipt before an Execution can write.
          </p>
        </div>
        {publication && (
          <button
            type="button"
            className="button-ghost text-sm"
            disabled={restoring || busyCommand !== null || operationActive(operation)}
            onClick={() => { void prepare(); }}
            data-testid="publication-prepare-new-release"
          >
            {busyCommand === "prepare" ? "Preparing…" : "Prepare new Release"}
          </button>
        )}
      </div>

      {!publication && !routeUnavailable && (
        <div className="space-y-3">
          <fieldset className="flex flex-wrap gap-4 text-sm" disabled={busyCommand !== null}>
            <legend className="mb-2 text-xs muted">Wikidata target</legend>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="publication-target"
                checked={target === "test"}
                onChange={() => setTarget("test")}
                data-testid="publication-target-test"
              />
              test.wikidata.org
            </label>
            <label className="flex items-center gap-2" title={source === "canonical" ? "" : "Live publication requires the canonical source."}>
              <input
                type="radio"
                name="publication-target"
                checked={target === "live"}
                onChange={() => setTarget("live")}
                disabled={source !== "canonical"}
                data-testid="publication-target-live"
              />
              www.wikidata.org
            </label>
          </fieldset>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={deferConnections}
              disabled={busyCommand !== null || prepareJobId !== null}
              onChange={(event) => setDeferConnections(event.target.checked)} />
            Defer connections that need new QIDs
          </label>
          {deferConnections && <p className="text-xs text-warn">
            This Release retains deferred claims for review. A later Release must add the deferred connections after their targets receive QIDs.
          </p>}
          <button
            type="button"
            className="button-primary text-sm"
            disabled={restoring || busyCommand !== null || prepareJobId !== null || build.total === 0}
            onClick={() => { void prepare(); }}
            data-testid="publication-prepare"
          >
            {busyCommand === "prepare" || prepareJobId ? "Preparing Release…" : "Prepare Release"}
          </button>
          <p className="text-xs muted">
            The current Studio data stays available until the Publication service becomes active.
          </p>
        </div>
      )}

      {routeUnavailable && (
        <p className="rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-warn" role="status" data-testid="publication-compatibility-notice">
          The Publication service is not available on this backend. The current Studio controls remain available.
        </p>
      )}

      {publication && release && (
        <>
          {publication.profile_version === "1-nodes" && <p className="text-sm text-warn">
            This Release defers connections that need new QIDs. Review the deferred claims below.
            A later Release must add these connections after their targets receive QIDs.
          </p>}
          {!publication.source_current && (
            <p className="rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-warn" data-testid="publication-stale-release-notice">
              The backend reports a stale source. Prepare a new Release before review or publication.
            </p>
          )}
          {entityPage.error && <p className="text-sm text-danger" role="alert">{entityPage.error}</p>}
          <WikidataPublicationControls
            publication={publication}
            entities={entityPage.source === "publication" ? entityPage.items : []}
            busyCommand={prepareJobId ? "dry_run" : busyCommand === "prepare" ? null : busyCommand}
            error={error}
            onAdvance={advance}
            auditHref={`/runs/${encodeURIComponent(runId)}/wikidata-studio/publications/${encodeURIComponent(publication.publication_id)}/audit`}
          />

          <div className="rounded-lg border border-white/10 p-3 space-y-2" data-testid="publication-entity-page">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="kicker">Publication entities</div>
                <p className="text-xs muted">
                  {entityPage.total.toLocaleString()} entities · 50 or fewer per cursor page
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="button-ghost text-xs"
                  disabled={!entityPage.hasPrevious || entityPage.loading}
                  onClick={entityPage.previous}
                  data-testid="publication-entities-previous"
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="button-ghost text-xs"
                  disabled={!entityPage.hasNext || entityPage.loading}
                  onClick={entityPage.next}
                  data-testid="publication-entities-next"
                >
                  Next
                </button>
              </div>
            </div>
            <ul className="grid gap-2 md:grid-cols-2">
              {entityPage.items.map((entity) => (
                <li key={entity.entity_id} className="flex items-start justify-between gap-2 rounded border border-white/5 p-2 text-xs">
                  <div className="min-w-0">
                    <span className="block text-ink">{entity.label}</span>
                    <span className="muted">{entity.entity_kind} · {entity.statement_count} statements</span>
                    {!!entity.deferred_statements?.length && <details className="mt-2 text-warn">
                      <summary className="cursor-pointer">
                        {entity.deferred_statements.length} deferred {entity.deferred_statements.length === 1 ? "connection" : "connections"}
                      </summary>
                      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all text-ink">
                        {JSON.stringify(entity.deferred_statements, null, 2)}
                      </pre>
                    </details>}
                  </div>
                  <span className={entity.review_status === "approved" ? "text-success" : "text-warn"}>{entity.review_status}</span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}

      {jobSnapshot && <div>
        <p className="text-xs muted" data-testid="publication-job-counts">{jobSnapshot.progress.processed ?? 0} / {jobSnapshot.progress.total ?? 0} entities</p>
        <JobProgressInline job={jobSnapshot} labels={{running: "Publication job:",
        succeeded: "Complete:", failed: "Failed:", cancelled: "Cancelled:"}} /></div>}
      {prepareJobId && <button type="button" className="button-ghost text-sm"
        onClick={() => { void RunJobs.cancel(runId, prepareJobId).catch((caught: unknown) => {
          setError(caught instanceof ApiError ? caught.detail : String(caught));
        }); }}>Cancel Publication job</button>}
      {!publication && error && <p className="text-sm text-danger" role="alert">{error}</p>}
    </section>
  );
}
