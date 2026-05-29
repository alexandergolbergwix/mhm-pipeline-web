import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { Studio, type StudioBuild, type StudioItem } from "@/api/wikidataStudio";

export default function WikidataStudio() {
  const { runId } = useParams<{ runId: string }>();
  const [build, setBuild] = useState<StudioBuild | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number>(0);
  const [filter, setFilter] = useState<"all" | "manuscript" | "person" | "work">("all");

  async function refresh() {
    if (!runId) return;
    setLoading(true); setError(null);
    try {
      setBuild(await Studio.build(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void refresh(); }, [runId]);

  if (error) return <Layout><div className="glass p-6 text-red-300">{error}</div></Layout>;
  if (!build) return <Layout><p className="muted">{loading ? "Building items via the desktop pipeline…" : "Loading…"}</p></Layout>;

  const items = build.items
    .map((it, idx) => ({ it, idx }))
    .filter(({ it }) => filter === "all" || it.entity_type === filter);
  const current = items.length > 0 ? items[Math.min(selected, items.length - 1)].it : null;

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-2">
          <div className="kicker">
            Wikidata Studio · <Link to={`/runs/${runId}`} className="hover:text-ink underline">back to run</Link>
          </div>
          <h2 className="text-2xl font-semibold">Items ready to upload</h2>
          <p className="muted text-sm leading-relaxed max-w-2xl">
            Built by the same{" "}
            <code className="text-biu-sky">converter.wikidata.item_builder.WikidataItemBuilder</code>{" "}
            the desktop pipeline uses — every safety guard, every property mapping,
            every Hebrew transliteration. Only your <b className="text-ink">approved</b>{" "}
            authority matches feed it.
          </p>

          <div className="grid grid-cols-5 gap-3 pt-2">
            <Stat label="Records" value={build.record_count} />
            <Stat label="Approved matches" value={build.approved_match_count} />
            <Stat label="Items" value={build.summary.total_items} highlight />
            <Stat label="Statements" value={build.summary.statements} />
            <Stat label="MS / P / W"
                  value={`${build.summary.manuscripts} / ${build.summary.persons} / ${build.summary.works}`} />
          </div>

          <div className="flex flex-wrap gap-2 pt-2">
            <button onClick={refresh} disabled={loading} className="button-ghost text-sm">
              {loading ? "Rebuilding…" : "Rebuild"}
            </button>
            <a href={Studio.qsUrl(runId!)} download className="button-primary text-sm">
              Download QuickStatements.txt
            </a>
          </div>
        </section>

        {build.summary.total_items === 0 ? (
          <section className="glass p-6 text-center muted">
            No items yet. Approve some authority matches first — go to the{" "}
            <Link to={`/runs/${runId}`} className="text-biu-sky hover:underline">Review</Link>{" "}
            page and tick the rows you trust.
          </section>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-4">

            {/* Left: item list */}
            <aside className="glass p-3 max-h-[70vh] overflow-auto">
              <div className="flex gap-1 mb-2 text-xs">
                {(["all", "manuscript", "person", "work"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => { setFilter(f); setSelected(0); }}
                    className={`px-2 py-0.5 rounded-full transition ${
                      filter === f ? "bg-white/12 text-ink" : "muted hover:text-ink"
                    }`}>{f}</button>
                ))}
              </div>
              <ul>
                {items.map(({ it, idx }, listIdx) => (
                  <li key={idx}>
                    <button onClick={() => setSelected(listIdx)}
                            className={`block w-full text-left px-2 py-2 rounded-lg transition ${
                              listIdx === selected ? "bg-white/8 text-ink" : "muted hover:text-ink hover:bg-white/5"
                            }`}>
                      <div className="flex justify-between gap-2 items-baseline">
                        <span className="text-sm truncate">
                          {(it.labels?.en || it.labels?.he ||
                            Object.values(it.labels ?? {})[0]) || "(no label)"}
                        </span>
                        <span className="kicker shrink-0">{it.entity_type ?? "?"}</span>
                      </div>
                      {it.existing_qid && (
                        <span className="font-mono text-[10px] text-biu-sky">
                          updates {it.existing_qid}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
                {items.length === 0 && (
                  <p className="muted text-sm italic px-2">No items of this type.</p>
                )}
              </ul>
            </aside>

            {/* Right: item detail */}
            <main>
              {current && <ItemPanel item={current} />}
            </main>
          </div>
        )}

        {/* Full QuickStatements text */}
        <details className="glass p-6">
          <summary className="cursor-pointer kicker">QuickStatements output ({build.quickstatements.length.toLocaleString()} chars)</summary>
          <pre className="text-[11px] font-mono whitespace-pre-wrap mt-3"
               style={{ background: "rgba(0,0,0,0.36)", border: "1px solid var(--line)", borderRadius: 10, padding: 10, maxHeight: "60vh", overflow: "auto" }}>
            {build.quickstatements || "(empty)"}
          </pre>
        </details>
      </div>
    </Layout>
  );
}


function ItemPanel({ item }: { item: StudioItem }) {
  const labels       = item.labels ?? {};
  const descriptions = item.descriptions ?? {};
  const aliases      = item.aliases ?? {};
  const statements   = item.statements ?? [];

  return (
    <div className="glass p-6 space-y-5 max-h-[70vh] overflow-auto">
      <header className="space-y-1">
        <div className="kicker">{item.entity_type ?? "item"}</div>
        <h3 className="text-xl font-semibold">
          {labels.en || labels.he || Object.values(labels)[0] || "(no label)"}
        </h3>
        {item.existing_qid && (
          <p className="muted text-sm">
            Will <b className="text-biu-sky">update</b>{" "}
            <a className="text-biu-sky font-mono hover:underline"
               href={`https://www.wikidata.org/wiki/${item.existing_qid}`}
               target="_blank" rel="noreferrer">{item.existing_qid}</a>
          </p>
        )}
      </header>

      {Object.keys(labels).length > 0 && (
        <Section title="Labels">
          <KvList items={labels} />
        </Section>
      )}
      {Object.keys(descriptions).length > 0 && (
        <Section title="Descriptions">
          <KvList items={descriptions} />
        </Section>
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


function Stat({ label, value, highlight }: { label: string; value: number | string; highlight?: boolean }) {
  return (
    <div className="glass-pill px-3 py-2">
      <div className="kicker">{label}</div>
      <div className={`text-xl font-semibold ${highlight ? "text-biu-sky" : ""}`}>{value}</div>
    </div>
  );
}
