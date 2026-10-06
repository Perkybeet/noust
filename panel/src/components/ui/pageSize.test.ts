import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_PAGE_SIZE, PAGE_SIZES, pageSizeKey, parsePageSize, readPageSize, usePageSize, writePageSize } from "./pageSize";

describe("page sizes", () => {
  it("offer 20, 50, 100 and all, and start at 20", () => {
    expect(PAGE_SIZES).toEqual([20, 50, 100, "all"]);
    expect(DEFAULT_PAGE_SIZE).toBe(20);
    expect(readPageSize("apps")).toBe(20);
  });

  it("are remembered per list, in the browser", () => {
    writePageSize("apps", 50);
    writePageSize("databases", "all");
    expect(readPageSize("apps")).toBe(50);
    expect(readPageSize("databases")).toBe("all");
    expect(readPageSize("services")).toBe(20);
    expect(window.localStorage.getItem(pageSizeKey("apps"))).toBe("50");
    expect(window.localStorage.getItem(pageSizeKey("databases"))).toBe("all");
  });

  it("go back to the default when what is stored is not one of the sizes offered", () => {
    for (const stored of ["", "7", "-20", "twenty", "50.5", "1e2", "null"]) {
      window.localStorage.setItem(pageSizeKey("apps"), stored);
      expect(readPageSize("apps"), stored).toBe(20);
    }
    expect(parsePageSize("100")).toBe(100);
    expect(parsePageSize("all")).toBe("all");
    expect(parsePageSize(null)).toBe(20);
  });

  it("still work, for this page only, when the browser refuses to store them", () => {
    const refuse = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    const { result } = renderHook(() => usePageSize("apps"));
    act(() => {
      result.current[1](100);
    });
    expect(result.current[0]).toBe(100);
    expect(readPageSize("apps")).toBe(100);
    refuse.mockRestore();
    // Stored once the browser allows it again, which also ends what was only held in memory.
    writePageSize("apps", 20);
    expect(readPageSize("apps")).toBe(20);
  });

  it("read as the default when storage cannot be read at all", () => {
    const refuse = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(readPageSize("apps")).toBe(20);
    refuse.mockRestore();
  });

  it("reach every table of a list at once, and are read back after a reload", () => {
    const first = renderHook(() => usePageSize("apps"));
    const second = renderHook(() => usePageSize("apps"));
    act(() => {
      first.result.current[1](50);
    });
    expect(second.result.current[0]).toBe(50);
    first.unmount();
    second.unmount();
    expect(renderHook(() => usePageSize("apps")).result.current[0]).toBe(50);
  });
});
