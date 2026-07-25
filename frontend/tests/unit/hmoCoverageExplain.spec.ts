import {describe, expect, it} from "vitest";

import type {HmoCoverageEntry} from "@/api/hmoStudio";
import {
  coverageRowHoverText,
  projectionStatusExplanation,
  projectionStatusLabel,
  wikidataPropertyLabel,
} from "@/utils/hmoCoverageExplain";

const sample: HmoCoverageEntry = {
  class_uri: "https://w3id.org/mhm/ontology#Codicological_Unit",
  class_local_name: "Codicological_Unit",
  class_label: "Codicological unit",
  hmo_node_count: 131,
  projection_status: "summarized_in_wikidata",
  wikidata_representation: "Manuscript-level part count and content/folio qualifiers",
  wikidata_properties: ["P2635", "P7535", "P1574", "P958"],
  projected_item_count: 0,
  notes: "CU nodes remain in HMO; Wikidata uses summary claims unless a part is independently notable.",
};

describe("hmoCoverageExplain", () => {
  it("labels and explains projection statuses", () => {
    expect(projectionStatusLabel("summarized_in_wikidata")).toBe("summarised");
    expect(projectionStatusExplanation("summarized_in_wikidata")).toMatch(/summarised onto/i);
  });

  it("builds a hover blurb with representation, notes, and properties", () => {
    const text = coverageRowHoverText(sample);
    expect(text).toContain("Codicological_Unit — summarised");
    expect(text).toContain("Manuscript-level part count");
    expect(text).toContain("CU nodes remain in HMO");
    expect(text).toContain("P2635 (number of parts of this work)");
    expect(text).toContain("Click the row for the full explanation");
  });

  it("maps known Wikidata property labels", () => {
    expect(wikidataPropertyLabel("P921")).toBe("main subject");
    expect(wikidataPropertyLabel("P999999")).toBe("Wikidata property");
  });
});
