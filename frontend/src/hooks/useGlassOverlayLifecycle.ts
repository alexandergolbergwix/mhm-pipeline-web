/**
 * useGlassOverlayLifecycle — pause the full-screen R3F LiquidGlassCanvas
 * while a modal/drawer overlay is open so nested glass surfaces and
 * backdrop work do not peg the main thread.
 *
 * Mirrors the throttle pattern in AuthorityTable scroll handling.
 */

import {useEffect} from "react";

export function useGlassOverlayLifecycle(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    window.dispatchEvent(new CustomEvent("mhm-glass-throttle"));
    return () => {
      window.dispatchEvent(new CustomEvent("mhm-glass-resume"));
    };
  }, [active]);
}
