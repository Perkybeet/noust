import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toDocument } from "../../components/widgets/model";
import { WRITE_DELAY_MS, dropPanel, flushPanelStore, keepPanel, markClean, readPanelCache, resetPanelStore, settleHere, takeServerPanels } from "./panelStore";
import type { SavedPanel } from "./panels";

const layout = (id: string) => toDocument([{ id, type: id, size: "small", settings: {} }]);
const panel = (id: string, name = id): Omit<SavedPanel, "dirty"> => ({ id, name, scope: "server", layout: layout(id) });

beforeEach(() => {
  resetPanelStore();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the panels this browser holds", () => {
  it("start with none, and no word from the server yet", () => {
    expect(readPanelCache()).toEqual({ version: 1, settled: false, panels: [] });
  });

  it("keep a panel at once, in memory, as not yet known to the server", () => {
    keepPanel(panel("data"));
    expect(readPanelCache().panels).toEqual([{ ...panel("data"), dirty: true }]);
  });

  it("replace the panel of that id and put a new one last", () => {
    keepPanel(panel("a"));
    keepPanel(panel("b"));
    keepPanel(panel("a", "A again"));
    expect(readPanelCache().panels.map((entry) => [entry.id, entry.name])).toEqual([
      ["a", "A again"],
      ["b", "b"],
    ]);
  });

  it("forget one, and nothing when it was not there", () => {
    keepPanel(panel("a"));
    const before = readPanelCache();
    dropPanel("zzz");
    expect(readPanelCache()).toBe(before);
    dropPanel("a");
    expect(readPanelCache().panels).toEqual([]);
  });

  it("give the same object until something changes, so a component drawn from it is not drawn again for nothing", () => {
    keepPanel(panel("a"));
    expect(readPanelCache()).toBe(readPanelCache());
  });
});

describe("what is written to the browser", () => {
  it("waits for a run of changes to stop, and then writes the last of them", () => {
    vi.useFakeTimers();
    keepPanel(panel("a"));
    keepPanel(panel("a", "second"));
    expect(window.localStorage.getItem("noust.panels")).toBeNull();
    vi.advanceTimersByTime(WRITE_DELAY_MS - 1);
    expect(window.localStorage.getItem("noust.panels")).toBeNull();
    vi.advanceTimersByTime(1);
    const written = JSON.parse(window.localStorage.getItem("noust.panels") ?? "null") as { panels: { name: string }[] };
    expect(written.panels.map((entry) => entry.name)).toEqual(["second"]);
  });

  it("is written at once when the page is about to go", () => {
    vi.useFakeTimers();
    keepPanel(panel("a"));
    window.dispatchEvent(new Event("pagehide"));
    expect(window.localStorage.getItem("noust.panels")).not.toBeNull();
    flushPanelStore();
  });

  it("is read from the browser when the page opens, and a damaged copy is no copy", () => {
    window.localStorage.setItem("noust.panels", JSON.stringify({ version: 1, settled: true, panels: [{ ...panel("kept"), dirty: false }, { nonsense: true }] }));
    resetPanelStoreMemoryOnly();
    expect(readPanelCache().panels.map((entry) => entry.id)).toEqual(["kept"]);
    expect(readPanelCache().settled).toBe(true);
    window.localStorage.setItem("noust.panels", "{not json");
    resetPanelStoreMemoryOnly();
    expect(readPanelCache()).toEqual({ version: 1, settled: false, panels: [] });
    window.localStorage.setItem("noust.panels", JSON.stringify({ version: 2, panels: [] }));
    resetPanelStoreMemoryOnly();
    expect(readPanelCache().panels).toEqual([]);
  });

  it("is read again, in this tab, when another tab changed it", () => {
    keepPanel(panel("mine"));
    flushPanelStore();
    window.localStorage.setItem("noust.panels", JSON.stringify({ version: 1, settled: true, panels: [{ ...panel("theirs"), dirty: false }] }));
    window.dispatchEvent(new StorageEvent("storage", { key: "noust.panels" }));
    expect(readPanelCache().panels.map((entry) => entry.id)).toEqual(["theirs"]);
  });

  it("does not stop the page when the browser refuses to store", () => {
    vi.useFakeTimers();
    const refuse = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    keepPanel(panel("a"));
    expect(() => vi.advanceTimersByTime(WRITE_DELAY_MS)).not.toThrow();
    expect(readPanelCache().panels).toHaveLength(1);
    refuse.mockRestore();
  });
});

/** The page opens again: memory is forgotten, the browser's own copy is not. */
function resetPanelStoreMemoryOnly(): void {
  const kept = window.localStorage.getItem("noust.panels");
  resetPanelStore();
  if (kept !== null) window.localStorage.setItem("noust.panels", kept);
  window.dispatchEvent(new StorageEvent("storage", { key: "noust.panels" }));
}

describe("what the server says", () => {
  it("is taken in, in its order, and from then on none means none", () => {
    takeServerPanels([panel("a"), panel("b")]);
    expect(readPanelCache()).toMatchObject({ settled: true });
    expect(readPanelCache().panels.map((entry) => [entry.id, entry.dirty])).toEqual([
      ["a", false],
      ["b", false],
    ]);
    takeServerPanels([]);
    expect(readPanelCache()).toEqual({ version: 1, settled: true, panels: [] });
  });

  it("does not replace what was changed here and the server has not heard", () => {
    keepPanel(panel("a", "changed here"));
    takeServerPanels([panel("a", "the server's"), panel("b")]);
    expect(readPanelCache().panels.map((entry) => [entry.id, entry.name, entry.dirty])).toEqual([
      ["a", "changed here", true],
      ["b", "b", false],
    ]);
  });

  it("keeps a panel made here that the server does not have yet", () => {
    keepPanel(panel("new"));
    takeServerPanels([panel("old")]);
    expect(readPanelCache().panels.map((entry) => entry.id).sort()).toEqual(["new", "old"]);
  });

  it("marks a panel as heard only if it is still as it was sent", () => {
    keepPanel(panel("a"));
    const sent = readPanelCache().panels[0]?.layout;
    if (sent === undefined) throw new Error("no panel");
    keepPanel({ ...panel("a"), layout: layout("changed again") });
    markClean("a", sent);
    expect(readPanelCache().panels[0]?.dirty).toBe(true);
    const current = readPanelCache().panels[0]?.layout;
    if (current === undefined) throw new Error("no panel");
    markClean("a", current);
    expect(readPanelCache().panels[0]?.dirty).toBe(false);
  });

  it("is not waited for by the access token: what the browser holds is all there is", () => {
    settleHere();
    expect(readPanelCache().settled).toBe(true);
  });
});
