import {render, screen} from "@testing-library/react";
import {expect, it} from "vitest";

import {ResearchChat} from "@/components/research/ResearchChat";
import {ResearchMarkdown, parseMarkdownBlocks} from "@/components/research/ResearchMarkdown";
import {parseNumericCell} from "@/components/research/ResearchCharts";

it("shows bouncing dots while busy with no stream text", () => {
  render(
    <ResearchChat
      messages={[{role: "user", content: "how many links"}]}
      streamingText=""
      busy
      error={null}
      activity={[]}
      thinking={[]}
      onSend={() => undefined}
    />,
  );
  expect(screen.getByTestId("research-chat-waiting")).toBeVisible();
  expect(screen.getByRole("button", {name: "…"})).toBeDisabled();
});

it("hides the wait dots once stream text arrives", () => {
  render(
    <ResearchChat
      messages={[{role: "user", content: "how many links"}]}
      streamingText="The graph has"
      busy
      error={null}
      activity={[]}
      thinking={[]}
      onSend={() => undefined}
    />,
  );
  expect(screen.queryByTestId("research-chat-waiting")).toBeNull();
  expect(screen.getByText("The graph has")).toBeVisible();
});

it("parses collapsed inline EDA markdown into headings, tables, QID links, and signal tiles", () => {
  const collapsed =
    "# EDA – Project uploads & corpus snapshot **This project job has published 4 Wikidata work items on top of an 897-manuscript corpus, led by Venice (44 mss), Amsterdam (34), Jerusalem (29), Mantua (26).** " +
    "## 1. Published Wikidata items | QID | Label | Type | Status | |---|---|---|---| Q191825 | משנה (Mishnah) | work | studio:canonical | | Q623354 | הגדה של פסח | work | studio:canonical | " +
    "- Scope: 4 items ## 2. Corpus snapshot - **897 manuscripts · 963 works · 2,117 persons · 203 places · 159,948 triples** - Person role attributions: **805 scribe, 230 author, 111 owner**";

  const blocks = parseMarkdownBlocks(collapsed);
  expect(blocks.some((b) => b.kind === "h1")).toBe(true);
  expect(blocks.some((b) => b.kind === "h2")).toBe(true);
  expect(blocks.some((b) => b.kind === "table")).toBe(true);

  render(<ResearchMarkdown text={collapsed} variant="canvas" />);
  expect(screen.getByTestId("signal-stat-manuscripts-value")).toHaveTextContent("897");
  expect(screen.getByTestId("signal-stat-works-value")).toHaveTextContent("963");
  expect(screen.getByTestId("signal-donut-chart")).toBeVisible();
  expect(screen.getByTestId("signal-data-table")).toBeVisible();
  expect(screen.getByRole("link", {name: /Q191825/i})).toHaveAttribute(
    "href",
    "https://www.wikidata.org/wiki/Q191825",
  );
});

it("renders manuscript rows with blank lines and missing trailing pipes as a table", () => {
  const report = [
    "Source: research_manuscripts",
    "",
    "| Control number | Title | Production year | |---|---|---|",
    "",
    "| 990000825080205171 | ספר המבחר | 1501",
    "",
    "| 990000592310205171 | גלא עמיקתא | 1600",
    "",
    "Total manuscripts in corpus: 897.",
  ].join("\n");

  render(<ResearchMarkdown text={report} variant="canvas" />);
  expect(screen.getByRole("table")).toBeVisible();
  expect(screen.getByText("990000825080205171")).toBeVisible();
  expect(screen.getByText("ספר המבחר")).toBeVisible();
  expect(screen.getByText("גלא עמיקתא")).toBeVisible();
  expect(screen.getByText("Total manuscripts in corpus: 897.")).toBeVisible();
});

it("does not treat control numbers as numeric metrics", () => {
  expect(parseNumericCell("990000825080205171")).toBeNull();
  expect(parseNumericCell("00123")).toBeNull();
  expect(parseNumericCell("1501")).toBe(1501);
});
