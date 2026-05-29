import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import {
  Studio,
  type ReconcileOutcome,
  type StudioBuild,
  type StudioItem,
  type UploadOutcome,
} from "@/api/wikidataStudio";

type EntityFilter = "all" | "manuscript" | "person" | "work";
type ExistFilter  = "any" | "existing" | "new";
type SortKey      = "label" | "statements" | "entity_type" | "wikidata";


export default function WikidataStudio() {
  const { runId } = useParams<{ runId: string }>();
  const [build, setBuild] = useState<StudioBuild | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [approvedOnly, setApprovedOnly] = useState(false);

  // search + filter + sort state
  const [query, setQuery]               = useState("");
  const [entityFilter, setEntityFilter] = useState<EntityFilter>("all");
  const [existFilter, setExistFilter]   = useState<ExistFilter>("any");
  const [minStmts, setMinStmts]         = useState<number>(0);
  const [sortKey, setSortKey]           = useState<SortKey>("label");
  const [sortDesc, setSortDesc]         = useState(false);

  const [selectedIdx, setSelectedIdx] = useState(0);

  // reconcile/upload state
  const [reconcileMap, setReconcileMap] = useState<Record<string, ReconcileOutcome>>({});
  const [uploadMap, setUploadMap]       = useState<Record<string, UploadOutcome>>({});
  const [busy, setBusy] = useState<"reconcile" | "dry" | "live" | null>(null);
  const [lastUpload, setLastUpload] = useState<{
    dry_run: boolean; moratorium_lifted: boolean; test_mode: boolean;
  } | null>(null);

  async function refresh(nextApprovedOnly?: boolean) {
    if (!runId) return;
    const flag = nextApprovedOnly ?? approvedOnly;
    setLoading(true); setError(null);
    try { setBuild(await Studio.build(runId, flag)); }
    catch (e) { setError(e instanceof ApiError ? e.detail : String(e)); }
    finally   { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, [runId]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function reconcile() {
    if (!runId) return;
    setBusy("reconcile"); setError(null);
    try {
      const r = await Studio.reconcile(runId, approvedOnly);
      const map: Record<string, ReconcileOutcome> = {};
      r.outcomes.forEach((o, i) => { map[`${o.entity_type}:${i}`] = o; });
      setReconcileMap(map);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); }
  }

  async function doUpload(dry: boolean) {
    if (!runId) return;
    setBusy(dry ? "dry" : "live"); setError(null);
    try {
      const r = await Studio.upload(runId, { dry_run: dry, approved_only: approvedOnly });
      const map: Record<string, UploadOutcome> = {};
      r.outcomes.forEach((o, i) => { map[`${o.entity_type}:${i}`] = o; });
      setUploadMap(map);
      setLastUpload({
        dry_run: r.dry_run, moratorium_lifted: r.moratorium_lifted, test_mode: r.test_mode,
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(null); }
  }

  // ── filtered + sorted items ───────────────────────────────────────────
  const view = useMemo(() => {
    if (!build) return [] as Array<{ it: StudioItem; idx: number; key: string }>;
    const raw = build.items.map((it, idx) => ({
      it, idx, key: `${it.entity_type ?? "other"}:${idx}`,
    }));
    const q = query.trim().toLowerCase();
    const filtered = raw.filter(({ it }) => {
      if (entityFilter !== "all" && it.entity_type !== entityFilter) return false;
      const hasQid = !!it.existing_qid;
      if (existFilter === "existing" && !hasQid) return false;
      if (existFilter === "new"      &&  hasQid) return false;
      const stmts = (it.statements ?? []).length;
      if (stmts < minStmts) return false;
      if (q) {
        const haystack = [
          ...Object.values(it.labels ?? {}),
          ...Object.values(it.descriptions ?? {}),
          ...Object.values(it.aliases ?? {}).flat(),
          it.existing_qid ?? "",
          it.entity_type ?? "",
        ].join(" ").toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    filtered.sort((a, b) => cmp(a.it, b.it, sortKey, sortDesc));
    return filtered;
  }, [build, query, entityFilter, existFilter, minStmts, sortKey, sortDesc]);

  if (error) return <Layout><div className="glass p-6 text-red-300">{error}</div></Layout>;
  if (!build) return <Layout><p className="muted">{loading ? "Building items…" : "Loading…"}</p></Layout>;

  const current = view.length > 0 ? view[Math.min(selectedIdx, view.length - 1)].it : null;
  const currentKey = view.length > 0 ? view[Math.min(selectedIdx, view.length - 1)].key : "";

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-2">
          <div className="kicker">
            Wikidata Studio · <Link to={`/runs/${runId}`} className="hover:text-ink underline">back to run</Link>
          </div>
          <h2 className="text-2xl font-semibold">Items ready to upload</h2>
          <p className="muted text-sm leading-relaxed max-w-2xl">
            Every candidate match feeds the builder by default. Flip
            <b className="text-ink"> Approved only</b> before exporting
            the final QuickStatements. <b className="text-ink">Reconcile</b>{" "}
            SPARQL-queries Wikidata so duplicates show up before upload;
            <b className="text-ink"> Upload</b> writes through the same{" "}
            four guards that protect against modifying items you didn't
            create.
          </p>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 pt-2">
            <Stat label="Records" value={build.record_count} />
            <Stat label="Matches fed" value={build.used_match_count}
                  sub={`${build.approved_match_count} approved · ${build.pending_match_count} pending`} />
            <Stat label="Items" value={build.summary.total_items} highlight />
            <Stat label="Statements" value={build.summary.statements} />
            <Stat label="MS / P / W"
                  value={`${build.summary.manuscripts} / ${build.summary.persons} / ${build.summary.works}`} />
          </div>

          {/* Action row */}
          <div className="flex flex-wrap gap-2 pt-2 items-center">
            <div className="glass-pill px-1 py-1 flex gap-1 text-xs">
              <button
                onClick={() => { setApprovedOnly(false); void refresh(false); }}
                className={`px-3 py-1 rounded-full transition ${
                  !approvedOnly ? "bg-white/12 text-ink" : "muted hover:text-ink"
                }`}>All matches</button>
              <button
                onClick={() => { setApprovedOnly(true); void refresh(true); }}
                className={`px-3 py-1 rounded-full transition ${
                  approvedOnly ? "bg-white/12 text-ink" : "muted hover:text-ink"
                }`}>Approved only</button>
            </div>
            <button onClick={() => refresh()} disabled={loading || !!busy} className="button-ghost text-sm">
              {loading ? "Rebuilding…" : "Rebuild"}
            </button>
            <button onClick={reconcile} disabled={!!busy} className="button-ghost text-sm">
              {busy === "reconcile" ? "Reconciling…" : "Reconcile with Wikidata"}
            </button>
            <button onClick={() => doUpload(true)} disabled={!!busy} className="button-primary text-sm">
              {busy === "dry" ? "Running…" : "Dry-run upload"}
            </button>
            <button onClick={() => doUpload(false)} disabled={!!busy} className="button-ghost text-sm text-yellow-300">
              {busy === "live" ? "Uploading…" : "Live upload"}
            </button>
            <a href={Studio.qsUrl(runId!, approvedOnly)} download className="button-ghost text-sm">
              Download QuickStatements.txt
            </a>
          </div>

          {lastUpload && (
            <p className="text-xs muted pt-2">
              Last upload: {lastUpload.dry_run ? "dry-run" : "live"}
              {!lastUpload.dry_run && (
                <> · moratorium {lastUpload.moratorium_lifted ? "lifted" : "in effect"}
                  {lastUpload.test_mode && " · test.wikidata.org"}</>
              )}
            </p>
          )}
        </section>

        {build.summary.total_items === 0 ? (
          <section className="glass p-6 text-center muted">
            {build.used_match_count === 0 && approvedOnly
              ? <>No approved matches yet. Either click <b className="text-ink">All matches</b> above
                  to preview, or approve rows on the{" "}
                  <Link to={`/runs/${runId}`} className="text-biu-sky hover:underline">Review</Link> page.</>
              : <>No items yet. Upload a MARC file via the project page first.</>}
          </section>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">

            {/* Left: advanced search + filter + sort + list */}
            <aside className="glass p-3 max-h-[78vh] overflow-auto space-y-3">
              <input
                value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="Search…"
                className="input-glass !py-1.5 text-sm" />

              <div className="grid grid-cols-2 gap-2 text-xs">
                {(["all", "manuscript", "person", "work"] as EntityFilter[]).map((f) => (
                  <Chip key={f} active={entityFilter === f} onClick={() => setEntityFilter(f)}>
                    {f}
                  </Chip>
                ))}
              </div>

              <div className="text-xs space-y-1.5">
                <div className="flex items-center gap-2">
                  <label className="muted text-[11px] w-20 shrink-0">On Wikidata</label>
                  <select className="input-glass !py-1 text-xs flex-1"
                          value={existFilter}
                          onChange={(e) => setExistFilter(e.target.value as ExistFilter)}>
                    <option value="any">any</option>
                    <option value="existing">already exists</option>
                    <option value="new">not yet</option>
                  </select>
                </div>
                <div className="flex items-center gap-2">
                  <label className="muted text-[11px] w-20 shrink-0">Min stmts</label>
                  <input type="number" min={0} value={minStmts}
                         onChange={(e) => setMinStmts(Math.max(0, parseInt(e.target.value) || 0))}
                         className="input-glass !py-1 text-xs flex-1" />
                </div>
                <div className="flex items-center gap-2">
                  <label className="muted text-[11px] w-20 shrink-0">Sort</label>
                  <select className="input-glass !py-1 text-xs flex-1"
                          value={sortKey}
                          onChange={(e) => setSortKey(e.target.value as SortKey)}>
                    <option value="label">label</option>
                    <option value="statements"># statements</option>
                    <option value="entity_type">entity type</option>
                    <option value="wikidata">existing QID</option>
                  </select>
                  <button onClick={() => setSortDesc((v) => !v)}
                          title={sortDesc ? "descending" : "ascending"}
                          className="button-ghost !py-0.5 !px-2 text-xs">
                    {sortDesc ? "↓" : "↑"}
                  </button>
                </div>
              </div>

              <div className="border-t border-white/5 pt-2 text-[10px] muted uppercase tracking-wider">
                {view.length} of {build.items.length}
              </div>

              <ul>
                {view.map(({ it, key }, listIdx) => {
                  const rec = reconcileMap[key];
                  const up  = uploadMap[key];
                  return (
                    <li key={key}>
                      <button onClick={() => setSelectedIdx(listIdx)}
                              className={`block w-full text-left px-2 py-2 rounded-lg transition ${
                                listIdx === selectedIdx
                                  ? "bg-white/8 text-ink"
                                  : "muted hover:text-ink hover:bg-white/5"
                              }`}>
                        <div className="flex justify-between gap-2 items-baseline">
                          <span className="text-sm truncate">
                            {labelOf(it) || "(no label)"}
                          </span>
                          <span className="kicker shrink-0">{it.entity_type ?? "?"}</span>
                        </div>
                        <div className="flex items-center gap-1 flex-wrap mt-0.5 text-[10px]">
                          <span className="muted">{(it.statements ?? []).length} stmts</span>
                          {it.existing_qid && (
                            <span className="text-biu-sky font-mono">↻{it.existing_qid}</span>
                          )}
                          {rec?.existing_qid && !it.existing_qid && (
                            <span className="text-yellow-300 font-mono" title={rec.message}>
                              found {rec.existing_qid}
                            </span>
                          )}
                          {up && (
                            <span className={statusTone(up.status)} title={up.message}>
                              {up.status}
                            </span>
                          )}
                        </div>
                      </button>
                    </li>
                  );
                })}
                {view.length === 0 && (
                  <p className="muted text-sm italic px-2">Nothing matches your filter.</p>
                )}
              </ul>
            </aside>

            {/* Right: detail */}
            <main>
              {current && (
                <ItemPanel
                  item={current}
                  reconcile={reconcileMap[currentKey]}
                  upload={uploadMap[currentKey]} />
              )}
            </main>
          </div>
        )}

        <details className="glass p-6">
          <summary className="cursor-pointer kicker">
            QuickStatements output ({build.quickstatements.length.toLocaleString()} chars)
          </summary>
          <pre className="text-[11px] font-mono whitespace-pre-wrap mt-3"
               style={{ background: "rgba(0,0,0,0.36)", border: "1px solid var(--line)", borderRadius: 10, padding: 10, maxHeight: "60vh", overflow: "auto" }}>
            {build.quickstatements || "(empty)"}
          </pre>
        </details>
      </div>
    </Layout>
  );
}


// ── components ──────────────────────────────────────────────────────────


function ItemPanel({
  item, reconcile, upload,
}: {
  item: StudioItem;
  reconcile?: ReconcileOutcome;
  upload?: UploadOutcome;
}) {
  const labels = item.labels ?? {};
  const descriptions = item.descriptions ?? {};
  const aliases = item.aliases ?? {};
  const statements = item.statements ?? [];

  return (
    <div className="glass p-6 space-y-5 max-h-[78vh] overflow-auto">
      <header className="space-y-1">
        <div className="kicker">{item.entity_type ?? "item"}</div>
        <h3 className="text-xl font-semibold">{labelOf(item) || "(no label)"}</h3>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {item.existing_qid && (
            <a className="text-biu-sky font-mono hover:underline"
               href={`https://www.wikidata.org/wiki/${item.existing_qid}`}
               target="_blank" rel="noreferrer">
              ↻ updates {item.existing_qid}
            </a>
          )}
          {reconcile && (
            <span className="glass-pill px-2 py-0.5 text-xs">
              reconcile: {reconcile.existing_qid
                ? <>found <span className="font-mono text-biu-sky">{reconcile.existing_qid}</span> via {reconcile.method}</>
                : <span className="muted">no Wikidata match</span>}
            </span>
          )}
          {upload && (
            <span className={`glass-pill px-2 py-0.5 text-xs ${statusTone(upload.status)}`} title={upload.message}>
              upload: {upload.status}
              {upload.qid && <span className="font-mono ml-1">{upload.qid}</span>}
            </span>
          )}
        </div>
      </header>

      {Object.keys(labels).length > 0 && (
        <Section title="Labels"><KvList items={labels} /></Section>
      )}
      {Object.keys(descriptions).length > 0 && (
        <Section title="Descriptions"><KvList items={descriptions} /></Section>
      )}
      {Object.keys(aliases).length > 0 && (
        <Section title="Aliases">
          {Object.entries(aliases).map(([lang, list]) => (
            <p key={lang} className="text-sm">
              <span className="kicker mr-2">{lang}</span>{list.join(" · ")}
            </p>
          ))}
        </Section>
      )}

      <Section title={`Statements (${statements.length})`}>
        <ul className="space-y-2">
          {statements.map((s, i) => (
            <li key={i} className="glass-pill px-3 py-2 text-sm">
              <div className="flex items-baseline gap-2 flex-wrap">
                <PropertyPill p={s.property ?? s.property_id} />
                <span className="font-mono text-xs">
                  {renderValue(s.value, s.value_id, s.value_type)}
                </span>
                {s.rank && s.rank !== "normal" && (
                  <span className="ml-2 kicker text-biu-sky">{s.rank}</span>
                )}
              </div>
              {(s.qualifiers && (s.qualifiers as unknown[]).length > 0) && (
                <p className="muted text-xs mt-1">{(s.qualifiers as unknown[]).length} qualifier(s)</p>
              )}
              {(s.references && (s.references as unknown[]).length > 0) && (
                <p className="muted text-xs">{(s.references as unknown[]).length} reference(s)</p>
              )}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}


function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
            className={`px-2 py-1 rounded-md transition text-[11px] text-center border border-transparent ${
              active
                ? "bg-white/12 text-ink border-white/15"
                : "muted hover:text-ink hover:bg-white/5"
            }`}>{children}</button>
  );
}


function PropertyPill({ p }: { p?: string }) {
  if (!p) return <span className="muted">P?</span>;
  return (
    <a href={`https://www.wikidata.org/wiki/Property:${p}`}
       target="_blank" rel="noreferrer"
       className="text-biu-sky font-mono text-xs hover:underline">{p}</a>
  );
}


function renderValue(v: unknown, vid?: string, kind?: string): string {
  if (kind === "somevalue") return "(somevalue)";
  if (kind === "novalue")   return "(novalue)";
  if (vid) return vid;
  if (typeof v === "string") return v;
  if (v == null) return "—";
  return JSON.stringify(v);
}


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="kicker mb-2">{title}</div>
      {children}
    </section>
  );
}


function KvList({ items }: { items: Record<string, string> }) {
  return (
    <dl className="grid grid-cols-[60px_1fr] gap-x-3 text-sm">
      {Object.entries(items).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="kicker">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}


function Stat({
  label, value, highlight, sub,
}: { label: string; value: number | string; highlight?: boolean; sub?: string }) {
  return (
    <div className="glass-pill px-3 py-2">
      <div className="kicker">{label}</div>
      <div className={`text-xl font-semibold ${highlight ? "text-biu-sky" : ""}`}>{value}</div>
      {sub && <div className="muted text-[10px] leading-tight mt-0.5">{sub}</div>}
    </div>
  );
}


// ── helpers ─────────────────────────────────────────────────────────────


function labelOf(it: StudioItem): string {
  const l = it.labels ?? {};
  return l.en || l.he || Object.values(l)[0] || "";
}


function cmp(a: StudioItem, b: StudioItem, key: SortKey, desc: boolean): number {
  let r = 0;
  if (key === "label") r = labelOf(a).localeCompare(labelOf(b), undefined, { sensitivity: "base" });
  if (key === "statements") r = ((a.statements ?? []).length) - ((b.statements ?? []).length);
  if (key === "entity_type") r = (a.entity_type ?? "").localeCompare(b.entity_type ?? "");
  if (key === "wikidata") r = (a.existing_qid ? 0 : 1) - (b.existing_qid ? 0 : 1);
  return desc ? -r : r;
}


function statusTone(status: string): string {
  if (status === "success" || status === "updated" || status === "exists") return "text-biu-sky";
  if (status === "skipped" || status === "pending") return "muted";
  if (status === "failed") return "text-red-300";
  return "text-yellow-300";
}
