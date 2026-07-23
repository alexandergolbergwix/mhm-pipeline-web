import {useEffect, useRef} from "react";

import {Glass} from "@/components/glass";

export interface HmoPublishConfirmationDialogProps {
  title?: string;
  itemLabel: string;
  action: "publish" | "update";
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}

export function HmoPublishConfirmationDialog({
  title = "Confirm publication",
  itemLabel,
  action,
  onConfirm,
  onCancel,
  busy = false,
}: HmoPublishConfirmationDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onCancel();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [busy, onCancel]);

  const isUpdate = action === "update";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="presentation">
      <Glass
        variant="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="hmo-publish-dialog-title"
        className="w-full max-w-lg space-y-4 p-6"
      >
        <div>
          <div className="kicker">Publication</div>
          <h2 id="hmo-publish-dialog-title" className="text-lg font-medium">{title}</h2>
        </div>
        <p className="text-sm leading-relaxed">
          {isUpdate
            ? `This will update the published catalogue entry for ${itemLabel}.`
            : `This will publish ${itemLabel} as a new catalogue entry.`}
        </p>
        <p className="muted text-sm leading-relaxed">
          Review the entry first. The change will be recorded and can be checked in the publication history.
        </p>
        <div className="flex justify-end gap-2">
          <button ref={cancelRef} type="button" className="button-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="button-primary" onClick={onConfirm} disabled={busy}>
            {busy ? "Publishing…" : isUpdate ? "Confirm update" : "Confirm publication"}
          </button>
        </div>
      </Glass>
    </div>
  );
}
