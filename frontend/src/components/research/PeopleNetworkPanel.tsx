/**
 * People Network — D3 force-directed graph of scribes, authors, owners.
 *
 * Nodes are sized by ms_count (number of manuscripts they appear in).
 * Edges appear when two people share at least one manuscript.
 *
 * The server returns pre-computed spring-layout {x, y} positions (networkx)
 * so the blocking O(n²) simulation is skipped on mount.  Nodes are placed
 * at the server positions and given a short low-alpha settle to remove any
 * residual overlaps.  Drag and zoom remain fully client-side (D3).
 */
import {useEffect, useRef, useMemo} from "react";
import * as d3 from "d3";
import {researchApi, type PeopleNetwork} from "@/api/research";
import {PanelShell, useAsync} from "./_shared";

const ROLE_COLOR: Record<string, string> = {
  scribe: "#38bdf8",
  author: "#a78bfa",
  owner:  "#34d399",
};

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  role: string;
  ms_count: number;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  ms: string;
}

function NetworkGraph({network}: {network: PeopleNetwork}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  // Initialise nodes from server positions; D3 will update x/y in place.
  const {nodes, links}: {nodes: SimNode[]; links: SimLink[]} = useMemo(() => {
    const nodeMap = new Map<string, SimNode>();
    for (const n of network.nodes) {
      nodeMap.set(n.id, {
        ...n,
        // Seed with server-side spring-layout coordinates.
        // D3 uses x/y as mutable state; we do NOT set fx/fy (fixed)
        // so the short settle pass can still resolve collisions.
        x: n.x,
        y: n.y,
        vx: 0,
        vy: 0,
      });
    }
    const simLinks: SimLink[] = network.links.flatMap((l) => {
      const source = nodeMap.get(l.source);
      const target = nodeMap.get(l.target);
      if (!source || !target) return [];
      return [{source, target, ms: l.ms}];
    });
    return {nodes: [...nodeMap.values()], links: simLinks};
  }, [network]);

  useEffect(() => {
    if (!svgRef.current) return;
    const el = svgRef.current;
    const W = el.clientWidth  || 680;
    const H = el.clientHeight || 420;

    const radius = (n: SimNode) => 4 + Math.sqrt(n.ms_count) * 3;

    // Scale server positions (range ~[-800,800]) to SVG viewport
    const scaleX = d3.scaleLinear().domain([-800, 800]).range([40, W - 40]);
    const scaleY = d3.scaleLinear().domain([-800, 800]).range([40, H - 40]);
    for (const n of nodes) {
      if (n.x != null) n.x = scaleX(n.x);
      if (n.y != null) n.y = scaleY(n.y);
    }

    // Short settle: high alphaDecay so it stops fast; collision only so
    // the global topology (from the server) is preserved.
    const simulation = d3.forceSimulation<SimNode>(nodes)
      .force("link",    d3.forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(80).strength(0.05))
      .force("collide", d3.forceCollide<SimNode>((d) => radius(d) + 4))
      .alphaDecay(0.15)   // converges in ~30 ticks instead of ~300
      .alpha(0.2);        // start low so positions barely shift

    const svg = d3.select(el);
    svg.selectAll("*").remove();

    const g = svg.append("g");

    // Zoom
    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.2, 6])
        .on("zoom", (event) => g.attr("transform", event.transform)),
    );

    const link = g.append("g")
      .attr("stroke", "#334155")
      .attr("stroke-opacity", 0.6)
      .selectAll<SVGLineElement, SimLink>("line")
      .data(links)
      .join("line")
      .attr("stroke-width", 1.2);

    const node = g.append("g")
      .selectAll<SVGCircleElement, SimNode>("circle")
      .data(nodes)
      .join("circle")
      .attr("r",    (d) => radius(d))
      .attr("fill", (d) => ROLE_COLOR[d.role] ?? "#94a3b8")
      .attr("fill-opacity", 0.85)
      .attr("stroke", "#1e293b")
      .attr("stroke-width", 1)
      .style("cursor", "pointer")
      .call(
        d3.drag<SVGCircleElement, SimNode>()
          .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
          .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end",   (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }),
      );

    // Labels for high-degree nodes only
    const label = g.append("g")
      .selectAll<SVGTextElement, SimNode>("text")
      .data(nodes.filter((n) => n.ms_count >= 2))
      .join("text")
      .text((d) => d.label)
      .attr("font-size", "9px")
      .attr("fill", "#cbd5e1")
      .attr("pointer-events", "none")
      .attr("text-anchor", "middle")
      .attr("dy", (d) => -(radius(d) + 3));

    // Tooltip
    node
      .on("mouseover", (event, d) => {
        const tip = tooltipRef.current;
        if (!tip) return;
        tip.style.display = "block";
        tip.style.left = (event.offsetX + 12) + "px";
        tip.style.top  = (event.offsetY - 10) + "px";
        tip.innerHTML = `<strong>${d.label}</strong><br/><span class="muted">${d.role} · ${d.ms_count} ms</span>`;
      })
      .on("mousemove", (event) => {
        const tip = tooltipRef.current;
        if (!tip) return;
        tip.style.left = (event.offsetX + 12) + "px";
        tip.style.top  = (event.offsetY - 10) + "px";
      })
      .on("mouseleave", () => {
        const tip = tooltipRef.current;
        if (tip) tip.style.display = "none";
      });

    simulation.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as SimNode).x ?? 0)
        .attr("y1", (d) => (d.source as SimNode).y ?? 0)
        .attr("x2", (d) => (d.target as SimNode).x ?? 0)
        .attr("y2", (d) => (d.target as SimNode).y ?? 0);
      node
        .attr("cx", (d) => d.x ?? 0)
        .attr("cy", (d) => d.y ?? 0);
      label
        .attr("x", (d) => d.x ?? 0)
        .attr("y", (d) => d.y ?? 0);
    });

    return () => { simulation.stop(); };
  }, [nodes, links]);

  return (
    <div className="relative w-full h-[420px] rounded-xl overflow-hidden bg-white/5 border border-white/10">
      <svg ref={svgRef} width="100%" height="100%" />
      <div
        ref={tooltipRef}
        className="absolute hidden z-10 code-surface px-3 py-2 text-xs text-ink pointer-events-none shadow-xl"
      />
      <div className="absolute bottom-3 right-3 flex gap-3 text-xs muted">
        {Object.entries(ROLE_COLOR).map(([role, color]) => (
          <span key={role} className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-full" style={{background: color}} />
            {role}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function PeopleNetworkPanel({projectId}: {projectId: string}) {
  const {data, loading, error} = useAsync<PeopleNetwork>(
    () => researchApi.peopleNetwork(projectId),
    [projectId],
  );

  return (
    <PanelShell
      title="People Network"
      subtitle="Scribes, authors, and owners across the manuscript corpus — drag to explore"
      loading={loading}
      empty={!loading && data?.nodes.length === 0}
    >
      {error && <p className="text-danger text-sm">{error}</p>}
      {data && data.nodes.length > 0 && <NetworkGraph network={data} />}
    </PanelShell>
  );
}
