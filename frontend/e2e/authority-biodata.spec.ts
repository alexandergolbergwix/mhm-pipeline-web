/**
 * E2E — Authority biodata enrichment surfaced in the detail drawer.
 */
import {expect, test} from "@playwright/test";
import {
  makeAuthorityState,
  makeMatch,
  installAuthorityMocks,
  gotoAuthority,
} from "./fixtures/authority-fixtures";

test.describe("Authority review — biodata enrichment", () => {

  test("detail drawer shows occupations and places from biodata_authority", async ({page}) => {
    const state = makeAuthorityState({
      matches: [
        makeMatch({
          id: "m-rashi",
          control_number: "MS-RASHI",
          entity_text: "שלמה בן יצחק",
          role: "author",
          entity_kind: "person",
          confidence: "high",
          viaf_id: "27066507",
          source: "viaf",
          payload: {
            sources: ["viaf"],
            biodata_sources: ["viaf"],
            biodata_version: 1,
            occupations: ["rabbi", "Bible commentator"],
            name_variants_he: ["רש״י"],
            name_variants_lat: ["Rashi, 1040-1105", "Salomon ben Isaac"],
            biodata_authority: {
              dates: {birth: "1040", death: "1105"},
              places: {country: ["France"]},
              names: {he: ["רש״י"], lat: ["Rashi, 1040-1105"]},
              occupations: ["rabbi", "Bible commentator"],
              notes: [],
            },
          },
        }),
      ],
    });
    await installAuthorityMocks(page, state);
    await gotoAuthority(page);

    await expect(page.getByTestId("authority-row-m-rashi")).toBeVisible({timeout: 8000});
    await page.getByTestId("authority-row-m-rashi").click();
    await expect(page.getByTestId("authority-detail-drawer")).toBeVisible();
    await expect(page.getByTestId("drawer-card-biodata")).toBeVisible();
    await expect(page.getByText("Bible commentator")).toBeVisible();
    await expect(page.getByText("France")).toBeVisible();
    await expect(page.getByText("רש״י")).toBeVisible();
  });
});
