/**
 * AuthorityMatchEditDialog — hand-edit authority match fields.
 * Wired to PATCH /runs/{runId}/matches/{matchId}/edit.
 */

import { useCallback, useState } from "react";

import {ApiError} from "@/api/client";
import {Runs, type AuthorityMatch} from "@/api/runs";
import {Glass} from "@/components/glass";

export interface AuthorityMatchEditDialogProps {
  runId: string;
  match: AuthorityMatch;
  onClose: () => void;
  onSaved: (next: AuthorityMatch) => void;
}

type EditState = {
  matched_name: string;
  entity_text: string;
  role: string;
  mazal_id: string;
  viaf_id: string;
  wikidata_qid: string;
  confidence: "high" | "medium" | "low";
};

export function AuthorityMatchEditDialog({
  runId, match, onClose, onSaved,
}: AuthorityMatchEditDialogProps) {
  const [state, setState] = useState<EditState>({
    matched_name:  match.matched_name ?? "",
    entity_text:   match.entity_text ?? "",
    role:          match.role ?? "",
    mazal_id:      match.mazal_id ?? "",
    viaf_id:       match.viaf_id ?? "",
    wikidata_qid:  match.wikidata_qid ?? "",
    confidence:    (match.confidence as EditState["confidence"]) || "medium",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = useCallback(async () => {
    setSaving(true); setError(null);
    try {
      const next = await Runs.editMatch(runId, match.id, {
        matched_name: state.matched_name || undefined,
        entity_text:  state.entity_text || undefined,
        role:         state.role || undefined,
        mazal_id:     state.mazal_id || undefined,
        viaf_id:      state.viaf_id || undefined,
        wikidata_qid: state.wikidata_qid || undefined,
        confidence:   state.confidence,
      });
      onSaved(next);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSaving(false);
    }
  }, [runId, match.id, state, onSaved, onClose]);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
         data-testid="authority-match-edit-modal"
         role="dialog" aria-modal="true">
      <Glass className="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Edit authority match</div>
            <div className="text-sm muted font-mono">{match.control_number}</div>
          </div>
          <button type="button" onClick={onClose} className="button-ghost h-8 px-3 text-xs">
            Close
          </button>
        </div>
        <div className="space-y-3 overflow-auto p-4">
          <Field label="Entity (MARC heading)" value={state.entity_text}
                 onChange={(v) => setState((p) => ({...p, entity_text: v}))} />
          <Field label="Matched authority" value={state.matched_name}
                 onChange={(v) => setState((p) => ({...p, matched_name: v}))} />
          <Field label="Role" value={state.role}
                 onChange={(v) => setState((p) => ({...p, role: v}))} />
          <Field label="Mazal / NLI ID" value={state.mazal_id}
                 onChange={(v) => setState((p) => ({...p, mazal_id: v}))} mono />
          <Field label="VIAF ID" value={state.viaf_id}
                 onChange={(v) => setState((p) => ({...p, viaf_id: v}))} mono />
          <Field label="Wikidata QID" value={state.wikidata_qid}
                 onChange={(v) => setState((p) => ({...p, wikidata_qid: v}))} mono />
          <label className="block text-xs uppercase tracking-wide kicker">Confidence</label>
          <select value={state.confidence}
                  onChange={(e) => setState((p) => ({
                    ...p, confidence: e.target.value as EditState["confidence"],
                  }))}
                  className="input-glass h-8 w-full text-sm">
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
          </select>
          {error ? (
            <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              {error}
            </div>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-white/10 px-4 py-3">
          <button type="button" onClick={onClose} className="button-ghost h-8 px-3 text-xs" disabled={saving}>
            Cancel
          </button>
          <button type="button" onClick={handleSave} data-testid="authority-match-edit-save"
                  className="button-primary h-8 px-3 text-xs" disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </Glass>
    </div>
  );
}

function Field({
  label, value, onChange, mono,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  mono?: boolean;
}) {
  return (
    <div>
      <label className="block text-xs uppercase tracking-wide kicker">{label}</label>
      <input value={value} onChange={(e) => onChange(e.target.value)}
             className={`input-glass mt-1 h-8 w-full text-sm ${mono ? "font-mono" : ""}`} />
    </div>
  );
}
