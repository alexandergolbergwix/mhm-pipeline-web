import {GlassPill} from "@/components/glass";
import type {StudioItem} from "@/api/wikidataStudio";
import {
  resolveWikidataItemDataStatus,
  WIKIDATA_DATA_STATUS_LABEL,
  type WikidataItemDataStatus,
} from "@/utils/wikidataItemDataStatus";

const DATA_STATUS_TONE: Record<WikidataItemDataStatus, string> = {
  new: "text-amber-200",
  will_update: "text-warn",
  updated: "text-biu-sky",
};

export interface WikidataItemDataStatusBadgeProps {
  item: StudioItem;
}

export function WikidataItemDataStatusBadge({item}: WikidataItemDataStatusBadgeProps) {
  const status = resolveWikidataItemDataStatus(item);
  return (
    <GlassPill
      className={`px-2 py-0.5 text-[10px] kicker ${DATA_STATUS_TONE[status]}`}
      title={WIKIDATA_DATA_STATUS_LABEL[status]}
    >
      {WIKIDATA_DATA_STATUS_LABEL[status]}
    </GlassPill>
  );
}
