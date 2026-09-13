/**
 * Research Assistant — chat + editable canvas at
 * /runs/:runId/linked-data-explorer
 *
 * Replaces the tabbed Linked Data Explorer. The agent runs on Modal
 * (or the local AG-UI stub). Heroku holds wiki credentials and tools.
 */
import {useCallback, useEffect, useState} from "react";
import {Link, useParams} from "react-router-dom";
import {Layout} from "@/components/Layout";
import {Runs, type RunDetail} from "@/api/runs";
import {
  ResearchAgent,
  streamAgui,
  type AguiMessage,
  type ResearchArtifact,
} from "@/api/researchAgent";
import {ResearchChat} from "@/components/research/ResearchChat";
import {ResearchCanvas} from "@/components/research/ResearchCanvas";
import {ProvenanceHeader} from "@/components/research/ProvenanceHeader";
import {
  applyAguiEvent,
  humanizeAgentError,
  emptyCanvas,
  type AssistantUiState,
} from "@/lib/canvasState";

export default function ResearchAssistant() {
  const {runId} = useParams<{runId: string}>();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [agentUrl, setAgentUrl] = useState("/api/research-agent/agui");
  const [toolGrant, setToolGrant] = useState("");
  const [ui, setUi] = useState<AssistantUiState>({
    messages: [],
    canvas: emptyCanvas,
    artifacts: {},
    streamingText: "",
    busy: false,
    error: null,
  });

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    Runs.get(runId).then((r) => { if (!cancelled) setRun(r); }).catch(() => {});
    return () => { cancelled = true; };
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    ResearchAgent.startSession(runId)
      .then(async (session) => {
        if (cancelled) return;
        setThreadId(session.thread_id);
        setAgentUrl(session.agent_url);
        setToolGrant(session.tool_grant);
        const detail = await ResearchAgent.getThread(session.thread_id);
        if (cancelled) return;
        const artifacts: Record<string, ResearchArtifact> = {};
        for (const art of detail.artifacts) artifacts[art.artifact_key] = art;
        setUi((prev) => ({
          ...prev,
          messages: detail.messages.length ? detail.messages : prev.messages,
          canvas: detail.canvas_state?.artifacts
            ? detail.canvas_state
            : session.canvas_state || emptyCanvas,
          artifacts,
        }));
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setUi((prev) => ({
            ...prev,
            error: humanizeAgentError(
              err instanceof Error ? err.message : "Could not start the research session.",
            ),
          }));
        }
      });
    return () => { cancelled = true; };
  }, [runId]);

  const send = useCallback(async (text: string) => {
    if (!threadId || !toolGrant) return;
    const userMsg: AguiMessage = {id: crypto.randomUUID(), role: "user", content: text};
    const nextMessages = [...ui.messages, userMsg];
    setUi((prev) => ({
      ...prev,
      messages: nextMessages,
      busy: true,
      error: null,
      streamingText: "",
    }));
    try {
      await streamAgui({
        agentUrl,
        toolGrant,
        threadId,
        messages: nextMessages,
        state: ui.canvas,
        onEvent: (event) => {
          setUi((prev) => applyAguiEvent(prev, event));
        },
      });
      const detail = await ResearchAgent.getThread(threadId);
      const artifacts: Record<string, ResearchArtifact> = {};
      for (const art of detail.artifacts) artifacts[art.artifact_key] = art;
      setUi((prev) => ({
        ...prev,
        artifacts,
        canvas: detail.canvas_state || prev.canvas,
        busy: false,
      }));
    } catch (err) {
      setUi((prev) => ({
        ...prev,
        busy: false,
        error: humanizeAgentError(
          err instanceof Error ? err.message : "The agent stream failed.",
        ),
      }));
    }
  }, [agentUrl, threadId, toolGrant, ui.canvas, ui.messages]);

  if (!runId) return null;
  const projectId = run?.project_id ?? "";
  const artifactList = Object.values(ui.artifacts);

  return (
    <Layout>
      <div className="min-h-screen px-4 py-6 max-w-7xl mx-auto space-y-5">
        <div className="kicker">
          {run ? (
            <Link to={`/projects/${run.project_id}`} className="hover:text-ink underline">
              Project
            </Link>
          ) : (
            <span className="text-muted">Project</span>
          )}
          {" · "}
          <Link to={`/runs/${runId}/overview`} className="hover:text-ink underline">Run</Link>
          {" · "}
          <Link to={`/runs/${runId}/wikidata-studio`} className="hover:text-ink underline">Wikidata Studio</Link>
          {" · "}
          <span className="text-biu-sky">Research Assistant</span>
        </div>

        {projectId ? (
          <ProvenanceHeader projectId={projectId} runId={runId} />
        ) : null}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
          <ResearchChat
            messages={ui.messages}
            streamingText={ui.streamingText}
            busy={ui.busy}
            error={ui.error}
            onSend={(text) => { void send(text); }}
          />
          {threadId && projectId ? (
            <ResearchCanvas
              projectId={projectId}
              threadId={threadId}
              artifacts={artifactList}
              activeKey={ui.canvas.active_key}
              onSelect={(key) => {
                setUi((prev) => ({
                  ...prev,
                  canvas: {...prev.canvas, active_key: key},
                }));
              }}
              onSaved={(art) => {
                setUi((prev) => ({
                  ...prev,
                  artifacts: {...prev.artifacts, [art.artifact_key]: art},
                  canvas: {
                    artifacts: [
                      ...prev.canvas.artifacts.filter((a) => a.key !== art.artifact_key),
                      {
                        key: art.artifact_key,
                        kind: art.kind,
                        title: art.title,
                        version: art.version,
                        updated_by: art.created_by,
                      },
                    ],
                    active_key: art.artifact_key,
                  },
                }));
              }}
            />
          ) : (
            <p className="muted text-sm">Loading canvas…</p>
          )}
        </div>
      </div>
    </Layout>
  );
}
