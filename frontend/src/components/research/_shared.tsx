import React from "react";

export function Spinner() {
  return (
    <div className="flex items-center gap-3 py-10 justify-center">
      <span className="animate-spin text-xl text-biu-sky">⟳</span>
      <span className="muted text-sm">Loading…</span>
    </div>
  );
}

export function ErrorBox({msg}: {msg: string}) {
  return (
    <div className="glass p-4 text-red-400 text-sm rounded-lg border border-red-500/30">
      {msg}
    </div>
  );
}

export function EmptyBox({msg}: {msg: string}) {
  return (
    <div className="glass p-6 text-center muted text-sm rounded-lg">
      {msg}
    </div>
  );
}

export function PanelShell({
  title,
  subtitle,
  children,
  loading,
  empty,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  loading?: boolean;
  empty?: boolean;
}) {
  return (
    <section className="glass p-6 space-y-4">
      <div className="space-y-0.5">
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        {subtitle && <p className="muted text-sm">{subtitle}</p>}
      </div>
      {loading ? (
        <div className="flex items-center gap-3 py-8 justify-center">
          <span className="animate-spin text-xl">⟳</span>
          <span className="muted text-sm">Loading…</span>
        </div>
      ) : empty ? (
        <p className="muted text-sm py-8 text-center">
          No data yet — build the RDF graph for your runs first.
        </p>
      ) : children}
    </section>
  );
}

export function StatPill({label, value}: {label: string; value: number | string}) {
  return (
    <div className="flex flex-col items-center gap-1 px-4 py-3 rounded-xl bg-white/5 border border-white/10">
      <span className="text-2xl font-bold text-biu-sky tabular-nums">{value.toLocaleString()}</span>
      <span className="text-xs muted">{label}</span>
    </div>
  );
}

export function useAsync<T>(
  fn: () => Promise<T>,
  deps: React.DependencyList,
  skip?: boolean,
): {data: T | null; loading: boolean; error: string | null} {
  const [data, setData] = React.useState<T | null>(null);
  const [loading, setLoading] = React.useState(!skip);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (skip) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch((e) => { if (!cancelled) { setError(String(e?.message ?? e)); setLoading(false); } });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, skip]);

  return {data, loading, error};
}
