import {useCallback, useEffect, useState} from "react";

import {ApiError} from "@/api/client";
import {HmoStudioItems, type HmoStudioItem} from "@/api/hmoStudioItems";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {SectionImportButton} from "@/components/import/SectionImportButton";
import {Glass} from "@/components/glass";
import {HmoItemDetailDrawer} from "@/components/hmo/HmoItemDetailDrawer";
import {HmoItemTable} from "@/components/hmo/HmoItemTable";
import {HmoItemVerificationModal} from "@/components/hmo/HmoItemVerificationModal";
import {useHmoItemVerifySession} from "@/hooks/useHmoItemVerifySession";

export interface HmoItemsPanelProps {
  runId: string;
  projectId?: string;
  buildPresent: boolean;
  refreshToken?: number;
}

export function HmoItemsPanel({runId, projectId, buildPresent, refreshToken}: HmoItemsPanelProps) {
  const [items, setItems] = useState<HmoStudioItem[]>([]);
  const [filteredIds, setFilteredIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<HmoStudioItem | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyIds, setVerifyIds] = useState<string[] | undefined>(undefined);

  const verifySession = useHmoItemVerifySession(runId, () => {
    void load();
  });

  const load = useCallback(async () => {
    if (!buildPresent) return;
    setLoading(true);
    setError(null);
    try {
      const res = await HmoStudioItems.list(runId);
      setItems(res.items);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }, [buildPresent, runId]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const handleToggleApproved = useCallback(async (item: HmoStudioItem, next: boolean) => {
    await HmoStudioItems.patchOverride(runId, item.local_id, {approved: next});
    await load();
  }, [load, runId]);

  return (
    <Glass as="section" className="p-6 space-y-4" data-testid="hmo-items-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">Wikibase Items</div>
          <h3 className="text-lg font-medium">{items.length} resolved item{items.length === 1 ? "" : "s"}</h3>
          <p className="muted text-sm mt-1">
            Review table for this run&apos;s item build. <b>On wiki?</b> means a live QID mapping exists;
            <b> Last push</b> is the latest upload attempt (new item, linked existing, updated, or failed with reason).
            Open a row for overrides, AI verify, and single-item push.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SectionExportMenu section="wikibase_items" runId={runId} availableFormats={["json", "csv"]} />
          <SectionImportButton section="wikibase_items" runId={runId} accept=".json" onComplete={() => void load()} />
          <button type="button" className="button-ghost text-xs" onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={!filteredIds.length}
            data-testid="hmo-items-verify-ai"
            onClick={() => {
              setVerifyIds(filteredIds);
              setVerifyOpen(true);
            }}
          >
            Verify with AI ({filteredIds.length})
          </button>
        </div>
      </div>

      {!buildPresent && (
        <p className="muted text-sm">Build items first using the panel above.</p>
      )}
      {error && <p className="text-danger text-sm">{error}</p>}
      {buildPresent && !loading && (
        <HmoItemTable
          items={items}
          onFilteredChange={setFilteredIds}
          onOpenItem={setOpenItem}
          onToggleApproved={(item, next) => void handleToggleApproved(item, next)}
        />
      )}
      {loading && <p className="muted text-sm">Loading items…</p>}

      {openItem && (
        <HmoItemDetailDrawer
          runId={runId}
          projectId={projectId}
          item={openItem}
          allItems={items}
          onClose={() => setOpenItem(null)}
          onSaved={() => void load()}
          onVerify={() => {
            setVerifyIds([openItem.local_id]);
            setVerifyOpen(true);
          }}
        />
      )}

      {verifyOpen && (
        <HmoItemVerificationModal
          runId={runId}
          scopeLabel={verifyIds?.length === 1 ? `Item ${verifyIds[0]}` : `${verifyIds?.length ?? 0} items`}
          itemIds={verifyIds}
          session={verifySession}
          onClose={() => setVerifyOpen(false)}
        />
      )}
    </Glass>
  );
}
