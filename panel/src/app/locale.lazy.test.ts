import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CHROME, catalogsIdle, dropNamespaces, hasNamespace, loadCatalog, loadNamespaces, loadedNamespaces } from "../i18n/catalogs";
import { startIdlePreload, stopIdlePreload } from "../i18n/preload";
import { goCold } from "../test/coldCatalog";
import { LOCALE_STORAGE_KEY, getLocale, initLocale, resetLocale, setLocale, useLocale } from "./locale";

/**
 * The language switch against catalogs that arrive by namespace: the first load brings the chrome
 * and the first page's texts before anything is drawn, and a switch brings the new language up to
 * what the page on screen has, so no sentence turns into English or a key as it changes.
 */

let restore: (() => Promise<void>) | undefined;

afterEach(async () => {
  stopIdlePreload();
  await restore?.();
  restore = undefined;
  await catalogsIdle();
  await loadCatalog("en");
  dropNamespaces("es", []);
  resetLocale("en");
});

describe("the first load", () => {
  it("brings the chrome of the stored language and the texts of the first page, and nothing else", async () => {
    restore = goCold().restore;
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "es");
    await initLocale(["databases", "dumpImport"]);
    expect(getLocale()).toBe("es");
    for (const namespace of [...CHROME, "databases", "dumpImport"] as const) expect(hasNamespace("es", namespace)).toBe(true);
    expect(hasNamespace("es", "backups")).toBe(false);
    expect(loadedNamespaces("es").length).toBe(CHROME.length + 2);
  });

  it("is English with no wait for the chrome, which is bundled, and fetches the first page's texts", async () => {
    restore = goCold().restore;
    await initLocale(["databases"]);
    expect(getLocale()).toBe("en");
    expect(hasNamespace("en", "databases")).toBe(true);
    expect(hasNamespace("en", "backups")).toBe(false);
  });

  it("stays in English, on the page, when the language cannot be fetched", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "es");
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    // A namespace nobody registered cannot be fetched: the same refusal as a lost connection.
    await initLocale(["nowhere" as never]);
    expect(getLocale()).toBe("en");
    expect(error).toHaveBeenCalledWith(expect.stringContaining("the console stays in English"), expect.any(Error));
  });
});

describe("switching", () => {
  it("brings the new language up to the namespaces the page on screen has", async () => {
    restore = goCold().restore;
    await loadNamespaces("en", ["backups", "cron"]);
    await act(() => setLocale("es"));
    expect(getLocale()).toBe("es");
    expect(loadedNamespaces("es")).toEqual(loadedNamespaces("en"));
    expect(hasNamespace("es", "backups")).toBe(true);
    expect(hasNamespace("es", "databases")).toBe(false);
  });

  it("ends on the last choice when two overlap, whatever each one had to fetch", async () => {
    restore = goCold().restore;
    await loadNamespaces("en", ["backups"]);
    const { result } = renderHook(() => useLocale());
    const toSpanish = result.current[1]("es");
    const toEnglish = result.current[1]("en");
    await act(async () => {
      await Promise.all([toSpanish, toEnglish]);
    });
    expect(result.current[0]).toBe("en");
  });

  it("wakes the idle preload, so the language it switched to is completed in the background", async () => {
    restore = goCold().restore;
    const work: (() => void)[] = [];
    startIdlePreload(
      () => getLocale(),
      (next) => {
        work.push(next);
      },
    );
    // English is completed first, batch by batch, and the loop ends.
    for (let round = 0; round < 40 && work.length > 0; round += 1) {
      work.splice(0).forEach((next) => {
        next();
      });
      await catalogsIdle();
      await Promise.resolve();
    }
    expect(work).toHaveLength(0);

    await act(() => setLocale("es"));
    expect(work.length).toBeGreaterThan(0);
  });
});
