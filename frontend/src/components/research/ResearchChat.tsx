import {useCallback, useEffect, useRef, useState} from "react";
import {Glass} from "@/components/glass";
import type {AguiMessage} from "@/api/researchAgent";
import type {AssistantUiState} from "@/lib/canvasState";

function argSummary(args: string): string {
  const trimmed = args.trim();
  if (!trimmed) return "";
  try {
    const parsed = JSON.parse(trimmed) as Record<string, unknown>;
    const parts = Object.entries(parsed)
      .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
      .slice(0, 3)
      .map(([k, v]) => `${k}=${String(v).slice(0, 40)}`);
    return parts.join(", ");
  } catch {
    return trimmed.length > 60 ? `${trimmed.slice(0, 60)}…` : trimmed;
  }
}

function ActivityTrack({ui}: {ui: Pick<AssistantUiState, "activity" | "thinking">}) {
  const thinking = ui.thinking.at(-1);
  return (
    <div className="space-y-1">
      {thinking ? (
        <div className="text-xs italic text-muted/70 border-l-2 border-white/10 pl-2 max-h-24 overflow-hidden">
          <span className="kicker">thinking </span>
          <span className="whitespace-pre-wrap">{thinking.text}</span>
          {thinking.done ? null : <span className="animate-pulse"> …</span>}
        </div>
      ) : null}
      {ui.activity.map((a) => (
        <div key={a.id} className="text-xs text-muted flex items-center gap-1.5">
          {a.done ? (
            <span aria-hidden>✓</span>
          ) : (
            <span aria-hidden className="animate-pulse">◌</span>
          )}
          <span className="font-medium">{a.name}</span>
          {argSummary(a.args) ? <span className="truncate opacity-70">{argSummary(a.args)}</span> : null}
        </div>
      ))}
    </div>
  );
}

const QUICK_ACTIONS: {label: string; prompt: string}[] = [
  {label: "Overview", prompt: "Give me a corpus overview of the manuscripts"},
  {label: "Clusters", prompt: "Show co-occurring works in the same manuscript"},
  {label: "Network", prompt: "Show the people network of scribes authors and owners"},
  {label: "Movement map", prompt: "Open the manuscript movement map"},
  {label: "SPARQL", prompt: "Run a sample SPARQL query on the HMO graph"},
  {label: "What can you do?", prompt: "What kinds of questions can you answer? List the main capabilities with short examples (corpus, provenance, places, Wikidata links, exports)."},
];

/** Escape HTML, then apply the small markdown subset the planner uses
 * (bold, italic, inline code, links). No raw HTML ever passes through. */
export function renderChatMarkdown(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>',
    )
    .replace(
      /(^|[\s(])((?:https?:\/\/)[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>',
    );
}

/** Splits a trailing "Suggested next: a | b | c" line off the answer body. */
export function splitSuggestions(text: string): {body: string; suggestions: string[]} {
  const marker = "Suggested next:";
  const idx = text.lastIndexOf(marker);
  if (idx === -1) return {body: text, suggestions: []};
  const body = text.slice(0, idx).trimEnd();
  const suggestions = text
    .slice(idx + marker.length)
    .split("|")
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 3);
  return {body, suggestions};
}

function SuggestionChips({suggestions, onPick}: {suggestions: string[]; onPick: (text: string) => void}) {
  if (suggestions.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-2">
      {suggestions.map((s) => (
        <button
          key={s}
          type="button"
          onClick={() => onPick(s)}
          className="text-xs px-2.5 py-1 rounded-full border border-biu-sky/40 text-biu-sky hover:bg-biu-sky/10 transition-colors text-left"
        >
          {s}
        </button>
      ))}
    </div>
  );
}

function ChatMarkdown({text}: {text: string}) {
  const html = text
    .split(/\n{2,}/)
    .map((para) =>
      `<p>${para.split("\n").map((line) => renderChatMarkdown(line)).join("<br/>")}</p>`,
    )
    .join("");
  return <div className="space-y-2" dangerouslySetInnerHTML={{__html: html}} />;
}

export function ResearchChat({
  messages,
  streamingText,
  busy,
  error,
  activity,
  thinking,
  onSend,
}: {
  messages: AguiMessage[];
  streamingText: string;
  busy: boolean;
  error: string | null;
  activity: AssistantUiState["activity"];
  thinking: AssistantUiState["thinking"];
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({block: "end"});
  }, [busy, messages, streamingText, activity]);

  const submit = useCallback(() => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  }, [busy, draft, onSend]);

  return (
    <Glass className="flex flex-col h-full min-h-[32rem] gap-3">
      <div className="flex flex-wrap gap-1">
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.label}
            type="button"
            disabled={busy}
            onClick={() => onSend(action.prompt)}
            className="text-xs px-2 py-1 rounded-lg border border-white/15 text-muted hover:text-ink hover:bg-white/5"
          >
            {action.label}
          </button>
        ))}
      </div>
      <div
        className="flex-1 overflow-y-auto space-y-3 text-sm"
        data-testid="research-chat-log"
        aria-busy={busy}
      >
        {messages.map((msg, idx) => {
          const isAssistant = msg.role !== "user";
          const {body, suggestions} = isAssistant
            ? splitSuggestions(messageText(msg))
            : {body: messageText(msg), suggestions: []};
          return (
            <div
              key={idx}
              className={msg.role === "user" ? "text-ink" : "text-muted"}
            >
              <div className="kicker mb-0.5">{msg.role === "user" ? "You" : "Assistant"}</div>
              {msg.role === "user" ? (
                <p className="whitespace-pre-wrap">{body}</p>
              ) : (
                <>
                  <ChatMarkdown text={body} />
                  {!busy && idx === messages.length - 1 ? (
                    <SuggestionChips suggestions={suggestions} onPick={onSend} />
                  ) : null}
                </>
              )}
            </div>
          );
        })}
        {busy ? <ActivityTrack ui={{activity, thinking}} /> : null}
        {streamingText ? (
          <div className="text-muted">
            <div className="kicker mb-0.5">Assistant</div>
            <ChatMarkdown text={splitSuggestions(streamingText).body} />
          </div>
        ) : null}
        {busy && !streamingText ? <WaitingDots /> : null}
        <div ref={endRef} />
      </div>
      {error ? <p className="text-xs text-warn">{error}</p> : null}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="sr-only" htmlFor="research-chat-input">Message</label>
        <textarea
          id="research-chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={3}
          disabled={busy}
          placeholder="Ask about this corpus, SPARQL, Wikidata, or provenance…"
          className="flex-1 rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-ink disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy}
          className="self-end px-3 py-2 rounded-lg bg-biu-sky/20 text-biu-sky text-sm disabled:opacity-50"
        >
          {busy ? "…" : "Send"}
        </button>
      </form>
    </Glass>
  );
}

function messageText(msg: AguiMessage): string {
  if (typeof msg.content === "string") return msg.content;
  return msg.content.map((part) => part.text || "").join("");
}

function WaitingDots() {
  return (
    <div
      className="text-muted"
      data-testid="research-chat-waiting"
      role="status"
      aria-live="polite"
    >
      <div className="kicker mb-0.5">Assistant</div>
      <div className="flex items-center gap-1.5 h-5" aria-label="The assistant is working">
        <span className="h-2 w-2 rounded-full bg-biu-sky animate-bounce" style={{animationDelay: "0ms"}} />
        <span className="h-2 w-2 rounded-full bg-biu-sky animate-bounce" style={{animationDelay: "150ms"}} />
        <span className="h-2 w-2 rounded-full bg-biu-sky animate-bounce" style={{animationDelay: "300ms"}} />
      </div>
    </div>
  );
}
