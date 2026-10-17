/**
 * Where the catalogs are kept. English is bundled with the console; every other language is
 * a same-origin hashed chunk fetched with `import()`, so it costs nothing to an English
 * reader and needs no inline JSON (the CSP and Trusted Types allow it as any other chunk).
 */

import { expand } from "./community";
import type { CommunityFile } from "./community";
import { COMMUNITY_LOCALES } from "./communityLocales";
import * as en from "./en";
import type { Catalog, Locale, Messages } from "./types";

// Plain copies of the module namespaces, which are sealed: a test can graft a namespace on
// to exercise a shape (a plural, a missing key) the real catalogs do not have yet.
const loaded: Partial<Record<Locale, Catalog<Messages>>> = { en: { ...en } };

// Annotated with Catalog<Messages>: a namespace missing from es/index.ts fails to compile here.
const typedLoaders: { es: () => Promise<Catalog<Messages>> } = {
  es: () => import("./es"),
};

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

const loaders: Partial<Record<Locale, () => Promise<Catalog<Messages>>>> = {
  ...typedLoaders,
  ...Object.fromEntries(COMMUNITY_LOCALES.flatMap((entry) => {
    const load = communityLoader(entry.code);
    return load === undefined ? [] : [[entry.code, load] as const];
  })),
};

const pending = new Map<Locale, Promise<void>>();

/** The catalog of a language once it is loaded; English always is. */
export function catalogFor(locale: Locale): Catalog<Messages> | undefined {
  return loaded[locale];
}

export function isCatalogLoaded(locale: Locale): boolean {
  return loaded[locale] !== undefined;
}

/**
 * Fetches a language's catalog once. Concurrent callers share the request; a failed one is
 * forgotten so the next call tries again.
 */
export function loadCatalog(locale: Locale): Promise<void> {
  if (locale === "en" || loaded[locale] !== undefined) return Promise.resolve();
  let request = pending.get(locale);
  if (request === undefined) {
    const load = loaders[locale];
    if (load === undefined) return Promise.reject(new Error(`No catalog is registered for ${locale}`));
    request = load().then((catalog) => {
      loaded[locale] = { ...catalog };
    });
    pending.set(locale, request);
    request.catch(() => pending.delete(locale));
  }
  return request;
}
