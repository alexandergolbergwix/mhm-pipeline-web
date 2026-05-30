/**
 * Vitest config — unit + component tests for the MHM Pipeline web app.
 *
 * Mirrors the production Vite config (path alias `@` → `src/`, React
 * plugin) so a component compiles in tests exactly the way it does at
 * runtime. We deliberately do NOT inherit `vite.config.ts` so e.g. the
 * production proxy / build config doesn't leak into the test env.
 *
 * Run: `yarn test:unit` (one-shot) / `yarn test:unit:watch` (HMR).
 */

import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";


export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    globals: true,
    include: ["tests/**/*.test.{ts,tsx}", "tests/**/*.spec.{ts,tsx}"],
    // Playwright e2e specs live under ./e2e — don't let Vitest grab them.
    exclude: ["node_modules", "dist", "e2e/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      exclude: [
        "tests/**", "e2e/**", "node_modules/**", "dist/**",
        "**/*.d.ts", "**/*.config.*",
      ],
    },
  },
});
