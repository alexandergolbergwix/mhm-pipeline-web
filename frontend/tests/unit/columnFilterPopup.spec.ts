import {describe, expect, it} from "vitest";

import {EMPTY_STRING_SET} from "@/utils/renderStable";

describe("ColumnFilterPopup stability", () => {
  it("EMPTY_STRING_SET keeps a stable reference", () => {
    expect(EMPTY_STRING_SET.size).toBe(0);
    expect(EMPTY_STRING_SET).toBe(EMPTY_STRING_SET);
  });
});
