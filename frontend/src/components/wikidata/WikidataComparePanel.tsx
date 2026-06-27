/**
 * WikidataComparePanel — GitHub-style merge view for an existing Wikidata item
 * vs the Studio-built candidate.
 */

import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {
  Studio,
  type CompareFieldRow,
  type StudioItem,
  type WikidataCompareResult,
} from "@/api/wikidataStudio";
import {Glass, GlassPill} from "@/components/glass";
import {useGlassOverlayLifecycle} from "@/hooks/useGlassOverlayLifecycle";

export interface WikidataComparePanelProps {
  runId: string;
  item: StudioItem;
  qid: string;
  approvedOnly: boolean;
  onClose: () => void;
  onApplied: () => void;
}

type RowChoice = Record<string, "wikidata" | "studio">;

function rowId(row: CompareFieldRow): string {
  return `${row.kind}:${row.key}`;
}

function statusClass(status: CompareFieldRow["status"]): string {
  if (status === "conflict") return "text-warn";
  if (status === "same") return "text-subtle";
  if (status === "wikidata_only") return "text-biu-sky";
  return "text-ink";
}

export function WikidataComparePanel({
  runId,
  item,
  qid,
  approvedOnly,
  onClose,
  onApplied,
}: WikidataComparePanelProps) {
  const [compare, setCompare] = useState<WikidataCompareResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [choices, setChoices] = useState<RowChoice>({});
  const [saving, setSaving] = useState(false);

  useGlassOverlayLifecycle(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void Studio.compareWithWikidata(runId, item.local_id ?? "", {
      qid,
      approvedOnly,
    })
      .then((data) => {
        if (cancelled) return;
        setCompare(data);
        const initial: RowChoice = {};
        for (const row of data.rows) {
          if (row.status === "conflict") {
            initial[rowId(row)] = "studio";
          }
        }
        setChoices(initial);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof ApiError ? e.detail : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [runId, item.local_id, qid, approvedOnly]);

  const conflictRows = useMemo(
    () => (compare?.rows ?? []).filter((r) => r.status === "conflict"),
    [compare],
  );

  const apply = useCallback(async (policy: "wikidata" | "studio" | "custom") => {
    if (!item.local_id) return;
    setSaving(true);
    setError(null);
    try {
      const choiceList: Array<{kind: CompareFieldRow["kind"]; key: string; source: "wikidata" | "studio"}> = [];
      for (const [id, source] of Object.entries(choices)) {
        const row = compare?.rows.find((r) => rowId(r) === id);
        if (row) choiceList.push({kind: row.kind, key: row.key, source});
      }

      await Studio.applyWikidataCompare(runId, item.local_id, {
        policy,
        choices: policy === "custom" ? choiceList : [],
        qid,
        approvedOnly,
      });
      onApplied();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }, [runId, item.local_id, choices, compare, qid, approvedOnly, onApplied, onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="wikidata-compare-title"
      onClick={onClose}
    >
      <Glass
        variant="modal"
        className="w-full max-w-5xl max-h-[90vh] overflow-hidden flex flex-col shadow-2xl"
        onClick={(e) => { e.stopPropagation(); }}
      >
        <header className="p-4 border-b border-[var(--line)] space-y-2 shrink-0">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 id="wikidata-compare-title" className="text-lg font-semibold">
                Compare with Wikidata
              </h2>
              <p className="text-sm muted">
                {compare?.studio_label ?? item.labels?.en ?? item.local_id}
                {" · "}
                <a
                  href={`https://www.wikidata.org/wiki/${qid}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-biu-sky font-mono hover:underline"
                >
                  {qid}
                </a>
              </p>
            </div>
            <button type="button" className="button-ghost text-sm" onClick={onClose}>
              Close
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="button-ghost text-xs"
              disabled={saving || loading}
              onClick={() => { void apply("wikidata"); }}
            >
              Accept all from Wikidata
            </button>
            <button
              type="button"
              className="button-ghost text-xs"
              disabled={saving || loading}
              onClick={() => { void apply("studio"); }}
            >
              Accept all from Studio
            </button>
            <button
              type="button"
              className="button-primary text-xs"
              disabled={saving || loading || conflictRows.length === 0}
              onClick={() => { void apply("custom"); }}
            >
              {saving ? "Applying…" : "Apply statement-by-statement choices"}
            </button>
          </div>
        </header>

        <div className="overflow-auto flex-1 p-4 space-y-3">
          {loading && <p className="muted text-sm">Loading live Wikidata data…</p>}
          {error && <p className="text-warn text-sm">{error}</p>}
          {compare && !loading && (
            <>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <GlassPill className="px-3 py-2">
                  <span className="muted">On Wikidata</span>
                  <div className="font-medium">{compare.wikidata.statement_count} statements</div>
                </GlassPill>
                <GlassPill className="px-3 py-2">
                  <span className="muted">Conflicts</span>
                  <div className="font-medium">{compare.conflict_count}</div>
                </GlassPill>
              </div>

              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="text-left text-xs muted border-b border-[var(--line)]">
                    <th className="py-2 pr-2 w-28">Field</th>
                    <th className="py-2 pr-2">Wikidata (current)</th>
                    <th className="py-2 pr-2">Studio (our change)</th>
                    <th className="py-2 w-36">Resolve</th>
                  </tr>
                </thead>
                <tbody>
                  {compare.rows.filter((r) => r.status !== "same").map((row) => {
                    const id = rowId(row);
                    const isConflict = row.status === "conflict";
                    return (
                      <tr
                        key={id}
                        className={`border-b border-[var(--line)] align-top ${isConflict ? "bg-warn/5" : ""}`}
                      >
                        <td className="py-2 pr-2">
                          <div className="font-medium">{row.label}</div>
                          <div className={`text-[10px] uppercase ${statusClass(row.status)}`}>
                            {row.status.replace("_", " ")}
                          </div>
                        </td>
                        <td className="py-2 pr-2 font-mono text-xs break-all">
                          {row.wikidata_value ?? "—"}
                        </td>
                        <td className="py-2 pr-2 font-mono text-xs break-all">
                          {row.studio_value ?? "—"}
                        </td>
                        <td className="py-2">
                          {isConflict ? (
                            <div className="flex flex-col gap-1 text-xs">
                              <label className="flex items-center gap-1 cursor-pointer">
                                <input
                                  type="radio"
                                  name={id}
                                  checked={choices[id] === "wikidata"}
                                  onChange={() => { setChoices((c) => ({...c, [id]: "wikidata"})); }}
                                />
                                Wikidata
                              </label>
                              <label className="flex items-center gap-1 cursor-pointer">
                                <input
                                  type="radio"
                                  name={id}
                                  checked={choices[id] === "studio"}
                                  onChange={() => { setChoices((c) => ({...c, [id]: "studio"})); }}
                                />
                                Studio
                              </label>
                            </div>
                          ) : (
                            <span className="text-xs muted">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {compare.rows.every((r) => r.status === "same") && (
                <p className="muted text-sm">Studio item matches live Wikidata for all compared fields.</p>
              )}
            </>
          )}
        </div>
      </Glass>
    </div>
  );
}
