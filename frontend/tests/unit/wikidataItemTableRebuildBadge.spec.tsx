/**
 * WikidataItemTable — "Rebuild required" badge.
 *
 * The badge shows only for rows whose rebuild_required flag is true.
 */

import {describe, expect, it, vi} from "vitest";
import {render, screen} from "@testing-library/react";

import type {StudioItem} from "@/api/wikidataStudio";
import {WikidataItemTable} from "@/components/wikidata/WikidataItemTable";

const ITEMS: StudioItem[] = [
  {local_id: "ms:edited", entity_type: "manuscript", labels: {en: "Edited"}, rebuild_required: true},
  {local_id: "ms:built", entity_type: "manuscript", labels: {en: "Built"}, rebuild_required: false},
];

describe("WikidataItemTable rebuild badge", () => {
  it("shows the badge only on the row flagged rebuild_required", () => {
    render(<WikidataItemTable items={ITEMS} onOpenItem={vi.fn()} />);

    expect(screen.getByTestId("wikidata-item-rebuild-required-ms:edited").textContent)
      .toBe("Rebuild required");
    expect(screen.queryByTestId("wikidata-item-rebuild-required-ms:built")).toBeNull();
  });
});
