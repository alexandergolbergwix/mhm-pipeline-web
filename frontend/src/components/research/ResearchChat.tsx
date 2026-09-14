import {useCallback, useEffect, useRef, useState} from "react";
import {Glass} from "@/components/glass";
import type {AguiMessage} from "@/api/researchAgent";

const QUICK_ACTIONS: {label: string; prompt: string}[] = [
  {label: "Overview", prompt: "Give me a corpus overview of the manuscripts"},
  {label: "Clusters", prompt: "Show co-occurring works in the same manuscript"},
  {label: "Network", prompt: "Show the people network of scribes authors and owners"},
  {label: "Movement map", prompt: "Open the manuscript movement map"},
  {label: "SPARQL", prompt: "Run a sample SPARQL query on the HMO graph"},
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
  onSend,
}: {
  messages: AguiMessage[];
  streamingText: string;
  busy: boolean;
  error: string | null;
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({block: "end"});
  }, [busy, messages, streamingText]);

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
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={msg.role === "user" ? "text-ink" : "text-muted"}
          >
            <div className="kicker mb-0.5">{msg.role === "user" ? "You" : "Assistant"}</div>
            {msg.role === "user" ? (
              <p className="whitespace-pre-wrap">{messageText(msg)}</p>
            ) : (
              <ChatMarkdown text={messageText(msg)} />
            )}
          </div>
        ))}
        {streamingText ? (
          <div className="text-muted">
            <div className="kicker mb-0.5">Assistant</div>
            <ChatMarkdown text={streamingText} />
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
