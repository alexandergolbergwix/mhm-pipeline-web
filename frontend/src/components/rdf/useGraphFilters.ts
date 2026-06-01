/**
 * useGraphFilters — pure hook computing which graph nodes + edges
 * survive the active search, type, predicate, SHACL, and
 * neighbourhood-radius filters.
 *
 * Result: ``ActiveSets`` — two Sets of ids that the StageRdf canvas
 * uses to toggle a ``.dim`` class on cytoscape elements.
 *
 * Hot path is O(N + E). ``query`` is debounced upstream so this
 * memo only re-runs when filters change.
 */

import { useMemo } from "react";

import type { GraphEdge, GraphNode } from "@/api/rdf";

export interface ActiveSets {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
}

export interface GraphFilterState {
  query:       string;             // free-text search
  types:       Set<string>;        // active node types (OR)
  predicates:  Set<string>;        // active edge predicate_labels (OR)
  shaclOnly:   boolean;            // restrict to SHACL focus_nodes (+ 1-hop)
  radius:      0 | 1 | 2;          // 0 = off, 1 or 2 hops from selectedId
}

export function emptyFilterState(): GraphFilterState {
  return {
    query:      "",
    types:      new Set(),
    predicates: new Set(),
    shaclOnly:  false,
    radius:     0,
  };
}

/** Build O(1)-lookup edge index from node id → adjacent edges. */
function buildAdjacency(edges: GraphEdge[]): Map<string, GraphEdge[]> {
  const adj = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    if (!adj.has(e.source)) adj.set(e.source, []);
    if (!adj.has(e.target)) adj.set(e.target, []);
    adj.get(e.source)!.push(e);
    adj.get(e.target)!.push(e);
  }
  return adj;
}

/** BFS from ``start`` over the undirected adjacency, up to ``radius`` hops. */
function neighbourhood(
  start: string,
  adj:   Map<string, GraphEdge[]>,
  radius: number,
): Set<string> {
  const visited = new Set<string>([start]);
  let frontier: string[] = [start];
  for (let hop = 0; hop < radius; hop++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const e of adj.get(id) ?? []) {
        const other = e.source === id ? e.target : e.source;
        if (!visited.has(other)) {
          visited.add(other);
          next.push(other);
        }
      }
    }
    frontier = next;
    if (frontier.length === 0) break;
  }
  return visited;
}

interface UseGraphFiltersArgs {
  nodes:      GraphNode[];
  edges:      GraphEdge[];
  state:      GraphFilterState;
  shaclFocus: Set<string>;
  selectedId: string | null;
}

export function useGraphFilters({
  nodes, edges, state, shaclFocus, selectedId,
}: UseGraphFiltersArgs): ActiveSets {
  return useMemo(() => {
    const adj = buildAdjacency(edges);

    // — 1. Search match — produces a set of nodeIds the query "touched".
    //
    // Search hits expand to edge endpoints too: typing "owner" matches
    // every "owned by" edge's predicate, and we light up the endpoints.
    const needle = state.query.trim().toLocaleLowerCase();
    let searchNodeIds: Set<string> | null = null;
    if (needle.length > 0) {
      searchNodeIds = new Set();

      // Node label / type / property match
      for (const n of nodes) {
        const hay = [
          n.label,
          n.type,
          ...Object.values(n.properties ?? {}).flat(),
        ].join(" ").toLocaleLowerCase();
        if (hay.includes(needle)) searchNodeIds.add(n.id);
      }
      // Edge predicate match expands to source/target
      for (const e of edges) {
        const label = (e.predicate_label || e.predicate || "").toLocaleLowerCase();
        if (label.includes(needle)) {
          searchNodeIds.add(e.source);
          searchNodeIds.add(e.target);
        }
      }
    }

    // — 2. Type filter — OR within the chip row
    const typeOk = (n: GraphNode): boolean =>
      state.types.size === 0 || state.types.has(n.type);

    // — 3. SHACL-only — restrict to focus_nodes + 1-hop context
    let shaclScope: Set<string> | null = null;
    if (state.shaclOnly && shaclFocus.size > 0) {
      shaclScope = new Set(shaclFocus);
      for (const id of shaclFocus) {
        for (const e of adj.get(id) ?? []) {
          shaclScope.add(e.source);
          shaclScope.add(e.target);
        }
      }
    }

    // — 4. Neighbourhood radius around the selected node
    let radiusScope: Set<string> | null = null;
    if (state.radius > 0 && selectedId) {
      radiusScope = neighbourhood(selectedId, adj, state.radius);
    }

    // — 5. Compute final node set
    const nodeIds = new Set<string>();
    for (const n of nodes) {
      if (!typeOk(n)) continue;
      if (searchNodeIds !== null && !searchNodeIds.has(n.id)) continue;
      if (shaclScope     !== null && !shaclScope.has(n.id))   continue;
      if (radiusScope    !== null && !radiusScope.has(n.id))  continue;
      nodeIds.add(n.id);
    }
    // Selected node is always kept visible so the detail panel never
    // refers to something the user can't see.
    if (selectedId) nodeIds.add(selectedId);

    // — 6. Compute final edge set
    const predicateActive = state.predicates.size > 0;
    const edgeIds = new Set<string>();
    for (const e of edges) {
      // Edge must connect two surviving nodes.
      if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
      // Predicate filter (OR within row)
      if (predicateActive) {
        const lbl = e.predicate_label || e.predicate || "";
        if (!state.predicates.has(lbl)) continue;
      }
      edgeIds.add(e.id);
    }

    // — 7. Orphan-dim: if predicate filter is active, nodes that ended
    //    up with no surviving edges get greyed out too. This is the
    //    visual cue that the predicate filter is real.
    if (predicateActive) {
      const reached = new Set<string>();
      for (const id of edgeIds) {
        const e = edges.find(x => x.id === id);
        if (!e) continue;
        reached.add(e.source);
        reached.add(e.target);
      }
      // But always keep the selected node.
      if (selectedId) reached.add(selectedId);
      // Replace nodeIds with the orphan-filtered set.
      for (const id of [...nodeIds]) {
        if (!reached.has(id)) nodeIds.delete(id);
      }
    }

    return { nodeIds, edgeIds };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, state, shaclFocus, selectedId]);
}
