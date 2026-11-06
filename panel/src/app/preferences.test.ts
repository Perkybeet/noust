import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RAIL_KEY, WIDTH_KEY, readPreferences, setPageWidth, setRailFold, usePreferences } from "./preferences";

describe("the preferences of the frame", () => {
  beforeEach(() => {
    window.localStorage.clear();
    // The store keeps its last value between tests: reading again is what a new page load does.
    act(() => {
      setRailFold("expanded");
      setPageWidth("comfortable");
    });
    window.localStorage.clear();
  });

  it("starts with the rail unfolded and the comfortable width, and has no choice of presentation", () => {
    expect(readPreferences()).toEqual({ rail: "expanded", width: "comfortable" });
  });

  it("keeps each choice per browser, like the theme, and removes the key when it goes back to the default", () => {
    act(() => {
      setRailFold("folded");
      setPageWidth("full");
    });
    expect(window.localStorage.getItem(RAIL_KEY)).toBe("folded");
    expect(window.localStorage.getItem(WIDTH_KEY)).toBe("full");
    expect(readPreferences()).toEqual({ rail: "folded", width: "full" });

    act(() => {
      setRailFold("expanded");
    });
    expect(window.localStorage.getItem(RAIL_KEY)).toBeNull();
  });

  it("ignores a stored value that is not one of the choices, and the one-bar choice of 4.1", () => {
    window.localStorage.setItem("noust.nav", "single");
    window.localStorage.setItem(RAIL_KEY, "hidden");
    window.localStorage.setItem(WIDTH_KEY, "huge");
    expect(readPreferences()).toEqual({ rail: "expanded", width: "comfortable" });
  });

  it("re-renders whoever reads it when a choice changes, and for nobody else's keys", () => {
    const { result } = renderHook(() => usePreferences());
    expect(result.current.rail).toBe("expanded");
    act(() => {
      setPageWidth("full");
    });
    expect(result.current.width).toBe("full");
    expect(result.current.rail).toBe("expanded");
  });

  it("follows another tab: a storage event with the key of a choice reads it again", () => {
    const { result } = renderHook(() => usePreferences());
    window.localStorage.setItem(RAIL_KEY, "folded");
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: RAIL_KEY }));
    });
    expect(result.current.rail).toBe("folded");
  });

  it("does not read again for the key of a choice that no longer exists", () => {
    const { result } = renderHook(() => usePreferences());
    const before = result.current;
    window.localStorage.setItem("noust.nav", "single");
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "noust.nav" }));
    });
    expect(result.current).toBe(before);
  });

  it("still applies a choice for this page when the browser refuses to store it", () => {
    const refuse = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    try {
      const { result } = renderHook(() => usePreferences());
      act(() => {
        setRailFold("folded");
      });
      expect(result.current.rail).toBe("folded");
    } finally {
      refuse.mockRestore();
    }
  });
});
