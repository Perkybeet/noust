import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDebounced } from "./useDebounced";

describe("useDebounced", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts with the value it is given", () => {
    const { result } = renderHook(() => useDebounced("25565", 400));
    expect(result.current).toBe("25565");
  });

  it("follows a value only after it has stood still for the delay", () => {
    const { result, rerender } = renderHook(({ value }) => useDebounced(value, 400), { initialProps: { value: "2" } });
    rerender({ value: "25" });
    act(() => {
      vi.advanceTimersByTime(399);
    });
    expect(result.current).toBe("2");
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe("25");
  });

  it("never settles on a value that was replaced before the delay: each digit restarts the wait", () => {
    const { result, rerender } = renderHook(({ value }) => useDebounced(value, 400), { initialProps: { value: "2" } });
    const seen: string[] = [];
    for (const typed of ["25", "255", "2556", "25565"]) {
      rerender({ value: typed });
      act(() => {
        vi.advanceTimersByTime(300);
      });
      seen.push(result.current);
    }
    // Nothing settled while the person kept typing: one read is asked for, for the whole number.
    expect(seen).toEqual(["2", "2", "2", "2"]);
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(result.current).toBe("25565");
  });
});
