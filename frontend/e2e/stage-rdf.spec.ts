/**
 * E2E suite for the RDF graph view (StageRdf) filter + label work.
 *
 * Every backend endpoint is mocked via [installRdfMocks]. The spec
 * focuses on the user-visible filter surface — we don't try to
 * inspect Cytoscape's internal canvas; instead we assert on the
 * visible-count badge that StageRdf surfaces in DOM (the same
 * source-of-truth the user reads), and on aria/dom-attribute state
 * on every chip.
 */
import { expect, test } from "@playwright/test";

import {
  TEST_RUN_ID,
  installRdfMocks,
  makeRdfState,
  gotoRdf,
} from "./fixtures/rdf-fixtures";

void TEST_RUN_ID;

test.describe.configure({ mode: "parallel" });

test.describe("RDF graph view — filter bar + smart search", () => {

  test("filter bar renders once the graph loads", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);

    await expect(page.getByTestId("graph-filters")).toBeVisible({timeout: 15000});
    await expect(page.getByTestId("graph-search")).toBeVisible();
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText(`${state.graph.nodes.length} / ${state.graph.nodes.length} in view`);
  });

  test("type chips render the 8 ontology classes with counts", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    for (const t of [
      "Manuscript", "Person", "Work", "Place",
      "Event", "Organization", "Codicological", "Other",
    ]) {
      await expect(page.getByTestId(`graph-chip-type-${t}`)).toBeVisible();
    }
    // Manuscript chip should read "Manuscript (5)" based on the fixture
    await expect(page.getByTestId("graph-chip-type-Manuscript"))
      .toContainText("(5)");
    await expect(page.getByTestId("graph-chip-type-Person"))
      .toContainText("(8)");
  });

  test("clicking the Person chip drops the visible-count to 8", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    await page.getByTestId("graph-chip-type-Person").click();
    await expect(page.getByTestId("graph-chip-type-Person"))
      .toHaveAttribute("data-active", "true");
    // 8 persons of 30 nodes survive (selectedId is null at this point)
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("8 / 8 in view");
  });

  test("predicate chips render top predicates by frequency", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    // "owned by" has 12 edges — highest count, must appear in the head
    await expect(page.getByTestId("graph-chip-pred-owned-by")).toBeVisible();
    await expect(page.getByTestId("graph-chip-pred-owned-by"))
      .toContainText("(12)");
  });

  test("clicking owned-by predicate chip greys non-matching edges + orphan nodes", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    await page.getByTestId("graph-chip-pred-owned-by").click();

    // 12 owned-by edges connect 5 MSes + 8 persons = 13 surviving nodes
    // (every other type becomes an orphan).
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("13 / 13 in view");
  });

  test("free-text search matches node labels (Maimonides) + edge predicates (owner)", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    // Maimonides matches exactly one node
    await page.getByTestId("graph-search").fill("Maimonides");
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("1 / 1 in view", {timeout: 8000});

    // Clear and search edge predicate — "owned" hits "owned by"
    // edges, expanding to their 13 endpoint nodes.
    await page.getByTestId("graph-search").fill("");
    await page.getByTestId("graph-search").fill("owned");
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("13 / 13 in view", {timeout: 8000});
  });

  test("SHACL chip appears only after Validate runs", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    // Before validation: SHACL chip is hidden
    await expect(page.getByTestId("graph-chip-shacl")).toHaveCount(0);

    // Click the Validate button — fires the mock /validate route
    await page.getByRole("button", { name: /Validate \(SHACL\)/ }).click();

    // SHACL chip materialises with the 2-violation count
    await expect(page.getByTestId("graph-chip-shacl"))
      .toBeVisible({ timeout: 3000 });
    await expect(page.getByTestId("graph-chip-shacl"))
      .toContainText("SHACL flagged (2)");

    // Click it — visible set drops to the 2 flagged nodes + their
    // 1-hop neighbours. Fixture: urn:ms:1 + urn:p:1 are flagged.
    await page.getByTestId("graph-chip-shacl").click();
    await expect(page.getByTestId("graph-chip-shacl"))
      .toHaveAttribute("data-active", "true");
    // The visible count must drop strictly below 30 (the exact
    // value depends on 1-hop neighbours and is fine to leave
    // loose so the test isn't a fixture-shape oracle).
    const txt = await page.getByTestId("graph-visible-count").textContent();
    expect(txt).toBeTruthy();
    const match = (txt ?? "").match(/(\d+)\s*\/\s*30/);
    expect(match).not.toBeNull();
    const visible = Number(match![1]);
    expect(visible).toBeGreaterThan(0);
    expect(visible).toBeLessThan(30);
  });

  test("'Clear all' restores the full graph and resets the search box", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    // Apply a couple of filters
    await page.getByTestId("graph-chip-type-Person").click();
    await page.getByTestId("graph-search").fill("Maimonides");
    await expect(page.getByTestId("graph-clear-filters")).toBeVisible();

    // Clear all
    await page.getByTestId("graph-clear-filters").click();

    await expect(page.getByTestId("graph-search")).toHaveValue("");
    await expect(page.getByTestId("graph-chip-type-Person"))
      .toHaveAttribute("data-active", "false");
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("30 / 30 in view");
  });

  test("type filter + free-text search AND-combine", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    // Person chip → 8. Adding the search "Maimonides" → 1.
    await page.getByTestId("graph-chip-type-Person").click();
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("8 / 8 in view");

    await page.getByTestId("graph-search").fill("Maimonides");
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("1 / 1 in view", {timeout: 8000});
  });

  test("multiple type chips OR-combine within the row", async ({ page }) => {
    const state = makeRdfState();
    await installRdfMocks(page, state);
    await gotoRdf(page);
    await page.getByTestId("graph-filters").waitFor();

    // Person (8) + Work (5) → 13 visible
    await page.getByTestId("graph-chip-type-Person").click();
    await page.getByTestId("graph-chip-type-Work").click();
    await expect(page.getByTestId("graph-visible-count"))
      .toContainText("13 / 13 in view");
  });
});
