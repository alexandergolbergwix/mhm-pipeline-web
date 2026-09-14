/**
 * Research Assistant — chat + editable canvas at
 * /runs/:runId/linked-data-explorer
 *
 * Replaces the tabbed Linked Data Explorer. The agent runs on Modal
 * (or the local AG-UI stub). Heroku holds wiki credentials and tools.
 */
import {useCallback, useEffect, useRef, useState} from "react";
import {Link, useParams} from "react-router-dom";
import {Layout} from "@/components/Layout";
import {Runs, type RunDetail} from "@/api/runs";
import {
  ResearchAgent,
  streamAgui,
  startAguiAsync,
  type AguiMessage,
  type ResearchArtifact,
} from "@/api/researchAgent";
import {ResearchChat} from "@/components/research/ResearchChat";
import {ResearchCanvas} from "@/components/research/ResearchCanvas";
import {ProvenanceHeader} from "@/components/research/ProvenanceHeader";
import {ResearchThreadBar} from "@/components/research/ResearchThreadBar";
import {
  applyAguiEvent,
  humanizeAgentError,
  emptyCanvas,
  type AssistantUiState,
} from "@/lib/canvasState";

const DEFAULT_TITLE = "Research session";
const threadStorageKey = (runId: string) => `mhm-research-thread:${runId}`;

export default function ResearchAssistant() {
  const {runId} = useParams<{runId: string}>();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threadTitle, setThreadTitle] = useState<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [agentUrl, setAgentUrl] = useState("/api/research-agent/agui");
  const [agentMode, setAgentMode] = useState("local");
  const [toolGrant, setToolGrant] = useState("");
  const [ui, setUi] = useState<AssistantUiState>({
    messages: [],
    canvas: emptyCanvas,
    artifacts: {},
    streamingText: "",
    busy: false,
    error: null,
  });
  const autoTitledRef = useRef(false);

  const applyThreadDetail = useCallback(
    (detail: {title: string; messages: AguiMessage[]; canvas_state: AssistantUiState["canvas"]; artifacts: ResearchArtifact[]}) => {
      const artifacts: Record<string, ResearchArtifact> = {};
      for (const art of detail.artifacts) artifacts[art.artifact_key] = art;
      setThreadTitle(detail.title);
      autoTitledRef.current = detail.title !== DEFAULT_TITLE;
      setUi((prev) => ({
        ...prev,
        messages: detail.messages,
        canvas: detail.canvas_state?.artifacts ? detail.canvas_state : emptyCanvas,
        artifacts,
      }));
    },
    [],
  );

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    Runs.get(runId).then((r) => { if (!cancelled) setRun(r); }).catch(() => {});
    return () => { cancelled = true; };
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const stored = localStorage.getItem(threadStorageKey(runId));
    const boot = async (threadId?: string, retryOnMissing = false) => {
      try {
        const session = await ResearchAgent.startSession(runId, {threadId});
        if (cancelled) return;
        setThreadId(session.thread_id);
        setAgentUrl(session.agent_url);
      setAgentMode(session.agent_mode);
        setToolGrant(session.tool_grant);
        localStorage.setItem(threadStorageKey(runId), session.thread_id);
        const detail = await ResearchAgent.getThread(session.thread_id);
        if (cancelled) return;
        applyThreadDetail(detail);
      } catch (err) {
        // A stored thread may have been deleted — fall back to resume/latest.
        if (retryOnMissing) {
          localStorage.removeItem(threadStorageKey(runId));
          await boot(undefined);
          return;
        }
        if (!cancelled) {
          setUi((prev) => ({
            ...prev,
            error: humanizeAgentError(
              err instanceof Error ? err.message : "Could not start the research session.",
            ),
          }));
        }
      }
    };
    void boot(stored ?? undefined, Boolean(stored));
    return () => { cancelled = true; };
  }, [runId, applyThreadDetail]);

  const saveTranscript = useCallback(async (
    threadId: string,
    messages: AguiMessage[],
  ) => {
    try {
      await ResearchAgent.patchThread(threadId, {messages});
      const detail = await ResearchAgent.getThread(threadId);
      setThreadTitle(detail.title);
      if (!autoTitledRef.current && messages.some((m) => m.role === "user")) {
        autoTitledRef.current = true;
        try {
          const {title} = await ResearchAgent.autoTitle(threadId);
          setThreadTitle(title);
        } catch {
          /* keep the fallback title */
        }
      }
      setHistoryKey((k) => k + 1);
    } catch {
      /* transcript save is best-effort */
    }
  }, []);

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
      let finalMessages = nextMessages;
      const onEvent = (event: Record<string, unknown>) => {
        setUi((prev) => {
          const next = applyAguiEvent(prev, event);
          if (next.messages.length) finalMessages = next.messages;
          return next;
        });
      };
      if (agentMode === "modal") {
        await startAguiAsync({
          toolGrant,
          threadId,
          messages: nextMessages,
          state: ui.canvas,
          onEvent,
        });
      } else {
        await streamAgui({
          agentUrl,
          toolGrant,
          threadId,
          messages: nextMessages,
          state: ui.canvas,
          onEvent,
        });
      }
      const detail = await ResearchAgent.getThread(threadId);
      const artifacts: Record<string, ResearchArtifact> = {};
      for (const art of detail.artifacts) artifacts[art.artifact_key] = art;
      setUi((prev) => ({
        ...prev,
        artifacts,
        canvas: detail.canvas_state || prev.canvas,
        busy: false,
      }));
      void saveTranscript(threadId, finalMessages);
    } catch (err) {
      setUi((prev) => ({
        ...prev,
        busy: false,
        error: humanizeAgentError(
          err instanceof Error ? err.message : "The agent stream failed.",
        ),
      }));
    } finally {
      // Tool upserts happen server-side and survive stream failures —
      // refresh the canvas even when the stream errors (production
      // incident 2026-09-14: an H18 cut hid a freshly placed map).
      try {
        const detail = await ResearchAgent.getThread(threadId);
        const artifacts: Record<string, ResearchArtifact> = {};
        for (const art of detail.artifacts) artifacts[art.artifact_key] = art;
        setUi((prev) => ({
          ...prev,
          artifacts,
          canvas: detail.canvas_state || prev.canvas,
          busy: false,
        }));
      } catch {
        /* best-effort refresh */
      }
    }
  }, [agentUrl, agentMode, threadId, toolGrant, runId, saveTranscript, ui.canvas, ui.messages]);

  const newChat = useCallback(async () => {
    if (!runId) return;
    try {
      const session = await ResearchAgent.startSession(runId, {newThread: true});
      setThreadId(session.thread_id);
      setAgentUrl(session.agent_url);
      setAgentMode(session.agent_mode);
      setToolGrant(session.tool_grant);
      localStorage.setItem(threadStorageKey(runId), session.thread_id);
      setThreadTitle(DEFAULT_TITLE);
      autoTitledRef.current = false;
      setUi({
        messages: [],
        canvas: emptyCanvas,
        artifacts: {},
        streamingText: "",
        busy: false,
        error: null,
      });
      setHistoryKey((k) => k + 1);
    } catch (err) {
      setUi((prev) => ({
        ...prev,
        error: humanizeAgentError(
          err instanceof Error ? err.message : "Could not start a new chat.",
        ),
      }));
    }
  }, [runId]);

  const selectThread = useCallback(async (id: string) => {
    if (!runId) return;
    try {
      const session = await ResearchAgent.startSession(runId, {threadId: id});
      setThreadId(session.thread_id);
      setAgentUrl(session.agent_url);
      setAgentMode(session.agent_mode);
      setToolGrant(session.tool_grant);
      localStorage.setItem(threadStorageKey(runId), session.thread_id);
      const detail = await ResearchAgent.getThread(session.thread_id);
      applyThreadDetail(detail);
    } catch (err) {
      setUi((prev) => ({
        ...prev,
        error: humanizeAgentError(
          err instanceof Error ? err.message : "Could not open that chat.",
        ),
      }));
    }
  }, [runId, applyThreadDetail]);

  const renameThread = useCallback(async (title: string) => {
    if (!threadId) return;
    setThreadTitle(title);
    try {
      await ResearchAgent.patchThread(threadId, {title});
      setHistoryKey((k) => k + 1);
    } catch {
      /* rename is best-effort; local title already updated */
    }
  }, [threadId]);

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

        <ResearchThreadBar
          projectId={projectId}
          runId={runId}
          threadId={threadId}
          title={threadTitle}
          busy={ui.busy}
          refreshKey={historyKey}
          onSelectThread={(id) => { void selectThread(id); }}
          onNewChat={() => { void newChat(); }}
          onRename={(title) => { void renameThread(title); }}
        />

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
