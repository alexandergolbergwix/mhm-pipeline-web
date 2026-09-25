/**
 * ItemUploadPanel — filter-scoped publish.
 *
 * With an active table filter, the upload bar gains "Publish filtered (N)"
 * next to "Publish approved entries"; it must push exactly the filtered
 * local_ids (e.g. a Publication-failed view) instead of the whole corpus.
 */

import {describe, expect, it, vi, beforeEach} from "vitest";
import {render, screen, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {ItemUploadPanel} from "@/components/hmo/ItemUploadPanel";

vi.mock("@/api/hmoStudio", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/hmoStudio")>();
  return {
    ...actual,
    HmoStudio: {
      ...actual.HmoStudio,
      itemStatus: vi.fn().mockResolvedValue({build_present: true}),
      uploadItems: vi.fn().mockResolvedValue({
        id: "job-1",
        kind: "hmo_item_upload",
        status: "running",
        progress: {},
        params: {},
        result: null,
        error: null,
        created_by: null,
        started_at: null,
        finished_at: null,
        cancel_requested_at: null,
        created_at: null,
        updated_at: null,
      }),
    },
    isItemUploadJob: vi.fn().mockReturnValue(true),
    itemUploadResultFromJob: vi.fn(),
  };
});

vi.mock("@/api/hmoStudioItems", () => ({
  HmoStudioItems: {
    list: vi.fn().mockResolvedValue({items: []}),
    page: vi.fn().mockResolvedValue({items: [], has_more: false}),
    filteredIds: vi.fn().mockResolvedValue({entries: []}),
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

describe("<ItemUploadPanel> filtered publish", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("pushes exactly the filtered local_ids", async () => {
    const user = userEvent.setup();
    render(
      <ItemUploadPanel
        runId="run-1"
        wikibaseConfigured
        filteredScopeIds={["A", "B"]}
      />,
    );

    const button = await screen.findByTestId("hmo-upload-filtered-submit");
    expect(button.textContent).toContain("Preview filtered (2)");
    await user.click(button);

    const {HmoStudio} = await import("@/api/hmoStudio");
    await waitFor(() => {
      expect(vi.mocked(HmoStudio.uploadItems)).toHaveBeenCalledWith(
        "run-1", true, false, false, ["A", "B"],
      );
    });
  });

  it("omits the filtered button without a scope", async () => {
    render(<ItemUploadPanel runId="run-1" wikibaseConfigured />);

    await waitFor(() => {
      expect(screen.queryByTestId("hmo-upload-filtered-submit")).toBeNull();
    });
  });
});