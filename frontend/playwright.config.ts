/**
 * Playwright config — true-browser e2e for the MHM Pipeline web app.
 *
 * Single chromium project for now. Vite dev server is started
 * automatically via the `webServer` config so `yarn test:e2e` is
 * self-contained (no need to pre-start the server manually).
 *
 * Run:
 *   yarn test:e2e        # headless
 *   yarn test:e2e:ui     # UI mode for debugging
 *
 * Browsers must be installed once with:
 *   npx playwright install chromium
 */

import { defineConfig, devices } from "@playwright/test";


export default defineConfig({
  testDir: "./e2e",
  // Specs live under `e2e/**/*.spec.ts`; the smoke covers the login
  // page mount. Build out flows as the UI lands.
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  // 3 workers keeps the Vite dev server + Playwright IPC stable under
  // local load (the admin-panel serial block occupies 1 worker for ~2 min;
  // 3 total means 2 workers free for everything else).  CI keeps 2.
  workers: process.env.CI ? 2 : 3,
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  // Start the Vite dev server automatically unless an external server is
  // already running (E2E_BASE_URL env var skips this).
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: "yarn dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
