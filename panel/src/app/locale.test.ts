import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LEGACY_LOCALE_STORAGE_KEY, LOCALE_STORAGE_KEY, browserLocale, getLocale, initLocale, readLocale, resetLocale, setLocale, useLocale } from "./locale";

describe("locale", () => {
  it("starts in English in tests, on <html> too", () => {
    expect(getLocale()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("defaults to the first of the browser's languages the console speaks", () => {
    expect(browserLocale(["es-ES", "en"])).toBe("es");
    expect(browserLocale(["es-419"])).toBe("es");
    expect(browserLocale(["fr-FR", "es", "en"])).toBe("es");
    expect(browserLocale(["en-GB", "es"])).toBe("en");
    expect(browserLocale(["de", "fr"])).toBe("en");
    vi.stubGlobal("navigator", { languages: ["es-MX", "en-US"], language: "es-MX" });
    expect(readLocale()).toBe("es");
  });

  it("prefers the stored choice to the browser's, and ignores one it does not know", () => {
    vi.stubGlobal("navigator", { languages: ["es-ES"], language: "es-ES" });
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "en");
    expect(readLocale()).toBe("en");
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "klingon");
    expect(readLocale()).toBe("es");
  });

  it("falls back to the browser when storage is unavailable", () => {
    vi.stubGlobal("navigator", { languages: ["es"], language: "es" });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(readLocale()).toBe("es");
  });

  it("persists a choice, applies it to <html> and re-renders subscribers", async () => {
    const { result } = renderHook(() => useLocale());
    expect(result.current[0]).toBe("en");
    await act(async () => {
      await result.current[1]("es");
    });
    expect(result.current[0]).toBe("es");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("es");
    expect(document.documentElement.lang).toBe("es");
  });

  it("still switches when the choice cannot be stored", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    await setLocale("es");
    expect(getLocale()).toBe("es");
  });

  it("loads the stored language before the first render", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "es");
    await initLocale();
    expect(getLocale()).toBe("es");
    expect(document.documentElement.lang).toBe("es");
    resetLocale();
    expect(getLocale()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("ends on the last choice when two overlap", async () => {
    const first = setLocale("es");
    const second = setLocale("en");
    await Promise.all([first, second]);
    expect(getLocale()).toBe("en");
  });

  it("follows a change made in another tab", async () => {
    const { result } = renderHook(() => useLocale());
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "es");
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: LOCALE_STORAGE_KEY, newValue: "es" }));
    });
    await waitFor(() => {
      expect(result.current[0]).toBe("es");
    });
    expect(document.documentElement.lang).toBe("es");
  });

  describe("the WASM to Noust key migration", () => {
    it("reads the new key when only it is set", () => {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, "es");
      expect(readLocale()).toBe("es");
      expect(window.localStorage.getItem(LEGACY_LOCALE_STORAGE_KEY)).toBeNull();
    });

    it("reads, migrates and removes the legacy key when only it is set", () => {
      window.localStorage.setItem(LEGACY_LOCALE_STORAGE_KEY, "es");
      expect(readLocale()).toBe("es");
      expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("es");
      expect(window.localStorage.getItem(LEGACY_LOCALE_STORAGE_KEY)).toBeNull();
    });

    it("prefers the new key when both are set", () => {
      window.localStorage.setItem(LEGACY_LOCALE_STORAGE_KEY, "en");
      window.localStorage.setItem(LOCALE_STORAGE_KEY, "es");
      expect(readLocale()).toBe("es");
    });
  });
});
