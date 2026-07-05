import {useCallback, useEffect, useRef, useState} from "react";
import {Link} from "react-router-dom";
import {exportResults, linkedDataExplorerApi, type ExportFormat, type SparqlDataSource, type SparqlResponse} from "@/api/linkedDataExplorer";
import {savedQueriesApi, extractParams, substituteParams, type SavedQuery} from "@/api/savedQueries";
import {ApiError} from "@/api/client";
import {encodePermalink, decodePermalink} from "./permalink";
import {EvidenceDrawer} from "./EvidenceDrawer";
import {PanelShell} from "./_shared";
import {Glass} from "@/components/glass";

// ── Query templates ──────────────────────────────────────────────────────────

interface Template {
  label: string;
  source: SparqlDataSource;
  query: string;
}

const TEMPLATES: Template[] = [
  {
    label: "Works co-occurring in manuscripts",
    source: "hmo",
    query: `PREFIX hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?ms ?msLabel ?work1 ?work1Label ?work2 ?work2Label WHERE {
  ?ms hm:has_work ?work1 ;
      hm:has_work ?work2 .
  FILTER(?work1 < ?work2)
  OPTIONAL { ?ms    rdfs:label ?msLabel    . FILTER(LANG(?msLabel)    = "he" || LANG(?msLabel)    = "") }
  OPTIONAL { ?work1 rdfs:label ?work1Label . FILTER(LANG(?work1Label) = "he" || LANG(?work1Label) = "") }
  OPTIONAL { ?work2 rdfs:label ?work2Label . FILTER(LANG(?work2Label) = "he" || LANG(?work2Label) = "") }
}
ORDER BY ?ms
LIMIT 200`,
  },
  {
    label: "People network by role",
    source: "hmo",
    query: `PREFIX hm:    <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?ms ?person ?role ?label WHERE {
  { ?ms hm:has_scribe ?person . BIND("scribe" AS ?role) }
  UNION { ?ms hm:has_owner ?person . BIND("owner" AS ?role) }
  UNION { ?work hm:has_author ?person . ?ms hm:has_work ?work . BIND("author" AS ?role) }
  ?person a cidoc:E21_Person .
  OPTIONAL { ?person rdfs:label ?label . FILTER(LANG(?label) = "he" || LANG(?label) = "") }
}
ORDER BY ?role ?label
LIMIT 500`,
  },
  {
    label: "Ownership chains per manuscript",
    source: "hmo",
    query: `PREFIX hm:   <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?ms ?msLabel ?owner ?ownerLabel WHERE {
  ?ms hm:has_owner ?owner .
  OPTIONAL { ?ms    rdfs:label ?msLabel    . FILTER(LANG(?msLabel)    = "he" || LANG(?msLabel)    = "") }
  OPTIONAL { ?owner rdfs:label ?ownerLabel . FILTER(LANG(?ownerLabel) = "he" || LANG(?ownerLabel) = "") }
}
ORDER BY ?ms ?ownerLabel`,
  },
  {
    label: "Production and mention places",
    source: "hmo",
    query: `PREFIX hm:    <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX wgs84: <http://www.w3.org/2003/01/geo/wgs84_pos#>

SELECT DISTINCT ?place ?placeLabel ?type ?lat ?lon WHERE {
  {
    ?ms hm:has_production_event ?ev .
    ?ev hm:has_production_place ?place .
    BIND("production" AS ?type)
  } UNION {
    ?ms hm:mentions_place ?place .
    BIND("mentioned" AS ?type)
  }
  OPTIONAL { ?place rdfs:label ?placeLabel . FILTER(LANG(?placeLabel) = "he" || LANG(?placeLabel) = "") }
  OPTIONAL { ?place wgs84:lat ?lat ; wgs84:long ?lon . }
}
ORDER BY ?placeLabel`,
  },
  {
    label: "Works attributed to a specific author",
    source: "hmo",
    query: `PREFIX hm:   <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

# Replace the FILTER value with the name you are searching for
SELECT DISTINCT ?work ?workLabel ?author ?authorLabel WHERE {
  ?work hm:has_author ?author .
  OPTIONAL { ?work   rdfs:label ?workLabel   . FILTER(LANG(?workLabel)   = "he" || LANG(?workLabel)   = "") }
  OPTIONAL { ?author rdfs:label ?authorLabel . FILTER(LANG(?authorLabel) = "he" || LANG(?authorLabel) = "") }
  FILTER(CONTAINS(LCASE(STR(?authorLabel)), ""))
}
ORDER BY ?authorLabel`,
  },
  {
    label: "Manuscripts from a given century",
    source: "hmo",
    query: `PREFIX hm:   <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

# Manuscripts with production dates in the 14th century (year 1300–1399)
SELECT DISTINCT ?ms ?label ?date WHERE {
  ?ms a hm:Manuscript_Object ;
      hm:production_date_earliest ?date .
  OPTIONAL { ?ms rdfs:label ?label . FILTER(LANG(?label) = "he" || LANG(?label) = "") }
  FILTER(?date >= 1300 && ?date < 1400)
}
ORDER BY ?date`,
  },
  {
    label: "Items uploaded to Wikibase",
    source: "wikibase",
    query: `SELECT ?item ?itemLabel WHERE {
  ?item wikibase:sitelinks [] .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "he,en" . }
}
LIMIT 100`,
  },
  {
    label: "Linked Wikidata authorities (by NLI J9U ID)",
    source: "wikidata",
    query: `SELECT ?item ?itemLabel ?nliId WHERE {
  ?item wdt:P8189 ?nliId .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "he,en" . }
}
LIMIT 100`,
  },
  {
    label: "HMO item by source URI",
    source: "wikibase",
    query: `SELECT ?item WHERE {
  ?item wdt:P<PASTE_PID> "<PASTE_SOURCE_URI>" .
}`,
  },
  {
    label: "HMO items of a class",
    source: "wikibase",
    query: `SELECT ?item ?label WHERE {
  ?item wdt:P31 wd:<CLASS_QID> .
  ?item rdfs:label ?label .
  FILTER(LANG(?label) = "en" || LANG(?label) = "he")
}
LIMIT 200`,
  },
  {
    label: "HMO items missing an English label",
    source: "wikibase",
    query: `SELECT ?item WHERE {
  ?item wdt:P31 ?class .
  FILTER NOT EXISTS { ?item rdfs:label ?l . FILTER(LANG(?l) = "en") }
}
LIMIT 200`,
  },
  {
    label: "HMO items linking to an entity",
    source: "wikibase",
    query: `SELECT ?item WHERE {
  ?item ?p wd:<TARGET_QID> .
}
LIMIT 200`,
  },
];

// ── Source selector ──────────────────────────────────────────────────────────

const SOURCE_LABELS: Record<SparqlDataSource, {label: string; emoji: string; note?: string}> = {
  hmo:      {label: "HMO Graph",  emoji: "📄", note: "Project RDF — full HMO/CIDOC-CRM triples"},
  wikibase: {label: "Wikibase",   emoji: "🏛", note: "Uploaded items in the project Wikibase"},
  wikidata: {label: "Wikidata",   emoji: "🌐", note: "Public Wikidata — authority links"},
  corpus:   {label: "My corpus",  emoji: "🗄", note: "All projects you are a member of"},
};

function SourceToggle({
  value,
  onChange,
  wikibaseConfigured,
}: {
  value: SparqlDataSource;
  onChange: (s: SparqlDataSource) => void;
  wikibaseConfigured: boolean;
}) {
  return (
    <div className="flex gap-1 flex-wrap">
      {(["hmo", "wikibase", "wikidata", "corpus"] as SparqlDataSource[]).map((src) => {
        const {label, emoji, note} = SOURCE_LABELS[src];
        const disabled = src === "wikibase" && !wikibaseConfigured;
        return (
          <button
            key={src}
            onClick={() => !disabled && onChange(src)}
            disabled={disabled}
            title={disabled ? "Wikibase endpoint not configured (WIKIBASE_SPARQL_URL)" : note}
            className={`flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg transition-colors border ${
              value === src
                ? "bg-biu-sky/15 text-biu-sky border-biu-sky/30"
                : disabled
                  ? "text-disabled border-white/5 cursor-not-allowed"
                  : "text-muted hover:text-ink hover:bg-white/5 border-transparent"
            }`}
          >
            <span>{emoji}</span> {label}
          </button>
        );
      })}
    </div>
  );
}

// ── Results table ────────────────────────────────────────────────────────────

function _isUri(value: string | null): boolean {
  if (!value) return false;
  return value.startsWith("http://") || value.startsWith("https://") || value.startsWith("urn:");
}

function ResultsTable({result, projectId, onUriClick}: {result: SparqlResponse; projectId: string; onUriClick: (uri: string) => void}) {
  if (result.columns.length === 0) {
    return <p className="muted text-sm">Query returned no columns.</p>;
  }
  if (result.rows.length === 0) {
    return <p className="muted text-sm">Query returned no results.</p>;
  }

  return (
    <div className="space-y-2">
      {result.truncated && (
        <p className="text-xs text-amber-400/80">
          Results capped at 1 000 rows — add a LIMIT clause to your query to see a specific range.
        </p>
      )}
      <div className="overflow-x-auto rounded-lg border border-white/10">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-white/10 bg-white/5">
              {result.columns.map((col) => (
                <th key={col} className="px-3 py-2 text-left font-medium text-biu-sky whitespace-nowrap">
                  ?{col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, i) => (
              <tr key={i} className={`border-b border-white/5 ${i % 2 === 0 ? "" : "bg-white/[0.02]"}`}>
                {row.map((cell, j) => (
                  <td key={j} className="px-3 py-1.5 text-ink/80 max-w-xs truncate" title={cell ?? ""}>
                    {cell === null ? (
                      <span className="muted italic">—</span>
                    ) : _isUri(cell) ? (
                      <span className="flex items-center gap-1.5 max-w-xs">
                        <Link
                          to={`/projects/${projectId}/entity?uri=${encodeURIComponent(cell)}`}
                          className="text-biu-sky hover:underline truncate block"
                          title={`Open entity page: ${cell}`}
                        >
                          {cell}
                        </Link>
                        <button
                          onClick={() => onUriClick(cell)}
                          className="shrink-0 text-[10px] px-1 py-0.5 rounded bg-white/10 text-ink/60 hover:bg-white/20 hover:text-ink"
                          title="Quick evidence peek"
                        >
                          ⓘ
                        </button>
                      </span>
                    ) : (
                      cell
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs muted text-right">{result.rows.length.toLocaleString()} rows</p>
    </div>
  );
}

// ── Main panel ───────────────────────────────────────────────────────────────

const EXPORT_FORMATS: {format: ExportFormat; label: string}[] = [
  {format: "csv",    label: "CSV"},
  {format: "json",   label: "JSON"},
  {format: "bibtex", label: "BibTeX"},
  {format: "ris",    label: "RIS"},
];

export default function SparqlConsolePanel({projectId}: {projectId: string}) {
  const [source, setSource] = useState<SparqlDataSource>("hmo");
  const [query, setQuery] = useState(TEMPLATES[0].query);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SparqlResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const exportRef = useRef<HTMLDivElement>(null);

  // Evidence drawer
  const [evidenceUri, setEvidenceUri] = useState<string | null>(null);

  // Saved queries
  const [savedQueries, setSavedQueries] = useState<SavedQuery[]>([]);
  const [saveName, setSaveName] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});

  const reloadSaved = useCallback(async () => {
    try { setSavedQueries(await savedQueriesApi.list(projectId)); } catch { /* viewer may not be editor */ }
  }, [projectId]);

  useEffect(() => { reloadSaved(); }, [reloadSaved]);

  // Wikibase is assumed unconfigured until proven otherwise at runtime.
  const [wikibaseConfigured, setWikibaseConfigured] = useState(true);

  // On mount: read ?q= from the URL, decode, pre-fill + auto-run.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const encoded = params.get("q");
    if (encoded) {
      const decoded = decodePermalink(encoded);
      if (decoded) {
        setQuery(decoded);
        setSource("hmo");
        // Auto-run after state settles
        setTimeout(() => {
          linkedDataExplorerApi.executeSparql(projectId, decoded, "hmo")
            .then(setResult)
            .catch((e) => setError(e instanceof ApiError ? e.detail : String(e)));
        }, 0);
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Close export dropdown when clicking outside.
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function loadTemplate(t: Template) {
    setQuery(t.query);
    setSource(t.source);
    setResult(null);
    setError(null);
  }

  async function handleExport(format: ExportFormat) {
    setExportOpen(false);
    setExportError(null);
    try {
      await exportResults(projectId, query, format);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    }
  }

  function copyPermalink() {
    const encoded = encodePermalink(query);
    const url = new URL(window.location.href);
    url.searchParams.set("q", encoded);
    navigator.clipboard.writeText(url.toString()).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  const params = extractParams(query);

  async function saveQuery() {
    if (!saveName.trim()) return;
    await savedQueriesApi.create(projectId, {name: saveName.trim(), query});
    setSaveName("");
    setSaveOpen(false);
    reloadSaved();
  }

  async function deleteQuery(id: string) {
    await savedQueriesApi.delete(projectId, id);
    reloadSaved();
  }

  async function run() {
    if (!query.trim()) return;
    setRunning(true);
    setError(null);
    setResult(null);
    // Substitute {{param}} placeholders before sending
    const finalQuery = params.length > 0 ? substituteParams(query, paramValues) : query;
    try {
      const data = await linkedDataExplorerApi.executeSparql(projectId, finalQuery, source);
      setResult(data);
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : String(e);
      if (e instanceof ApiError && e.status === 503 && source === "wikibase") {
        setWikibaseConfigured(false);
        setSource("hmo");
      }
      setError(msg);
    } finally {
      setRunning(false);
    }
  }

  return (
    <PanelShell
      title="SPARQL Console"
      subtitle="Query the project's linked data across three sources: local HMO graph, project Wikibase, or public Wikidata."
    >
      <div className="space-y-4">
        {/* Source + template row */}
        <div className="flex flex-wrap gap-3 items-start justify-between">
          <SourceToggle value={source} onChange={setSource} wikibaseConfigured={wikibaseConfigured} />
          <select
            className="text-sm bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-ink cursor-pointer"
            defaultValue=""
            onChange={(e) => {
              const t = TEMPLATES[Number(e.target.value)];
              if (t) loadTemplate(t);
              e.target.value = "";
            }}
          >
            <option value="" disabled>Load template…</option>
            {TEMPLATES.map((t, i) => (
              <option key={i} value={i}>{SOURCE_LABELS[t.source].emoji} {t.label}</option>
            ))}
          </select>
        </div>

        {/* Saved queries section */}
        {savedQueries.length > 0 && (
          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="text-xs muted">Saved:</span>
            {savedQueries.map((sq) => (
              <span key={sq.id} className="flex items-center gap-1">
                <button
                  onClick={() => { setQuery(sq.query); setResult(null); setError(null); setParamValues({}); }}
                  className="text-xs px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-ink border border-white/10"
                >
                  {sq.name}
                </button>
                <button
                  onClick={() => deleteQuery(sq.id)}
                  className="text-xs text-muted hover:text-danger px-1"
                  title="Delete saved query"
                >✕</button>
              </span>
            ))}
          </div>
        )}

        {/* Source note */}
        <p className="text-xs muted">
          {SOURCE_LABELS[source].note}
          {source === "wikidata" && (
            <span className="text-amber-400/70 ml-2">
              · Wikidata's public endpoint may be slow; queries are cached for 10 min
            </span>
          )}
        </p>

        {/* Editor */}
        <div className="relative">
          <textarea
            className="w-full h-48 font-mono text-xs bg-black/30 border border-white/10 rounded-lg p-3 text-ink resize-y focus:outline-none focus:border-biu-sky/40 placeholder:muted"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setResult(null); setError(null); }}
            spellCheck={false}
            placeholder="Enter a SPARQL SELECT or CONSTRUCT query…"
            dir="ltr"
          />
        </div>

        {/* Param inputs — rendered when {{placeholders}} are present */}
        {params.length > 0 && (
          <div className="flex flex-wrap gap-3 items-center p-3 rounded-lg bg-white/[0.03] border border-white/10">
            <span className="text-xs muted">Parameters:</span>
            {params.map((p) => (
              <label key={p} className="flex items-center gap-1.5 text-xs">
                <span className="text-biu-sky font-mono">{`{{${p}}}`}</span>
                <input
                  type="text"
                  className="bg-black/30 border border-white/10 rounded px-2 py-1 text-ink text-xs focus:outline-none focus:border-biu-sky/40 w-40"
                  placeholder={p}
                  value={paramValues[p] ?? ""}
                  onChange={(e) => setParamValues((v) => ({...v, [p]: e.target.value}))}
                />
              </label>
            ))}
          </div>
        )}

        {/* Run button + Export + Copy link */}
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={run}
            disabled={running || !query.trim()}
            className="button-primary text-sm !py-1.5"
          >
            {running ? "Running…" : "▶ Run query"}
          </button>
          {result && (
            <button
              onClick={() => { setResult(null); setError(null); }}
              className="button-ghost text-sm !py-1.5"
            >
              Clear
            </button>
          )}
          {/* Export dropdown — only when there are results */}
          {result && (
            <div className="relative" ref={exportRef}>
              <button
                onClick={() => setExportOpen((o) => !o)}
                className="button-ghost text-sm !py-1.5"
              >
                Export ▾
              </button>
              {exportOpen && (
                <div className="absolute left-0 top-full mt-1 z-20 bg-surface border border-white/10 rounded-lg shadow-lg py-1 min-w-[120px]">
                  {EXPORT_FORMATS.map(({format, label}) => (
                    <button
                      key={format}
                      onClick={() => handleExport(format)}
                      className="w-full text-left px-4 py-1.5 text-sm hover:bg-white/5 text-ink"
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {/* Copy permalink */}
          {query.trim() && (
            <button
              onClick={copyPermalink}
              className="button-ghost text-sm !py-1.5"
              title="Copy a shareable link to this query"
            >
              {copied ? "✓ Copied!" : "🔗 Copy link"}
            </button>
          )}
          {/* Save query */}
          {query.trim() && !saveOpen && (
            <button
              onClick={() => setSaveOpen(true)}
              className="button-ghost text-sm !py-1.5"
              title="Save this query"
            >
              💾 Save
            </button>
          )}
          {saveOpen && (
            <span className="flex items-center gap-1.5">
              <input
                autoFocus
                type="text"
                placeholder="Query name…"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") saveQuery(); if (e.key === "Escape") setSaveOpen(false); }}
                className="bg-black/30 border border-white/10 rounded px-2 py-1 text-ink text-xs focus:outline-none focus:border-biu-sky/40 w-40"
              />
              <button onClick={saveQuery} disabled={!saveName.trim()} className="button-primary text-xs !py-1">Save</button>
              <button onClick={() => setSaveOpen(false)} className="button-ghost text-xs !py-1">Cancel</button>
            </span>
          )}
          {running && <span className="animate-spin text-biu-sky text-lg">⟳</span>}
        </div>
        {exportError && (
          <p className="text-xs text-danger">{exportError}</p>
        )}

        {/* Error */}
        {error && (
          <Glass variant="compact" className="p-3 text-danger text-sm rounded-lg border border-red-500/30">
            {error}
          </Glass>
        )}

        {/* Results */}
        {result && (
          <ResultsTable
            result={result}
            projectId={projectId}
            onUriClick={(uri) => setEvidenceUri(uri)}
          />
        )}
      </div>

      {/* Evidence drawer — shown when a URI cell is clicked */}
      <EvidenceDrawer
        projectId={projectId}
        uri={evidenceUri}
        onClose={() => setEvidenceUri(null)}
      />
    </PanelShell>
  );
}
