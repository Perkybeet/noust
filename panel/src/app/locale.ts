/**
 * The console's language: English or Spanish, chosen per browser.
 *
 * Until the operator picks one, the browser's preferred languages decide. The choice is
 * stored per browser and applied in main.tsx before the first render, with the Spanish
 * catalog already loaded, so the console never flashes English text or reflows into Spanish.
 * `<html lang>` follows it, so screen readers pronounce each language with the right voice.
 */

import { useSyncExternalStore } from "react";

import { isCatalogLoaded, loadCatalog } from "../i18n/catalogs";
import type { Locale } from "../i18n/types";

export type { Locale } from "../i18n/types";

export const LOCALE_STORAGE_KEY = "wasm.locale";

/**
 * The choices, each named in its own language (an autonym, never translated): someone who
 * cannot read the current language must still recognise their own. `lang` goes on each
 * label so it is pronounced correctly.
 */
export const LOCALE_CHOICES: readonly { value: Locale; label: string }[] = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
];

function isLocale(value: unknown): value is Locale {
  return value === "en" || value === "es";
}

/** The first of the browser's preferred languages the console speaks; English otherwise. */
export function browserLocale(languages: readonly string[] = navigator.languages): Locale {
  const preferred = languages.length > 0 ? languages : [navigator.language];
  for (const tag of preferred) {
    const base = tag.toLowerCase().split("-")[0];
    if (isLocale(base)) return base;
  }
  return "en";
}

export function readLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(stored) ? stored : browserLocale();
  } catch {
    // Storage can be disabled (privacy modes, some embedded browsers): the browser decides.
    return browserLocale();
  }
}

export function applyLocale(locale: Locale, root: HTMLElement = document.documentElement): void {
  root.lang = locale;
}

const listeners = new Set<() => void>();
let current: Locale | null = null;
// The newest request wins: picking Spanish and then English before the Spanish catalog
// arrives must end in English.
let latest = 0;

function snapshot(): Locale {
  if (current === null) {
    // Only a language whose catalog is here can be shown; initLocale() loads it first.
    const stored = readLocale();
    current = isCatalogLoaded(stored) ? stored : "en";
  }
  return current;
}

function notify(): void {
  for (const listener of listeners) listener();
}

/** The active language, for code outside components (toasts, validation messages). */
export function getLocale(): Locale {
  return snapshot();
}

/**
 * Loads the stored (or browser) language's catalog and applies it to <html>. Await it once,
 * before the first render. A catalog that cannot be fetched leaves the console in English
 * for this page rather than blank.
 */
export async function initLocale(): Promise<void> {
  const wanted = readLocale();
  try {
    await loadCatalog(wanted);
    current = wanted;
  } catch (error) {
    console.error(`Could not load the ${wanted} catalog; the console stays in English.`, error);
    current = "en";
  }
  applyLocale(current);
  notify();
}

/**
 * Switches the console's language: fetches the catalog if needed, then persists, applies and
 * re-renders every subscriber. Rejects when the catalog cannot be fetched, leaving the
 * current language in place, so the caller can say so.
 */
export async function setLocale(locale: Locale): Promise<void> {
  const request = ++latest;
  await loadCatalog(locale);
  if (request !== latest) return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Not persisted, still applied for this page: the operator sees what they picked.
  }
  current = locale;
  applyLocale(locale);
  notify();
}

/** Puts a language in place without storing it. For tests, which all start in English. */
export function resetLocale(locale: Locale = "en"): void {
  latest += 1;
  current = isCatalogLoaded(locale) ? locale : "en";
  applyLocale(current);
  notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab changed the language: follow it, so two tabs of one console never disagree.
  const onStorage = (event: StorageEvent): void => {
    if (event.key !== LOCALE_STORAGE_KEY && event.key !== null) return;
    const next = readLocale();
    const request = ++latest;
    loadCatalog(next).then(
      () => {
        // Every subscription hears the event; the last one to ask applies it for all.
        if (request !== latest) return;
        current = next;
        applyLocale(next);
        notify();
      },
      (error: unknown) => {
        console.error(`Could not load the ${next} catalog another tab switched to.`, error);
      },
    );
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useLocale(): readonly [Locale, (locale: Locale) => Promise<void>] {
  return [useSyncExternalStore(subscribe, snapshot), setLocale] as const;
}
