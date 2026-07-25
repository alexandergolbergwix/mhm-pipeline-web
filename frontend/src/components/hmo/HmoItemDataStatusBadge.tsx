import {GlassPill} from "@/components/glass";
import {
  DATA_STATUS_LABEL,
  resolveHmoItemDataStatus,
  type HmoItemDataStatus,
} from "@/utils/hmoItemDataStatus";
import type {HmoStudioItem} from "@/api/hmoStudioItems";

const DATA_STATUS_TONE: Record<HmoItemDataStatus, string> = {
  new: "text-amber-200",
  will_update: "text-warn",
  updated: "text-biu-sky",
  failed: "text-danger",
};

export interface HmoItemDataStatusBadgeProps {
  item: HmoStudioItem;
}

export function HmoItemDataStatusBadge({item}: HmoItemDataStatusBadgeProps) {
  const status = resolveHmoItemDataStatus(item);
  return (
    <GlassPill
      className={`px-2 py-0.5 text-[10px] kicker ${DATA_STATUS_TONE[status]}`}
      title={DATA_STATUS_LABEL[status]}
      data-testid={`hmo-item-data-status-${item.local_id}`}
    >
      {DATA_STATUS_LABEL[status]}
    </GlassPill>
  );
}
