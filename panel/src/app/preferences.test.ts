import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { NAV_KEY, RAIL_KEY, WIDTH_KEY, readPreferences, setNavPresentation, setPageWidth, setRailFold, usePreferences } from "./preferences";

describe("the preferences of the frame", () => {
  beforeEach(() => {
    window.localStorage.clear();
    // The store keeps its last value between tests: reading again is what a new page load does.
    act(() => {
      setNavPresentation("double");
      setRailFold("expanded");
      setPageWidth("comfortable");
    });
    window.localStorage.clear();
  });

  it("starts with the double bar, the rail unfolded and the comfortable width", () => {
    expect(readPreferences()).toEqual({ presentation: "double", rail: "expanded", width: "comfortable" });
  });

  it("keeps each choice per browser, like the theme, and removes the key when it goes back to the default", () => {
    act(() => {
      setNavPresentation("single");
      setRailFold("folded");
      setPageWidth("full");
    });
    expect(window.localStorage.getItem(NAV_KEY)).toBe("single");
    expect(window.localStorage.getItem(RAIL_KEY)).toBe("folded");
    expect(window.localStorage.getItem(WIDTH_KEY)).toBe("full");
    expect(readPreferences()).toEqual({ presentation: "single", rail: "folded", width: "full" });

    act(() => {
      setNavPresentation("double");
    });
    expect(window.localStorage.getItem(NAV_KEY)).toBeNull();
  });

  it("ignores a stored value that is not one of the choices", () => {
    window.localStorage.setItem(NAV_KEY, "triple");
    window.localStorage.setItem(RAIL_KEY, "hidden");
    window.localStorage.setItem(WIDTH_KEY, "huge");
    expect(readPreferences()).toEqual({ presentation: "double", rail: "expanded", width: "comfortable" });
  });

  it("re-renders whoever reads it when a choice changes, and for nobody else's keys", () => {
    const { result } = renderHook(() => usePreferences());
    expect(result.current.presentation).toBe("double");
    act(() => {
      setNavPresentation("single");
    });
    expect(result.current.presentation).toBe("single");
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

  it("still applies a choice for this page when the browser refuses to store it", () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new DOMException("denied", "SecurityError");
    };
    try {
      const { result } = renderHook(() => usePreferences());
      act(() => {
        setRailFold("folded");
      });
      expect(result.current.rail).toBe("folded");
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});
