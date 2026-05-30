/**
 * Vitest setup file — runs once per test worker, before any test.
 *
 * Three things this setup pins:
 *
 *  • `@testing-library/jest-dom` matchers (`toBeInTheDocument`,
 *    `toHaveAttribute`, …) imported globally so every `expect()` call
 *    has access without per-test boilerplate.
 *
 *  • `cleanup()` after each test — unmounts every rendered component
 *    so DOM artefacts from one test never leak into the next. Without
 *    this, multiple `render()` calls in one file stack their nodes
 *    onto `document.body` and `getByText` becomes ambiguous.
 *
 *  • `ResizeObserver` + `matchMedia` shims — jsdom doesn't ship them
 *    and several Radix-style components fall back to them. Provide
 *    no-op constructors so render() doesn't throw.
 */

import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";


afterEach(() => {
  cleanup();
});


// ── jsdom shims ──────────────────────────────────────────────────────

class ResizeObserverMock {
  observe   = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
// @ts-expect-error — augmenting the jsdom global
globalThis.ResizeObserver = ResizeObserverMock;


// matchMedia — read-only-ish on jsdom; define if missing.
if (typeof globalThis.matchMedia !== "function") {
  globalThis.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
}
