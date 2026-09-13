/**
 * Thread history bar for the Research Assistant: New chat, current title
 * (inline rename), and a dropdown of past chats for this run.
 */
import {useCallback, useEffect, useRef, useState} from "react";
import {ResearchAgent, type ThreadSummary} from "@/api/researchAgent";

interface Props {
  projectId: string;
  runId: string;
  threadId: string | null;
  title: string | null;
  busy: boolean;
  refreshKey: number;
  onSelectThread: (threadId: string) => void;
  onNewChat: () => void;
  onRename: (title: string) => void;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const hours = diffMs / 3_600_000;
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  return d.toLocaleDateString();
}

export function ResearchThreadBar({
  projectId,
  runId,
  threadId,
  title,
  busy,
  refreshKey,
  onSelectThread,
  onNewChat,
  onRename,
}: Props) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [loaded, setLoaded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await ResearchAgent.listThreads(projectId, runId);
      setThreads(list);
    } catch {
      /* history is best-effort */
    } finally {
      setLoaded(true);
    }
  }, [projectId, runId]);

  useEffect(() => {
    void refresh();
  }, [refresh, threadId, refreshKey]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const startRename = () => {
    setDraft(title ?? "");
    setEditing(true);
  };

  const commitRename = () => {
    setEditing(false);
    const next = draft.trim();
    if (next && next !== title) onRename(next);
  };

  return (
    <div className="flex items-center gap-2 flex-wrap relative">
      <button
        type="button"
        className="button-ghost text-sm py-1.5"
        onClick={onNewChat}
        disabled={busy}
      >
        + New chat
      </button>

      {threadId && editing ? (
        <span className="flex items-center gap-1">
          <input
            ref={inputRef}
            className="input-glass text-sm py-1 w-64"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") setEditing(false);
            }}
          />
          <button type="button" className="button-ghost text-sm py-1" onClick={commitRename}>
            Save
          </button>
          <button type="button" className="button-ghost text-sm py-1" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </span>
      ) : (
        <button
          type="button"
          className="text-ink font-medium text-sm px-2 py-1 rounded-lg hover:bg-[var(--ghost-hover)] transition"
          onClick={startRename}
          title="Rename chat"
          disabled={!threadId}
        >
          {title || "Chat"}
          <span className="text-ink-faint ml-1.5">✎</span>
        </button>
      )}

      <button
        type="button"
        className="button-ghost text-sm py-1.5 ml-auto"
        onClick={() => setOpen((v) => !v)}
        disabled={!loaded}
      >
        History {threads.length ? `(${threads.length})` : ""}
      </button>

      {open ? (
        <div className="absolute right-0 top-full mt-1 z-20 w-80 max-h-80 overflow-auto glass rounded-2xl p-2">
          {threads.length === 0 ? (
            <p className="muted text-sm px-2 py-1">No past chats yet.</p>
          ) : (
            threads.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`block w-full text-left px-2 py-1.5 rounded-lg text-sm transition hover:bg-[var(--ghost-hover)] ${
                  t.id === threadId ? "bg-[var(--nav-active-bg)]" : ""
                }`}
                onClick={() => {
                  setOpen(false);
                  if (t.id !== threadId) onSelectThread(t.id);
                }}
              >
                <span className="block truncate">{t.title}</span>
                <span className="text-ink-faint text-xs">
                  {t.message_count} messages · {formatWhen(t.updated_at)}
                </span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
