import {useEffect, useState} from "react";

import {JudgeModels, type Tier1ModelList} from "@/api/judgeModels";

export function useTier1Model(defaultFromList = true) {
  const [list, setList] = useState<Tier1ModelList | null>(null);
  const [tierModel, setTierModel] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void JudgeModels.list()
      .then((data) => {
        if (cancelled) return;
        setList(data);
        if (defaultFromList) {
          const preferred = data.models.find((m) => m.id === data.default && m.available)
            ?? data.models.find((m) => m.available)
            ?? data.models[0];
          setTierModel(preferred?.id ?? data.default);
        }
      })
      .catch(() => {
        if (!cancelled) setList(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [defaultFromList]);

  const selected = list?.models.find((m) => m.id === tierModel) ?? null;

  return {
    list,
    tierModel,
    setTierModel,
    loading,
    selected,
  };
}

interface Tier1ModelSelectProps {
  tierModel: string;
  onChange: (modelId: string) => void;
  disabled?: boolean;
  list: Tier1ModelList | null;
  loading?: boolean;
}

export function Tier1ModelSelect({
  tierModel,
  onChange,
  disabled,
  list,
  loading,
}: Tier1ModelSelectProps) {
  const models = list?.models ?? [];
  const selected = models.find((m) => m.id === tierModel);

  return (
    <div className="flex flex-col gap-1 text-xs">
      <label className="muted" htmlFor="tier1-model-select">Tier-1 judge</label>
      <select
        id="tier1-model-select"
        value={tierModel}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled || loading || models.length === 0}
        className="input-glass !py-1 text-sm min-w-[220px]"
        title={
          selected && !selected.available
            ? "This model is not configured on the server — pick another or add credentials."
            : selected && !selected.supports_agentic
              ? "Agentic escalation is disabled for this model; verification runs in linear mode."
              : undefined
        }
      >
        {loading && <option value="">Loading…</option>}
        {!loading && models.length === 0 && <option value="">No models</option>}
        {models.map((m) => (
          <option key={m.id} value={m.id} disabled={!m.available}>
            {m.label}{!m.available ? " (unavailable)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
