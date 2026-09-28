/**
 * Where the catalogs are kept. English is bundled with the console; every other language is
 * a same-origin hashed chunk fetched with `import()`, so it costs nothing to an English
 * reader and needs no inline JSON (the CSP and Trusted Types allow it as any other chunk).
 */

import * as en from "./en";
import type { Catalog, Locale, Messages } from "./types";

// Plain copies of the module namespaces, which are sealed: a test can graft a namespace on
// to exercise a shape (a plural, a missing key) the real catalogs do not have yet.
const loaded: Partial<Record<Locale, Catalog<Messages>>> = { en: { ...en } };

// Annotated with Catalog<Messages>: a namespace missing from es/index.ts fails to compile here.
const loaders: Record<Exclude<Locale, "en">, () => Promise<Catalog<Messages>>> = {
  es: () => import("./es"),
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
    request = loaders[locale]().then((catalog) => {
      loaded[locale] = { ...catalog };
    });
    pending.set(locale, request);
    request.catch(() => pending.delete(locale));
  }
  return request;
}
