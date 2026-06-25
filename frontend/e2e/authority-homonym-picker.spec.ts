/**
 * E2E — Authority homonym candidate picker (Rule W-37 / W-19).
 */
import {expect, test} from "@playwright/test";
import {
  makeAuthorityState,
  makeMatch,
  installAuthorityMocks,
  gotoAuthority,
} from "./fixtures/authority-fixtures";

test.describe("Authority review — homonym picker", () => {

  test("drawer lists candidates and pick updates mazal_id", async ({page}) => {
    const state = makeAuthorityState({
      matches: [
        makeMatch({
          id: "m-shlomo",
          control_number: "MS-HOM",
          entity_text: "שלמה",
          role: "author",
          entity_kind: "person",
          confidence: "low",
          mazal_id: "",
          payload: {
            sources: [],
            guard_flags: ["homonym_unresolved"],
            homonym_candidates: [
              {
                mazal_id: "987011111111105171",
                preferred_name_heb: "שלמה בן יצחק",
                dates: "1040-1105",
                main_marc_tag: "100",
                score: 120,
              },
              {
                mazal_id: "987022222222205171",
                preferred_name_heb: "שלמה (נושא)",
                dates: "",
                main_marc_tag: "150",
                score: 70,
              },
            ],
          },
        }),
      ],
    });
    await installAuthorityMocks(page, state);
    await gotoAuthority(page);

    await page.getByTestId("authority-row-m-shlomo").click();
    await expect(page.getByTestId("authority-detail-drawer")).toBeVisible();
    await expect(page.getByTestId("drawer-card-homonym")).toBeVisible();
    await expect(page.getByTestId("drawer-card-homonym")).toContainText("987011111111105171");

    await page.getByTestId("drawer-card-homonym").getByRole("button", {name: "Pick"}).first().click();
    await expect(page.getByTestId("drawer-card-match")).toContainText("987011111111105171");
  });

  test("search notes hint בעריכת filters editorial blob", async ({page}) => {
    const state = makeAuthorityState({
      matches: [
        makeMatch({
          id: "m-editor",
          control_number: "MS-EDIT",
          entity_text: "מאיר פופרש",
          role: "editor",
          entity_kind: "person",
          confidence: "low",
        }),
        makeMatch({
          id: "m-other",
          control_number: "MS-OTHER",
          entity_text: "ירושלים",
          role: "production place",
          entity_kind: "place",
        }),
      ],
      noteIndex: {
        "MS-EDIT": "בעריכת מאיר פופרש. מהדורה ראשונה.",
        "MS-OTHER": "מקום ייצור",
      },
    });
    await installAuthorityMocks(page, state);
    await gotoAuthority(page);

    await page.getByRole("button", {name: "בעריכת"}).click();
    await expect(page.getByTestId(/^authority-row-/)).toHaveCount(1);
    await expect(page.getByTestId("authority-row-m-editor")).toBeVisible();
  });
});
