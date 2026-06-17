/**
 * MarcFieldEditorDialog — hand-edit MARC JSON for one run record.
 * PATCH /runs/{runId}/records/{controlNumber}
 */

import {useCallback, useEffect, useState} from "react";

import {ApiError} from "@/api/client";
import {Runs} from "@/api/runs";
import {Glass} from "@/components/glass";

const MARC_FIELDS = [
  "title", "authors", "contributors", "dates", "notes", "provenance",
  "contents", "genres", "language", "place", "physical_description",
  "shelfmark", "collection",
] as const;

export interface MarcFieldEditorDialogProps {
  runId: string;
  controlNumber: string;
  onClose: () => void;
  onSaved?: () => void;
}

export function MarcFieldEditorDialog({
  runId, controlNumber, onClose, onSaved,
}: MarcFieldEditorDialogProps) {
  const [marc, setMarc] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    Runs.getRecord(runId, controlNumber)
      .then((rec) => { if (!cancelled) setMarc(rec.marc ?? {}); })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.detail : String(err));
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, controlNumber]);

  const setField = useCallback((key: string, raw: string) => {
    setMarc((prev) => {
      const next = {...prev};
      if (!raw.trim()) {
        delete next[key];
        return next;
      }
      try {
        next[key] = JSON.parse(raw);
      } catch {
        next[key] = raw;
      }
      return next;
    });
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true); setError(null); setBanner(null);
    try {
      await Runs.editRecord(runId, controlNumber, marc);
      setBanner("MARC updated — rebuild RDF then rebuild manifests to apply.");
      onSaved?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSaving(false);
    }
  }, [runId, controlNumber, marc, onSaved]);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
         data-testid="marc-field-editor-modal" role="dialog" aria-modal="true">
      <Glass className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Edit MARC record</div>
            <div className="font-mono text-sm">{controlNumber}</div>
          </div>
          <button type="button" onClick={onClose} className="button-ghost h-8 px-3 text-xs">
            Close
          </button>
        </div>
        <div className="flex-1 space-y-3 overflow-auto p-4">
          {loading ? (
            <p className="muted text-sm">Loading MARC…</p>
          ) : (
            <>
              {MARC_FIELDS.map((key) => (
                <label key={key} className="block">
                  <span className="kicker text-[10px]">{key}</span>
                  <textarea
                    value={fieldToText(marc[key])}
                    onChange={(e) => setField(key, e.target.value)}
                    rows={key === "notes" || key === "provenance" ? 3 : 1}
                    className="input-glass mt-1 w-full font-mono text-xs"
                  />
                </label>
              ))}
              <details>
                <summary className="kicker cursor-pointer text-xs">Raw JSON</summary>
                <textarea
                  value={JSON.stringify(marc, null, 2)}
                  onChange={(e) => {
                    try {
                      setMarc(JSON.parse(e.target.value) as Record<string, unknown>);
                      setError(null);
                    } catch {
                      setError("Invalid JSON in raw editor.");
                    }
                  }}
                  rows={8}
                  className="input-glass mt-2 w-full font-mono text-xs"
                />
              </details>
            </>
          )}
          {banner ? (
            <p className="text-xs text-biu-sky">{banner}</p>
          ) : null}
          {error ? (
            <p className="text-xs text-red-300">{error}</p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-white/10 px-4 py-3">
          <button type="button" onClick={onClose} className="button-ghost h-8 px-3 text-xs" disabled={saving}>
            Cancel
          </button>
          <button type="button" onClick={handleSave} data-testid="marc-field-editor-save"
                  className="button-primary h-8 px-3 text-xs" disabled={saving || loading}>
            {saving ? "Saving…" : "Save MARC"}
          </button>
        </div>
      </Glass>
    </div>
  );
}

function fieldToText(val: unknown): string {
  if (val === undefined || val === null) return "";
  if (typeof val === "string") return val;
  return JSON.stringify(val);
}
