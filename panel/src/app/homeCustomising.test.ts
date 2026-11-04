import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { setHomeCustomising, useHomeCustomising } from "./homeCustomising";

afterEach(() => {
  act(() => {
    setHomeCustomising(false);
  });
});

describe("whether Home is being customised", () => {
  it("starts as no, and is what the page and the column of Home both read", () => {
    const page = renderHook(() => useHomeCustomising());
    const column = renderHook(() => useHomeCustomising());
    expect(page.result.current).toBe(false);
    act(() => {
      setHomeCustomising(true);
    });
    expect(page.result.current).toBe(true);
    expect(column.result.current).toBe(true);
    act(() => {
      setHomeCustomising(false);
    });
    expect(page.result.current).toBe(false);
    expect(column.result.current).toBe(false);
  });

  it("does not redraw whoever reads it when it is set to what it already is", () => {
    let draws = 0;
    renderHook(() => {
      draws += 1;
      return useHomeCustomising();
    });
    const before = draws;
    act(() => {
      setHomeCustomising(false);
    });
    expect(draws).toBe(before);
  });
});
