import { describe, it, expect } from "vitest";
import { extractParams, substituteParams } from "@/api/savedQueries";

describe("extractParams", () => {
  it("finds a single placeholder", () => {
    expect(extractParams("SELECT ?w WHERE { ?w hm:has_author <{{author}}> }")).toEqual(["author"]);
  });

  it("deduplicates repeated placeholders", () => {
    expect(extractParams("{{x}} AND {{x}}")).toEqual(["x"]);
  });

  it("finds multiple distinct placeholders", () => {
    expect(extractParams("{{author}} {{century}}")).toEqual(["author", "century"]);
  });

  it("returns empty for no placeholders", () => {
    expect(extractParams("SELECT ?s WHERE { ?s ?p ?o }")).toEqual([]);
  });
});

describe("substituteParams", () => {
  it("substitutes a known placeholder", () => {
    const q = "FILTER(?a = <{{author}}>)";
    expect(substituteParams(q, { author: "urn:person:1" })).toBe("FILTER(?a = <urn:person:1>)");
  });

  it("leaves unknown placeholders intact", () => {
    const q = "{{known}} {{unknown}}";
    expect(substituteParams(q, { known: "x" })).toBe("x {{unknown}}");
  });
});
