/**
 * Smoke e2e — login page mounts and renders the auth form.
 *
 * This is the GREEN baseline for CI. The full login flow needs a
 * backend with a real user; that's the next spec to write. Here we
 * pin the cheapest thing that catches "is the SPA bundle broken":
 *
 *  • the document title from `index.html` reaches the browser
 *  • the login route renders a heading ("MHM Pipeline")
 *  • the email + password inputs exist + accept focus
 *  • the submit button exists
 *
 * Requires the dev server to be running on http://localhost:5173 (or
 * pass E2E_BASE_URL=...). Run with `yarn test:e2e`.
 */

import { expect, test } from "@playwright/test";


test.describe("Login page smoke", () => {
  test("the page title contains 'MHM'", async ({ page }) => {
    await page.goto("/login");
    // `index.html` ships `<title>MHM Pipeline</title>`; React doesn't
    // touch it for the login route. The title is the cheapest way to
    // confirm the document made it from disk → browser.
    await expect(page).toHaveTitle(/MHM/i);
  });

  test("the login form is visible", async ({ page }) => {
    await page.goto("/login");
    await expect(
      page.getByRole("heading", { name: /MHM Pipeline/i }),
    ).toBeVisible();

    // The auth form has type="email" + type="password" inputs. We
    // don't pin a specific accessible name because the labels are
    // ``<span class="text-sm muted">Email</span>`` — `getByLabel`
    // would catch them, and so would the input type.
    const email = page.getByLabel(/email/i);
    await expect(email).toBeVisible();
    await email.click();
    await expect(email).toBeFocused();

    const password = page.getByLabel(/password/i);
    await expect(password).toBeVisible();

    // A submit-flavoured control should exist (button or input).
    await expect(
      page.getByRole("button").filter({ hasText: /sign in|continue|log\s*in/i }),
    ).toBeVisible();
  });
});
