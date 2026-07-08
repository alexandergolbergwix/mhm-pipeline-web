import {useEffect, useRef, type ReactNode} from "react";
import {createPortal} from "react-dom";

import {Glass} from "@/components/glass";

export interface ClickDetailPopoverProps {
  x: number;
  y: number;
  title: string;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
  panelClassName?: string;
}

export function ClickDetailPopover({x, y, title, onClose, children, testId, panelClassName}: ClickDetailPopoverProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      const node = ref.current;
      if (node && event.target instanceof Node && !node.contains(event.target)) {
        onClose();
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const left = Math.min(x, window.innerWidth - 340);
  const top = Math.min(y + 8, window.innerHeight - 240);
  const panelCls = panelClassName ?? "w-[min(340px,calc(100vw-1.5rem))] max-h-[min(240px,50vh)]";

  return createPortal(
    <Glass
      ref={ref}
      className={`fixed z-[60] overflow-y-auto p-3 space-y-2 shadow-xl text-xs ${panelCls}`}
      style={{left, top}}
      data-testid={testId}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="font-medium text-sm text-ink">{title}</div>
      <div className="space-y-1.5">{children}</div>
      <button type="button" className="button-ghost text-[10px] w-full" onClick={onClose}>
        Close
      </button>
    </Glass>,
    document.body,
  );
}
