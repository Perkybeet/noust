import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { THEME_STORAGE_KEY, initTheme, readTheme, setTheme, useColourScheme, useTheme } from "./theme";

describe("theme", () => {
  beforeEach(() => {
    initTheme();
  });

  it("follows the system until a theme is chosen", () => {
    expect(readTheme()).toBe("system");
    expect(document.documentElement.dataset["theme"]).toBeUndefined();
  });

  it("persists a pinned theme and applies it to <html>", () => {
    setTheme("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.dataset["theme"]).toBe("dark");
    setTheme("system");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(document.documentElement.dataset["theme"]).toBeUndefined();
  });

  it("applies the stored choice before the first render", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    initTheme();
    expect(document.documentElement.dataset["theme"]).toBe("light");
  });

  it("ignores a stored value that is not a theme", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "neon");
    expect(readTheme()).toBe("system");
  });

  it("re-renders subscribers when the theme changes", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current[0]).toBe("system");
    act(() => {
      result.current[1]("light");
    });
    expect(result.current[0]).toBe("light");
  });

  it("reads the stored key", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    expect(readTheme()).toBe("dark");
  });
});

describe("the colour scheme the console is drawn in", () => {
  type Listener = (event: { matches: boolean }) => void;
  let dark = false;
  let listeners: Listener[] = [];

  beforeEach(() => {
    initTheme();
    dark = false;
    listeners = [];
    window.matchMedia = ((query: string) => ({
      get matches() {
        return query === "(prefers-color-scheme: dark)" && dark;
      },
      media: query,
      addEventListener: (_: string, listener: Listener) => listeners.push(listener),
      removeEventListener: (_: string, listener: Listener) => {
        listeners = listeners.filter((entry) => entry !== listener);
      },
    })) as unknown as typeof window.matchMedia;
  });

  it("is the pinned theme, whatever the system prefers", () => {
    dark = true;
    setTheme("light");
    const { result } = renderHook(() => useColourScheme());
    expect(result.current).toBe("light");
    act(() => setTheme("dark"));
    expect(result.current).toBe("dark");
  });

  it("is what the system prefers while no theme is pinned, and follows it", () => {
    const { result } = renderHook(() => useColourScheme());
    expect(result.current).toBe("light");
    act(() => {
      dark = true;
      for (const listener of listeners) listener({ matches: true });
    });
    expect(result.current).toBe("dark");
  });

  it("is light where the browser cannot say", () => {
    // @ts-expect-error a browser without matchMedia, as jsdom is
    delete window.matchMedia;
    const { result } = renderHook(() => useColourScheme());
    expect(result.current).toBe("light");
  });
});
