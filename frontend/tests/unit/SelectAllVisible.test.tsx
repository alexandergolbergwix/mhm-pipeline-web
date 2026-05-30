/**
 * SelectAllVisible — smoke render + interaction.
 *
 * Three states the widget renders + their interaction contracts:
 *
 *  • none selected → label reads "Select all N visible rows"
 *  • partial       → checkbox.indeterminate == true, label reads
 *                    "{k} of N visible rows selected"
 *  • all selected  → label reads "All N visible rows selected"
 *
 * Clicking the checkbox toggles between (all selected) and (none
 * selected); never bypasses the visible filter.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SelectAllVisible } from "@/components/SelectAllVisible";


describe("<SelectAllVisible>", () => {
  it("renders the 'select all N' label when nothing is selected", () => {
    render(
      <SelectAllVisible
        visibleCount={5}
        selectedCount={0}
        onSelectAll={() => {}}
        onClear={() => {}}
      />,
    );
    expect(screen.getByText(/Select all/i)).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("shows the 'all N selected' label when everything is selected", () => {
    render(
      <SelectAllVisible
        visibleCount={5}
        selectedCount={5}
        onSelectAll={() => {}}
        onClear={() => {}}
      />,
    );
    expect(screen.getByText(/All/i)).toBeInTheDocument();
    expect(screen.getByText(/selected/i)).toBeInTheDocument();
  });

  it("marks the checkbox indeterminate on partial selection", () => {
    render(
      <SelectAllVisible
        visibleCount={5}
        selectedCount={2}
        onSelectAll={() => {}}
        onClear={() => {}}
      />,
    );
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    // React doesn't bind .indeterminate; the component does so in
    // useEffect. The DOM property is the only source of truth.
    expect(checkbox.indeterminate).toBe(true);
    expect(checkbox.checked).toBe(false);
  });

  it("clicking the checkbox while empty fires onSelectAll", async () => {
    const onSelectAll = vi.fn();
    const onClear     = vi.fn();
    render(
      <SelectAllVisible
        visibleCount={3}
        selectedCount={0}
        onSelectAll={onSelectAll}
        onClear={onClear}
      />,
    );
    await userEvent.click(screen.getByRole("checkbox"));
    expect(onSelectAll).toHaveBeenCalledTimes(1);
    expect(onClear).not.toHaveBeenCalled();
  });

  it("clicking the checkbox while all-selected fires onClear", async () => {
    const onSelectAll = vi.fn();
    const onClear     = vi.fn();
    render(
      <SelectAllVisible
        visibleCount={3}
        selectedCount={3}
        onSelectAll={onSelectAll}
        onClear={onClear}
      />,
    );
    await userEvent.click(screen.getByRole("checkbox"));
    expect(onClear).toHaveBeenCalledTimes(1);
    expect(onSelectAll).not.toHaveBeenCalled();
  });

  it("clicking the inline 'clear' button while partial fires onClear", async () => {
    const onClear = vi.fn();
    render(
      <SelectAllVisible
        visibleCount={5}
        selectedCount={2}
        onSelectAll={() => {}}
        onClear={onClear}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("honours the custom label noun", () => {
    render(
      <SelectAllVisible
        visibleCount={7}
        selectedCount={0}
        onSelectAll={() => {}}
        onClear={() => {}}
        label="matches"
      />,
    );
    expect(screen.getByText(/visible matches/i)).toBeInTheDocument();
  });
});
