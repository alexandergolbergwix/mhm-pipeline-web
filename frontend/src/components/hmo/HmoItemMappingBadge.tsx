import {GlassPill} from "@/components/glass";

const MAPPING_LABEL: Record<string, string> = {
  would_create: "not on wiki",
  created: "on wiki",
};

const MAPPING_TONE: Record<string, string> = {
  would_create: "text-amber-200",
  created: "text-biu-sky",
};

export interface HmoItemMappingBadgeProps {
  status: string;
}

export function HmoItemMappingBadge({status}: HmoItemMappingBadgeProps) {
  const label = MAPPING_LABEL[status] ?? status;
  const tone = MAPPING_TONE[status] ?? "muted";
  return (
    <GlassPill className={`px-2 py-0.5 text-[10px] kicker ${tone}`} title={status}>
      {label}
    </GlassPill>
  );
}
