import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {SourceReview, type SourceReviewRecord} from "@/api/wikidataSourceReview";
import {WikidataSourceReview} from "@/components/wikidata/WikidataSourceReview";

vi.mock("@/api/wikidataSourceReview", () => ({SourceReview: {get: vi.fn(), save: vi.fn()}}));

function sourceRecord(): SourceReviewRecord {
  return {
    control_number: "990001", source_hash: "a".repeat(64), counts: {identity_review: 1, unchecked_passage: 1},
    original_occurrences_available: true,
    requirements: [{point: 2, title: "Physical manuscript and work model", status: "documented_model_difference"}],
    passages: [], source_gaps: [], stale_choices: [], record_status_notes: ["רשומה זמנית"], catalog_references: [],
    model_decision: {status: "documented_model_difference", physical_manuscript_and_work: "separate_items"},
    candidates: [{kind: "work_occurrence", candidate_key: "b".repeat(64), control_number: "990001",
      source_hash: "a".repeat(64), source_key: "505:1", source_field: "505", source_text: "עץ חיים, 21ב–22ב",
      title: "עץ חיים", author: "חיים ויטאל", folio: "21ב–22ב", status: "identity_review"}],
  };
}

describe("source review", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(SourceReview.get).mockResolvedValue({local_id: "ms:1", records: [sourceRecord()]});
    vi.mocked(SourceReview.save).mockResolvedValue({rebuild_required: true});
  });

  it("saves the exact occurrence and source hash without publication approval", async () => {
    const saved = vi.fn();
    render(<WikidataSourceReview runId="run-1" localId="ms:1" onSaved={saved} />);
    expect(await screen.findByText(/Catalogue status: רשומה זמנית/)).toBeVisible();
    fireEvent.click(screen.getByText(/^עץ חיים ·/));
    expect(screen.getByText(/Source folio: 21ב–22ב/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Existing target QID"), {target: {value: "Q123"}});
    fireEvent.change(screen.getByLabelText("Evidence URL"), {target: {value: "https://www.nli.org.il/test"}});
    fireEvent.change(screen.getByLabelText("Reason or unresolved fact"), {target: {value: "The source and target agree."}});
    fireEvent.click(screen.getByRole("button", {name: "Save local choice"}));
    await waitFor(() => expect(SourceReview.save).toHaveBeenCalledWith("run-1", {
      local_id: "ms:1", control_number: "990001", candidate_key: "b".repeat(64), expected_source_hash: "a".repeat(64),
      target_qid: "Q123", evidence_uri: "https://www.nli.org.il/test", reason: "The source and target agree.", decision: "select",
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("Rebuild the items");
    expect(saved).toHaveBeenCalledOnce();
  });

  it("retains a stale-source error and never reports a saved choice", async () => {
    vi.mocked(SourceReview.save).mockRejectedValue(new Error("The source changed."));
    const saved = vi.fn();
    render(<WikidataSourceReview runId="run-1" localId="ms:1" onSaved={saved} />);
    await screen.findByText(/Source record 990001/);
    fireEvent.click(screen.getByText(/^עץ חיים ·/));
    fireEvent.change(screen.getByLabelText("Existing target QID"), {target: {value: "Q123"}});
    fireEvent.change(screen.getByLabelText("Evidence URL"), {target: {value: "https://www.nli.org.il/test"}});
    fireEvent.change(screen.getByLabelText("Reason or unresolved fact"), {target: {value: "The source and target agree."}});
    fireEvent.click(screen.getByRole("button", {name: "Save local choice"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("The source changed.");
    expect(saved).not.toHaveBeenCalled();
  });

  it.each(["success", "failure"])("ignores an old save %s after the curator opens another run", async outcome => {
    let finishSave: (() => void) | undefined;
    vi.mocked(SourceReview.save).mockImplementationOnce(() => new Promise((resolve, reject) => {
      finishSave = () => outcome === "success"
        ? resolve({rebuild_required: true}) : reject(new Error("The old source changed."));
    }));
    const saved = vi.fn();
    const {rerender} = render(<WikidataSourceReview key="ms:1" runId="run-1" localId="ms:1" onSaved={saved} />);
    await screen.findByText(/Source record 990001/);
    fireEvent.click(screen.getByText(/^עץ חיים ·/));
    fireEvent.change(screen.getByLabelText("Existing target QID"), {target: {value: "Q123"}});
    fireEvent.change(screen.getByLabelText("Evidence URL"), {target: {value: "https://www.nli.org.il/test"}});
    fireEvent.change(screen.getByLabelText("Reason or unresolved fact"), {target: {value: "The source and target agree."}});
    fireEvent.click(screen.getByRole("button", {name: "Save local choice"}));
    expect(SourceReview.save).toHaveBeenCalledOnce();

    vi.mocked(SourceReview.get).mockResolvedValue({local_id: "ms:1", records: [{...sourceRecord(), control_number: "990002", candidates: []}]});
    rerender(<WikidataSourceReview key="ms:1" runId="run-2" localId="ms:1" onSaved={saved} />);
    await screen.findByText(/Source record 990002/);
    await act(async () => {
      if (!finishSave) throw new Error("The test did not start the save.");
      finishSave();
    });

    expect(saved).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(SourceReview.get).toHaveBeenCalledTimes(2);
  });
});
