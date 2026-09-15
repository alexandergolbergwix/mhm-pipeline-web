import {expect, test} from "@playwright/test";
import {installStudioMocks, makeBuildResponse, makeStudioItem, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

for (const total of [236, 736]) {
  test(`AI filters and verification include all ${total} items across API pages`, async ({page}) => {
    const items = Array.from({length: total}, (_, index) => makeStudioItem({
      local_id: `item-${index + 1}`, labels: {en: `Item ${index + 1}`},
      ai_verdict: index < 97 ? {overall: "full"} : null,
    }));
    await installStudioMocks(page, makeBuildResponse({items, total}));
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio?*`, async (route) => {
      const query = new URL(route.request().url()).searchParams;
      const number = Number(query.get("page") ?? 1);
      const size = Number(query.get("page_size") ?? 100);
      expect(size).toBeLessThanOrEqual(500);
      await route.fulfill({json: makeBuildResponse({
        items: items.slice((number - 1) * size, number * size),
        total, page: number, page_size: size,
      })});
    });
    const scopes: string[][] = [];
    page.on("request", (request) => {
      if (request.method() !== "POST" || !request.url().endsWith(`/runs/${TEST_RUN_ID}/jobs`)) return;
      const body: {kind?: string; params?: {item_ids?: string[]}} = request.postDataJSON();
      if (body.kind === "wikidata_verify") scopes.push(body.params?.item_ids ?? []);
    });
    await page.addInitScript(() => localStorage.setItem("mhm.studio.reviewMode", "modern"));
    await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
    await expect(page.getByTestId("wikidata-items-verify-ai")).toHaveText(`Verify visible (${total})`);
    await expect(page.getByRole("button", {name: `Approve all visible (${total})`, exact: true})).toBeVisible();
    await page.getByTestId("wikidata-item-col-ai_verdict").click();
    const choices = page.getByTestId("filter-mode-checkbox");
    await expect(choices.locator("label", {hasText: "full"})).toContainText("(97)");
    await expect(choices.locator("label", {hasText: "not verified"})).toContainText(`(${total - 97})`);
    await page.getByRole("button", {name: "Cancel", exact: true}).click();
    await page.getByTestId("wikidata-items-verify-ai").click();
    await page.getByRole("button", {name: "Start verification", exact: true}).click();
    await expect.poll(() => scopes.at(-1)?.length).toBe(total);
    expect(scopes.at(-1)).toContain(`item-${total}`);
  });
}
