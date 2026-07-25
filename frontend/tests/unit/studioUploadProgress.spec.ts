import {describe, expect, it} from "vitest";

import {
  collectNewProgressOutcomes,
  patchHmoItemsFromUploadOutcomes,
  patchWikidataItemsFromUploadOutcomes,
} from "@/utils/studioUploadProgress";

describe("collectNewProgressOutcomes", () => {
  it("returns only unseen local_ids from the rolling window", () => {
    const seen = new Set<string>(["a"]);
    const fresh = collectNewProgressOutcomes(
      {
        recent_item_outcomes: [
          {local_id: "a", status: "created", wikibase_id: "Q1"},
          {local_id: "b", status: "updated", wikibase_id: "Q2"},
        ],
        item_outcome: {local_id: "b", status: "updated", wikibase_id: "Q2"},
      },
      seen,
    );
    expect(fresh).toEqual([{local_id: "b", status: "updated", wikibase_id: "Q2"}]);
    expect(seen.has("b")).toBe(true);
  });
});

describe("patchHmoItemsFromUploadOutcomes", () => {
  it("patches only matching rows and maps status to upload_outcome", () => {
    const items = [
      {
        local_id: "QDraft_A",
        status: "would_create",
        wikibase_id: null as string | null,
        upload_outcome: null as string | null,
        upload_message: "",
        upload_at: null as string | null,
      },
      {
        local_id: "QDraft_B",
        status: "would_create",
        wikibase_id: null as string | null,
        upload_outcome: null as string | null,
        upload_message: "",
        upload_at: null as string | null,
      },
    ];
    const next = patchHmoItemsFromUploadOutcomes(
      items,
      [{local_id: "QDraft_A", status: "created", wikibase_id: "Q99", message: "ok"}],
      "2026-07-25T12:00:00Z",
    );
    expect(next[0]).toMatchObject({
      local_id: "QDraft_A",
      status: "created",
      wikibase_id: "Q99",
      upload_outcome: "create",
      upload_message: "ok",
      upload_at: "2026-07-25T12:00:00Z",
    });
    expect(next[1]).toBe(items[1]);
  });

  it("marks failed outcomes without inventing a QID", () => {
    const items = [{
      local_id: "QDraft_A",
      status: "would_create",
      wikibase_id: null as string | null,
      upload_outcome: null as string | null,
      upload_message: "",
      upload_at: null as string | null,
    }];
    const next = patchHmoItemsFromUploadOutcomes(
      items,
      [{local_id: "QDraft_A", status: "failed", message: "boom"}],
      "t",
    );
    expect(next[0]).toMatchObject({
      status: "would_create",
      wikibase_id: null,
      upload_outcome: "failed",
      upload_message: "boom",
    });
  });
});

describe("patchWikidataItemsFromUploadOutcomes", () => {
  it("sets existing_qid and upload_outcome for success", () => {
    const items = [{
      local_id: "person:1",
      existing_qid: null as string | null,
      upload_outcome: null as string | null,
      upload_message: "",
      upload_at: null as string | null,
    }];
    const next = patchWikidataItemsFromUploadOutcomes(
      items,
      [{local_id: "person:1", status: "success", qid: "Q1"}],
      "t",
    );
    expect(next[0]).toMatchObject({
      existing_qid: "Q1",
      upload_outcome: "create",
    });
  });
});
