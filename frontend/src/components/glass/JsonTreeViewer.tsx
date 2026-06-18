/**
 * JsonTreeViewer — pure, read-only JSON tree.
 *
 * Used by HistoryDiffModal to render "before" / "after" sides of a diff.
 * Highlights specific JSON Pointer paths (RFC 6902) with a yellow background
 * so a curator can see at a glance which fields changed.
 *
 * No external dependencies — pure React + Tailwind + <GlassPill>.
 */

import { useState, useCallback, useMemo } from "react";

import {GlassPill} from "@/components/glass";

export interface JsonTreeViewerProps {
  value: unknown;
  rootLabel?: string;
  initiallyOpenDepth?: number;
  highlightPaths?: string[];
  /** Above this many total nodes the interactive tree is replaced by a
   *  compact raw-JSON fallback — rendering one stateful React node per
   *  key explodes for large MARC / Wikibase snapshots. */
  maxNodes?: number;
}

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

const STRING_TRUNCATE_AT = 80;

/**
 * Escape a single JSON Pointer reference token per RFC 6901:
 * "~" → "~0", "/" → "~1". Order matters — "~" must be escaped first.
 */
function escapePointerToken(token: string): string {
  return token.replace(/~/g, "~0").replace(/\//g, "~1");
}

function joinPointer(parent: string, token: string | number): string {
  const t = typeof token === "number" ? String(token) : escapePointerToken(token);
  return `${parent}/${t}`;
}

function isPlainObject(v: unknown): v is { [key: string]: JsonValue } {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isJsonArray(v: unknown): v is JsonValue[] {
  return Array.isArray(v);
}

/** Cheap recursive node count (object keys + array items at every level;
 *  primitives count as 1). Used to decide whether the interactive tree is
 *  too large to render. */
function countNodes(v: unknown): number {
  if (isPlainObject(v)) {
    return 1 + Object.values(v).reduce((sum: number, child) => sum + countNodes(child), 0);
  }
  if (isJsonArray(v)) {
    return 1 + v.reduce((sum: number, child) => sum + countNodes(child), 0);
  }
  return 1;
}

function truncate(s: string, max: number): { text: string; truncated: boolean } {
  if (s.length <= max) return { text: s, truncated: false };
  return { text: `${s.slice(0, max)}…`, truncated: true };
}

interface NodeProps {
  value: unknown;
  path: string;
  depth: number;
  initiallyOpenDepth: number;
  highlightPaths: Set<string>;
  label?: string;
}

function PrimitiveValue({ value }: { value: unknown }): JSX.Element {
  if (value === null) {
    return <span className="muted italic">null</span>;
  }
  if (typeof value === "boolean") {
    return <span className="muted">{value ? "true" : "false"}</span>;
  }
  if (typeof value === "number") {
    return <span className="muted">{String(value)}</span>;
  }
  if (typeof value === "string") {
    const { text, truncated } = truncate(value, STRING_TRUNCATE_AT);
    return (
      <span
        className="text-string"
        title={truncated ? value : undefined}
      >
        {`"${text}"`}
      </span>
    );
  }
  // Fallback for unexpected types (e.g. undefined, symbol, function).
  return <span className="muted italic">{String(value)}</span>;
}

function TreeNode({
  value,
  path,
  depth,
  initiallyOpenDepth,
  highlightPaths,
  label,
}: NodeProps): JSX.Element {
  const [open, setOpen] = useState<boolean>(depth < initiallyOpenDepth);
  const toggle = useCallback(() => setOpen((o) => !o), []);

  const isHighlighted = highlightPaths.has(path);
  const rowBg = isHighlighted ? "highlight-row" : "";

  // Primitive leaf.
  if (!isPlainObject(value) && !isJsonArray(value)) {
    return (
      <div className={`flex items-start gap-2 py-0.5 px-1 rounded ${rowBg}`}>
        {label !== undefined && (
          <span className="kicker shrink-0">{label}:</span>
        )}
        <PrimitiveValue value={value} />
      </div>
    );
  }

  const isArray = isJsonArray(value);
  const count = isArray ? value.length : Object.keys(value).length;
  const summary = isArray
    ? `[ ${count} item${count === 1 ? "" : "s"} ]`
    : `{ ${count} key${count === 1 ? "" : "s"} }`;
  const chevron = open ? "▼" : "▶";

  return (
    <div
      className={`rounded ${rowBg}`}
      data-testid={`json-node-${path}`}
    >
      <button
        type="button"
        onClick={toggle}
        className="flex items-center gap-2 py-0.5 px-1 w-full text-left hover:bg-white/5 rounded"
        aria-expanded={open}
      >
        <span className="muted text-xs w-3 shrink-0">{chevron}</span>
        {label !== undefined && (
          <span className="kicker shrink-0">{label}:</span>
        )}
        <span className="muted text-xs">{summary}</span>
      </button>
      {open && count > 0 && (
        <div className="ml-4 border-l border-white/10 pl-2">
          {isArray
            ? value.map((child, idx) => (
                <TreeNode
                  key={idx}
                  value={child}
                  path={joinPointer(path, idx)}
                  depth={depth + 1}
                  initiallyOpenDepth={initiallyOpenDepth}
                  highlightPaths={highlightPaths}
                  label={String(idx)}
                />
              ))
            : Object.keys(value).map((key) => (
                <TreeNode
                  key={key}
                  value={value[key]}
                  path={joinPointer(path, key)}
                  depth={depth + 1}
                  initiallyOpenDepth={initiallyOpenDepth}
                  highlightPaths={highlightPaths}
                  label={key}
                />
              ))}
        </div>
      )}
    </div>
  );
}

export function JsonTreeViewer({
  value,
  rootLabel,
  initiallyOpenDepth = 2,
  highlightPaths = [],
  maxNodes = 500,
}: JsonTreeViewerProps): JSX.Element {
  const highlightSet = useMemo(
    () => new Set<string>(highlightPaths),
    [highlightPaths],
  );
  const nodeCount = useMemo(() => countNodes(value), [value]);

  if (nodeCount > maxNodes) {
    return (
      <GlassPill
        data-testid="json-tree-viewer"
        className="font-mono text-sm text-subtle p-2 overflow-auto block w-full"
      >
        <div className="muted text-xs mb-2">
          Object too large to render as a tree ({nodeCount} nodes). Showing raw JSON.
        </div>
        <pre className="whitespace-pre-wrap break-all text-string/90">
          {JSON.stringify(value, null, 2)}
        </pre>
      </GlassPill>
    );
  }

  return (
    <GlassPill
      data-testid="json-tree-viewer"
      className="font-mono text-sm text-subtle p-2 overflow-auto block w-full"
    >
      <TreeNode
        value={value}
        path=""
        depth={0}
        initiallyOpenDepth={initiallyOpenDepth}
        highlightPaths={highlightSet}
        label={rootLabel}
      />
    </GlassPill>
  );
}

export default JsonTreeViewer;
