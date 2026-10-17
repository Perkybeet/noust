import { describe, expect, it, vi } from "vitest";

import { createCatalogStore, namespaceOf } from "./catalogStore";
import type { CatalogSources, NamespaceModule } from "./catalogStore";

/**
 * The bookkeeping of which messages are here, on chunks the test hands over, so every rule is
 * checked without a build: a request is shared, a failure is retried, a language that is one file
 * falls back to English by namespace, and only an arrival that somebody was waiting for is news.
 */

interface Deferred<T> {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
  readonly reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const chunk = (namespace: string, messages: object): NamespaceModule => ({ [namespace]: messages });

/** Two namespaces, `nav` bundled in English and `apps` a chunk, in English and Spanish, plus a language that is a file. */
function build(overrides: Partial<CatalogSources> = {}) {
  const loadEnglishApps = vi.fn(() => Promise.resolve(chunk("apps", { title: "Applications" })));
  const loadSpanishNav = vi.fn(() => Promise.resolve(chunk("nav", { home: "Inicio" })));
  const loadSpanishApps = vi.fn(() => Promise.resolve(chunk("apps", { title: "Aplicaciones" })));
  const loadPortuguese = vi.fn(() => Promise.resolve({ nav: { home: "Início" } }));
  const sources: CatalogSources = {
    namespaces: ["apps", "nav"],
    chrome: ["nav"],
    bundled: { nav: { home: "Home" } },
    typed: {
      en: { apps: loadEnglishApps, nav: () => Promise.resolve(chunk("nav", { home: "Home" })) },
      es: { apps: loadSpanishApps, nav: loadSpanishNav },
    },
    files: { pt: loadPortuguese },
    ...overrides,
  };
  return { store: createCatalogStore(sources), loadEnglishApps, loadSpanishNav, loadSpanishApps, loadPortuguese };
}

describe("namespaceOf", () => {
  it("is the first segment of a key, or the whole of one without a dot", () => {
    expect(namespaceOf("databases.list.title")).toBe("databases");
    expect(namespaceOf("time")).toBe("time");
  });
});

describe("a typed language", () => {
  it("starts with what is bundled and nothing else", () => {
    const { store } = build();
    expect(store.has("en", "nav")).toBe(true);
    expect(store.has("en", "apps")).toBe(false);
    expect(store.tree("en")).toEqual({ nav: { home: "Home" } });
    expect(store.tree("es")).toBeUndefined();
    expect(store.loaded("en")).toEqual(["nav"]);
  });

  it("is ready to paint once its chrome is here: English always, Spanish after its first chunks", async () => {
    const { store } = build();
    expect(store.isReady("en")).toBe(true);
    expect(store.isReady("es")).toBe(false);
    await store.load("es", ["nav"]);
    expect(store.isReady("es")).toBe(true);
    expect(store.has("es", "apps")).toBe(false);
  });

  it("fetches a namespace once and shares the request between callers", async () => {
    const gate = deferred<NamespaceModule>();
    const load = vi.fn(() => gate.promise);
    const { store } = build({ typed: { en: { apps: load, nav: load }, es: {} } });
    const first = store.load("en", ["apps"]);
    const second = store.load("en", ["apps"]);
    expect(load).toHaveBeenCalledTimes(1);
    gate.resolve(chunk("apps", { title: "Applications" }));
    await Promise.all([first, second]);
    expect(store.tree("en")).toMatchObject({ apps: { title: "Applications" } });
    // Loaded is loaded: nothing is fetched twice, and nothing waits.
    await store.load("en", ["apps"]);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("forgets a request that failed, so the next one tries again", async () => {
    let attempt = 0;
    const load = vi.fn(() => {
      attempt += 1;
      return attempt === 1 ? Promise.reject(new Error("offline")) : Promise.resolve(chunk("apps", { title: "Applications" }));
    });
    const { store } = build({ typed: { en: { apps: load, nav: load }, es: {} } });
    await expect(store.load("en", ["apps"])).rejects.toThrow("offline");
    expect(store.has("en", "apps")).toBe(false);
    await store.load("en", ["apps"]);
    expect(store.has("en", "apps")).toBe(true);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("refuses a namespace nobody registered, and a chunk that does not export its namespace", async () => {
    const { store } = build({ typed: { en: { apps: () => Promise.resolve(chunk("other", {})), nav: () => Promise.resolve({}) }, es: {} } });
    await expect(store.load("en", ["nowhere"])).rejects.toThrow(/No en catalog is registered for the namespace "nowhere"/);
    await expect(store.load("en", ["apps"])).rejects.toThrow(/does not export "apps"/);
  });

  it("refuses a language nobody registered", async () => {
    const { store } = build();
    await expect(store.load("zz", ["apps"])).rejects.toThrow(/No catalog is registered for zz/);
    await expect(store.loadAll("zz")).rejects.toThrow(/No catalog is registered for zz/);
  });

  it("loads every namespace of a language on request", async () => {
    const { store } = build();
    await store.loadAll("es");
    expect(store.loaded("es")).toEqual(["apps", "nav"]);
    expect(store.tree("es")).toEqual({ apps: { title: "Aplicaciones" }, nav: { home: "Inicio" } });
  });

  it("brings another language up to what this one has", async () => {
    const { store } = build();
    await store.load("en", ["apps"]);
    await store.load("es", store.loaded("en"));
    expect(store.loaded("es")).toEqual(["apps", "nav"]);
  });

  it("leaves a grafted key alone: the tree is a plain copy a test may write to", async () => {
    const { store } = build();
    await store.load("es", ["nav"]);
    const tree = store.tree("es");
    expect(tree).toBeDefined();
    if (tree !== undefined) tree["zz"] = { only: "graft" };
    expect(store.tree("es")).toMatchObject({ zz: { only: "graft" } });
    expect(store.has("es", "zz")).toBe(true);
  });
});

describe("a language that is a file", () => {
  it("is fetched whole, with the English namespaces it falls back to", async () => {
    const { store, loadPortuguese, loadEnglishApps } = build();
    expect(store.isReady("pt")).toBe(false);
    await store.load("pt", ["apps"]);
    expect(loadPortuguese).toHaveBeenCalledTimes(1);
    expect(loadEnglishApps).toHaveBeenCalledTimes(1);
    expect(store.isReady("pt")).toBe(true);
    // Whole: whatever it has no text for is English's gap to fill, not a namespace to wait for.
    expect(store.has("pt", "apps")).toBe(true);
    expect(store.tree("pt")).toEqual({ nav: { home: "Início" } });
    expect(store.isLoaded("pt", ["apps"])).toBe(true);
  });

  it("is not ready to draw a namespace whose English is not here", async () => {
    const { store } = build();
    await store.load("pt", []);
    expect(store.isLoaded("pt", ["nav"])).toBe(true);
    expect(store.isLoaded("pt", ["apps"])).toBe(false);
  });

  it("is fetched once however many callers ask", async () => {
    const { store, loadPortuguese } = build();
    await Promise.all([store.load("pt", ["apps"]), store.load("pt", ["nav"]), store.loadAll("pt")]);
    expect(loadPortuguese).toHaveBeenCalledTimes(1);
  });

  it("borrows English's namespaces when another language replaces it", async () => {
    const { store } = build();
    await store.load("pt", ["apps"]);
    expect(store.loaded("pt")).toEqual(["apps", "nav"]);
  });

  it("is refused when the file is not a catalog", async () => {
    const { store } = build({ files: { pt: () => Promise.resolve("not a catalog") } });
    await expect(store.load("pt", [])).rejects.toThrow(/not a catalog/);
  });
});

describe("a text asked for before it was here", () => {
  it("is fetched, and only its arrival is news", async () => {
    const { store } = build();
    const heard = vi.fn();
    store.subscribe(heard);
    const before = store.version();

    // Loaded by a page's route, nobody was waiting: no component has anything to redraw.
    await store.load("en", ["apps"]);
    expect(store.version()).toBe(before);
    expect(heard).not.toHaveBeenCalled();

    store.miss("es", "apps");
    expect(store.version()).toBe(before);
    await store.idle();
    expect(store.version()).toBe(before + 1);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(store.has("es", "apps")).toBe(true);
  });

  it("tells the watchers which namespace of which language was missed", () => {
    const { store } = build();
    const watcher = vi.fn();
    const stop = store.watchMisses(watcher);
    store.miss("en", "apps");
    expect(watcher).toHaveBeenCalledWith("en", "apps");
    stop();
    store.miss("es", "apps");
    expect(watcher).toHaveBeenCalledTimes(1);
  });

  it("ignores a namespace that does not exist, which is a typo and not a chunk to fetch", () => {
    const { store, loadEnglishApps } = build();
    const watcher = vi.fn();
    store.watchMisses(watcher);
    store.miss("en", "nowhere");
    expect(watcher).not.toHaveBeenCalled();
    expect(loadEnglishApps).not.toHaveBeenCalled();
  });

  it("logs a chunk that cannot be fetched instead of throwing in whoever asked", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { store } = build({ typed: { en: { apps: () => Promise.reject(new Error("offline")), nav: () => Promise.resolve({}) }, es: {} } });
    expect(() => {
      store.miss("en", "apps");
    }).not.toThrow();
    await store.idle();
    expect(error).toHaveBeenCalledWith(expect.stringContaining('"apps"'), expect.any(Error));
    // And the next miss asks again.
    store.miss("en", "apps");
    await store.idle();
    expect(error).toHaveBeenCalledTimes(2);
  });

  it("wakes a language that is a file once the file lands, for every namespace missed in it", async () => {
    const { store } = build();
    store.miss("pt", "apps");
    store.miss("pt", "nav");
    const before = store.version();
    await store.idle();
    expect(store.version()).toBe(before + 1);
  });
});

describe("the cold start", () => {
  it("forgets a language's namespaces but the ones kept", async () => {
    const { store } = build();
    await store.loadAll("en");
    store.drop("en", ["nav"]);
    expect(store.has("en", "nav")).toBe(true);
    expect(store.has("en", "apps")).toBe(false);
  });

  it("forgets a language that is a file whole", async () => {
    const { store } = build();
    await store.load("pt", []);
    store.drop("pt", []);
    expect(store.isReady("pt")).toBe(false);
  });

  it("waits for what is in flight before it says it is idle", async () => {
    const gate = deferred<NamespaceModule>();
    const { store } = build({ typed: { en: { apps: () => gate.promise, nav: () => gate.promise }, es: {} } });
    void store.load("en", ["apps"]);
    let idle = false;
    void store.idle().then(() => {
      idle = true;
    });
    await Promise.resolve();
    expect(idle).toBe(false);
    gate.resolve(chunk("apps", {}));
    await store.idle();
    expect(idle).toBe(true);
  });
});
