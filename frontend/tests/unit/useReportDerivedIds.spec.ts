import {renderHook} from "@testing-library/react";
import {describe, expect, it, vi} from "vitest";

import {useReportDerivedIds} from "@/hooks/useReportDerivedIds";

describe("useReportDerivedIds", () => {
  it("calls onChange once when ids are unchanged across rerenders", () => {
    const onChange = vi.fn();
    const ids = ["a", "b"];
    const {rerender} = renderHook(
      ({derivedIds, handler}) => useReportDerivedIds(derivedIds, handler),
      {initialProps: {derivedIds: ids, handler: onChange}},
    );

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(["a", "b"]);

    rerender({derivedIds: ids, handler: onChange});
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("calls onChange again when ids change", () => {
    const onChange = vi.fn();
    const {rerender} = renderHook(
      ({derivedIds}) => useReportDerivedIds(derivedIds, onChange),
      {initialProps: {derivedIds: ["a"]}},
    );

    rerender({derivedIds: ["a", "b"]});
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith(["a", "b"]);
  });

  it("does not call onChange when handler identity changes but ids are unchanged", () => {
    const first = vi.fn();
    const second = vi.fn();
    const ids = ["x"];
    const {rerender} = renderHook(
      ({derivedIds, handler}) => useReportDerivedIds(derivedIds, handler),
      {initialProps: {derivedIds: ids, handler: first}},
    );

    expect(first).toHaveBeenCalledTimes(1);
    rerender({derivedIds: ids, handler: second});
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(0);
  });
});
