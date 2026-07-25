import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {
  HmoStudio,
  type HmoAuthorityConflictGroup,
  type HmoAuthorityConflictsReport,
  type HmoAuthorityInvalidRow,
} from "@/api/hmoStudio";
import {Glass} from "@/components/glass";

interface HmoAuthorityConflictPanelProps {
  runId: string;
  /** Bump after upload failure / rebuild so the panel reloads. */
  refreshToken?: unknown;
  onResolved?: () => void;
}

function defaultKeepId(group: HmoAuthorityConflictGroup): string {
  const ranked = [...group.owners].sort((a, b) => {
    const conf = (c: string) => (c === "high" ? 0 : c === "medium" ? 1 : 2);
    const byConf = conf(a.confidence) - conf(b.confidence);
    if (byConf !== 0) return byConf;
    return b.entity_text.length - a.entity_text.length;
  });
  return ranked[0]?.match_id ?? "";
}

function conflictKey(group: HmoAuthorityConflictGroup): string {
  return `${group.kind}:${group.identifier}`;
}

/**
 * Run-level AuthorityMatch identifier collisions that block HMO item upload
 * (Rules W-86 / W-95). Curator keeps one row per shared ID; the rest are
 * unapproved via the HMO-scoped resolve endpoint (not the retired Authority UI).
 */
export function HmoAuthorityConflictPanel({
  runId,
  refreshToken,
  onResolved,
}: HmoAuthorityConflictPanelProps) {
  const [report, setReport] = useState<HmoAuthorityConflictsReport | null>(null);
  const [keepByConflict, setKeepByConflict] = useState<Record<string, string>>({});
  const [unapproveInvalid, setUnapproveInvalid] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const next = await HmoStudio.authorityConflicts(runId);
      setReport(next);
      const keeps: Record<string, string> = {};
      for (const group of next.conflicts) {
        keeps[conflictKey(group)] = defaultKeepId(group);
      }
      setKeepByConflict(keeps);
      const invalidMarks: Record<string, boolean> = {};
      for (const row of next.invalid) {
        invalidMarks[row.match_id] = true;
      }
      setUnapproveInvalid(invalidMarks);
      if (next.ready) setStatus(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const blocked = Boolean(report && !report.ready);
  const conflictCount = report?.conflict_count ?? 0;
  const invalidCount = report?.invalid_count ?? 0;

  const canResolve = useMemo(() => {
    if (!report || report.ready) return false;
    if (report.conflicts.some((g) => !keepByConflict[conflictKey(g)])) return false;
    return true;
  }, [report, keepByConflict]);

  const handleResolve = useCallback(async () => {
    if (!report) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const keep_match_ids = report.conflicts
        .map((g) => keepByConflict[conflictKey(g)])
        .filter(Boolean);
      const unapprove_match_ids = report.invalid
        .filter((row) => unapproveInvalid[row.match_id])
        .map((row) => row.match_id);
      const next = await HmoStudio.resolveAuthorityConflicts(runId, {
        keep_match_ids,
        unapprove_match_ids,
      });
      setReport(next);
      setStatus(next.message || "Conflicts resolved.");
      if (next.ready) {
        const keeps: Record<string, string> = {};
        setKeepByConflict(keeps);
        setUnapproveInvalid({});
        onResolved?.();
      } else {
        const keeps: Record<string, string> = {};
        for (const group of next.conflicts) {
          keeps[conflictKey(group)] = defaultKeepId(group);
        }
        setKeepByConflict(keeps);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }, [report, keepByConflict, unapproveInvalid, runId, onResolved]);

  if (!report) {
    if (error) {
      return <p className="text-danger text-sm" role="alert">{error}</p>;
    }
    return null;
  }

  if (!blocked && !status) {
    return null;
  }

  return (
    <Glass
      as="section"
      className="space-y-3 border border-amber-500/40 p-4"
      data-testid="hmo-authority-conflicts"
    >
      {blocked ? (
        <>
          <div>
            <h3 className="text-sm font-semibold text-amber-200">
              Authority conflicts block upload
            </h3>
            <p className="mt-1 text-xs muted">
              {conflictCount} shared identifier{conflictCount === 1 ? "" : "s"}
              {invalidCount > 0 ? ` and ${invalidCount} invalid VIAF field(s)` : ""}{" "}
              among approved matches. Keep one name per ID (or unapprove invalid
              rows), then rebuild items if needed and retry publish.
            </p>
          </div>

          <div className="space-y-4">
            {report.conflicts.map((group) => (
              <ConflictGroupCard
                key={conflictKey(group)}
                group={group}
                keepId={keepByConflict[conflictKey(group)] ?? ""}
                onKeepChange={(matchId) =>
                  setKeepByConflict((prev) => ({
                    ...prev,
                    [conflictKey(group)]: matchId,
                  }))
                }
              />
            ))}
            {report.invalid.map((row) => (
              <InvalidRowCard
                key={row.match_id}
                row={row}
                checked={Boolean(unapproveInvalid[row.match_id])}
                onChange={(checked) =>
                  setUnapproveInvalid((prev) => ({
                    ...prev,
                    [row.match_id]: checked,
                  }))
                }
              />
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="button-primary text-xs"
              disabled={!canResolve || busy}
              data-testid="hmo-authority-conflicts-resolve"
              onClick={() => void handleResolve()}
            >
              {busy ? "Resolving…" : "Keep selected & unapprove the rest"}
            </button>
            <button
              type="button"
              className="button-ghost text-xs"
              disabled={busy}
              onClick={() => void load()}
            >
              Refresh
            </button>
          </div>
        </>
      ) : null}

      {status && (
        <p className="text-sm text-biu-sky" role="status" data-testid="hmo-authority-conflicts-status">
          {status}
        </p>
      )}
      {error && <p className="text-danger text-sm" role="alert">{error}</p>}
    </Glass>
  );
}

function ConflictGroupCard({
  group,
  keepId,
  onKeepChange,
}: {
  group: HmoAuthorityConflictGroup;
  keepId: string;
  onKeepChange: (matchId: string) => void;
}) {
  return (
    <div
      className="space-y-2 rounded border border-white/10 p-3"
      data-testid={`hmo-authority-conflict-${group.kind}-${group.identifier}`}
    >
      <div className="text-xs font-medium text-amber-100">
        {group.kind}={group.identifier}
      </div>
      <ul className="space-y-2">
        {group.owners.map((owner) => (
          <li key={owner.match_id} className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              name={`keep-${conflictKey(group)}`}
              className="mt-1"
              checked={keepId === owner.match_id}
              onChange={() => onKeepChange(owner.match_id)}
              data-testid={`hmo-authority-keep-${owner.match_id}`}
            />
            <div className="min-w-0">
              <div className="font-medium" dir="auto">{owner.entity_text}</div>
              <div className="text-xs muted">
                {owner.role || "—"} · CN {owner.control_number || "—"} ·{" "}
                {owner.confidence || "?"} · {owner.source || "?"}
                {owner.matched_name && owner.matched_name !== owner.entity_text
                  ? ` · matched as ${owner.matched_name}`
                  : ""}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function InvalidRowCard({
  row,
  checked,
  onChange,
}: {
  row: HmoAuthorityInvalidRow;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-2 rounded border border-white/10 p-3 text-sm">
      <input
        type="checkbox"
        className="mt-1"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div>
        <div className="font-medium" dir="auto">{row.entity_text}</div>
        <div className="text-xs muted">
          invalid {row.kind}={row.identifier} — {row.reason}
        </div>
      </div>
    </label>
  );
}
