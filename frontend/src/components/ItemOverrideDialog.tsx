/**
 * ItemOverrideDialog — per-item Wikidata Studio curator overrides.
 * PATCH /runs/{runId}/wikidata-studio/items/{localId}
 */

import {useCallback, useState} from "react";

import {ApiError} from "@/api/client";
import {
  Studio,
  type ItemOverridePayload,
  type StudioItem,
} from "@/api/wikidataStudio";
import {Glass} from "@/components/glass";

export interface ItemOverrideDialogProps {
  runId: string;
  item: StudioItem;
  onClose: () => void;
  onSaved: () => void;
}

type LangMap = Record<string, string>;

function langMapFromRecord(rec: Record<string, string> | undefined): LangMap {
  if (!rec) return {};
  return {...rec};
}

export function ItemOverrideDialog({runId, item, onClose, onSaved}: ItemOverrideDialogProps) {
  const localId = item.local_id ?? "";
  const [labels, setLabels] = useState<LangMap>(langMapFromRecord(item.labels));
  const [descriptions, setDescriptions] = useState<LangMap>(
    langMapFromRecord(item.descriptions),
  );
  const [aliasLang, setAliasLang] = useState("he");
  const [aliasText, setAliasText] = useState(
    (item.aliases?.he ?? item.aliases?.en ?? []).join(" · "),
  );
  const [stmtProperty, setStmtProperty] = useState("");
  const [stmtValue, setStmtValue] = useState("");
  const [removeIdx, setRemoveIdx] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = useCallback(async () => {
    if (!localId) {
      setError("Item has no local_id — cannot save override.");
      return;
    }
    setSaving(true); setError(null);
    const payload: ItemOverridePayload = {
      labels:       Object.fromEntries(
        Object.entries(labels).map(([k, v]) => [k, v || null]),
      ),
      descriptions: Object.fromEntries(
        Object.entries(descriptions).map(([k, v]) => [k, v || null]),
      ),
    };
    const aliases = aliasText
      .split(/[·,;]/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (aliases.length > 0) {
      payload.aliases = {[aliasLang]: aliases};
    }
    if (stmtProperty.trim() && stmtValue.trim()) {
      payload.add_statements = [{
        property: stmtProperty.trim(),
        value:    stmtValue.trim(),
      }];
    }
    const rm = parseInt(removeIdx, 10);
    if (!Number.isNaN(rm) && rm >= 0) {
      payload.remove_statements = [rm];
    }
    try {
      await Studio.patchItemOverride(runId, localId, payload);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSaving(false);
    }
  }, [
    runId, localId, labels, descriptions, aliasLang, aliasText,
    stmtProperty, stmtValue, removeIdx, onSaved, onClose,
  ]);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
         data-testid="item-override-modal" role="dialog" aria-modal="true">
      <Glass className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <div className="kicker">Edit Wikidata item</div>
            <div className="text-sm font-mono muted">{localId || "(no local_id)"}</div>
          </div>
          <button type="button" onClick={onClose} className="button-ghost h-8 px-3 text-xs">
            Close
          </button>
        </div>
        <div className="space-y-4 overflow-auto p-4 text-sm">
          <LangEditor title="Labels" map={labels} onChange={setLabels} />
          <LangEditor title="Descriptions" map={descriptions} onChange={setDescriptions} />
          <section className="space-y-2">
            <div className="kicker">Aliases</div>
            <div className="flex gap-2">
              <input value={aliasLang} onChange={(e) => setAliasLang(e.target.value)}
                     placeholder="lang" className="input-glass h-8 w-16 font-mono text-xs" />
              <input value={aliasText} onChange={(e) => setAliasText(e.target.value)}
                     placeholder="alias1 · alias2"
                     className="input-glass h-8 flex-1 text-sm" />
            </div>
          </section>
          <section className="space-y-2">
            <div className="kicker">Add statement</div>
            <div className="flex gap-2">
              <input value={stmtProperty} onChange={(e) => setStmtProperty(e.target.value)}
                     placeholder="P31" className="input-glass h-8 w-24 font-mono text-xs" />
              <input value={stmtValue} onChange={(e) => setStmtValue(e.target.value)}
                     placeholder="Q87167" className="input-glass h-8 flex-1 font-mono text-xs" />
            </div>
          </section>
          <section className="space-y-2">
            <div className="kicker">Remove statement (index)</div>
            <input value={removeIdx} onChange={(e) => setRemoveIdx(e.target.value)}
                   type="number" min={0} placeholder="0"
                   className="input-glass h-8 w-24 text-sm" />
          </section>
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
          <button type="button" onClick={handleSave} data-testid="item-override-save"
                  className="button-primary h-8 px-3 text-xs" disabled={saving || !localId}>
            {saving ? "Saving…" : "Save & rebuild"}
          </button>
        </div>
      </Glass>
    </div>
  );
}

function LangEditor({
  title, map, onChange,
}: {
  title: string;
  map: LangMap;
  onChange: (m: LangMap) => void;
}) {
  const entries = Object.entries(map);
  return (
    <section className="space-y-2">
      <div className="kicker">{title}</div>
      {entries.map(([lang, val]) => (
        <div key={lang} className="flex gap-2">
          <span className="kicker w-10 shrink-0 pt-1.5">{lang}</span>
          <input value={val}
                 onChange={(e) => onChange({...map, [lang]: e.target.value})}
                 className="input-glass h-8 flex-1 text-sm" />
          <button type="button" className="button-ghost text-xs"
                  onClick={() => {
                    const next = {...map};
                    delete next[lang];
                    onChange(next);
                  }}>✕</button>
        </div>
      ))}
      <button type="button" className="button-ghost text-xs"
              onClick={() => onChange({...map, "": ""})}>
        + Add language
      </button>
    </section>
  );
}
