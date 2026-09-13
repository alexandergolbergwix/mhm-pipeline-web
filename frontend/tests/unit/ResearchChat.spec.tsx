import {render, screen} from "@testing-library/react";
import {expect, it} from "vitest";

import {ResearchChat} from "@/components/research/ResearchChat";

it("shows bouncing dots while busy with no stream text", () => {
  render(
    <ResearchChat
      messages={[{role: "user", content: "how many links"}]}
      streamingText=""
      busy
      error={null}
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
      onSend={() => undefined}
    />,
  );
  expect(screen.queryByTestId("research-chat-waiting")).toBeNull();
  expect(screen.getByText("The graph has")).toBeVisible();
});
