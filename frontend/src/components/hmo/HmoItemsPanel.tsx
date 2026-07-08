import {useCallback, useEffect, useMemo, useState} from "react";

import {ApiError} from "@/api/client";
import {HmoStudioItems, type HmoStudioItem} from "@/api/hmoStudioItems";
import {SectionExportMenu} from "@/components/export/SectionExportMenu";
import {SectionImportButton} from "@/components/import/SectionImportButton";
import {Glass} from "@/components/glass";
import {HmoItemDetailDrawer} from "@/components/hmo/HmoItemDetailDrawer";
import {HmoItemTable} from "@/components/hmo/HmoItemTable";
import {HmoItemVerificationModal} from "@/components/hmo/HmoItemVerificationModal";
import {ItemBuildPanel} from "@/components/hmo/ItemBuildPanel";
import {ItemUploadPanel} from "@/components/hmo/ItemUploadPanel";

export interface HmoItemsPanelProps {
  runId: string;
  projectId?: string;
  buildPresent: boolean;
  refreshToken?: number;
  rdfPresent?: boolean;
  wikibaseConfigured?: boolean;
  onLifecycleChange?: () => void;
}

export function HmoItemsPanel({
  runId,
  projectId,
  buildPresent,
  refreshToken,
  rdfPresent = false,
  wikibaseConfigured = false,
  onLifecycleChange,
}: HmoItemsPanelProps) {
  const [items, setItems] = useState<HmoStudioItem[]>([]);
  const [filteredIds, setFilteredIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<HmoStudioItem | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyIds, setVerifyIds] = useState<string[] | undefined>(undefined);
  const [verifyActionId, setVerifyActionId] = useState<string | undefined>(undefined);

  const autofixItemIds = useMemo(() => {
    const visible = new Set(filteredIds);
    return items
      .filter((item) => visible.has(item.local_id) && Boolean(item.wikibase_id?.trim()))
      .map((item) => item.local_id);
  }, [filteredIds, items]);

  const openVerify = useCallback((itemIds: string[], actionId?: string) => {
    setVerifyIds(itemIds);
    setVerifyActionId(actionId);
    setVerifyOpen(true);
  }, []);

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
            Review table for this run&apos;s item build. <b>Data status</b> shows whether each row is
            new, already on the wiki and due for a reupload, or was updated in the last push.
            <b> Last push</b> is the latest upload attempt (create, adopt, update, skip, or failed).
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
            onClick={() => openVerify(filteredIds, "audit_hmo_wikibase_item")}
          >
            Verify with AI ({filteredIds.length})
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            disabled={!autofixItemIds.length}
            title="Compare each item's live Wikibase entity against the build and propose fixes you can apply per row (requires a QID)."
            data-testid="hmo-items-autofix-ai"
            onClick={() => openVerify(autofixItemIds, "autofix_hmo_wikibase_item")}
          >
            Autofix with AI ({autofixItemIds.length})
          </button>
        </div>
      </div>

      <div className="space-y-3 border-b border-white/5 pb-4" data-testid="hmo-item-lifecycle-bar">
        <ItemBuildPanel
          runId={runId}
          rdfPresent={rdfPresent}
          compact
          onBuilt={onLifecycleChange}
        />
        <ItemUploadPanel
          runId={runId}
          wikibaseConfigured={wikibaseConfigured}
          refreshToken={refreshToken}
          compact
          onUploaded={onLifecycleChange}
        />
      </div>

      {!buildPresent && (
        <p className="muted text-sm">Build items above before the review table loads.</p>
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
          onVerify={() => openVerify([openItem.local_id], "audit_hmo_wikibase_item")}
          onAutofix={
            openItem.wikibase_id?.trim()
              ? () => openVerify([openItem.local_id], "autofix_hmo_wikibase_item")
              : undefined
          }
        />
      )}

      {verifyOpen && (
        <HmoItemVerificationModal
          runId={runId}
          scopeLabel={verifyIds?.length === 1 ? `Item ${verifyIds[0]}` : `${verifyIds?.length ?? 0} items`}
          itemIds={verifyIds}
          initialActionId={verifyActionId}
          onVerdictsLanded={() => void load()}
          onClose={() => {
            setVerifyOpen(false);
            setVerifyActionId(undefined);
          }}
        />
      )}
    </Glass>
  );
}
