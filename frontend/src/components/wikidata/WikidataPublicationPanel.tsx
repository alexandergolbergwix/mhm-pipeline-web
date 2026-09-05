import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {RunJobs} from "@/api/runJobs";
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

const PROFILE_ID = "mhm-wikidata";
const PROFILE_VERSION = "1";

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
  const [target, setTarget] = useState<PublicationTarget>("test");
  const [publication, setPublication] = useState<PublicationSummary | null>(null);
  const [operation, setOperation] = useState<PublicationOperation | null>(null);
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
    onPublicationActiveChange?.(publication !== null);
  }, [onPublicationActiveChange, publication]);

  useEffect(() => {
    if (source !== "canonical" && target === "live") setTarget("test");
  }, [source, target]);

  const prepare = useCallback(async () => {
    setBusyCommand("prepare");
    setError(null);
    setRouteUnavailable(false);
    try {
      const response = await PublicationApi.prepare(runId, {
        profile_id: PROFILE_ID,
        profile_version: PROFILE_VERSION,
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
      if (caught instanceof ApiError && (caught.status === 404 || caught.status === 405)) {
        setRouteUnavailable(true);
      } else {
        setError(caught instanceof ApiError ? caught.detail : String(caught));
      }
    } finally {
      setBusyCommand(null);
    }
  }, [approvedOnly, runId, source, target]);

  useEffect(() => {
    if (!prepareJobId) return;
    const timer = window.setTimeout(() => {
      void RunJobs.get(runId, prepareJobId).then(async (job) => {
        if (job.status === "failed" || job.status === "cancelled") {
          setError(job.error ?? "Publication preparation did not complete.");
          setPrepareJobId(null);
          return;
        }
        const publicationId = typeof job.result?.publication_id === "string"
          ? job.result.publication_id
          : null;
        if (job.status === "succeeded" && publicationId) {
          const response = await PublicationApi.read(runId, publicationId, {type: "summary"});
          setPublication(response.publication);
          setOperation(null);
          setPrepareJobId(null);
        }
      }).catch((caught: unknown) => {
        setError(caught instanceof ApiError ? caught.detail : String(caught));
        setPrepareJobId(null);
      });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [prepareJobId, runId]);

  const advance = useCallback(async (command: PublicationAdvanceCommand) => {
    if (!publication) return;
    setBusyCommand(command.type);
    setError(null);
    try {
      const response = await PublicationApi.advance(runId, publication.publication_id, command);
      setPublication(response.publication);
      setOperation(response.operation ?? null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setBusyCommand(null);
    }
  }, [publication, runId]);

  useEffect(() => {
    if (!publication || !operationActive(operation)) return;
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
  }, [operation, publication, runId]);

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
            disabled={busyCommand !== null || operationActive(operation)}
            onClick={() => { void prepare(); }}
            data-testid="publication-prepare-new-release"
          >
            {busyCommand === "prepare" ? "Preparing…" : "Prepare new Release"}
          </button>
        )}
      </div>

      {!publication && (
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
          <button
            type="button"
            className="button-primary text-sm"
            disabled={busyCommand !== null || prepareJobId !== null || build.total === 0}
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
          {!publication.source_current && (
            <p className="rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-warn" data-testid="publication-stale-release-notice">
              The backend reports a stale source. Prepare a new Release before review or publication.
            </p>
          )}
          {entityPage.error && <p className="text-sm text-danger" role="alert">{entityPage.error}</p>}
          <WikidataPublicationControls
            publication={publication}
            entities={entityPage.source === "publication" ? entityPage.items : []}
            busyCommand={busyCommand === "prepare" ? null : busyCommand}
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
                  <span>
                    <span className="block text-ink">{entity.label}</span>
                    <span className="muted">{entity.entity_kind} · {entity.statement_count} statements</span>
                  </span>
                  <span className={entity.review_status === "approved" ? "text-success" : "text-warn"}>{entity.review_status}</span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}

      {!publication && error && <p className="text-sm text-danger" role="alert">{error}</p>}
    </section>
  );
}
