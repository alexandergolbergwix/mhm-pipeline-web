import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {
  RuleVerify,
  type RuleMeta,
  type RuleVerifyEntityRow,
  type RuleVerifyResults,
  type RuleVerifySettings,
} from "@/api/ruleVerify";
import type {RunJobSnapshot} from "@/api/runJobs";
import {Glass} from "@/components/glass";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {ensureRunJob} from "@/utils/waitForRunJob";

export interface RuleVerificationPanelProps {
  runId: string;
  onClose: () => void;
  onApproved?: () => void;
}

type ViewMode = "summary" | "entities";

const STATE_SEVERITY: Record<string, number> = {
  fail: 3, error: 2, pass: 1, not_relevant: 0, unchecked: 0,
};

const STATE_BADGE: Record<string, string> = {
  pass: "text-emerald-700 bg-emerald-500/10 border-emerald-500/30",
  fail: "text-danger bg-red-500/10 border-red-500/30",
  error: "text-amber-700 bg-amber-500/10 border-amber-500/30",
  not_relevant: "muted bg-white/5 border-white/10",
  unchecked: "muted bg-white/5 border-white/10",
};

function StateBadge({state}: {state: string}) {
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-medium ${STATE_BADGE[state] ?? STATE_BADGE.unchecked}`}
    >
      {state}
    </span>
  );
}

export function RuleVerificationPanel({runId, onClose, onApproved}: RuleVerificationPanelProps) {
  const [catalog, setCatalog] = useState<RuleMeta[]>([]);
  const [results, setResults] = useState<RuleVerifyResults | null>(null);
  const [settings, setSettings] = useState<RuleVerifySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("summary");
  const [ruleFilter, setRuleFilter] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [togglingRule, setTogglingRule] = useState<string | null>(null);
  const [approveBusy, setApproveBusy] = useState(false);
  const [approveFeedback, setApproveFeedback] = useState<string | null>(null);
  const [verifyJob, setVerifyJob] = useState<RunJobSnapshot | null>(null);
  const upsertJob = useRunJobs((s) => s.upsertJob);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [cat, res, set] = await Promise.all([
        RuleVerify.catalog(runId),
        RuleVerify.results(runId),
        RuleVerify.settings(),
      ]);
      setCatalog(cat);
      setResults(res);
      setSettings(set);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [runId]);

  useEffect(() => { void load(); }, [load]);

  const {setTrackedJobId, ensureJobPolling} = useRunJobAttachment(
    runId,
    "hmo_rule_verify",
    (job) => {
      setVerifyJob(job);
      if (job.status === "succeeded") void load(true);
      if (job.status === "failed" || job.status === "cancelled") {
        setError(job.error ?? "Rule check failed.");
      }
    },
  );

  const running = verifyJob != null && isJobActive(verifyJob.status);

  const runVerify = useCallback(async () => {
    setError(null);
    try {
      const started = await ensureRunJob(runId, "hmo_rule_verify", {});
      upsertJob(started);
      setVerifyJob(started);
      setTrackedJobId(started.id);
      ensureJobPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [ensureJobPolling, runId, setTrackedJobId, upsertJob]);

  const blockedRules = useMemo(
    () => new Set(
      Object.entries(settings?.blocked_rules ?? {})
        .filter(([, v]) => v)
        .map(([k]) => k),
    ),
    [settings],
  );

  const toggleBlocking = useCallback(async (ruleId: string) => {
    setTogglingRule(ruleId);
    const current = settings?.blocked_rules ?? {};
    const next = {...current};
    if (next[ruleId]) delete next[ruleId]; else next[ruleId] = true;
    try {
      const saved = await RuleVerify.saveSettings({
        blocked_rules: next,
        filter_presets: settings?.filter_presets ?? null,
      });
      setSettings(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTogglingRule(null);
    }
  }, [settings]);

  const catalogById = useMemo(() => {
    const map = new Map<string, RuleMeta>();
    catalog.forEach((m) => map.set(m.id, m));
    return map;
  }, [catalog]);

  const entityRows = useMemo(() => {
    if (!results) return [];
    let rows = results.items;
    if (ruleFilter.length) {
      rows = rows.filter((r) =>
        r.results.some(
          (res) => ruleFilter.includes(res.rule_id) && res.state === "fail",
        ),
      );
      rows = rows.map((r) => ({
        ...r,
        results: r.results.filter(
          (res) => ruleFilter.includes(res.rule_id) || res.state === "fail",
        ),
      }));
    }
    const q = search.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (r) => (r.label ?? "").toLowerCase().includes(q)
          || r.local_id.toLowerCase().includes(q),
      );
    }
    return [...rows].sort(
      (a, b) => (STATE_SEVERITY[b.overall] ?? 0) - (STATE_SEVERITY[a.overall] ?? 0)
        || a.local_id.localeCompare(b.local_id),
    );
  }, [results, ruleFilter, search]);

  const approvableIds = useMemo(
    () => entityRows
      .filter((r) => r.overall === "pass")
      .map((r) => r.local_id),
    [entityRows],
  );

  const approveFiltered = useCallback(async () => {
    if (!approvableIds.length) return;
    setApproveBusy(true);
    setApproveFeedback(null);
    try {
      const blocked = [...blockedRules];
      const preview = await RuleVerify.bulkApprovePreview(
        runId, approvableIds, blocked.length ? blocked : undefined,
      );
      const excludedCount = preview.excluded.length;
      const ok = window.confirm(
        `Approve ${preview.eligible.length} of ${preview.total} items?`
        + (excludedCount ? ` ${excludedCount} excluded by your blocking rules.` : ""),
      );
      if (!ok) {
        setApproveBusy(false);
        return;
      }
      await RuleVerify.bulkApprove(runId, preview.eligible, blocked.length ? blocked : undefined);
      setApproveFeedback(
        `Approve job started for ${preview.eligible.length} items`
        + (excludedCount ? ` (${excludedCount} excluded by blocking rules)` : "") + ".",
      );
      onApproved?.();
    } catch (e) {
      setApproveFeedback(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setApproveBusy(false);
    }
  }, [approvableIds, blockedRules, onApproved, runId]);

  const perRuleRows = useMemo(() => {
    if (!results) return [];
    return catalog
      .map((meta) => ({
        meta,
        tally: results.per_rule[meta.id] ?? {pass: 0, fail: 0, not_relevant: 0, error: 0},
      }))
      .sort((a, b) => b.tally.fail - a.tally.fail || a.meta.id.localeCompare(b.meta.id));
  }, [catalog, results]);

  const failingRuleIds = useMemo(() => {
    if (!results) return [];
    return Object.entries(results.per_rule)
      .filter(([, t]) => t.fail > 0)
      .sort((a, b) => b[1].fail - a[1].fail)
      .map(([rid]) => rid);
  }, [results]);

  return (
    <Glass as="section" className="p-6 space-y-4" data-testid="rule-verification-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">Rule-Based Verification</div>
          <h3 className="text-lg font-medium" data-testid="rule-verify-heading">
            Deterministic checks — no AI
          </h3>
          <p className="muted text-sm mt-1">
            Every rule runs on every item. Advisory by default: pick the rules that
            block approval in the summary view, then approve from the filter.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="button-ghost text-xs" disabled={loading} onClick={() => void load(true)}>
            Refresh
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={loading || running}
            data-testid="rule-verify-run"
            onClick={() => void runVerify()}
          >
            {running ? "Checking…" : "Check all items"}
          </button>
          <button type="button" className="button-ghost text-xs" onClick={onClose}>Close</button>
        </div>
      </div>

      {error && <p className="text-danger text-sm">{error}</p>}
      {loading && <p className="muted text-sm" role="status">Loading rule results…</p>}
      {verifyJob && isJobActive(verifyJob.status) && (
        <JobProgressInline
          job={verifyJob}
          labels={{
            running: "Checking items…",
            succeeded: "Rule check complete:",
            failed: "Rule check failed:",
            cancelled: "Rule check cancelled:",
          }}
        />
      )}
      {approveFeedback && <p className="text-sm text-biu-sky" role="status">{approveFeedback}</p>}


      {results && !loading && (
        <>
          <div className="flex flex-wrap items-center gap-2" data-testid="rule-verify-filters">
            <input
              type="search"
              className="input text-xs w-48"
              placeholder="Search label or id…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="flex gap-1 ml-auto">
              <button
                type="button"
                className={`button-ghost text-xs ${view === "summary" ? "font-semibold underline" : ""}`}
                onClick={() => setView("summary")}
              >
                Summary
              </button>
              <button
                type="button"
                className={`button-ghost text-xs ${view === "entities" ? "font-semibold underline" : ""}`}
                onClick={() => setView("entities")}
              >
                Per entity ({entityRows.length})
              </button>
            </div>
          </div>

          {view === "summary" ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="rule-verify-summary-table">
                <thead>
                  <tr className="muted text-left text-xs uppercase tracking-wide">
                    <th className="py-2 pr-3">Rule</th>
                    <th className="py-2 pr-3">pass</th>
                    <th className="py-2 pr-3">fail</th>
                    <th className="py-2 pr-3">n/a</th>
                    <th className="py-2 pr-3">error</th>
                    <th className="py-2 pr-3">Blocks approval</th>
                  </tr>
                </thead>
                <tbody>
                  {perRuleRows.map(({meta, tally}) => (
                    <tr key={meta.id} className="border-t border-white/5">
                      <td className="py-2 pr-3">
                        <div className="font-medium">{meta.title}</div>
                        <div className="muted text-xs">{meta.description}</div>
                      </td>
                      <td className="py-2 pr-3">{tally.pass}</td>
                      <td className="py-2 pr-3">
                        {tally.fail > 0 ? (
                          <button
                            type="button"
                            className="underline text-danger"
                            onClick={() => { setRuleFilter([meta.id]); setView("entities"); }}
                          >
                            {tally.fail}
                          </button>
                        ) : tally.fail}
                      </td>
                      <td className="py-2 pr-3">{tally.not_relevant}</td>
                      <td className="py-2 pr-3">{tally.error}</td>
                      <td className="py-2 pr-3">
                        <label className="inline-flex items-center gap-2 text-xs">
                          <input
                            type="checkbox"
                            checked={Boolean(settings?.blocked_rules?.[meta.id])}
                            disabled={togglingRule === meta.id}
                            onChange={() => void toggleBlocking(meta.id)}
                            data-testid={`rule-verify-block-${meta.id}`}
                          />
                          {Boolean(settings?.blocked_rules?.[meta.id]) ? "blocking" : "advisory"}
                        </label>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="space-y-2" data-testid="rule-verify-entities">
              {failingRuleIds.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="muted">Filter by failing rule:</span>
                  {failingRuleIds.map((rid) => (
                    <button
                      key={rid}
                      type="button"
                      className={`button-ghost text-xs ${ruleFilter.includes(rid) ? "underline font-semibold" : ""}`}
                      onClick={() => setRuleFilter(
                        ruleFilter.includes(rid)
                          ? ruleFilter.filter((x) => x !== rid)
                          : [...ruleFilter, rid],
                      )}
                    >
                      {catalogById.get(rid)?.title ?? rid}
                    </button>
                  ))}
                  {ruleFilter.length > 0 && (
                    <button type="button" className="button-ghost text-xs" onClick={() => setRuleFilter([])}>
                      Clear
                    </button>
                  )}
                </div>
              )}
              <table className="w-full text-sm">
                <thead>
                  <tr className="muted text-left text-xs uppercase tracking-wide">
                    <th className="py-2 pr-3">Item</th>
                    <th className="py-2 pr-3">Overall</th>
                    <th className="py-2 pr-3">Failing rules</th>
                  </tr>
                </thead>
                <tbody>
                  {entityRows.map((row) => (
                    <RuleEntityRow
                      key={row.local_id}
                      row={row}
                      expanded={expanded === row.local_id}
                      onToggle={() => setExpanded(expanded === row.local_id ? null : row.local_id)}
                      catalogById={catalogById}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {view === "summary" && (
            <button
              type="button"
              className="button-primary text-xs"
              disabled={approveBusy || approvableIds.length === 0}
              data-testid="rule-verify-approve-filtered"
              onClick={() => void approveFiltered()}
              title="Approve the items matching the current filter. Items failing your blocking rules are excluded after a preview."
            >
              {approveBusy ? "Starting…" : "Approve by filter…"}
            </button>
          )}
        </>
      )}
    </Glass>
  );
}

interface RuleEntityRowProps {
  row: RuleVerifyEntityRow;
  expanded: boolean;
  onToggle: () => void;
  catalogById: Map<string, RuleMeta>;
}

function RuleEntityRow({row, expanded, onToggle, catalogById}: RuleEntityRowProps) {
  const failing = row.results.filter((r) => r.state === "fail");
  return (
    <>
      <tr className="border-t border-white/5 cursor-pointer" onClick={onToggle}>
        <td className="py-2 pr-3">
          <div className="font-medium">{row.label ?? row.local_id}</div>
          <div className="muted text-xs">{row.local_id}{row.wikibase_id ? ` · ${row.wikibase_id}` : ""}</div>
        </td>
        <td className="py-2 pr-3"><StateBadge state={row.overall} /></td>
        <td className="py-2 pr-3">
          {failing.length === 0
            ? <span className="muted text-xs">none</span>
            : (
              <div className="flex flex-wrap gap-1">
                {failing.slice(0, 3).map((r) => (
                  <span
                    key={r.rule_id}
                    className="rounded bg-red-500/10 border border-red-500/30 px-1.5 py-0.5 text-[10px]"
                  >
                    {catalogById.get(r.rule_id)?.title ?? r.rule_id}
                  </span>
                ))}
                {failing.length > 3 && <span className="muted text-[10px]">+{failing.length - 3}</span>}
              </div>
            )}
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-white/5 bg-white/[0.02]">
          <td colSpan={3} className="py-3 px-3">
            <div className="space-y-1 text-xs" data-testid={`rule-verify-detail-${row.local_id}`}>
              {row.results.length === 0 && (row.pass_count ?? 0) === 0 && (
                <span className="muted">No rule results recorded.</span>
              )}
              {row.results.map((res) => (
                <div key={res.rule_id} className="flex items-start gap-2">
                  <StateBadge state={res.state} />
                  <span className="font-medium">{catalogById.get(res.rule_id)?.title ?? res.rule_id}</span>
                  {res.field && <span className="muted">[{res.field}]</span>}
                  {res.message && <span className="muted">{res.message}</span>}
                </div>
              ))}
              {(row.pass_count ?? 0) > 0 && (
                <div className="muted pt-1">+ {row.pass_count} more rule(s) passed</div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
