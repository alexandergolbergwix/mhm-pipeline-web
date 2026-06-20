/**
 * EntityFilterChips — three dimension chip rows (Source / Type / Role)
 * + a free-text search box. AND across dimensions; OR within each.
 */

import { useMemo, useState } from "react";

import {
  ENTITY_ROLES,
  ENTITY_SOURCES,
  ENTITY_TYPES,
  Entity,
  EntityRole,
  EntitySource,
  EntityType,
} from "@/api/extractionApprovals";
import {Glass, GlassPill} from "@/components/glass";
import {useReportFilteredEntities} from "@/hooks/useReportDerivedIds";

export interface EntityFilterChipsProps {
  entities: Entity[];
  /** Must be stable (useCallback) or omitted — see Rule W-36. */
  onFilteredChange: (filtered: Entity[]) => void;
}

interface FilterState {
  sources: Set<EntitySource>;
  types: Set<EntityType>;
  roles: Set<EntityRole>;
  search: string;
}

function emptyState(): FilterState {
  return { sources: new Set(), types: new Set(), roles: new Set(), search: "" };
}

function applyFilters(entities: Entity[], state: FilterState): Entity[] {
  const needle = state.search.trim().toLocaleLowerCase();
  return entities.filter((e) => {
    if (state.sources.size > 0 && !state.sources.has(e.source)) return false;
    if (state.types.size > 0 && !state.types.has(e.type)) return false;
    if (state.roles.size > 0 && !state.roles.has(e.role)) return false;
    if (needle.length > 0) {
      const hay = `${e.text} ${e.control_number}`.toLocaleLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
}

interface ChipProps {
  label: string;
  active: boolean;
  count: number;
  onToggle: () => void;
  testId?: string;
}
function Chip({ label, active, count, onToggle, testId }: ChipProps) {
  return (
    <GlassPill as="button" type="button" onClick={onToggle}
      data-testid={testId}
      data-active={active} className={`text-xs ${active ? "border-biu-sky text-biu-sky" : "muted"}`} aria-pressed={active}>
      {label}<span className="ml-1 opacity-70">({count})</span>
    </GlassPill>
  );
}

export function EntityFilterChips({ entities, onFilteredChange }: EntityFilterChipsProps) {
  const [state, setState] = useState<FilterState>(() => emptyState());

  const counts = useMemo(() => {
    const sources: Record<string, number> = {};
    const types: Record<string, number> = {};
    const roles: Record<string, number> = {};
    for (const e of entities) {
      sources[e.source] = (sources[e.source] ?? 0) + 1;
      types[e.type] = (types[e.type] ?? 0) + 1;
      const r = e.role || "";
      roles[r] = (roles[r] ?? 0) + 1;
    }
    return { sources, types, roles };
  }, [entities]);

  const filtered = useMemo(() => applyFilters(entities, state), [entities, state]);
  useReportFilteredEntities(filtered, onFilteredChange);

  const toggle = <T extends string>(key: "sources" | "types" | "roles", value: T) => {
    setState((prev) => {
      const next = new Set(prev[key] as Set<string>);
      if (next.has(value)) next.delete(value); else next.add(value);
      return { ...prev, [key]: next as never };
    });
  };

  const clear = () => setState(emptyState());

  const anyActive = state.sources.size > 0 || state.types.size > 0 ||
                    state.roles.size > 0 || state.search.length > 0;

  return (
    <Glass variant="compact" className="space-y-3 p-3" data-testid="entity-filter-chips">
      <div className="flex items-center gap-2">
        <input type="text"
          placeholder="Search text or MS id…"
          data-testid="entity-search"
          value={state.search}
          onChange={(e) => setState((prev) => ({ ...prev, search: e.target.value }))}
          className="input-glass h-8 flex-1 text-sm" />
        {anyActive ? (
          <button type="button" onClick={clear}
                  data-testid="clear-filters"
                  className="button-ghost h-8 px-3 text-xs">Clear filters</button>
        ) : null}
      </div>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2" data-testid="filter-row-source">
          <span className="kicker w-16">Source</span>
          {ENTITY_SOURCES.map((s) => (
            <Chip key={s} label={s}
                  testId={`chip-source-${s}`}
                  active={state.sources.has(s)} count={counts.sources[s] ?? 0}
                  onToggle={() => toggle<EntitySource>("sources", s)} />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2" data-testid="filter-row-type">
          <span className="kicker w-16">Type</span>
          {ENTITY_TYPES.map((t) => (
            <Chip key={t} label={t}
                  testId={`chip-type-${t}`}
                  active={state.types.has(t)} count={counts.types[t] ?? 0}
                  onToggle={() => toggle<EntityType>("types", t)} />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2" data-testid="filter-row-role">
          <span className="kicker w-16">Role</span>
          {ENTITY_ROLES.map((r) => (
            <Chip key={r || "_blank"} label={r || "(none)"}
                  testId={`chip-role-${r || "blank"}`}
                  active={state.roles.has(r)} count={counts.roles[r] ?? 0}
                  onToggle={() => toggle<EntityRole>("roles", r)} />
          ))}
        </div>
      </div>
    </Glass>
  );
}
