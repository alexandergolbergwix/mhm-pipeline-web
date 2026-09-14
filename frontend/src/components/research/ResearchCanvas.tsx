import {lazy, Suspense, useEffect, useState} from "react";
import {Glass} from "@/components/glass";
import {ResearchAgent, type ResearchArtifact} from "@/api/researchAgent";

const ProvenanceMapPanel = lazy(() => import("@/components/research/ProvenanceMapPanel"));
const PeopleNetworkPanel = lazy(() => import("@/components/research/PeopleNetworkPanel"));
const CoOccurrencePanel = lazy(() => import("@/components/research/CoOccurrencePanel"));

export function ResearchCanvas({
  projectId,
  threadId,
  artifacts,
  activeKey,
  onSelect,
  onSaved,
}: {
  projectId: string;
  threadId: string;
  artifacts: ResearchArtifact[];
  activeKey: string | null;
  onSelect: (key: string) => void;
  onSaved: (artifact: ResearchArtifact) => void;
}) {
  const active = artifacts.find((a) => a.artifact_key === activeKey) ?? artifacts.at(-1) ?? null;
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!active) setExpanded(false);
  }, [active?.artifact_key]);
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const body = active ? (
    <ArtifactBody
      projectId={projectId}
      threadId={threadId}
      artifact={active}
      onSaved={onSaved}
    />
  ) : null;

  return (
    <Glass className="flex flex-col h-full min-h-[32rem] gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1">
          {artifacts.length === 0 ? (
            <span className="text-xs muted">Canvas is empty. Ask in chat or use an action.</span>
          ) : artifacts.map((art) => (
            <button
              key={art.artifact_key}
              type="button"
              onClick={() => onSelect(art.artifact_key)}
              className={`text-xs px-2 py-1 rounded-lg border ${
                art.artifact_key === active?.artifact_key
                  ? "border-biu-sky/40 bg-biu-sky/15 text-biu-sky"
                  : "border-white/10 text-muted"
              }`}
            >
              {art.title || art.artifact_key}
            </button>
          ))}
        </div>
        {active ? (
          <div className="flex gap-2 text-xs">
            <a
              className="text-biu-sky hover:underline"
              href={ResearchAgent.downloadUrl(threadId, active.artifact_key, downloadFormat(active.kind))}
            >
              Download
            </a>
            <a className="text-biu-sky hover:underline" href={ResearchAgent.exportUrl(threadId)}>
              Export session
            </a>
            <button
              type="button"
              className="text-biu-sky hover:underline"
              onClick={() => setExpanded(true)}
              title="Expand to full screen"
              aria-label="Expand canvas"
            >
              ⛶ Expand
            </button>
          </div>
        ) : null}
      </div>
      <div className="flex-1 overflow-auto" data-testid="research-canvas">
        {body}
      </div>
      {expanded && active ? (
        <div
          className="fixed inset-0 z-50 flex flex-col gap-3 p-4 overflow-hidden"
          style={{background: "var(--body-bg)"}}
          role="dialog"
          aria-modal="true"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium text-ink truncate">
              {active.title || active.artifact_key}
            </span>
            <div className="flex gap-3 text-xs">
              <a
                className="text-biu-sky hover:underline"
                href={ResearchAgent.downloadUrl(threadId, active.artifact_key, downloadFormat(active.kind))}
              >
                Download
              </a>
              <button
                type="button"
                className="text-biu-sky hover:underline"
                onClick={() => setExpanded(false)}
                aria-label="Close full screen"
              >
                ✕ Close
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-auto" data-testid="research-canvas-expanded">
            <ArtifactBody
              projectId={projectId}
              threadId={threadId}
              artifact={active}
              onSaved={onSaved}
            />
          </div>
        </div>
      ) : null}
    </Glass>
  );
}

function downloadFormat(kind: string): string {
  if (kind === "markdown") return "md";
  if (kind === "sparql" || kind === "table") return "csv";
  return "json";
}

function ArtifactBody({
  projectId,
  threadId,
  artifact,
  onSaved,
}: {
  projectId: string;
  threadId: string;
  artifact: ResearchArtifact;
  onSaved: (artifact: ResearchArtifact) => void;
}) {
  if (artifact.kind === "map") {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading map…</p>}>
        <ProvenanceMapPanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "sparql") {
    if (hasTabular(artifact.content)) {
      return <TableView content={artifact.content} />;
    }
    return (
      <div className="space-y-2">
        <p className="muted text-sm">
          {String(artifact.content?.text ?? "Query result stored. Ask the assistant for a table or chart.")}
        </p>
        {String(artifact.content?.query ?? "") ? (
          <pre className="text-xs rounded-lg bg-[var(--surface-inset)] border border-white/10 px-3 py-2 overflow-auto">{String(artifact.content.query)}</pre>
        ) : null}
      </div>
    );
  }
  if (artifact.kind === "chart") {
    return <ChartView content={artifact.content} />;
  }
  if (artifact.kind === "json" && artifact.artifact_key.includes("network")) {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading network…</p>}>
        <PeopleNetworkPanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "json" && artifact.artifact_key.includes("cooccurrence")) {
    return (
      <Suspense fallback={<p className="muted text-sm">Loading clusters…</p>}>
        <CoOccurrencePanel projectId={projectId} />
      </Suspense>
    );
  }
  if (artifact.kind === "table" || hasTabular(artifact.content)) {
    return <TableView content={artifact.content} />;
  }
  return (
    <MarkdownEditor
      threadId={threadId}
      artifact={artifact}
      onSaved={onSaved}
    />
  );
}

function hasTabular(content: Record<string, unknown>): boolean {
  return Array.isArray(content.columns) && Array.isArray(content.rows);
}

type ChartGroup = {name: string; items: Array<{label: string; count: number}>};

function ChartView({content}: {content: Record<string, unknown>}) {
  const groups = (Array.isArray(content.groups) ? content.groups : []) as ChartGroup[];
  const max = Math.max(1, ...groups.flatMap((g) => g.items.map((i) => i.count || 0)));
  const total = Number(content.total_claims) || groups.reduce((s, g) => s + g.items.reduce((a, i) => a + (i.count || 0), 0), 0);
  return (
    <div className="space-y-4">
      <div className="text-xs muted">
        {total.toLocaleString()} claims · {String(content.distinct_properties ?? "")} distinct properties
        {content.items_counted ? ` · ${Number(content.items_counted).toLocaleString()} items` : ""}
      </div>
      {groups.map((group) => (
        <div key={group.name} className="space-y-1.5">
          <div className="text-sm font-medium text-ink">{group.name}</div>
          {group.items.map((item) => (
            <div key={item.label} className="flex items-center gap-2">
              <div className="w-56 shrink-0 truncate text-xs" title={item.label}>{item.label}</div>
              <div className="flex-1 h-4 rounded bg-[var(--surface-inset)] overflow-hidden">
                <div
                  className="h-full rounded bg-[var(--accent, #4c8dff)]"
                  style={{width: `${Math.max(2, Math.round((item.count / max) * 100))}%`}}
                />
              </div>
              <div className="w-14 text-right text-xs tabular-nums">{item.count.toLocaleString()}</div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function TableView({content}: {content: Record<string, unknown>}) {
  const columns = (content.columns as string[]) || [];
  const rows = (content.rows as Array<Array<string | null>>) || [];
  return (
    <table className="w-full text-xs">
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col} className="text-left p-1 border-b border-white/10">{col}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j} className="p-1 border-b border-white/5">{cell ?? ""}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MarkdownEditor({
  threadId,
  artifact,
  onSaved,
}: {
  threadId: string;
  artifact: ResearchArtifact;
  onSaved: (artifact: ResearchArtifact) => void;
}) {
  const initial = String(artifact.content.text ?? JSON.stringify(artifact.content, null, 2));
  const [text, setText] = useState(initial);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setText(String(artifact.content.text ?? JSON.stringify(artifact.content, null, 2)));
  }, [artifact.id, artifact.version]);

  async function save() {
    setSaving(true);
    try {
      const saved = await ResearchAgent.saveArtifact(threadId, artifact.artifact_key, {
        title: artifact.title,
        kind: artifact.kind,
        content: {...artifact.content, text},
        created_by: "user",
      });
      onSaved(saved);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2">
      <label className="sr-only" htmlFor="canvas-editor">Canvas text</label>
      <textarea
        id="canvas-editor"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={18}
        className="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-ink font-mono"
      />
      <div className="flex items-center justify-between text-xs">
        <span className="muted">Version {artifact.version} · edit directly, then save</span>
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="px-3 py-1 rounded-lg bg-biu-sky/20 text-biu-sky"
        >
          Save
        </button>
      </div>
    </div>
  );
}
