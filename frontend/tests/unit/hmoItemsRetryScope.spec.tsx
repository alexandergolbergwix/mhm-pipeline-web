/**
 * HmoItemsPanel — corpus-wide retry scope.
 *
 * "Retry failed" must offer EVERY item whose latest audit write is failed
 * (server-side "Last push" state), not only the failed rows that happen to
 * sit on the visible 25-row page. A scoped retry's tail otherwise shrinks
 * the retry offer (2104 corpus failures showed as "Retry 3 failed").
 */

import {describe, expect, it, vi, beforeEach} from "vitest";
import {render, screen, waitFor} from "@testing-library/react";

import {HmoItemsPanel} from "@/components/hmo/HmoItemsPanel";

vi.mock("@/api/hmoStudio", () => ({
  HmoStudio: {
    itemStatus: vi.fn().mockResolvedValue({build_present: true}),
    uploadItems: vi.fn(),
  },
  isItemUploadJob: vi.fn().mockReturnValue(true),
  itemUploadResultFromJob: vi.fn(),
}));

vi.mock("@/api/hmoStudioItems", () => ({
  HmoStudioItems: {
    page: vi.fn().mockResolvedValue({
      items: [
        {local_id: "PAGE_FAIL_1", upload_outcome: "failed", wikibase_id: null, approved: null},
        {local_id: "PAGE_FAIL_2", upload_outcome: "failed", wikibase_id: null, approved: null},
        {local_id: "OK_1", upload_outcome: "created", wikibase_id: "Q1", approved: true},
      ],
      has_more: false,
      facets: {},
    }),
    filteredIds: vi.fn().mockResolvedValue({
      entries: Array.from({length: 2104}, (_, i) => ({
        local_id: `FAIL_${i}`,
        wikibase_id: null,
        approved: null,
      })),
      total: 2104,
    }),
  },
}));

vi.mock("@/api/hmoItemVerify", () => ({HmoItemVerify: {session: vi.fn()}}));

vi.mock("@/api/runJobs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/runJobs")>();
  return {
    ...actual,
    RunJobs: {
      ...actual.RunJobs,
      listForRun: vi.fn().mockResolvedValue({jobs: []}),
      get: vi.fn(),
      start: vi.fn(),
      cancel: vi.fn(),
    },
  };
});

describe("<HmoItemsPanel> retry scope", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("offers every corpus-wide failed item, not only the visible page's", async () => {
    render(
      <HmoItemsPanel runId="run-1" buildPresent wikibaseConfigured />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("hmo-upload-retry-failed-banner").textContent).toContain(
        "2104 items failed",
      );
    });
    const {HmoStudioItems} = await import("@/api/hmoStudioItems");
    expect(vi.mocked(HmoStudioItems.filteredIds)).toHaveBeenCalledWith(
      "run-1",
      {filters: {upload_outcome: ["failed"]}},
    );
  });
});