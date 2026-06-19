/**
 * useGlassOverlayLifecycle — pause the full-screen R3F LiquidGlassCanvas
 * while any modal/drawer overlay is open.
 *
 * Uses a refcount so closing one overlay does not resume R3F while another
 * is still mounted (e.g. drawer → edit modal handoff).
 */

import {useEffect} from "react";

let overlayCount = 0;

function throttleGlass(): void {
  if (overlayCount === 1) {
    window.dispatchEvent(new CustomEvent("mhm-glass-throttle"));
  }
}

function resumeGlass(): void {
  if (overlayCount === 0) {
    window.dispatchEvent(new CustomEvent("mhm-glass-resume"));
  }
}

export function useGlassOverlayLifecycle(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    overlayCount += 1;
    throttleGlass();
    return () => {
      overlayCount = Math.max(0, overlayCount - 1);
      resumeGlass();
    };
  }, [active]);
}

/** Test helper — reset overlay refcount between tests. */
export function resetGlassOverlayCount(): void {
  overlayCount = 0;
}
