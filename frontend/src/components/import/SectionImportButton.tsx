/**
 * SectionImportButton — hidden file-picker + inline result panel.
 *
 * Renders an "Import" glass-pill button.  Clicking it opens the native
 * file picker (filtered by ``accept``).  After the user selects a file
 * the upload runs and the result (imported / skipped / errors) is
 * shown inline as a small panel below the button.
 *
 * Props:
 *   - ``section``     — which pipeline section to import into
 *   - ``runId``       — UUID of the run
 *   - ``accept``      — file-input ``accept`` attribute (e.g. ".json,.csv")
 *   - ``onComplete``  — callback fired with the ImportResult after a
 *     successful import (use this to trigger a table refresh)
 *   - ``className``   — optional additional CSS classes
 *
 * Usage:
 * ```tsx
 * <SectionImportButton
 *   section="authority"
 *   runId={run.id}
 *   accept=".json,.csv"
 *   onComplete={(result) => refetchMatches()}
 * />
 * ```
 */

import {useRef, useState} from "react";

import {SectionImport, type ImportResult} from "@/api/sectionImport";
import {Glass} from "@/components/glass";

export type ImportSection =
  | "extraction"
  | "authority"
  | "rdf"
  | "wikibase"
  | "wikibase_items"
  | "wikidata-studio";

interface SectionImportButtonProps {
  section: ImportSection;
  runId: string;
  accept?: string;
  onComplete?: (result: ImportResult) => void;
  className?: string;
}

const SECTION_ACCEPT: Record<ImportSection, string> = {
  extraction:       ".json,.csv",
  authority:        ".json,.csv",
  rdf:              ".ttl",
  wikibase:         ".json",
  wikibase_items:   ".json",
  "wikidata-studio": ".json,.csv",
};

export function SectionImportButton({
  section,
  runId,
  accept,
  onComplete,
  className,
}: SectionImportButtonProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const acceptAttr = accept ?? SECTION_ACCEPT[section];
  const cls = className ?? "button-ghost text-sm inline-flex items-center gap-1";

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    // Reset so the same file can be re-imported after editing
    e.target.value = "";
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const fn = section === "wikidata-studio"
        ? SectionImport.wikidataStudio
        : section === "wikibase_items"
          ? SectionImport.wikibaseItems
          : SectionImport[section as Exclude<ImportSection, "wikidata-studio" | "wikibase_items">];
      const res = await fn(runId, file);
      setResult(res);
      onComplete?.(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col items-start gap-1" data-testid="section-import-button">
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={busy}
        className={cls}
        data-testid="section-import-trigger"
        aria-label={`Import into ${section}`}
      >
        {busy ? (
          <span className="animate-spin inline-block" aria-hidden="true">⟳</span>
        ) : (
          <span aria-hidden="true">↑</span>
        )}
        {busy ? "Importing…" : "Import"}
      </button>

      <input
        ref={inputRef}
        type="file"
        accept={acceptAttr}
        onChange={onFileChange}
        className="hidden"
        data-testid="section-import-file-input"
        aria-hidden="true"
      />

      {result !== null && (
        <Glass className="rounded-md px-3 py-2 text-xs space-y-0.5 min-w-[180px]" data-testid="section-import-result"
          role="status">
          <div className="text-green-300">
            {result.imported} imported
          </div>
          {result.skipped > 0 && (
            <div className="muted">{result.skipped} skipped</div>
          )}
          {result.errors.length > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-warn">
                {result.errors.length} error{result.errors.length > 1 ? "s" : ""}
              </summary>
              <ul className="mt-1 space-y-0.5 max-h-32 overflow-auto">
                {result.errors.map((e, i) => (
                  <li key={i} className="text-danger">
                    row {e.row}: {e.message}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Glass>
      )}

      {error && (
        <Glass className="rounded-md px-3 py-2 text-xs text-danger min-w-[180px]" data-testid="section-import-error"
          role="alert">
          {error}
        </Glass>
      )}
    </span>
  );
}
