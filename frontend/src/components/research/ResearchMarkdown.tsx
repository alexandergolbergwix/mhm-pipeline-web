import {useMemo, type ReactNode} from "react";
import {
  CHART_PALETTE,
  QidBadgeLink,
  SignalBarList,
  SignalDataTable,
  SignalDonutChart,
  SignalStatGrid,
  type SignalBarItem,
  type SignalStatItem,
} from "./ResearchCharts";

const QID_TOKEN_RE = /^Q\d+$/i;

/**
 * Normalizes markdown text when a model emits headings, bullets, or pipe tables
 * inline on a single line instead of separating them with newlines.
 */
export function normalizeMarkdownText(raw: string): string {
  let text = raw.replace(/\r\n/g, "\n").trim();
  if (!text) return "";

  // Split "# Heading **Bold lead..." when emitted on line 1 without a newline
  text = text.replace(/^(#{1,3}\s+[^\n#*|+]+?)\s+(\*\*[A-Z0-9])/m, "$1\n\n$2");

  // Ensure headings like " ## 1. Title" start on a new paragraph
  text = text.replace(/([^\n])\s+(#{1,4}\s+)/g, "$1\n\n$2");

  // Split collapsed pipe table separator "| |---|" into multi-line rows
  text = text.replace(/\|\s+\|(\s*:?-{2,}:?\s*\|)+/g, (match) => {
    const sep = match.replace(/^\|\s+/, "");
    return `|\n${sep}\n|`;
  });

  // Inside a collapsed pipe table, "| | Q123" or "| | Venice" is a row boundary
  text = text.replace(/\|\s*\|\s*(?=[^\n|]+\|)/g, "|\n| ");

  // Ensure a pipe table that starts inline after a heading or sentence starts on its own line
  text = text.replace(/^([^|\n]*\S)[ \t]+(\|[^\n|]+\|[^\n|]+\|)/gm, "$1\n\n$2");

  // Ensure trailing bullet list items after a pipe table start on a new line
  text = text.replace(/\|\s+(-\s+)/g, "|\n\n$1");

  // Split inline bullets like ". - **897" or ") - **897" or " - Person role"
  text = text.replace(/([.):])\s+(-\s+(?:\*\*|[A-Z]))/g, "$1\n$2");

  return text;
}

interface ParsedTable {
  headers: string[];
  rows: string[][];
}

function parseTableLines(lines: string[]): ParsedTable | null {
  const cleaned = lines
    .map((l) => l.trim())
    .filter((l) => l.startsWith("|") && l.includes("|", 1));
  if (cleaned.length < 2) return null;

  const splitRow = (line: string): string[] =>
    line
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());

  const headers = splitRow(cleaned[0]);
  if (!headers.length || headers.every((h) => !h)) return null;

  const isSep = (cells: string[]) =>
    cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c));

  const startIdx = isSep(splitRow(cleaned[1])) ? 2 : 1;
  const rows: string[][] = [];
  for (let i = startIdx; i < cleaned.length; i += 1) {
    const cells = splitRow(cleaned[i]);
    if (isSep(cells)) continue;
    if (cells.every((c) => !c)) continue;
    // If a single line still packed multiple rows of `headers.length` cells, unpack them
    if (headers.length >= 2 && cells.length >= headers.length * 2 && cells.length % headers.length === 0) {
      for (let offset = 0; offset < cells.length; offset += headers.length) {
        const slice = cells.slice(offset, offset + headers.length);
        if (!isSep(slice) && slice.some(Boolean)) {
          rows.push(slice);
        }
      }
    } else if (cells.length <= headers.length) {
      rows.push(headers.map((_, colIdx) => cells[colIdx] ?? ""));
    } else {
      return null;
    }
  }
  if (!rows.length) return null;
  return {headers, rows};
}

type MdBlock =
  | {kind: "h1"; text: string}
  | {kind: "h2"; text: string}
  | {kind: "h3"; text: string}
  | {kind: "p"; text: string}
  | {kind: "ul"; items: string[]}
  | {kind: "ol"; items: string[]}
  | {kind: "quote"; text: string}
  | {kind: "code"; lang: string; code: string}
  | {kind: "table"; table: ParsedTable}
  | {kind: "hr"};

export function parseMarkdownBlocks(raw: string): MdBlock[] {
  const normalized = normalizeMarkdownText(raw);
  if (!normalized) return [];
  const lines = normalized.split("\n");
  const blocks: MdBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const lang = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push({kind: "code", lang, code: codeLines.join("\n")});
      continue;
    }

    if (/^-{3,}$/.test(trimmed) || /^\*{3,}$/.test(trimmed)) {
      blocks.push({kind: "hr"});
      i += 1;
      continue;
    }

    if (trimmed.startsWith("# ")) {
      blocks.push({kind: "h1", text: trimmed.slice(2).trim()});
      i += 1;
      continue;
    }
    if (trimmed.startsWith("## ")) {
      blocks.push({kind: "h2", text: trimmed.slice(3).trim()});
      i += 1;
      continue;
    }
    if (trimmed.startsWith("### ") || trimmed.startsWith("#### ")) {
      const text = trimmed.replace(/^#{3,4}\s+/, "").trim();
      blocks.push({kind: "h3", text});
      i += 1;
      continue;
    }

    if (trimmed.startsWith("|") && trimmed.includes("|", 1)) {
      const tableLines: string[] = [];
      while (i < lines.length) {
        const row = lines[i].trim();
        if (row.startsWith("|") && row.includes("|", 1)) {
          tableLines.push(lines[i]);
          i += 1;
          continue;
        }
        if (!row || row === "|") {
          let next = i + 1;
          while (next < lines.length && (!lines[next].trim() || lines[next].trim() === "|")) next += 1;
          if (next < lines.length && lines[next].trim().startsWith("|") && lines[next].trim().includes("|", 1)) {
            i = next;
            continue;
          }
        }
        break;
      }
      const parsed = parseTableLines(tableLines);
      if (parsed) {
        blocks.push({kind: "table", table: parsed});
      } else {
        blocks.push({kind: "p", text: tableLines.join("\n")});
      }
      continue;
    }

    if (trimmed.startsWith("> ")) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push({kind: "quote", text: quoteLines.join(" ")});
      continue;
    }

    if (/^[-*•]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*•]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*•]\s+/, ""));
        i += 1;
      }
      blocks.push({kind: "ul", items});
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push({kind: "ol", items});
      continue;
    }

    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].trim().startsWith("#") &&
      !lines[i].trim().startsWith("|") &&
      !lines[i].trim().startsWith("```") &&
      !lines[i].trim().startsWith("> ") &&
      !/^[-*•]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i].trim());
      i += 1;
    }
    if (paraLines.length) {
      blocks.push({kind: "p", text: paraLines.join(" ")});
    } else {
      i += 1;
    }
  }

  return blocks;
}

/**
 * Extracts headline corpus KPIs, role distributions, and ranked place counts
 * when present in an analytical markdown report so the canvas automatically
 * surfaces visual signal cards alongside the prose.
 */
function extractAutoSignals(raw: string): {
  stats: SignalStatItem[];
  roles: SignalBarItem[];
  places: SignalBarItem[];
} {
  const stats: SignalStatItem[] = [];
  const roles: SignalBarItem[] = [];
  const places: SignalBarItem[] = [];

  const statPatterns: Array<{id: string; label: string; re: RegExp; color: string}> = [
    {id: "manuscripts", label: "Manuscripts", re: /\b([\d,]+)\s+manuscripts\b/i, color: "#77cce5"},
    {id: "works", label: "Works", re: /\b([\d,]+)\s+works\b/i, color: "#a78bfa"},
    {id: "persons", label: "Persons", re: /\b([\d,]+)\s+persons\b/i, color: "#34d399"},
    {id: "places", label: "Places", re: /\b([\d,]+)\s+places\b/i, color: "#f59e0b"},
    {id: "triples", label: "RDF Triples", re: /\b([\d,]+)\s+triples\b/i, color: "#60a5fa"},
  ];

  for (const pat of statPatterns) {
    const m = pat.re.exec(raw);
    if (m) {
      const val = Number(m[1].replace(/,/g, ""));
      if (Number.isFinite(val) && val > 0) {
        stats.push({id: pat.id, label: pat.label, value: val, color: pat.color});
      }
    }
  }

  const rolePatterns: Array<{key: string; label: string; re: RegExp; color: string}> = [
    {key: "scribe", label: "Scribes", re: /\b([\d,]+)\s+scribes?\b/i, color: "#77cce5"},
    {key: "author", label: "Authors", re: /\b([\d,]+)\s+authors?\b/i, color: "#a78bfa"},
    {key: "owner", label: "Owners", re: /\b([\d,]+)\s+owners?\b/i, color: "#34d399"},
  ];
  for (const rp of rolePatterns) {
    const m = rp.re.exec(raw);
    if (m) {
      const val = Number(m[1].replace(/,/g, ""));
      if (Number.isFinite(val) && val > 0) {
        roles.push({key: rp.key, label: rp.label, value: val, color: rp.color});
      }
    }
  }

  // Extract inline ranked places like "Venice (44 mss), Amsterdam (34), Jerusalem (29), Mantua (26)"
  const hasPlaceTable = /\|\s*Place\s*\|/i.test(raw) || /\|\s*Production place\s*\|/i.test(raw);
  if (!hasPlaceTable) {
    const placeInlineRe = /\b([A-Z][a-zA-ZÀ-ÿ\s'-]{2,24})\s*\((\d{1,4})(?:\s*mss?\.?)?\)/g;
    let pm = placeInlineRe.exec(raw);
    const seen = new Set<string>();
    while (pm !== null) {
      const name = pm[1].trim();
      const count = Number(pm[2]);
      if (
        count > 0 &&
        count < 1000 &&
        !seen.has(name.toLowerCase()) &&
        !/^(item|work|person|page|step|part|table|section)/i.test(name)
      ) {
        seen.add(name.toLowerCase());
        places.push({
          key: name,
          label: name,
          value: count,
          color: CHART_PALETTE[places.length % CHART_PALETTE.length],
        });
      }
      pm = placeInlineRe.exec(raw);
    }
  }

  return {
    stats: stats.length >= 3 ? stats : [],
    roles: roles.length >= 2 ? roles : [],
    places: places.length >= 3 ? places : [],
  };
}

export function renderInlineMarkdownNodes(text: string): ReactNode[] {
  const tokens = text.split(
    /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\bhttps?:\/\/[^\s<)]+|\bQ\d+\b)/g,
  );
  return tokens.map((tok, idx) => {
    if (!tok) return null;
    if (tok.startsWith("**") && tok.endsWith("**") && tok.length > 4) {
      return (
        <strong key={idx} className="font-semibold text-ink">
          {renderInlineMarkdownNodes(tok.slice(2, -2))}
        </strong>
      );
    }
    if (tok.startsWith("*") && tok.endsWith("*") && tok.length > 2) {
      return (
        <em key={idx} className="italic text-ink-subtle">
          {tok.slice(1, -1)}
        </em>
      );
    }
    if (tok.startsWith("`") && tok.endsWith("`") && tok.length > 2) {
      const inner = tok.slice(1, -1);
      if (QID_TOKEN_RE.test(inner)) {
        return <QidBadgeLink key={idx} qid={inner} />;
      }
      return (
        <code
          key={idx}
          className="rounded-md border border-white/10 bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-biu-sky"
        >
          {inner}
        </code>
      );
    }
    const linkMatch = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(tok);
    if (linkMatch) {
      const [, label, href] = linkMatch;
      const qidMatch = href.match(/\/(?:wiki|entity)\/(Q\d+)$/i);
      if (qidMatch && QID_TOKEN_RE.test(label.trim())) {
        return <QidBadgeLink key={idx} qid={qidMatch[1]} label={label} />;
      }
      return (
        <a
          key={idx}
          href={href}
          target="_blank"
          rel="noreferrer"
          className="text-biu-sky hover:underline font-medium"
        >
          {label} <span aria-hidden className="text-[10px] opacity-75">↗</span>
        </a>
      );
    }
    if (/^https?:\/\//.test(tok)) {
      const qidMatch = tok.match(/\/(?:wiki|entity)\/(Q\d+)$/i);
      if (qidMatch) {
        return <QidBadgeLink key={idx} qid={qidMatch[1]} />;
      }
      return (
        <a
          key={idx}
          href={tok}
          target="_blank"
          rel="noreferrer"
          className="text-biu-sky hover:underline break-all"
        >
          {tok}
        </a>
      );
    }
    if (QID_TOKEN_RE.test(tok)) {
      return <QidBadgeLink key={idx} qid={tok} />;
    }
    return <span key={idx}>{tok}</span>;
  });
}

/** Splits a trailing `Sources: ...` line into structured source pills. */
export function splitSourcesLine(text: string): {body: string; sources: string[]} {
  const lines = text.split("\n");
  const sources: string[] = [];
  const kept: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^\*{0,2}Sources:\*{0,2}\s+/i.test(trimmed)) {
      const rawList = trimmed.replace(/^\*{0,2}Sources:\*{0,2}\s+/i, "");
      const parts = rawList
        .split(/\s*[·|;]\s*|\s+,\s+/)
        .map((s) => s.trim())
        .filter(Boolean);
      sources.push(...parts);
    } else {
      kept.push(line);
    }
  }
  return {body: kept.join("\n").trim(), sources};
}

export function ResearchMarkdown({
  text,
  variant = "canvas",
}: {
  text: string;
  variant?: "canvas" | "chat";
}) {
  const {body, sources} = useMemo(() => splitSourcesLine(text), [text]);
  const blocks = useMemo(() => parseMarkdownBlocks(body), [body]);
  const signals = useMemo(
    () => (variant === "canvas" ? extractAutoSignals(body) : {stats: [], roles: [], places: []}),
    [body, variant],
  );

  const hasVisualSignals =
    variant === "canvas" &&
    (signals.stats.length > 0 || signals.roles.length > 0 || signals.places.length > 0);

  return (
    <div
      className={variant === "canvas" ? "space-y-4" : "space-y-2.5"}
      dir="auto"
    >
      {hasVisualSignals ? (
        <div className="space-y-3 rounded-2xl border border-biu-sky/20 bg-biu-sky/[0.04] p-3.5">
          <div className="flex items-center justify-between">
            <span className="kicker">Key Corpus Signals</span>
            <span className="text-[11px] text-muted">Auto-extracted from report</span>
          </div>
          {signals.stats.length > 0 ? (
            <SignalStatGrid
              items={signals.stats}
              cols={signals.stats.length >= 5 ? 5 : 4}
            />
          ) : null}
          {signals.roles.length > 0 || signals.places.length > 0 ? (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3 pt-1">
              {signals.roles.length > 0 ? (
                <div className="rounded-xl border border-white/10 bg-[var(--surface-inset)] p-3 space-y-2">
                  <div className="text-xs font-semibold text-ink">People by Role</div>
                  <SignalDonutChart items={signals.roles} unit="people" centerLabel="attributions" size={132} thickness={18} />
                </div>
              ) : null}
              {signals.places.length > 0 ? (
                <div className="rounded-xl border border-white/10 bg-[var(--surface-inset)] p-3 space-y-2">
                  <div className="text-xs font-semibold text-ink">Top Production Places Mentioned</div>
                  <SignalBarList items={signals.places} unit="mss" maxItems={6} showShare={false} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {blocks.map((block, idx) => {
        if (block.kind === "h1") {
          return (
            <div key={idx} className="border-b border-white/10 pb-2">
              <h2 className="text-lg font-bold text-ink tracking-tight">
                {renderInlineMarkdownNodes(block.text)}
              </h2>
            </div>
          );
        }
        if (block.kind === "h2") {
          return (
            <div key={idx} className="pt-2">
              <h3 className="text-sm font-semibold text-biu-sky uppercase tracking-wider flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-biu-sky" />
                <span>{renderInlineMarkdownNodes(block.text)}</span>
              </h3>
            </div>
          );
        }
        if (block.kind === "h3") {
          return (
            <h4 key={idx} className="text-xs font-semibold text-ink pt-1">
              {renderInlineMarkdownNodes(block.text)}
            </h4>
          );
        }
        if (block.kind === "hr") {
          return <hr key={idx} className="border-white/10 my-2" />;
        }
        if (block.kind === "quote") {
          return (
            <blockquote
              key={idx}
              className="border-l-2 border-biu-sky/50 pl-3 py-1 text-xs italic text-ink-subtle bg-white/[0.02] rounded-r-lg"
            >
              {renderInlineMarkdownNodes(block.text)}
            </blockquote>
          );
        }
        if (block.kind === "code") {
          return (
            <pre
              key={idx}
              className="rounded-xl border border-white/10 bg-[var(--code-bg)] p-3 text-xs font-mono text-ink overflow-x-auto"
            >
              <code>{block.code}</code>
            </pre>
          );
        }
        if (block.kind === "table") {
          return (
            <div
              key={idx}
              className="rounded-2xl border border-white/10 bg-white/[0.02] p-3"
            >
              <SignalDataTable
                columns={block.table.headers}
                rows={block.table.rows}
                defaultMode="table"
              />
            </div>
          );
        }
        if (block.kind === "ul") {
          return (
            <ul key={idx} className="space-y-1.5 text-sm leading-relaxed pl-4 list-disc marker:text-biu-sky">
              {block.items.map((item, itemIdx) => (
                <li key={itemIdx} className="text-ink-subtle">
                  {renderInlineMarkdownNodes(item)}
                </li>
              ))}
            </ul>
          );
        }
        if (block.kind === "ol") {
          return (
            <ol key={idx} className="space-y-1.5 text-sm leading-relaxed pl-5 list-decimal marker:text-biu-sky">
              {block.items.map((item, itemIdx) => (
                <li key={itemIdx} className="text-ink-subtle">
                  {renderInlineMarkdownNodes(item)}
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={idx} className="text-sm leading-relaxed text-ink-subtle">
            {renderInlineMarkdownNodes(block.text)}
          </p>
        );
      })}

      {sources.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-white/10">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted mr-1">
            Sources
          </span>
          {sources.map((src, idx) => (
            <span
              key={`${src}-${idx}`}
              className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] text-muted"
            >
              {renderInlineMarkdownNodes(src)}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
