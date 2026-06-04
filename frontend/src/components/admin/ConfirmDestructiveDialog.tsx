import {createPortal} from "react-dom";

interface Props {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  submitting?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDestructiveDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  submitting = false,
  onCancel,
  onConfirm,
}: Props) {
  if (!open) return null;

  const dialog = (
    <div
      data-testid="confirm-destructive-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="glass p-6 w-full max-w-md space-y-4">
        <div>
          <div className="kicker mb-1">Confirm action</div>
          <h3 className="text-lg font-semibold">{title}</h3>
          <p
            className="muted text-sm mt-2"
            dangerouslySetInnerHTML={{__html: description}}
          />
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            data-testid="confirm-dialog-cancel"
            onClick={onCancel}
            disabled={submitting}
            className="button-ghost text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="confirm-dialog-confirm"
            onClick={onConfirm}
            disabled={submitting}
            className="button-primary text-sm bg-red-500/80 hover:bg-red-500 border-red-400/60"
          >
            {submitting ? "…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
}
