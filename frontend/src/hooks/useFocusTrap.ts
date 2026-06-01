/**
 * useFocusTrap — WCAG 2.1.2 / 2.4.3 / 4.1.2 compliant focus trap for
 * modal dialogs and side drawers.
 *
 * When ``active`` flips to ``true``:
 *   1. Captures ``document.activeElement`` so we can restore it later.
 *   2. Moves focus to the first focusable element inside ``containerRef``.
 *
 * While ``active`` is ``true``:
 *   - Tab from the LAST focusable wraps to the FIRST.
 *   - Shift+Tab from the FIRST focusable wraps to the LAST.
 *   - Tab events outside the container are not affected; the listener
 *     only acts when focus is currently inside the container OR when
 *     the container is empty of focusables (in which case it preventDefaults
 *     to stop focus from leaking).
 *
 * When ``active`` flips to ``false`` (or the component unmounts):
 *   - Focus is returned to the element that had it before activation.
 *
 * Focusable selector:
 *   ``a[href]``, ``button:not([disabled])``, ``textarea:not([disabled])``,
 *   ``input:not([disabled]):not([type="hidden"])``,
 *   ``select:not([disabled])``, and anything with ``tabindex`` other
 *   than ``-1``. Hidden elements (``offsetParent === null``) are filtered
 *   out at trap time.
 */

import { useEffect } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  'input:not([disabled]):not([type="hidden"])',
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusable(container: HTMLElement): HTMLElement[] {
  const nodes = container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
  const out: HTMLElement[] = [];
  nodes.forEach((node) => {
    if (node.offsetParent === null) return;
    out.push(node);
  });
  return out;
}

export function useFocusTrap(
  active: boolean,
  containerRef: React.RefObject<HTMLElement>,
): void {
  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    const previouslyFocused =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;

    // Move focus to first focusable in the container.
    const initialFocusables = getFocusable(container);
    if (initialFocusables.length > 0) {
      initialFocusables[0].focus();
    } else {
      // No focusable child — focus the container itself so keyboard
      // users have a starting point and Escape handlers still work.
      const prevTabIndex = container.getAttribute("tabindex");
      if (prevTabIndex === null) container.setAttribute("tabindex", "-1");
      container.focus();
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key !== "Tab") return;
      if (!container) return;

      const focusables = getFocusable(container);
      if (focusables.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const activeEl = document.activeElement;

      if (event.shiftKey) {
        if (activeEl === first || !container.contains(activeEl)) {
          event.preventDefault();
          last.focus();
        }
        return;
      }

      if (activeEl === last || !container.contains(activeEl)) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown, true);

    return () => {
      document.removeEventListener("keydown", handleKeyDown, true);
      if (previouslyFocused && document.body.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [active, containerRef]);
}
