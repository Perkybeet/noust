/**
 * Which messages of which language are here, and how the rest arrive.
 *
 * A typed language (English, Spanish) is a set of namespaces, each its own chunk, fetched when a
 * page needs it. A language contributed as data is one file, fetched whole and held to English
 * key by key, so its namespaces are English's too. This module is the bookkeeping for both and
 * knows nothing of the console: `catalogs.ts` gives it the real chunks and the tests give it
 * their own, so every rule below (sharing a request, retrying a failed one, the English that a
 * language falls back to) is tested without a build.
 */

/** A catalog as a tree: namespaces down to texts and plural forms. */
export interface Tree {
  [key: string]: Tree | string | undefined;
}

/** What a namespace's chunk exports: the namespace itself, under its own name. */
export type NamespaceModule = Readonly<Record<string, unknown>>;

/** Everything the store is built from. */
export interface CatalogSources {
  /** Every namespace there is. */
  readonly namespaces: readonly string[];
  /** What the first paint of any route draws, for any language: it is bundled in English. */
  readonly chrome: readonly string[];
  /** English namespaces that are part of the bundle already (all of `chrome`, at least). */
  readonly bundled: Readonly<Record<string, unknown>>;
  /** Typed languages: a loader per namespace. */
  readonly typed: Readonly<Record<string, Readonly<Record<string, () => Promise<NamespaceModule>>>>>;
  /** Languages that are one file: the loader returns the nested catalog, whole. */
  readonly files: Readonly<Record<string, () => Promise<unknown>>>;
}

interface Slot {
  readonly tree: Tree;
  /** A file holds the language whole, whatever namespaces it happens to have text for. */
  readonly whole: boolean;
}

function isTree(value: unknown): value is Tree {
  return typeof value === "object" && value !== null;
}

/** The first segment of a dotted key: `databases` in `databases.list.title`. */
export function namespaceOf(key: string): string {
  const dot = key.indexOf(".");
  return dot < 0 ? key : key.slice(0, dot);
}

/** Builds the store over the given sources. */
export function createCatalogStore(sources: CatalogSources) {
  const registered = new Set(sources.namespaces);
  const slots = new Map<string, Slot>([["en", { tree: { ...(sources.bundled as Tree) }, whole: false }]]);
  const inflight = new Map<string, Promise<void>>();
  // What a `t()` asked for before it was here: the only arrivals worth re-rendering for.
  const missed = new Set<string>();
  const listeners = new Set<() => void>();
  const watchers = new Set<(locale: string, namespace: string) => void>();
  let version = 0;

  function arrived(locale: string, namespace: string | null): void {
    let wanted = false;
    if (namespace === null) {
      for (const key of [...missed]) {
        if (key.startsWith(`${locale}:`)) {
          missed.delete(key);
          wanted = true;
        }
      }
    } else {
      wanted = missed.delete(`${locale}:${namespace}`);
    }
    if (!wanted) return;
    version += 1;
    for (const listener of listeners) listener();
  }

  function has(locale: string, namespace: string): boolean {
    const slot = slots.get(locale);
    return slot !== undefined && (slot.whole || namespace in slot.tree);
  }

  function loadNamespace(locale: string, namespace: string): Promise<void> | undefined {
    if (has(locale, namespace)) return undefined;
    const key = `${locale}:${namespace}`;
    let request = inflight.get(key);
    if (request === undefined) {
      const load = sources.typed[locale]?.[namespace];
      if (load === undefined) return Promise.reject(new Error(`No ${locale} catalog is registered for the namespace "${namespace}"`));
      request = load().then(
        (module) => {
          inflight.delete(key);
          const messages = module[namespace];
          if (!isTree(messages)) throw new Error(`The ${locale} catalog of "${namespace}" does not export "${namespace}"`);
          let slot = slots.get(locale);
          if (slot === undefined) {
            slot = { tree: {}, whole: false };
            slots.set(locale, slot);
          }
          // A plain copy of the namespace: a test can graft a key on to exercise a shape the
          // real catalogs do not have yet.
          slot.tree[namespace] = messages;
          arrived(locale, namespace);
        },
        (error: unknown) => {
          // Forgotten, so the next ask tries again instead of replaying the failure.
          inflight.delete(key);
          throw error;
        },
      );
      inflight.set(key, request);
    }
    return request;
  }

  function loadFile(locale: string, load: () => Promise<unknown>): Promise<void> | undefined {
    if (slots.get(locale)?.whole === true) return undefined;
    const key = `${locale}:*`;
    let request = inflight.get(key);
    if (request === undefined) {
      request = load().then(
        (catalog) => {
          inflight.delete(key);
          if (!isTree(catalog)) throw new Error(`The ${locale} catalog file is not a catalog`);
          slots.set(locale, { tree: { ...catalog }, whole: true });
          arrived(locale, null);
        },
        (error: unknown) => {
          inflight.delete(key);
          throw error;
        },
      );
      inflight.set(key, request);
    }
    return request;
  }

  /**
   * Fetches the given namespaces of a language, once each; concurrent callers share the request. A
   * language that is one file is fetched whole, and what it falls back to (English) is fetched for
   * the namespaces asked. Rejects when a chunk cannot be fetched; the next call tries again.
   */
  function load(locale: string, namespaces: Iterable<string>): Promise<void> {
    const typed = sources.typed[locale] !== undefined;
    const file = sources.files[locale];
    if (!typed && file === undefined) return Promise.reject(new Error(`No catalog is registered for ${locale}`));
    const work: Promise<void>[] = [];
    if (!typed && file !== undefined) {
      const request = loadFile(locale, file);
      if (request !== undefined) work.push(request);
    }
    const language = typed ? locale : "en";
    for (const namespace of new Set(namespaces)) {
      const request = loadNamespace(language, namespace);
      if (request !== undefined) work.push(request);
    }
    return work.length === 0 ? Promise.resolve() : Promise.all(work).then(() => undefined);
  }

  /** Whether everything a language needs to draw `namespaces` is here already. */
  function isLoaded(locale: string, namespaces: Iterable<string>): boolean {
    const typed = sources.typed[locale] !== undefined;
    const language = typed ? locale : "en";
    if (!typed && slots.get(locale)?.whole !== true) return false;
    for (const namespace of namespaces) if (!has(language, namespace)) return false;
    return true;
  }

  return {
    /** The tree `translate` walks; undefined until the first namespace of the language is here. */
    tree: (locale: string): Tree | undefined => slots.get(locale)?.tree,
    load,
    isLoaded,
    /** Whether the language's own texts of a namespace are here (for a file, once it is). */
    has,
    /** Whether a language can draw the first paint of any route: its chrome is here. */
    isReady: (locale: string): boolean => isLoaded(locale, sources.chrome),
    /** Loads every namespace of a language. For tests, and for the idle preload when it is done waiting. */
    loadAll: (locale: string): Promise<void> => load(locale, sources.namespaces),
    /**
     * The namespaces a language has, which is what another language must be brought up to when it
     * replaces it: the page on screen was drawn with them. A language that is one file borrows
     * English's.
     */
    loaded: (locale: string): string[] => {
      const language = sources.typed[locale] !== undefined ? locale : "en";
      return sources.namespaces.filter((namespace) => has(language, namespace));
    },
    /** Whether a namespace exists, loaded or not. */
    isNamespace: (namespace: string): boolean => registered.has(namespace),
    /**
     * A `t()` found the namespace of its key missing. Starts loading it, remembers that somebody is
     * waiting so the arrival re-renders the components, and tells the watchers (tests, the page).
     * Never throws: a failed fetch is logged and the next miss tries again.
     */
    miss: (locale: string, namespace: string): void => {
      if (!registered.has(namespace)) return;
      missed.add(`${locale}:${namespace}`);
      for (const watcher of watchers) watcher(locale, namespace);
      void load(locale, [namespace]).catch((error: unknown) => {
        console.error(`Could not load the ${locale} texts of "${namespace}"`, error);
      });
    },
    /** Listens to misses. Returns the unsubscribe. */
    watchMisses: (watcher: (locale: string, namespace: string) => void): (() => void) => {
      watchers.add(watcher);
      return () => {
        watchers.delete(watcher);
      };
    },
    /** Bumps only when something that was missed arrives: the snapshot of `useSyncExternalStore`. */
    version: (): number => version,
    subscribe: (listener: () => void): (() => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    /** Resolves when nothing is being fetched. For tests, which must not let a fetch land in the next one. */
    idle: async (): Promise<void> => {
      while (inflight.size > 0) await Promise.allSettled([...inflight.values()]);
    },
    /** Forgets a language's namespaces but the ones kept: the cold start of a browser, for tests. */
    drop: (locale: string, keep: Iterable<string>): void => {
      const slot = slots.get(locale);
      if (slot === undefined) return;
      if (slot.whole) {
        slots.delete(locale);
        return;
      }
      const kept = new Set(keep);
      for (const namespace of Object.keys(slot.tree)) if (!kept.has(namespace)) Reflect.deleteProperty(slot.tree, namespace);
    },
  };
}

/** The store as `catalogs.ts` and the tests hold it. */
export type CatalogStore = ReturnType<typeof createCatalogStore>;
