/**
 * Orchestrator route — kick off an LLM-driven planning session and watch
 * the agent's flow live.
 *
 * Layout:
 *   - Goal + budget form
 *   - Animated AgentFlowDiagram (nodes light up as the agent acts)
 *   - Step log (one line per trace event)
 *   - Past-sessions list with a "Replay" affordance
 *
 * The page never sees secrets — the Gemini key is pulled server-side
 * from the per-user encrypted store and passed as env to the
 * eval-agent subprocess (Rule 50).
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Layout } from "@/components/Layout";
import {
  AgentFlowDiagram, makeInitialFlowState, reduceFlow, type FlowState,
} from "@/components/AgentFlowDiagram";
import {
  Orchestrator as OrchestratorApi,
  streamOrchestrator,
  type OrchestratorEvent,
  type SessionListing,
  type SessionTrace,
} from "@/api/orchestrator";


type Mode = "plan_only" | "supervised" | "autonomous";


export default function Orchestrator() {
  const [goal,        setGoal]        = useState(
    "Audit recent eval-agent runs and recommend the next benchmark to focus on.",
  );
  const [mode,        setMode]        = useState<Mode>("plan_only");
  const [useStub,     setUseStub]     = useState(false);
  const [maxSteps,    setMaxSteps]    = useState(12);
  const [maxSeconds,  setMaxSeconds]  = useState(180);
  const [running,     setRunning]     = useState(false);
  const [error,       setError]       = useState<string | null>(null);
  const [events,      setEvents]      = useState<OrchestratorEvent[]>([]);
  const [flow,        setFlow]        = useState<FlowState>(makeInitialFlowState());
  const [finalReport, setFinalReport] = useState<OrchestratorEvent | null>(null);
  const [sessions,    setSessions]    = useState<SessionListing[]>([]);
  const [replay,      setReplay]      = useState<SessionTrace | null>(null);

  const cancelRef = useRef<(() => void) | null>(null);

  // Latest event drives the diagram's animation.
  const lastEvent = events.length > 0 ? events[events.length - 1] : null;

  useEffect(() => { void refreshSessions(); }, []);

  async function refreshSessions(): Promise<void> {
    try {
      const list = await OrchestratorApi.listSessions(20);
      setSessions(list);
    } catch (e) {
      // Non-fatal — the run UI works without the list.
      console.warn("could not list sessions:", e);
    }
  }

  async function start(): Promise<void> {
    if (running) return;
    setRunning(true);
    setError(null);
    setEvents([]);
    setFinalReport(null);
    setFlow(makeInitialFlowState());
    setReplay(null);

    const { events: stream, cancel } = streamOrchestrator({
      goal, mode, max_steps: maxSteps, max_seconds: maxSeconds,
      use_stub_judge: useStub,
    });
    cancelRef.current = cancel;
    try {
      for await (const ev of stream) {
        setEvents((prev) => [...prev, ev]);
        setFlow((prev) => reduceFlow(prev, ev));
        if (ev.type === "session.final") setFinalReport(ev);
        if (ev.type === "session.end") break;
      }
    } catch (e) {
      // Don't show an error when the user explicitly stopped.
      if ((e as Error).name !== "AbortError") {
        setError((e as Error).message);
      }
    } finally {
      cancelRef.current = null;
      setRunning(false);
      void refreshSessions();
    }
  }

  function stop(): void {
    cancelRef.current?.();
  }

  async function openReplay(sessionId: string): Promise<void> {
    try {
      const t = await OrchestratorApi.trace(sessionId);
      setReplay(t);
      // Re-derive flow state by replaying every event through the reducer.
      let s = makeInitialFlowState();
      for (const ev of t.events) s = reduceFlow(s, ev as OrchestratorEvent);
      setEvents(t.events as OrchestratorEvent[]);
      setFlow(s);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-3">
          <div className="kicker">
            AI orchestrator · <Link to="/" className="hover:text-ink underline">back to projects</Link>
          </div>
          <h2 className="text-2xl font-semibold">LLM-driven evaluation orchestrator</h2>
          <p className="muted text-sm leading-relaxed max-w-3xl">
            An LLM brain decides which inspection to run next; Python
            validates and executes only allowlisted, read-only tools.
            Phase 1 (this build) is plan-only — no benchmarks fire, no
            mutations, no source edits. Every action is recorded.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_auto] gap-3 pt-2">
            <textarea value={goal} onChange={(e) => setGoal(e.target.value)}
                      rows={2}
                      className="input-glass !py-2 text-sm font-mono"
                      placeholder="Goal for this session…" />
            <div className="flex flex-col gap-2 text-xs">
              <label className="muted">Mode</label>
              <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}
                      className="input-glass !py-1 text-xs">
                <option value="plan_only">plan_only (Phase 1)</option>
                <option value="supervised">supervised (Phase 2 — empty allowlist)</option>
                <option value="autonomous">autonomous (Phase 4 — empty allowlist)</option>
              </select>
              <label className="muted">Max steps</label>
              <input type="number" min={1} max={64} value={maxSteps}
                     onChange={(e) => setMaxSteps(Math.max(1, parseInt(e.target.value) || 1))}
                     className="input-glass !py-1 text-xs" />
              <label className="muted">Max seconds</label>
              <input type="number" min={10} max={1800} value={maxSeconds}
                     onChange={(e) => setMaxSeconds(Math.max(10, parseInt(e.target.value) || 10))}
                     className="input-glass !py-1 text-xs" />
              <label className="muted flex items-center gap-2">
                <input type="checkbox" checked={useStub}
                       onChange={(e) => setUseStub(e.target.checked)} />
                <span>Stub judge (no LLM — demo)</span>
              </label>
            </div>
            <div className="flex flex-col gap-2 self-stretch justify-end">
              {!running
                ? <button onClick={start} className="button-primary text-sm">Start session</button>
                : <button onClick={stop}  className="button-ghost   text-sm text-yellow-300">Stop</button>}
            </div>
          </div>

          {error && <p className="text-red-300 text-sm">{error}</p>}
        </section>

        {/* Live diagram */}
        <section className="glass p-4">
          <div className="kicker mb-2">Agent flow</div>
          <AgentFlowDiagram lastEvent={lastEvent} flow={flow} />
          <div className="flex items-center gap-4 text-[11px] muted mt-2">
            <span>Step {flow.stepCount}</span>
            <span>·</span>
            <span>{events.length} events</span>
            {flow.finished && <><span>·</span><span className="text-biu-sky">done</span></>}
            {running     && <><span>·</span><span className="text-yellow-300">running…</span></>}
          </div>
        </section>

        {/* Step log */}
        <section className="glass p-4">
          <div className="kicker mb-2">Step log</div>
          <ul className="space-y-1 text-xs font-mono max-h-72 overflow-auto">
            {events.length === 0 && (
              <li className="muted italic">Waiting for the first event…</li>
            )}
            {events.map((ev, i) => (
              <li key={i} className="flex gap-2">
                <span className="muted shrink-0 w-32">{(ev.type as string).padEnd(18)}</span>
                <span className="text-ink/90">{stepLogLine(ev)}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* Final report */}
        {finalReport && (
          <section className="glass p-6">
            <div className="kicker">Final report</div>
            <FinalReport ev={finalReport} />
          </section>
        )}

        {/* Past sessions */}
        <section className="glass p-4">
          <div className="kicker mb-2">Past sessions ({sessions.length})</div>
          <table className="w-full text-xs">
            <thead className="muted text-left">
              <tr className="border-b border-white/10">
                <th className="py-1 pr-3">Session</th>
                <th className="py-1 pr-3">Mode</th>
                <th className="py-1 pr-3">Outcome</th>
                <th className="py-1 pr-3">Goal</th>
                <th className="py-1 pr-1"></th>
              </tr>
            </thead>
            <tbody>
              {sessions.length === 0 && (
                <tr><td colSpan={5} className="muted py-3 italic">No prior sessions.</td></tr>
              )}
              {sessions.map((s) => (
                <tr key={s.session_id} className="border-b border-white/5">
                  <td className="py-1 pr-3 font-mono">{s.session_id}</td>
                  <td className="py-1 pr-3">{s.mode ?? "?"}</td>
                  <td className="py-1 pr-3">
                    <span className={outcomeTone(s.outcome)}>{s.outcome ?? "—"}</span>
                  </td>
                  <td className="py-1 pr-3 truncate max-w-md" title={s.goal ?? ""}>{s.goal ?? "—"}</td>
                  <td className="py-1 pr-1 text-right">
                    <button onClick={() => openReplay(s.session_id)}
                            className="button-ghost !py-0.5 !px-2 text-xs">
                      Replay
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {replay && (
          <section className="glass p-6">
            <div className="kicker">Replay · {replay.session_id}</div>
            <pre className="text-xs whitespace-pre-wrap mt-3"
                 style={{ background: "rgba(0,0,0,0.36)",
                          border: "1px solid var(--line)",
                          borderRadius: 10, padding: 10,
                          maxHeight: "60vh", overflow: "auto" }}>
              {replay.final_report_md || "(no final report on this session)"}
            </pre>
          </section>
        )}
      </div>
    </Layout>
  );
}


// ── Helpers ───────────────────────────────────────────────────────────


function stepLogLine(ev: OrchestratorEvent): string {
  switch (ev.type) {
    case "session.start":  return `mode=${ev.mode} goal=${truncate(ev.goal as string, 80)}`;
    case "llm.turn":       return truncate(String(ev.thought_summary ?? ""), 120);
    case "tool.dispatch":  return `${ev.tool}(${JSON.stringify(ev.args ?? {})})`;
    case "tool.result":    return `${ev.tool} ${ev.ok ? "ok" : "FAIL"} — ${truncate(String(ev.summary ?? ""), 100)}`;
    case "policy.refuse":  return `refused ${ev.tool}: ${ev.reason} (${truncate(String(ev.detail ?? ""), 80)})`;
    case "session.final":  return truncate(String((ev.final as { summary?: string })?.summary ?? ""), 120);
    case "session.end":    return `${ev.outcome} · steps=${ev.steps_used} · wall=${(ev.wall_seconds as number).toFixed(2)}s`;
    case "runner.step":    return String(ev.message ?? "");
    case "runner.exit":    return `exit=${ev.return_code}`;
    case "llm.error":
    case "llm.parse_error":return truncate(String(ev.error ?? ""), 120);
    default:               return JSON.stringify(ev).slice(0, 200);
  }
}


function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}


function outcomeTone(outcome: string | null): string {
  if (outcome === "final")          return "text-biu-sky";
  if (outcome === "step_budget")    return "text-yellow-300";
  if (outcome === "wallclock_budget") return "text-yellow-300";
  if (outcome === "no_progress")    return "text-red-300";
  return "muted";
}


function FinalReport({ ev }: { ev: OrchestratorEvent }) {
  const final = (ev.final ?? {}) as {
    summary?: string;
    recommended_next_steps?: string[];
    risks?: string[];
    commands?: string[];
    evidence_paths?: string[];
  };
  const recs    = final.recommended_next_steps ?? [];
  const risks   = final.risks ?? [];
  const cmds    = final.commands ?? [];
  const sources = final.evidence_paths ?? [];
  return (
    <div className="space-y-3 mt-2 text-sm">
      <p className="text-ink">{final.summary}</p>
      {recs.length > 0 && (
        <div>
          <div className="kicker mb-1">Recommended next steps</div>
          <ul className="list-disc pl-5">
            {recs.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {cmds.length > 0 && (
        <div>
          <div className="kicker mb-1">Commands</div>
          <pre className="text-xs font-mono"
               style={{ background: "rgba(0,0,0,0.32)",
                        border: "1px solid var(--line)",
                        borderRadius: 8, padding: 8 }}>
            {cmds.join("\n")}
          </pre>
        </div>
      )}
      {risks.length > 0 && (
        <div>
          <div className="kicker mb-1">Risks</div>
          <ul className="list-disc pl-5 text-yellow-300/90">
            {risks.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {sources.length > 0 && (
        <div>
          <div className="kicker mb-1">Evidence</div>
          <ul className="list-disc pl-5 font-mono text-xs muted">
            {sources.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
