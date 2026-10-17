/**
 * Where the catalogs are kept, and how they reach the browser.
 *
 * Every namespace of every typed language (English, Spanish) is its own same-origin hashed chunk,
 * fetched with `import()`: it costs nothing to a page that does not draw it, and needs no inline
 * JSON (the CSP and Trusted Types allow it as any other chunk). Only the CHROME is bundled, in
 * English: the namespaces the first paint of any route draws before the router has said which page
 * it is. A page declares what else it draws in `routeNamespaces.gen.ts`, the router loads that
 * before the page is shown (`app/router.ts`), and once the first paint is done the rest is fetched
 * while the browser is idle (`preload.ts`), so navigating afterwards waits for nothing.
 *
 * What `t()` does when its namespace is still not here is decided in `translate.ts`.
 *
 * A language contributed as data is a single file, fetched whole; see `community.ts`.
 */

import { expand } from "./community";
import type { CommunityFile } from "./community";
import { COMMUNITY_LOCALES } from "./communityLocales";
import { createCatalogStore } from "./catalogStore";
import type { NamespaceModule } from "./catalogStore";
import { approvals } from "./en/approvals";
import { auth } from "./en/auth";
import { common } from "./en/common";
import { language } from "./en/language";
import { logSearch } from "./en/logSearch";
import { nav } from "./en/nav";
import { queue } from "./en/queue";
import { servers } from "./en/servers";
import { shell } from "./en/shell";
import { time } from "./en/time";
import type * as spanish from "./es";
import type { Catalog, Locale, Messages, Namespace } from "./types";

/**
 * What a first paint needs whatever the route: the navigation, the words every component shares,
 * sign-in, the shell, time, the queue and approvals (an API error names them on any page). Bundled
 * in English; in Spanish they are the first chunks fetched. A namespace belongs here only if a
 * cold render of the console's frame (`routes/namespaces.test.tsx`) draws it before any page has
 * said what it needs: every one added is paid for by every visitor, on every page.
 */
const CHROME_MODULES = { approvals, auth, common, language, logSearch, nav, queue, servers, shell, time } satisfies { [K in Namespace]?: Messages[K] };

type Complete<T extends Catalog<Messages>> = T;
/**
 * Spanish, held complete by the compiler: a namespace missing from `es/index.ts` is an error here,
 * as it was when the barrel was imported for real. The barrel is only a type now; the chunks come
 * from the files themselves (below), so adding a namespace is still the two lines it always was.
 */
export type SpanishCatalog = Complete<typeof spanish>;

/**
 * One loader per namespace file; `index.ts` is the barrel the types are read from, not a namespace.
 * English leaves out the chrome (the braces repeat CHROME_MODULES, and a test holds them equal): a
 * module that is imported statically and by `import()` is given a chunk of its own, which would
 * put ten more files on the first load and in the preload list of every page.
 */
const ENGLISH_CHUNKS = import.meta.glob<NamespaceModule>(["./en/*.ts", "!./en/index.ts", "!./en/{approvals,auth,common,language,logSearch,nav,queue,servers,shell,time}.ts"]);
const SPANISH_CHUNKS = import.meta.glob<NamespaceModule>(["./es/*.ts", "!./es/index.ts"]);

/** The namespace a chunk's path names: `./en/databases.ts` is `databases`. */
function byNamespace(chunks: Readonly<Record<string, () => Promise<NamespaceModule>>>): Record<string, () => Promise<NamespaceModule>> {
  return Object.fromEntries(Object.entries(chunks).map(([path, load]) => [path.slice(path.lastIndexOf("/") + 1, -".ts".length), load]));
}

/**
 * The files of languages contributed as data. Each is its own chunk, found by name: adding
 * `community/pt.json` adds a language without a line of code beyond its registration.
 */
const communityFiles: Readonly<Record<string, () => Promise<CommunityFile>>> = import.meta.glob<CommunityFile>("./community/*.json", {
  import: "default",
});

/**
 * A language contributed as data, loaded and read as the nested catalog the translator looks
 * keys up in. What the file leaves out is not here: `translate` falls back to English for it.
 *
 * @param code - The language's code, which names its file.
 * @param files - The files by path; the ones the build found unless a test gives its own.
 * @returns The loader, or undefined when the registry names a language whose file is not there.
 */
export function communityLoader(code: string, files: Readonly<Record<string, () => Promise<CommunityFile>>> = communityFiles): (() => Promise<Catalog<Messages>>) | undefined {
  const load = files[`./community/${code}.json`];
  return load === undefined ? undefined : () => load().then((file) => expand(file) as unknown as Catalog<Messages>);
}

// The chrome is here already: loading it again is answering with what the bundle holds.
const bundled = Object.fromEntries(Object.entries(CHROME_MODULES).map(([namespace, messages]) => [namespace, () => Promise.resolve({ [namespace]: messages })]));
const english = { ...byNamespace(ENGLISH_CHUNKS), ...bundled };

/** Every namespace there is, in alphabetical order. */
export const NAMESPACES = Object.keys(english).sort() as Namespace[];

/** The namespaces bundled with the console, in English. */
export const CHROME = Object.keys(CHROME_MODULES).sort() as Namespace[];

const store = createCatalogStore({
  namespaces: NAMESPACES,
  chrome: CHROME,
  bundled: CHROME_MODULES,
  typed: { en: english, es: byNamespace(SPANISH_CHUNKS) },
  files: Object.fromEntries(
    COMMUNITY_LOCALES.flatMap((entry) => {
      const load = communityLoader(entry.code);
      return load === undefined ? [] : [[entry.code, load] as const];
    }),
  ),
});

/**
 * The catalog of a language as far as it is loaded: a namespace is a key once it is here. Undefined
 * for a language none of whose namespaces has arrived.
 */
export function catalogFor(locale: Locale): Partial<Catalog<Messages>> | undefined {
  return store.tree(locale);
}

/** Whether a language can draw a first paint: its chrome is here. English always can. */
export function isCatalogLoaded(locale: Locale): boolean {
  return store.isReady(locale);
}

/** Whether the language's own texts of a namespace are here; a language that is one file has them all once it is. */
export function hasNamespace(locale: Locale, namespace: Namespace): boolean {
  return store.has(locale, namespace);
}

/** Whether a language can draw all of the namespaces: its own texts, and the English it falls back to. */
export function areNamespacesLoaded(locale: Locale, namespaces: readonly Namespace[]): boolean {
  return store.isLoaded(locale, namespaces);
}

/** The namespaces a language has, which another language must be brought up to when it replaces it. */
export function loadedNamespaces(locale: Locale): Namespace[] {
  return store.loaded(locale) as Namespace[];
}

/**
 * Fetches namespaces of a language, once each. Concurrent callers share the request; a failed one
 * is forgotten so the next call tries again. A language that is one file is fetched whole, and
 * English's namespaces with it, because that is what it falls back to.
 */
export function loadNamespaces(locale: Locale, namespaces: readonly Namespace[]): Promise<void> {
  return store.load(locale, namespaces);
}

/** Fetches a language entire. What a test, or a person switching language, needs; a page needs less. */
export function loadCatalog(locale: Locale): Promise<void> {
  return store.loadAll(locale);
}

/** Whether `namespace` is one of the console's. */
export function isNamespace(namespace: string): namespace is Namespace {
  return store.isNamespace(namespace);
}

/** A `t()` met a namespace that is not here: see `translate.ts`. */
export const missNamespace = store.miss;

/** Listens to every `t()` that met a namespace not yet loaded. For tests and for the page's own warning. */
export const watchMisses = store.watchMisses;

/** The counter of arrivals that somebody was waiting for, and its subscription: `useT` re-renders on it. */
export const catalogVersion = store.version;
export const subscribeCatalogs = store.subscribe;

/** Resolves when nothing is being fetched: a test waits for it before it forgets what was loaded. */
export const catalogsIdle = store.idle;

/** Forgets a language's namespaces except those kept: the cold start of a browser, for tests. */
export const dropNamespaces = store.drop;
