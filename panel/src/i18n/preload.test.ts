import { afterEach, describe, expect, it, vi } from "vitest";

import { goCold } from "../test/coldCatalog";
import { NAMESPACES, areNamespacesLoaded, catalogsIdle, loadCatalog, loadedNamespaces } from "./catalogs";
import { startIdlePreload, stopIdlePreload, wakeIdlePreload } from "./preload";
import type { Locale } from "./types";

/**
 * Once the first paint is done the rest of the language is fetched while idle, a few chunks at a
 * time, so that the page an operator opens next has its texts already. The browser's idle time is
 * a test's queue here: nothing runs until the test says so.
 */

let restore: (() => Promise<void>) | undefined;

afterEach(async () => {
  stopIdlePreload();
  vi.unstubAllGlobals();
  await restore?.();
  restore = undefined;
  await loadCatalog("en");
});

/** A scheduler that holds its work, and runs it when told. */
function queue() {
  const work: (() => void)[] = [];
  return {
    schedule: (next: () => void) => {
      work.push(next);
    },
    pending: () => work.length,
    /** Runs what is queued and waits for the chunks it asked for. */
    run: async () => {
      work.splice(0).forEach((next) => {
        next();
      });
      await catalogsIdle();
      await Promise.resolve();
    },
  };
}

describe("the idle preload", () => {
  it("fetches the rest of the language a few chunks at a time, until none is left", async () => {
    restore = goCold().restore;
    const idle = queue();
    const before = loadedNamespaces("en").length;
    startIdlePreload(() => "en", idle.schedule);
    expect(idle.pending()).toBe(1);

    await idle.run();
    // A batch, not all of them: a page's own requests are never queued behind a hundred.
    const afterFirst = loadedNamespaces("en").length;
    expect(afterFirst).toBeGreaterThan(before);
    expect(afterFirst - before).toBeLessThanOrEqual(6);
    expect(idle.pending()).toBe(1);

    for (let round = 0; round < 40 && idle.pending() > 0; round += 1) await idle.run();
    expect(loadedNamespaces("en")).toEqual([...NAMESPACES]);
    // Done: nothing is scheduled again.
    expect(idle.pending()).toBe(0);
  });

  it("does nothing when everything is already here", () => {
    const idle = queue();
    startIdlePreload(() => "en", idle.schedule);
    expect(idle.pending()).toBe(1);
    void idle.run();
    expect(idle.pending()).toBe(0);
  });

  it("follows the language that is active when each step runs, and wakes when it changes", async () => {
    restore = goCold().restore;
    let locale: Locale = "en";
    const idle = queue();
    startIdlePreload(() => locale, idle.schedule);
    for (let round = 0; round < 40 && idle.pending() > 0; round += 1) await idle.run();
    expect(areNamespacesLoaded("en", NAMESPACES)).toBe(true);
    expect(areNamespacesLoaded("es", NAMESPACES)).toBe(false);

    locale = "es";
    wakeIdlePreload();
    expect(idle.pending()).toBe(1);
    for (let round = 0; round < 40 && idle.pending() > 0; round += 1) await idle.run();
    expect(areNamespacesLoaded("es", NAMESPACES)).toBe(true);
  });

  it("is woken once, however many times the language changes meanwhile", () => {
    restore = goCold().restore;
    const idle = queue();
    startIdlePreload(() => "en", idle.schedule);
    wakeIdlePreload();
    wakeIdlePreload();
    expect(idle.pending()).toBe(1);
  });

  it("does not run for a connection that asked to save data: a page then waits for its own", () => {
    restore = goCold().restore;
    vi.stubGlobal("navigator", { connection: { saveData: true } });
    const idle = queue();
    startIdlePreload(() => "en", idle.schedule);
    expect(idle.pending()).toBe(0);
  });

  it("is stopped by stopIdlePreload, and a wake after that does nothing", () => {
    const idle = queue();
    startIdlePreload(() => "en", idle.schedule);
    stopIdlePreload();
    wakeIdlePreload();
    void idle.run();
    expect(idle.pending()).toBe(0);
  });

  it("uses the browser's idle time, with a deadline so a page that never goes idle still gets its texts", () => {
    const request = vi.fn();
    vi.stubGlobal("requestIdleCallback", request);
    startIdlePreload(() => "en");
    expect(request).toHaveBeenCalledWith(expect.any(Function), { timeout: 4000 });
  });

  it("falls back to a timer where the browser has no idle callback", () => {
    const timer = vi.spyOn(window, "setTimeout");
    expect(typeof window.requestIdleCallback).toBe("undefined");
    startIdlePreload(() => "en");
    expect(timer).toHaveBeenCalledWith(expect.any(Function), 250);
  });
});
