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
    endRef.current?.scrollIntoView({block: "end"});
  }, [messages, streamingText]);

  const submit = useCallback(() => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  }, [busy, draft, onSend]);

  return (
    <Glass className="flex flex-col h-full min-h-[32rem] p-3 gap-3">
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
      <div className="flex-1 overflow-y-auto space-y-3 text-sm" data-testid="research-chat-log">
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={msg.role === "user" ? "text-ink" : "text-muted"}
          >
            <div className="kicker mb-0.5">{msg.role === "user" ? "You" : "Assistant"}</div>
            <p className="whitespace-pre-wrap">{messageText(msg)}</p>
          </div>
        ))}
        {streamingText ? (
          <div className="text-muted">
            <div className="kicker mb-0.5">Assistant</div>
            <p className="whitespace-pre-wrap">{streamingText}</p>
          </div>
        ) : null}
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
          className="flex-1 rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm text-ink"
        />
        <button
          type="submit"
          disabled={busy}
          className="self-end px-3 py-2 rounded-lg bg-biu-sky/20 text-biu-sky text-sm"
        >
          Send
        </button>
      </form>
    </Glass>
  );
}

function messageText(msg: AguiMessage): string {
  if (typeof msg.content === "string") return msg.content;
  return msg.content.map((part) => part.text || "").join("");
}
