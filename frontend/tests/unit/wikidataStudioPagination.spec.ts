import {afterEach, describe, expect, it, vi} from "vitest";

import {fetchAllStudioItems, Studio, type StudioPage} from "@/api/wikidataStudio";

const firstPage: StudioPage = {
  run_id: "run", limit: 100, has_more: true, next_cursor: "next",
  total: 2, approved_count: 0, by_type: {work: 2},
  items: [{local_id: "one", entity_type: "work"}],
};

afterEach(() => vi.restoreAllMocks());

describe("Studio cursor load freshness", () => {
  it("stops an old cursor walk after the current page returns", async () => {
    let current = true;
    const page = vi.spyOn(Studio, "page").mockImplementation(async () => {
      current = false;
      return firstPage;
    });
    await expect(fetchAllStudioItems("run", {source: "canonical"}, {
      isCurrent: () => current,
    })).rejects.toMatchObject({name: "AbortError"});
    expect(page).toHaveBeenCalledTimes(1);
  });

  it("makes no request for a load that is already obsolete", async () => {
    const page = vi.spyOn(Studio, "page");
    await expect(fetchAllStudioItems("run", {}, {
      isCurrent: () => false,
    })).rejects.toMatchObject({name: "AbortError"});
    expect(page).not.toHaveBeenCalled();
  });

  it("retains every page and source parameter for the current load", async () => {
    const page = vi.spyOn(Studio, "page")
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce({
        ...firstPage, has_more: false, next_cursor: null,
        items: [{local_id: "two", entity_type: "work"}],
      });
    const result = await fetchAllStudioItems("run", {
      source: "canonical", approvedOnly: true,
    }, {isCurrent: () => true});
    expect(result.items.map((item) => item.local_id)).toEqual(["one", "two"]);
    expect(result.total).toBe(2);
    expect(page).toHaveBeenLastCalledWith("run", {
      source: "canonical", approvedOnly: true, cursor: "next", limit: 100,
    });
  });
});
