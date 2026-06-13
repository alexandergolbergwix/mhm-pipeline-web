import { describe, it, expect } from "vitest";
import { encodePermalink, decodePermalink } from "@/components/research/permalink";

describe("encodePermalink / decodePermalink", () => {
  it("round-trips ASCII query", () => {
    const q = "SELECT ?s WHERE { ?s a <urn:x> }";
    expect(decodePermalink(encodePermalink(q))).toBe(q);
  });

  it("round-trips Hebrew text", () => {
    const q = "# שאילתה עברית\nSELECT ?s ?label WHERE { ?s rdfs:label ?label }";
    expect(decodePermalink(encodePermalink(q))).toBe(q);
  });

  it("round-trips newlines", () => {
    const q = "SELECT ?a\nWHERE {\n  ?a ?b ?c\n}";
    expect(decodePermalink(encodePermalink(q))).toBe(q);
  });

  it("produces a URL-safe string (no +, /, =)", () => {
    const q = "SELECT ?s WHERE { ?s ?p ?o }";
    const encoded = encodePermalink(q);
    expect(encoded).not.toMatch(/[+/=]/);
  });

  it("decodePermalink returns null for garbage input", () => {
    expect(decodePermalink("!!!not-base64!!!")).toBeNull();
  });

  it("decodePermalink returns null for empty string", () => {
    expect(decodePermalink("")).toBeNull();
  });
});
