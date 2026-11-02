import {useCallback, useEffect, useRef, useState} from "react";
import {Glass} from "@/components/glass";
import type {AguiMessage} from "@/api/researchAgent";
import type {AssistantUiState} from "@/lib/canvasState";
import {ResearchMarkdown} from "./ResearchMarkdown";

const TOOL_LABELS: Record<string, string> = {
  research_summary: "Corpus summary",
  research_cooccurrence: "Work co-occurrence clusters",
  research_network: "People social network",
  research_ownership: "Manuscript ownership chains",
  research_geography: "Geographic distribution",
  research_provenance: "Provenance timeline",
  research_movement_map: "Provenance movement map",
  research_manuscripts: "Manuscript inventory",
  research_shortest_path: "Shortest semantic path",
  research_neighbors: "Entity neighborhood",
  research_sparql: "SPARQL graph query",
  research_entity: "Entity profile",
  research_evidence: "MARC & authority evidence",
  wikidata_uploaded_items: "Published Wikidata items",
  wikidata_fetch_items: "Live Wikidata claims",
  show_movement_map: "Canvas movement map",
  show_link_types: "Canvas link-type chart",
  show_wikidata_places: "Canvas Wikidata places map",
  show_chart: "Canvas signal chart",
  wikidata_pack: "Wikidata visual pack",
  canvas_upsert_artifact: "Save canvas report",
  canvas_describe: "Inspect canvas state",
  data_info: "Inspect dataset schema",
  data_select: "Filter dataset rows",
  data_distinct: "Rank distinct values",
  data_search: "Search dataset",
  data_agg: "Aggregate dataset metric",
};

function isSimpleScalar(v: unknown): v is string | number | boolean {
  return typeof v === "string" || typeof v === "number" || typeof v === "boolean";
}

function argSummary(args: string): string {
  const trimmed = args.trim();
  if (!trimmed) return "";
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return trimmed.slice(0, 60);
    }
    const parts = Object.entries(parsed)
      .filter(([, v]) => isSimpleScalar(v))
      .slice(0, 3)
      .map(([k, v]) => `${k}=${String(v).slice(0, 36)}`);
    return parts.join(" · ");
  } catch {
    return trimmed.length > 60 ? `${trimmed.slice(0, 60)}…` : trimmed;
  }
}

function ActivityTrack({ui}: {ui: Pick<AssistantUiState, "activity" | "thinking">}) {
  const [open, setOpen] = useState(true);
  const thinking = ui.thinking.at(-1);
  const activeStep = ui.activity.find((a) => !a.done);
  const doneCount = ui.activity.filter((a) => a.done).length;

  if (!thinking && ui.activity.length === 0) return null;

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 space-y-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {activeStep || (thinking && !thinking.done) ? (
            <span aria-hidden className="inline-block h-2 w-2 rounded-full bg-biu-sky animate-ping" />
          ) : (
            <span aria-hidden className="text-emerald-400 font-bold">✓</span>
          )}
          <span className="font-medium text-ink truncate">
            {activeStep
              ? (TOOL_LABELS[activeStep.name] ?? activeStep.name)
              : thinking && !thinking.done
                ? "Reasoning over corpus signals…"
                : `Completed ${doneCount} research step${doneCount === 1 ? "" : "s"}`}
          </span>
        </div>
        {ui.activity.length > 1 ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[11px] text-muted hover:text-ink"
          >
            {open ? "Hide" : `Steps (${ui.activity.length})`}
          </button>
        ) : null}
      </div>

      {thinking ? (
        <div className="text-[11px] italic text-muted/80 border-l-2 border-biu-sky/30 pl-2 max-h-20 overflow-hidden">
          <span className="kicker mr-1">thinking</span>
          <span className="whitespace-pre-wrap">{thinking.text}</span>
          {thinking.done ? null : <span className="animate-pulse"> …</span>}
        </div>
      ) : null}

      {open && ui.activity.length > 0 ? (
        <div className="space-y-1 pt-0.5 border-t border-white/5">
          {ui.activity.map((a) => (
            <div key={a.id} className="text-[11px] text-muted flex items-center gap-2">
              {a.done ? (
                <span aria-hidden className="text-emerald-400">✓</span>
              ) : (
                <span aria-hidden className="text-biu-sky animate-pulse">◌</span>
              )}
              <span className="font-medium text-ink-subtle">
                {TOOL_LABELS[a.name] ?? a.name}
              </span>
              {argSummary(a.args) ? (
                <span className="truncate opacity-70 font-mono text-[10px]">
                  {argSummary(a.args)}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const QUICK_ACTIONS: {label: string; prompt: string}[] = [
  {label: "Overview", prompt: "Give me a corpus overview of the manuscripts"},
  {label: "EDA", prompt: "Run a visual exploratory data analysis (EDA) of the manuscript corpus, production places, people roles, and Wikidata uploads"},
  {label: "Geography", prompt: "Show the geographic distribution and top production places of the manuscripts"},
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
    <div className="space-y-1.5 pt-2">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">
        Suggested next steps
      </div>
      <div className="flex flex-wrap gap-1.5">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="text-xs px-3 py-1.5 rounded-xl border border-biu-sky/35 bg-biu-sky/[0.07] text-biu-sky hover:bg-biu-sky/15 transition-colors text-left leading-snug"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
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
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
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
    <Glass className="flex flex-col h-full min-h-[42rem] gap-3">
      <div className="flex flex-wrap gap-1.5 border-b border-white/10 pb-2.5">
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.label}
            type="button"
            disabled={busy}
            onClick={() => onSend(action.prompt)}
            className="text-xs px-2.5 py-1 rounded-lg border border-white/15 bg-white/[0.03] text-muted hover:text-ink hover:border-biu-sky/40 hover:bg-biu-sky/10 transition-colors disabled:opacity-50"
          >
            {action.label}
          </button>
        ))}
      </div>

      <div
        className="flex-1 overflow-y-auto space-y-3.5 text-sm pr-1"
        data-testid="research-chat-log"
        aria-busy={busy}
      >
        {messages.length === 0 && !busy && !streamingText ? (
          <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-5 text-center space-y-2 my-4">
            <div className="kicker">MHM Research Assistant</div>
            <h3 className="text-base font-semibold text-ink">
              Explore Manuscripts, Provenance &amp; Linked Data
            </h3>
            <p className="text-xs text-muted max-w-md mx-auto leading-relaxed">
              Ask questions across the local RDF graph, project Wikibase, and live Wikidata.
              Visualizations, maps, networks, and sortable tables open automatically on the canvas.
            </p>
          </div>
        ) : null}

        {messages.map((msg, idx) => {
          const isAssistant = msg.role !== "user";
          const {body, suggestions} = isAssistant
            ? splitSuggestions(messageText(msg))
            : {body: messageText(msg), suggestions: []};

          if (!isAssistant) {
            return (
              <div key={idx} className="flex flex-col items-end">
                <div className="max-w-[90%] rounded-2xl rounded-br-sm border border-biu-sky/30 bg-biu-sky/15 px-3.5 py-2.5 text-ink">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-biu-sky mb-0.5">
                    You
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed" dir="auto">{body}</p>
                </div>
              </div>
            );
          }

          return (
            <div key={idx} className="space-y-1.5">
              <div className="rounded-2xl rounded-bl-sm border border-white/10 bg-white/[0.03] p-3.5 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="kicker">Assistant</span>
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard?.writeText(body).catch(() => undefined);
                      setCopiedIdx(idx);
                      window.setTimeout(() => setCopiedIdx((cur) => (cur === idx ? null : cur)), 1500);
                    }}
                    className="text-[11px] text-muted hover:text-ink transition-colors"
                  >
                    {copiedIdx === idx ? "Copied" : "Copy"}
                  </button>
                </div>
                <ResearchMarkdown text={body} variant="chat" />
              </div>
              {!busy && idx === messages.length - 1 ? (
                <SuggestionChips suggestions={suggestions} onPick={onSend} />
              ) : null}
            </div>
          );
        })}

        {busy ? <ActivityTrack ui={{activity, thinking}} /> : null}

        {streamingText ? (
          <div className="rounded-2xl rounded-bl-sm border border-biu-sky/25 bg-white/[0.03] p-3.5 space-y-2">
            <div className="kicker">Assistant</div>
            <ResearchMarkdown text={splitSuggestions(streamingText).body} variant="chat" />
          </div>
        ) : null}

        {busy && !streamingText ? <WaitingDots /> : null}
        <div ref={endRef} />
      </div>

      {error ? (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-warn">
          {error}
        </div>
      ) : null}

      <form
        className="flex gap-2 pt-1 border-t border-white/10"
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
          rows={2}
          disabled={busy}
          dir="auto"
          placeholder="Ask about this corpus, SPARQL, Wikidata, or provenance…"
          className="flex-1 rounded-xl bg-white/5 border border-white/10 px-3 py-2 text-sm text-ink focus:border-biu-sky outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy}
          className="self-end px-4 py-2 rounded-xl bg-biu-sky/20 border border-biu-sky/40 text-biu-sky font-medium text-sm hover:bg-biu-sky/30 transition-colors disabled:opacity-50"
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
      className="rounded-2xl border border-white/10 bg-white/[0.03] px-3.5 py-2.5 text-muted"
      data-testid="research-chat-waiting"
      role="status"
      aria-live="polite"
    >
      <div className="kicker mb-1">Assistant</div>
      <div className="flex items-center gap-1.5 h-5" aria-label="The assistant is working">
        <span className="h-2 w-2 rounded-full bg-biu-sky animate-bounce" style={{animationDelay: "0ms"}} />
        <span className="h-2 w-2 rounded-full bg-biu-sky animate-bounce" style={{animationDelay: "150ms"}} />
        <span className="h-2 w-2 rounded-full bg-biu-sky animate-bounce" style={{animationDelay: "300ms"}} />
      </div>
    </div>
  );
}
