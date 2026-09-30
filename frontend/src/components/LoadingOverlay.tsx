import {GlassPill} from "@/components/glass";

function ProgressBar() {
  return (
    <div className="mt-2 mx-auto w-32 h-1 rounded-full overflow-hidden bg-white/10">
      <div className="h-full w-1/3 bg-biu-sky animate-pulse" />
    </div>
  );
}

/** Centered loading card for full-page or section placeholders. */
export function LoadingPanel({
  title,
  detail,
}: {
  title: string;
  detail?: string | null;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-10">
      <GlassPill as="div" className="px-4 py-3 text-center space-y-1.5 max-w-md">
        <div className="text-sm text-ink">{title}</div>
        {detail ? <div className="muted text-[11px]">{detail}</div> : null}
        <ProgressBar />
      </GlassPill>
    </div>
  );
}

/** Frosted overlay for content that is still loading underneath. */
export function LoadingOverlay({
  message,
  detail,
  className = "",
  onDismiss,
  dismissLabel,
}: {
  message: string;
  detail?: string | null;
  className?: string;
  /** Long waits (e.g. studio builds) stay useful in the job tray — let the
   *  curator hide the frosted overlay and watch the inline progress instead. */
  onDismiss?: () => void;
  dismissLabel?: string;
}) {
  return (
    <div
      className={`absolute inset-0 z-20 flex items-center justify-center
                  bg-black/40 backdrop-blur-sm rounded-2xl ${className}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <GlassPill as="div" className="px-4 py-3 text-center space-y-1.5 max-w-sm">
        <div className="text-sm text-ink">{message}</div>
        {detail ? <div className="muted text-[11px]">{detail}</div> : null}
        <ProgressBar />
        {onDismiss && (
          <button
            type="button"
            className="button-ghost text-[11px]"
            onClick={onDismiss}
          >
            {dismissLabel ?? "Hide"}
          </button>
        )}
      </GlassPill>
    </div>
  );
}
