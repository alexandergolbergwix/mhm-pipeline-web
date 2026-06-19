/**
 * EntityEditModal — edit an entity's text/type/role with the source
 * MARC record shown side-by-side. The current entity's span is
 * highlighted in the rendered MARC text so the curator can confirm
 * the boundary.
 *
 * Mirrors the PyQt6 ExtractionEditor's per-row edit dialog. Saves
 * via ExtractionApprovals.patch.
 */

import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {MarcSourceApi, type MarcSource} from "@/api/marcSource";
import {
  ENTITY_ROLES, ENTITY_TYPES,
  Entity, EntityRole, EntityType, ExtractionApprovals,
} from "@/api/extractionApprovals";
import {
  MARC_FIELD_LABELS, orderedMarcKeys, stringifyMarcValue,
} from "@/components/extraction/marc-field-labels";
import {Glass} from "@/components/glass";
import {emitEntitiesRefreshed} from "@/cache/extractionCache";
import {useFocusTrap} from "@/hooks/useFocusTrap";
import {useGlassOverlayLifecycle} from "@/hooks/useGlassOverlayLifecycle";
import {langOf} from "@/utils/hebrew";

const MARC_HIGHLIGHT_CAP = 64_000;

export interface EntityEditModalProps {
  runId: string;
  entity: Entity;
  /** When opened from the detail drawer, reuse its already-loaded MARC. */
  marcSource?: MarcSource | null;
  onClose: () => void;
  onSaved: (entity: Entity) => void;
}

interface EditState {text: string; type: EntityType; role: EntityRole;}

function marcToString(marc: Record<string, unknown> | null): string {
  if (!marc) return "";
  const lines: string[] = [];
  for (const key of orderedMarcKeys(marc)) {
    const payload = marc[key];
    if (Array.isArray(payload)) {
      for (const sub of payload) lines.push(`${key}\t${JSON.stringify(sub)}`);
    } else {
      lines.push(`${key}\t${JSON.stringify(payload)}`);
    }
  }
  return lines.join("\n");
}

function highlightSpan(
  source: string, needle: string, start: number | null, end: number | null,
): {before: string; hit: string; after: string} {
  if (!source) return {before: "", hit: "", after: ""};
  if (start !== null && end !== null && start >= 0 && end > start && end <= source.length) {
    return {before: source.slice(0, start), hit: source.slice(start, end), after: source.slice(end)};
  }
  if (needle) {
    const idx = source.indexOf(needle);
    if (idx >= 0) {
      return {
        before: source.slice(0, idx),
        hit: source.slice(idx, idx + needle.length),
        after: source.slice(idx + needle.length),
      };
    }
  }
  return {before: source, hit: "", after: ""};
}

function StructuredMarcPanel({
  marc, highlightText,
}: {
  marc: Record<string, unknown>;
  highlightText: string;
}) {
  const keys = orderedMarcKeys(marc);
  const needle = highlightText.trim().toLowerCase();
  return (
    <div className="space-y-2 text-[11px]">
      {keys.map((k) => {
        const value = stringifyMarcValue(marc[k]);
        if (!value) return null;
        const lbl = MARC_FIELD_LABELS[k];
        const hit = needle.length >= 2 && value.toLowerCase().includes(needle);
        return (
          <div key={k} className="grid grid-cols-[100px_1fr] gap-2 items-baseline">
            <div className="text-right text-[10px] muted">{lbl?.label ?? k}</div>
            <div
              dir="auto"
              lang={langOf(value)}
              className={hit ? "rounded bg-yellow-300/30 text-ink px-1" : "text-ink/90"}
            >
              {value}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function EntityEditModal({runId, entity, marcSource, onClose, onSaved}: EntityEditModalProps) {
  const [state, setState] = useState<EditState>({
    text: entity.text, type: entity.type, role: entity.role,
  });
  const [source, setSource] = useState<MarcSource | null>(marcSource ?? null);
  const [loading, setLoading] = useState(marcSource == null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  useGlassOverlayLifecycle(true);
  useFocusTrap(true, modalRef);

  useEffect(() => {
    if (marcSource != null) {
      setSource(marcSource);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    MarcSourceApi.get(runId, entity.control_number)
      .then((src) => { if (!cancelled) setSource(src); })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not load MARC source.");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, entity.control_number, marcSource]);

  const marcText = useMemo(() => marcToString(source?.marc ?? null), [source]);
  const useStructuredMarc = marcText.length > MARC_HIGHLIGHT_CAP;
  const highlight = useMemo(
    () => (useStructuredMarc ? {before: "", hit: "", after: ""} : highlightSpan(marcText, state.text, entity.start, entity.end)),
    [marcText, state.text, entity.start, entity.end, useStructuredMarc],
  );

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await ExtractionApprovals.patch(runId, entity.id, {
        text: state.text, type: state.type, role: state.role,
      });
      emitEntitiesRefreshed(runId);
      onSaved(updated);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save entity.");
    } finally {
      setSaving(false);
    }
  }, [runId, entity.id, state, onSaved, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="entity-edit-modal"
      role="dialog"
      aria-modal="true"
    >
      <Glass
        ref={modalRef}
        variant="modal"
        refraction={false}
        className="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden"
      >
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Edit entity</div>
            <div className="text-sm muted">
              MS <span className="font-mono text-ink">{entity.control_number}</span> ·
              source <span className="text-ink">{entity.source}</span>
            </div>
          </div>
          <button type="button" onClick={onClose}
                  data-testid="entity-edit-close"
                  className="button-ghost h-8 px-3 text-xs">Close</button>
        </div>
        <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden p-4 md:grid-cols-2">
          <div className="flex flex-col gap-3 overflow-auto">
            <label className="block text-xs uppercase tracking-wide kicker">Text</label>
            <textarea value={state.text}
              data-testid="entity-edit-text"
              onChange={(e) => setState((p) => ({...p, text: e.target.value}))}
              rows={3} className="input-glass w-full text-sm" />
            <label className="block text-xs uppercase tracking-wide kicker">Type</label>
            <select value={state.type}
              data-testid="entity-edit-type"
              onChange={(e) => setState((p) => ({...p, type: e.target.value as EntityType}))}
              className="input-glass h-8 text-sm">
              {ENTITY_TYPES.map((t) => (<option key={t} value={t}>{t}</option>))}
            </select>
            <label className="block text-xs uppercase tracking-wide kicker">Role</label>
            <select value={state.role}
              data-testid="entity-edit-role"
              onChange={(e) => setState((p) => ({...p, role: e.target.value as EntityRole}))}
              className="input-glass h-8 text-sm">
              {ENTITY_ROLES.map((r) => (<option key={r || "_blank"} value={r}>{r || "—"}</option>))}
            </select>
            {error ? (
              <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-danger"
                   data-testid="entity-edit-error">
                {error}
              </div>
            ) : null}
            <div className="mt-auto flex items-center justify-end gap-2 pt-2">
              <button type="button" onClick={onClose}
                      className="button-ghost h-8 px-3 text-xs" disabled={saving}>Cancel</button>
              <button type="button" onClick={handleSave}
                      data-testid="entity-edit-save"
                      className="button-primary h-8 px-3 text-xs" disabled={saving}>
                {saving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </div>
          <div className="flex flex-col overflow-hidden">
            <div className="kicker mb-1">MARC source</div>
            <Glass variant="compact" refraction={false} className="flex-1 overflow-auto whitespace-pre-wrap p-3 font-mono text-xs leading-relaxed" data-testid="entity-edit-marc">
              {loading ? (
                <span className="muted">Loading MARC record…</span>
              ) : source?.marc && useStructuredMarc ? (
                <StructuredMarcPanel marc={source.marc} highlightText={state.text} />
              ) : marcText ? (
                <>
                  <span>{highlight.before}</span>
                  <span className="rounded bg-yellow-300/30 text-ink">{highlight.hit}</span>
                  <span>{highlight.after}</span>
                </>
              ) : (
                <span className="muted">No MARC source available.</span>
              )}
            </Glass>
          </div>
        </div>
      </Glass>
    </div>
  );
}
