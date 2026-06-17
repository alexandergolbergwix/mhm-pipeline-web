/**
 * Structured filter bar — column dropdown · operator dropdown · value combo.
 *
 * Mirrors the desktop pipeline's "structured filter" feature: multiple
 * AND-combined conditions, removable chips, value combo populated with
 * distinct values for the chosen column (editable so contains/numeric
 * targets can be typed).
 */

import { useState } from "react";
import {GlassPill} from "@/components/glass";

export type FilterColumn = {
  key: string;
  label: string;
  /** "text" gets equals/contains/empty-style operators; "number" gets >, <, ≥, ≤. */
  kind: "text" | "number" | "boolean";
};

export type FilterCondition = {
  column: string;
  operator: string;
  value: string;
};

export interface Props {
  columns: FilterColumn[];
  /** Distinct values per column key, for the value combo. */
  distinctValues: Record<string, string[]>;
  conditions: FilterCondition[];
  onChange: (conds: FilterCondition[]) => void;
}

const TEXT_OPS = [
  { id: "eq", label: "equals" },
  { id: "ne", label: "not equals" },
  { id: "contains", label: "contains" },
  { id: "ncontains", label: "does not contain" },
  { id: "starts", label: "starts with" },
  { id: "ends", label: "ends with" },
  { id: "empty", label: "is empty" },
  { id: "nempty", label: "is not empty" },
] as const;

const NUM_OPS = [
  { id: "eq", label: "=" },
  { id: "ne", label: "≠" },
  { id: "gt", label: ">" },
  { id: "gte", label: "≥" },
  { id: "lt", label: "<" },
  { id: "lte", label: "≤" },
  { id: "empty", label: "is empty" },
  { id: "nempty", label: "is not empty" },
] as const;

const BOOL_OPS = [
  { id: "true", label: "is true" },
  { id: "false", label: "is false" },
] as const;

const VALUELESS_OPS = new Set(["empty", "nempty", "true", "false"]);


export function StructuredFilter({ columns, distinctValues, conditions, onChange }: Props) {
  const [col, setCol] = useState<string>(columns[0]?.key ?? "");
  const [op, setOp]   = useState<string>("eq");
  const [val, setVal] = useState<string>("");

  const colSpec = columns.find((c) => c.key === col);
  const ops = !colSpec ? TEXT_OPS
    : colSpec.kind === "number" ? NUM_OPS
    : colSpec.kind === "boolean" ? BOOL_OPS
    : TEXT_OPS;
  const needsValue = !VALUELESS_OPS.has(op);
  const values = distinctValues[col] ?? [];

  function add() {
    if (!colSpec) return;
    if (needsValue && val === "") return;
    onChange([...conditions, { column: col, operator: op, value: val }]);
    setVal("");
  }

  function removeAt(i: number) {
    onChange(conditions.filter((_, j) => j !== i));
  }

  function clearAll() {
    onChange([]);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-center">
        <span className="kicker">Filter</span>

        <select value={col} onChange={(e) => { setCol(e.target.value); setOp("eq"); }} className="input-glass !py-1 text-sm w-auto">
          {columns.map((c) => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>

        <select value={op} onChange={(e) => setOp(e.target.value)} className="input-glass !py-1 text-sm w-auto">
          {ops.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>

        {needsValue && (
          <input
            list={`vals-${col}`}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            placeholder="value…"
            className="input-glass !py-1 text-sm w-44"
          />
        )}
        <datalist id={`vals-${col}`}>
          {values.map((v) => <option key={v} value={v} />)}
        </datalist>

        <button onClick={add} className="button-primary !py-1 text-sm">Add</button>
        {conditions.length > 0 && (
          <button onClick={clearAll} className="button-ghost !py-1 text-sm">Clear all</button>
        )}
      </div>

      {conditions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {conditions.map((c, i) => {
            const cSpec = columns.find((x) => x.key === c.column);
            const ops2 = cSpec?.kind === "number" ? NUM_OPS : cSpec?.kind === "boolean" ? BOOL_OPS : TEXT_OPS;
            const opLabel = ops2.find((o) => o.id === c.operator)?.label ?? c.operator;
            const valueless = VALUELESS_OPS.has(c.operator);
            return (
              <GlassPill as="div" className="px-3 py-1 text-xs flex items-center gap-2" key={i}>
                <span><b>{cSpec?.label ?? c.column}</b> {opLabel}{!valueless && <> <i>{c.value}</i></>}</span>
                <button onClick={() => removeAt(i)} className="opacity-70 hover:opacity-100" aria-label="remove">×</button>
              </GlassPill>
            );
          })}
        </div>
      )}
    </div>
  );
}


/** Evaluate the conditions against a row. Caller supplies a `get(col)` accessor. */
export function applyConditions<R>(
  rows: R[], conditions: FilterCondition[], get: (row: R, column: string) => string | number | boolean,
): R[] {
  if (conditions.length === 0) return rows;
  return rows.filter((row) =>
    conditions.every((c) => {
      const cell = get(row, c.column);
      const s = String(cell ?? "").toLowerCase();
      const v = c.value.toLowerCase();
      switch (c.operator) {
        case "eq":        return s === v;
        case "ne":        return s !== v;
        case "contains":  return s.includes(v);
        case "ncontains": return !s.includes(v);
        case "starts":    return s.startsWith(v);
        case "ends":      return s.endsWith(v);
        case "empty":     return s === "";
        case "nempty":    return s !== "";
        case "gt":  return Number(cell) >  Number(c.value);
        case "gte": return Number(cell) >= Number(c.value);
        case "lt":  return Number(cell) <  Number(c.value);
        case "lte": return Number(cell) <= Number(c.value);
        case "true":  return cell === true;
        case "false": return cell === false;
        default: return true;
      }
    })
  );
}
