import {useMemo, useState, type ReactNode} from "react";

export const CHART_PALETTE = [
  "#77cce5",
  "#a78bfa",
  "#34d399",
  "#f59e0b",
  "#ec4899",
  "#60a5fa",
  "#fb923c",
  "#2dd4bf",
] as const;

const QID_EXACT_RE = /^Q\d+$/i;
const QID_INLINE_RE = /\b(Q\d+)\b/g;

export interface SignalStatItem {
  id: string;
  label: string;
  value: number | string;
  unit?: string;
  note?: string;
  accent?: boolean;
  color?: string;
  trend?: number[];
  breakdown?: Array<{label: string; value: number | string; muted?: boolean}>;
}

export interface SignalBarItem {
  key: string;
  label: string;
  value: number;
  secondaryValue?: number;
  secondaryLabel?: string;
  badge?: string;
  href?: string;
  color?: string;
}

export interface SignalSeries {
  name: string;
  values: Array<number | null>;
  color?: string;
}

export function formatMetricNumber(value: number | string | null | undefined, unit?: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return unit ? `${value} ${unit}` : value;
  if (!Number.isFinite(value)) return "—";
  const formatted = value.toLocaleString(undefined, {maximumFractionDigits: 2});
  if (!unit) return formatted;
  return unit === "%" ? `${formatted}%` : `${formatted} ${unit}`;
}

export function parseNumericCell(cell: string | number | null | undefined): number | null {
  if (typeof cell === "number") return Number.isFinite(cell) ? cell : null;
  if (typeof cell !== "string") return null;
  const cleaned = cell.trim().replace(/,/g, "");
  if (!cleaned || /^q\d+$/i.test(cleaned)) return null;
  const num = Number(cleaned);
  return Number.isFinite(num) ? num : null;
}

function MiniSparkline({values, color = "#77cce5"}: {values: number[]; color?: string}) {
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length < 2) return null;
  const W = 96;
  const H = 28;
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = Math.max(1, max - min);
  const pts = finite.map((v, i) => {
    const x = (i / (finite.length - 1)) * (W - 4) + 2;
    const y = H - 3 - ((v - min) / span) * (H - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const area = `2,${H - 2} ${pts.join(" ")} ${W - 2},${H - 2}`;
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="shrink-0 overflow-visible" aria-hidden>
      <polygon points={area} fill={color} fillOpacity={0.16} />
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function SignalStatTile({item}: {item: SignalStatItem}) {
  const accentColor = item.color ?? "#77cce5";
  return (
    <div
      data-testid={`signal-stat-${item.id}`}
      className={`rounded-2xl border p-3.5 transition-colors flex flex-col justify-between gap-1.5 ${
        item.accent
          ? "border-biu-sky/40 bg-biu-sky/10"
          : "border-white/10 bg-[var(--surface-inset)] hover:border-white/20"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted truncate">
          {item.label}
        </span>
        <span
          className="inline-block h-2 w-2 rounded-full shrink-0"
          style={{background: accentColor}}
        />
      </div>
      <div className="flex items-end justify-between gap-2">
        <div
          className="text-2xl font-bold tabular-nums tracking-tight text-ink leading-none"
          data-testid={`signal-stat-${item.id}-value`}
        >
          {formatMetricNumber(item.value, item.unit)}
        </div>
        {item.trend && item.trend.length > 1 ? (
          <MiniSparkline values={item.trend} color={accentColor} />
        ) : null}
      </div>
      {item.breakdown && item.breakdown.length > 0 ? (
        <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 pt-1 border-t border-white/5 text-[11px] tabular-nums">
          {item.breakdown.map((b) => (
            <span key={b.label} className={b.muted ? "opacity-35" : "text-muted"}>
              <span className="opacity-75">{b.label}</span>{" "}
              <span className="text-ink font-medium">{formatMetricNumber(b.value)}</span>
            </span>
          ))}
        </div>
      ) : item.note ? (
        <div className="text-[11px] text-muted truncate pt-0.5">{item.note}</div>
      ) : null}
    </div>
  );
}

export function SignalStatGrid({items, cols = 4}: {items: SignalStatItem[]; cols?: 2 | 3 | 4 | 5}) {
  if (!items.length) return null;
  const colClass =
    cols === 5
      ? "grid-cols-2 sm:grid-cols-3 xl:grid-cols-5"
      : cols === 3
        ? "grid-cols-1 sm:grid-cols-3"
        : cols === 2
          ? "grid-cols-1 sm:grid-cols-2"
          : "grid-cols-2 sm:grid-cols-4";
  return (
    <div className={`grid ${colClass} gap-3`}>
      {items.map((item) => (
        <SignalStatTile key={item.id} item={item} />
      ))}
    </div>
  );
}

export function QidBadgeLink({qid, label}: {qid: string; label?: string}) {
  const clean = qid.trim().toUpperCase();
  return (
    <a
      href={`https://www.wikidata.org/wiki/${clean}`}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 rounded-full border border-biu-sky/35 bg-biu-sky/10 px-2 py-0.5 text-[11px] font-mono text-biu-sky hover:bg-biu-sky/20 transition-colors"
      title={`Open ${clean} on Wikidata`}
    >
      <span>{label ?? clean}</span>
      <span aria-hidden className="text-[9px] opacity-75">↗</span>
    </a>
  );
}

export function renderRichCellText(raw: string): ReactNode {
  const trimmed = raw.trim();
  if (!trimmed) return <span className="text-muted opacity-50">—</span>;
  if (QID_EXACT_RE.test(trimmed)) {
    return <QidBadgeLink qid={trimmed} />;
  }
  if (/^https?:\/\/[^\s]+$/.test(trimmed)) {
    const qidTail = trimmed.match(/\/entity\/(Q\d+)$/i) ?? trimmed.match(/\/wiki\/(Q\d+)$/i);
    if (qidTail) {
      return <QidBadgeLink qid={qidTail[1]} />;
    }
    const short = trimmed.replace(/^https?:\/\/(www\.)?/, "").slice(0, 44);
    return (
      <a
        href={trimmed}
        target="_blank"
        rel="noreferrer"
        className="text-biu-sky hover:underline font-mono text-[11px]"
      >
        {short}{trimmed.length > 52 ? "…" : ""} ↗
      </a>
    );
  }
  if (QID_INLINE_RE.test(trimmed)) {
    QID_INLINE_RE.lastIndex = 0;
    const parts: ReactNode[] = [];
    let last = 0;
    let match = QID_INLINE_RE.exec(trimmed);
    while (match !== null) {
      if (match.index > last) {
        parts.push(trimmed.slice(last, match.index));
      }
      parts.push(<QidBadgeLink key={`${match.index}-${match[1]}`} qid={match[1]} />);
      last = match.index + match[0].length;
      match = QID_INLINE_RE.exec(trimmed);
    }
    if (last < trimmed.length) {
      parts.push(trimmed.slice(last));
    }
    return <span dir="auto" className="inline-flex flex-wrap items-center gap-1">{parts}</span>;
  }
  return <span dir="auto">{trimmed}</span>;
}

export function SignalBarList({
  items,
  unit,
  total,
  maxItems = 15,
  color = "#77cce5",
  showRank = true,
  showShare = true,
}: {
  items: SignalBarItem[];
  unit?: string;
  total?: number;
  maxItems?: number;
  color?: string;
  showRank?: boolean;
  showShare?: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? items : items.slice(0, maxItems);
  const maxVal = useMemo(
    () => Math.max(1, ...items.map((i) => (Number.isFinite(i.value) ? i.value : 0))),
    [items],
  );
  const sumVal = useMemo(
    () => total ?? items.reduce((s, i) => s + (Number.isFinite(i.value) ? i.value : 0), 0),
    [items, total],
  );

  if (!items.length) return null;

  return (
    <div className="space-y-2" data-testid="signal-bar-list">
      {visible.map((item, idx) => {
        const pctOfMax = Math.max(2, Math.min(100, Math.round((item.value / maxVal) * 100)));
        const sharePct = sumVal > 0 ? Math.round((item.value / sumVal) * 1000) / 10 : 0;
        const barColor = item.color ?? color;
        return (
          <div
            key={item.key}
            className="group rounded-xl border border-white/5 bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/15 px-3 py-2 transition-colors"
          >
            <div className="flex items-center justify-between gap-2 mb-1">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                {showRank ? (
                  <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-white/5 text-[10px] font-semibold tabular-nums text-muted">
                    {idx + 1}
                  </span>
                ) : null}
                <div className="text-xs font-medium text-ink truncate" title={item.label} dir="auto">
                  {item.href ? (
                    <a
                      href={item.href}
                      target="_blank"
                      rel="noreferrer"
                      className="hover:text-biu-sky hover:underline"
                    >
                      {item.label}
                    </a>
                  ) : (
                    renderRichCellText(item.label)
                  )}
                </div>
                {item.badge ? (
                  <span className="shrink-0 rounded-full border border-white/15 bg-white/5 px-2 py-0.5 text-[10px] text-muted">
                    {item.badge}
                  </span>
                ) : null}
                {item.secondaryValue && item.secondaryValue > 0 ? (
                  <span
                    className="shrink-0 rounded-full border border-biu-sky/40 bg-biu-sky/10 px-1.5 py-0.5 text-[10px] text-biu-sky tabular-nums"
                    title={item.secondaryLabel ?? "Internal project links"}
                  >
                    ↺ {item.secondaryValue.toLocaleString()}
                  </span>
                ) : null}
              </div>
              <div className="flex items-center gap-2 shrink-0 text-xs tabular-nums">
                <span className="font-semibold text-ink">
                  {formatMetricNumber(item.value, unit)}
                </span>
                {showShare && sumVal > 0 ? (
                  <span className="text-[11px] text-muted w-11 text-right">
                    {sharePct}%
                  </span>
                ) : null}
              </div>
            </div>
            <div className="h-2 w-full rounded-full overflow-hidden bg-white/[0.07]">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${pctOfMax}%`,
                  background: `linear-gradient(90deg, ${barColor}cc, ${barColor})`,
                }}
              />
            </div>
          </div>
        );
      })}
      {items.length > maxItems ? (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="text-xs text-biu-sky hover:underline pt-1"
        >
          {showAll ? "Show top rows" : `Show all ${items.length} rows`}
        </button>
      ) : null}
    </div>
  );
}

export function SignalColumnChart({
  labels,
  series,
  unit,
  stacked = false,
  height = 220,
}: {
  labels: string[];
  series: SignalSeries[];
  unit?: string;
  stacked?: boolean;
  height?: number;
}) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  const maxVal = useMemo(() => {
    let peak = 1;
    for (let i = 0; i < labels.length; i += 1) {
      if (stacked) {
        const sum = series.reduce((acc, s) => acc + (s.values[i] ?? 0), 0);
        if (sum > peak) peak = sum;
      } else {
        for (const s of series) {
          const v = s.values[i] ?? 0;
          if (v > peak) peak = v;
        }
      }
    }
    return peak;
  }, [labels, series, stacked]);

  const ticks = useMemo(() => [1, 0.75, 0.5, 0.25, 0].map((r) => Math.round(maxVal * r)), [maxVal]);

  if (!labels.length || !series.length) return null;

  return (
    <div className="space-y-3" data-testid="signal-column-chart">
      <div className="flex gap-2">
        <div
          className="flex flex-col justify-between text-[10px] text-muted tabular-nums pr-1 select-none"
          style={{height}}
        >
          {ticks.map((t, idx) => (
            <span key={idx} className="leading-none text-right">
              {formatMetricNumber(t)}
            </span>
          ))}
        </div>
        <div className="relative flex-1" style={{height}}>
          <div className="pointer-events-none absolute inset-0 flex flex-col justify-between">
            {ticks.map((_, idx) => (
              <div key={idx} className="w-full border-b border-white/[0.07]" />
            ))}
          </div>
          <div className="relative z-10 flex h-full items-end gap-1.5 px-1">
            {labels.map((label, i) => {
              const isHovered = hoveredIdx === i;
              return (
                <div
                  key={`${label}-${i}`}
                  className={`relative flex-1 h-full flex flex-col justify-end items-center rounded-t-md transition-colors px-0.5 ${
                    isHovered ? "bg-white/[0.06]" : ""
                  }`}
                  onMouseEnter={() => setHoveredIdx(i)}
                  onMouseLeave={() => setHoveredIdx(null)}
                >
                  {isHovered ? (
                    <div className="pointer-events-none absolute -top-2 left-1/2 -translate-x-1/2 -translate-y-full z-20 rounded-xl border border-white/15 bg-[var(--surface-table-head)] px-2.5 py-1.5 text-[11px] shadow-xl whitespace-nowrap">
                      <div className="font-semibold text-ink mb-0.5" dir="auto">{label}</div>
                      {series.map((s, sIdx) => (
                        <div key={s.name} className="flex items-center justify-between gap-3 tabular-nums">
                          <span className="flex items-center gap-1 text-muted">
                            <span
                              className="inline-block h-2 w-2 rounded-full"
                              style={{background: s.color ?? CHART_PALETTE[sIdx % CHART_PALETTE.length]}}
                            />
                            {s.name}
                          </span>
                          <span className="font-medium text-ink">
                            {formatMetricNumber(s.values[i], unit)}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {stacked ? (
                    <div className="w-full max-w-[2.25rem] flex flex-col-reverse rounded-t-md overflow-hidden">
                      {series.map((s, sIdx) => {
                        const val = Math.max(0, s.values[i] ?? 0);
                        const hPct = (val / maxVal) * 100;
                        const fill = s.color ?? CHART_PALETTE[sIdx % CHART_PALETTE.length];
                        return (
                          <div
                            key={s.name}
                            style={{height: `${ (val > 0 ? Math.max(2, hPct) : 0) * (height / 100) }px`, background: fill}}
                          />
                        );
                      })}
                    </div>
                  ) : (
                    <div className="w-full h-full flex items-end justify-center gap-0.5">
                      {series.map((s, sIdx) => {
                        const val = Math.max(0, s.values[i] ?? 0);
                        const hPct = val > 0 ? Math.max(2, (val / maxVal) * 100) : 0;
                        const fill = s.color ?? CHART_PALETTE[sIdx % CHART_PALETTE.length];
                        return (
                          <div
                            key={s.name}
                            className="flex-1 max-w-[2rem] rounded-t-md transition-all duration-300"
                            style={{
                              height: `${hPct}%`,
                              background: `linear-gradient(180deg, ${fill}, ${fill}aa)`,
                              opacity: hoveredIdx === null || isHovered ? 0.95 : 0.55,
                            }}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div className="flex items-start gap-1.5 pl-8 pr-1">
        {labels.map((label, i) => {
          const showTick = labels.length <= 10 || i === 0 || i === labels.length - 1 || i % Math.ceil(labels.length / 6) === 0;
          return (
            <div key={`${label}-${i}`} className="flex-1 min-w-0 text-center">
              {showTick ? (
                <span className="block truncate text-[10px] text-muted" title={label} dir="auto">
                  {label}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
      {series.length > 1 ? (
        <div className="flex flex-wrap items-center justify-center gap-4 pt-1 text-xs">
          {series.map((s, sIdx) => (
            <span key={s.name} className="flex items-center gap-1.5 text-muted">
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{background: s.color ?? CHART_PALETTE[sIdx % CHART_PALETTE.length]}}
              />
              <span>{s.name}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function SignalAreaChart({
  labels,
  series,
  unit,
  height = 220,
}: {
  labels: string[];
  series: SignalSeries[];
  unit?: string;
  height?: number;
}) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const W = 640;
  const H = height;
  const PAD_X = 12;
  const PAD_Y = 14;

  const maxVal = useMemo(() => {
    let peak = 1;
    for (const s of series) {
      for (const v of s.values) {
        if (typeof v === "number" && v > peak) peak = v;
      }
    }
    return peak;
  }, [series]);

  if (labels.length < 2 || !series.length) {
    return <SignalColumnChart labels={labels} series={series} unit={unit} height={height} />;
  }

  const xAt = (i: number) => PAD_X + (i / (labels.length - 1)) * (W - PAD_X * 2);
  const yAt = (v: number) => H - PAD_Y - (Math.max(0, v) / maxVal) * (H - PAD_Y * 2);

  return (
    <div className="space-y-2" data-testid="signal-area-chart">
      <div className="relative w-full" style={{height}}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="none" className="overflow-visible">
          <defs>
            {series.map((s, sIdx) => {
              const color = s.color ?? CHART_PALETTE[sIdx % CHART_PALETTE.length];
              return (
                <linearGradient key={s.name} id={`area-grad-${sIdx}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.38} />
                  <stop offset="100%" stopColor={color} stopOpacity={0.02} />
                </linearGradient>
              );
            })}
          </defs>
          {[0, 0.25, 0.5, 0.75, 1].map((r, idx) => {
            const y = PAD_Y + r * (H - PAD_Y * 2);
            return (
              <line
                key={idx}
                x1={0}
                y1={y}
                x2={W}
                y2={y}
                stroke="currentColor"
                strokeOpacity={0.08}
                strokeWidth={1}
              />
            );
          })}
          {series.map((s, sIdx) => {
            const color = s.color ?? CHART_PALETTE[sIdx % CHART_PALETTE.length];
            const pts = labels.map((_, i) => `${xAt(i).toFixed(1)},${yAt(s.values[i] ?? 0).toFixed(1)}`);
            const areaPts = `${xAt(0).toFixed(1)},${(H - PAD_Y).toFixed(1)} ${pts.join(" ")} ${xAt(labels.length - 1).toFixed(1)},${(H - PAD_Y).toFixed(1)}`;
            return (
              <g key={s.name}>
                <polygon points={areaPts} fill={`url(#area-grad-${sIdx})`} />
                <polyline
                  points={pts.join(" ")}
                  fill="none"
                  stroke={color}
                  strokeWidth={2.25}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {hoveredIdx !== null && s.values[hoveredIdx] !== null ? (
                  <circle
                    cx={xAt(hoveredIdx)}
                    cy={yAt(s.values[hoveredIdx] ?? 0)}
                    r={4.5}
                    fill={color}
                    stroke="#00190d"
                    strokeWidth={1.5}
                  />
                ) : null}
              </g>
            );
          })}
          {hoveredIdx !== null ? (
            <line
              x1={xAt(hoveredIdx)}
              y1={PAD_Y}
              x2={xAt(hoveredIdx)}
              y2={H - PAD_Y}
              stroke="#77cce5"
              strokeOpacity={0.45}
              strokeDasharray="3 3"
              strokeWidth={1}
            />
          ) : null}
        </svg>
        <div className="absolute inset-0 flex">
          {labels.map((label, i) => (
            <div
              key={`${label}-${i}`}
              className="flex-1 h-full"
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
            />
          ))}
        </div>
        {hoveredIdx !== null ? (
          <div className="pointer-events-none absolute top-2 right-2 z-20 rounded-xl border border-white/15 bg-[var(--surface-table-head)] px-3 py-1.5 text-xs shadow-xl">
            <div className="font-semibold text-ink" dir="auto">{labels[hoveredIdx]}</div>
            {series.map((s, sIdx) => (
              <div key={s.name} className="flex items-center justify-between gap-3 tabular-nums">
                <span className="flex items-center gap-1.5 text-muted">
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{background: s.color ?? CHART_PALETTE[sIdx % CHART_PALETTE.length]}}
                  />
                  {s.name}
                </span>
                <span className="font-semibold text-ink">
                  {formatMetricNumber(s.values[hoveredIdx], unit)}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <div className="flex justify-between text-[10px] text-muted px-2 tabular-nums">
        <span>{labels[0]}</span>
        {labels.length > 2 ? <span>{labels[Math.floor(labels.length / 2)]}</span> : null}
        <span>{labels[labels.length - 1]}</span>
      </div>
    </div>
  );
}

export function SignalDonutChart({
  items,
  unit,
  centerLabel,
  size = 156,
  thickness = 20,
}: {
  items: SignalBarItem[];
  unit?: string;
  centerLabel?: string;
  size?: number;
  thickness?: number;
}) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const positive = useMemo(() => items.filter((i) => i.value > 0), [items]);
  const total = useMemo(() => positive.reduce((s, i) => s + i.value, 0), [positive]);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const gap = positive.length > 1 ? 2 : 0;

  const arcs = useMemo(() => {
    if (total <= 0) return [];
    let offset = 0;
    return positive.map((item, idx) => {
      const share = item.value / total;
      const rawLen = share * circumference;
      const length = Math.max(2, rawLen - gap);
      const start = offset;
      offset += rawLen;
      return {
        item,
        color: item.color ?? CHART_PALETTE[idx % CHART_PALETTE.length],
        length,
        start,
        pct: Math.round(share * 1000) / 10,
      };
    });
  }, [positive, total, circumference, gap]);

  if (!positive.length) return null;

  const activeArc = activeKey ? arcs.find((a) => a.item.key === activeKey) : null;

  return (
    <div className="flex flex-wrap items-center gap-5" data-testid="signal-donut-chart">
      <div className="relative shrink-0" style={{width: size, height: size}}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={thickness}
            stroke="rgba(255,255,255,0.06)"
          />
          {arcs.map(({item, color, length, start}) => {
            const dimmed = activeKey !== null && activeKey !== item.key;
            return (
              <circle
                key={item.key}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                strokeWidth={thickness}
                stroke={color}
                strokeDasharray={`${length} ${circumference - length}`}
                strokeDashoffset={-start}
                className={`cursor-pointer transition-opacity duration-200 ${dimmed ? "opacity-30" : "opacity-100"}`}
                onMouseEnter={() => setActiveKey(item.key)}
                onMouseLeave={() => setActiveKey(null)}
              >
                <title>{`${item.label}: ${formatMetricNumber(item.value, unit)}`}</title>
              </circle>
            );
          })}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center px-3">
          <span className="text-lg font-bold tabular-nums text-ink leading-tight">
            {formatMetricNumber(activeArc ? activeArc.item.value : total, unit)}
          </span>
          <span className="text-[10px] text-muted truncate max-w-full">
            {activeArc ? activeArc.item.label : (centerLabel ?? "total")}
          </span>
        </div>
      </div>

      <div className="flex-1 min-w-[12rem] space-y-1.5">
        {arcs.map(({item, color, pct}) => {
          const isLit = activeKey === item.key;
          return (
            <div
              key={item.key}
              onMouseEnter={() => setActiveKey(item.key)}
              onMouseLeave={() => setActiveKey(null)}
              className={`flex items-center justify-between gap-2 rounded-lg px-2 py-1 text-xs transition-colors cursor-default ${
                isLit ? "bg-white/10" : "hover:bg-white/5"
              }`}
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="inline-block h-2.5 w-2.5 rounded-full shrink-0" style={{background: color}} />
                <span className="truncate text-ink" dir="auto">{item.label}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0 tabular-nums">
                <span className="font-semibold text-ink">{formatMetricNumber(item.value, unit)}</span>
                <span className="text-[11px] text-muted w-10 text-right">{pct}%</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function SignalFunnel({
  steps,
  unit,
}: {
  steps: Array<{label: string; value: number; detail?: string}>;
  unit?: string;
}) {
  if (!steps.length) return null;
  const first = Math.max(1, steps[0].value);
  return (
    <div className="space-y-2.5" data-testid="signal-funnel">
      {steps.map((step, idx) => {
        const share = Math.round((step.value / first) * 1000) / 10;
        const barW = Math.max(3, Math.min(100, share));
        const color = CHART_PALETTE[idx % CHART_PALETTE.length];
        return (
          <div key={`${step.label}-${idx}`} className="space-y-1">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="font-medium text-ink" dir="auto">{step.label}</span>
              <span className="tabular-nums text-ink font-semibold">
                {formatMetricNumber(step.value, unit)}
                {idx > 0 ? <span className="ml-1.5 text-muted font-normal">· {share}%</span> : null}
              </span>
            </div>
            <div className="h-2.5 w-full rounded-full bg-white/[0.07] overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{width: `${barW}%`, background: color}}
              />
            </div>
            {step.detail ? <p className="text-[11px] text-muted">{step.detail}</p> : null}
          </div>
        );
      })}
    </div>
  );
}

type TableViewMode = "table" | "bars" | "columns" | "donut";

export function SignalDataTable({
  columns,
  rows,
  title,
  source,
  defaultMode = "table",
}: {
  columns: string[];
  rows: Array<Array<string | number | null>>;
  title?: string;
  source?: string;
  defaultMode?: TableViewMode;
}) {
  const [query, setQuery] = useState("");
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDesc, setSortDesc] = useState(true);
  const [mode, setMode] = useState<TableViewMode>(defaultMode);

  const numericCols = useMemo(() => {
    return columns.map((_, colIdx) => {
      let numCount = 0;
      let nonEmpty = 0;
      let maxVal = 0;
      for (const row of rows) {
        const cell = row[colIdx];
        if (cell === null || cell === undefined || String(cell).trim() === "") continue;
        nonEmpty += 1;
        const parsed = parseNumericCell(cell);
        if (parsed !== null) {
          numCount += 1;
          if (parsed > maxVal) maxVal = parsed;
        }
      }
      const isNumeric = nonEmpty > 0 && numCount >= Math.ceil(nonEmpty * 0.7);
      return {isNumeric, maxVal: Math.max(1, maxVal)};
    });
  }, [columns, rows]);

  const firstNumericIdx = useMemo(
    () => numericCols.findIndex((c, idx) => idx > 0 && c.isNumeric),
    [numericCols],
  );
  const labelColIdx = useMemo(() => {
    const nonNum = numericCols.findIndex((c) => !c.isNumeric);
    return nonNum >= 0 ? nonNum : 0;
  }, [numericCols]);

  const canChart = firstNumericIdx >= 0 && rows.length >= 2;

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const base = q
      ? rows.filter((r) => r.some((c) => String(c ?? "").toLowerCase().includes(q)))
      : rows;
    if (sortCol === null) return base;
    const isNum = numericCols[sortCol]?.isNumeric;
    return [...base].sort((a, b) => {
      if (isNum) {
        const na = parseNumericCell(a[sortCol]) ?? -Infinity;
        const nb = parseNumericCell(b[sortCol]) ?? -Infinity;
        return sortDesc ? nb - na : na - nb;
      }
      const sa = String(a[sortCol] ?? "");
      const sb = String(b[sortCol] ?? "");
      return sortDesc ? sb.localeCompare(sa) : sa.localeCompare(sb);
    });
  }, [rows, query, sortCol, sortDesc, numericCols]);

  const chartItems = useMemo<SignalBarItem[]>(() => {
    if (!canChart) return [];
    return filteredRows.flatMap((r, i) => {
      const label = String(r[labelColIdx] ?? "").trim();
      const val = parseNumericCell(r[firstNumericIdx]);
      if (!label || val === null) return [];
      return [{key: `${i}-${label}`, label, value: val}];
    });
  }, [canChart, filteredRows, labelColIdx, firstNumericIdx]);

  function toggleSort(idx: number) {
    if (sortCol === idx) {
      setSortDesc((d) => !d);
    } else {
      setSortCol(idx);
      setSortDesc(true);
    }
  }

  function downloadCsv() {
    const escapeCsv = (val: string | number | null | undefined) => {
      const s = String(val ?? "");
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [
      columns.map(escapeCsv).join(","),
      ...filteredRows.map((r) => r.map(escapeCsv).join(",")),
    ];
    const blob = new Blob([lines.join("\n")], {type: "text/csv;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(title || "research-table").toLowerCase().replace(/[^a-z0-9]+/g, "-")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!columns.length) return null;

  return (
    <div className="space-y-3" data-testid="signal-data-table">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 flex-1 min-w-[12rem]">
          {rows.length > 4 ? (
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Filter ${rows.length} rows…`}
              aria-label="Filter table rows"
              className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-ink w-48 focus:border-biu-sky outline-none"
            />
          ) : null}
          <span className="text-[11px] text-muted tabular-nums">
            {filteredRows.length === rows.length
              ? `${rows.length} row${rows.length === 1 ? "" : "s"}`
              : `${filteredRows.length} of ${rows.length} rows`}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          {canChart ? (
            <div className="flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5 text-[11px]">
              {(
                [
                  {id: "table", label: "Table"},
                  {id: "bars", label: "Ranked Bars"},
                  {id: "columns", label: "Columns"},
                  {id: "donut", label: "Donut"},
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setMode(tab.id)}
                  className={`px-2 py-0.5 rounded-md transition-colors ${
                    mode === tab.id
                      ? "bg-biu-sky/20 text-biu-sky font-medium"
                      : "text-muted hover:text-ink"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          ) : null}
          {rows.length > 0 ? (
            <button
              type="button"
              onClick={downloadCsv}
              className="rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/10 px-2.5 py-1 text-[11px] text-muted hover:text-ink transition-colors"
            >
              ↓ CSV
            </button>
          ) : null}
        </div>
      </div>

      {mode === "bars" && chartItems.length > 0 ? (
        <SignalBarList items={chartItems} unit={columns[firstNumericIdx]} />
      ) : mode === "columns" && chartItems.length > 0 ? (
        <SignalColumnChart
          labels={chartItems.slice(0, 16).map((i) => i.label)}
          series={[{name: columns[firstNumericIdx], values: chartItems.slice(0, 16).map((i) => i.value)}]}
        />
      ) : mode === "donut" && chartItems.length > 0 ? (
        <SignalDonutChart items={chartItems.slice(0, 10)} centerLabel={columns[firstNumericIdx]} />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-white/10 bg-[var(--surface-inset)]">
          <table className="w-full text-xs border-collapse">
            <thead className="table-head border-b border-white/10">
              <tr>
                {columns.map((col, idx) => {
                  const isNum = numericCols[idx]?.isNumeric;
                  const active = sortCol === idx;
                  return (
                    <th
                      key={`${col}-${idx}`}
                      onClick={() => toggleSort(idx)}
                      className={`py-2 px-3 font-semibold text-muted cursor-pointer select-none hover:text-ink transition-colors ${
                        isNum ? "text-right" : "text-left"
                      }`}
                    >
                      <span className="inline-flex items-center gap-1">
                        <span>{col}</span>
                        {active ? (
                          <span className="text-biu-sky text-[10px]">{sortDesc ? "↓" : "↑"}</span>
                        ) : null}
                      </span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row, rIdx) => (
                <tr
                  key={rIdx}
                  className="border-b border-white/5 last:border-0 hover:bg-white/[0.04] transition-colors"
                >
                  {columns.map((_, cIdx) => {
                    const cell = row[cIdx];
                    const colMeta = numericCols[cIdx];
                    const numVal = colMeta?.isNumeric ? parseNumericCell(cell) : null;
                    const barPct =
                      numVal !== null && colMeta && colMeta.maxVal > 0
                        ? Math.max(0, Math.min(100, Math.round((numVal / colMeta.maxVal) * 100)))
                        : 0;
                    return (
                      <td
                        key={cIdx}
                        className={`py-2 px-3 align-middle ${
                          colMeta?.isNumeric ? "text-right tabular-nums" : "text-left"
                        }`}
                      >
                        {numVal !== null ? (
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-16 h-1.5 rounded-full bg-white/[0.06] overflow-hidden hidden sm:block">
                              <div
                                className="h-full rounded-full bg-biu-sky/75"
                                style={{width: `${barPct}%`}}
                              />
                            </div>
                            <span className="font-medium text-ink">{numVal.toLocaleString()}</span>
                          </div>
                        ) : (
                          renderRichCellText(String(cell ?? ""))
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {source ? (
        <div className="text-[11px] text-muted">Source: {source}</div>
      ) : null}
    </div>
  );
}
