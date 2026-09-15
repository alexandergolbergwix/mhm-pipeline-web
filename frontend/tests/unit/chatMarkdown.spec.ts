import {renderChatMarkdown, splitSuggestions} from "@/components/research/ResearchChat";
import {describe, expect, it} from "vitest";

describe("renderChatMarkdown", () => {
  it("renders bold, italic, code, and links", () => {
    const html = renderChatMarkdown("**Top places** (by count)\n\n- *Hebrew* — `P407`");
    expect(html).toContain("<strong>Top places</strong>");
    expect(html).toContain("<em>Hebrew</em>");
    expect(html).toContain("<code>P407</code>");
  });

  it("links markdown links and bare URLs", () => {
    const html = renderChatMarkdown("see [Q141175772](https://www.wikidata.org/wiki/Q141175772) and https://www.wikidata.org/wiki/Q801");
    expect((html.match(/<a /g) ?? []).length).toBe(2);
    expect(html).toContain('href="https://www.wikidata.org/wiki/Q141175772"');
  });

  it("never lets raw HTML through", () => {
    const html = renderChatMarkdown("<script>alert(1)</script> **ok**");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("<strong>ok</strong>");
  });
});

describe("splitSuggestions", () => {
  it("extracts the Suggested next line into chips", () => {
    const {body, suggestions} = splitSuggestions(
      "The map is on the canvas.\n\nSuggested next: Export the dot→URL table? | Map one manuscript? | Show link types?",
    );
    expect(body).toBe("The map is on the canvas.");
    expect(suggestions).toHaveLength(3);
    expect(suggestions[0]).toContain("Export");
  });

  it("leaves answers without suggestions untouched", () => {
    const {body, suggestions} = splitSuggestions("Plain answer.");
    expect(body).toBe("Plain answer.");
    expect(suggestions).toHaveLength(0);
  });
});
