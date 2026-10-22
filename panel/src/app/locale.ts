/**
 * The console's language: English or Spanish, chosen per browser.
 *
 * Until the operator picks one, the browser's preferred languages decide. The choice is
 * stored per browser and applied in main.tsx before the first render, with the chrome of that
 * language and the texts of the first page already loaded, so the console never flashes English
 * text or reflows into Spanish.
 * `<html lang>` follows it, so screen readers pronounce each language with the right voice.
 */

import { useSyncExternalStore } from "react";

import { isCatalogLoaded, loadNamespaces, loadedNamespaces } from "../i18n/catalogs";
import { COMMUNITY_LOCALES } from "../i18n/communityLocales";
import type { CommunityCode } from "../i18n/communityLocales";
import { wakeIdlePreload } from "../i18n/preload";
import { LOCALES } from "../i18n/types";
import type { Locale, Namespace } from "../i18n/types";

export type { Locale } from "../i18n/types";

export const LOCALE_STORAGE_KEY = "noust.locale";

/**
 * The choices, each named in its own language (an autonym, never translated): someone who
 * cannot read the current language must still recognise their own. `lang` goes on each
 * label so it is pronounced correctly.
 */
export const LOCALE_CHOICES: readonly { value: Locale; label: string }[] = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  // A language contributed as data is offered once it is released; a draft is only previewed.
  ...COMMUNITY_LOCALES.filter((entry) => entry.status === "released").map((entry): { value: Locale; label: string } => ({ value: entry.code as CommunityCode, label: entry.autonym })),
];

/** Every language the console has a catalog for, drafts included: a translator previews one by choosing it. */
function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** The languages offered, which are the ones a browser's own preference may choose for a person. */
function isOffered(value: string): value is Locale {
  return LOCALE_CHOICES.some((choice) => choice.value === value);
}

/**
 * The first of the browser's preferred languages the console speaks; English otherwise. The whole
 * tag is tried before its language (`pt-BR`, then `pt`), so a regional translation wins where one
 * exists. A draft is never chosen for somebody: it is incomplete.
 */
export function browserLocale(languages: readonly string[] = navigator.languages): Locale {
  const preferred = languages.length > 0 ? languages : [navigator.language];
  for (const tag of preferred) {
    const [language, region] = tag.split("-");
    const full = region === undefined || language === undefined ? undefined : `${language.toLowerCase()}-${region.toUpperCase()}`;
    if (full !== undefined && isOffered(full)) return full;
    const base = tag.toLowerCase().split("-")[0];
    if (base !== undefined && isOffered(base)) return base;
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
 *
 * What is loaded is what English holds (the chrome that every first paint draws) and
 * `namespaces`: the ones the page the browser is opening needs, so its first paint is already in
 * the operator's language with no English flash and no reflow. The rest arrives while idle.
 *
 * @param namespaces - The namespaces of the first route, which the router would otherwise fetch after the first render.
 */
export async function initLocale(namespaces: readonly Namespace[] = []): Promise<void> {
  const wanted = readLocale();
  try {
    await loadNamespaces(wanted, [...loadedNamespaces("en"), ...namespaces]);
    current = wanted;
  } catch (error) {
    console.error(`Could not load the ${wanted} catalog; the console stays in English.`, error);
    current = "en";
  }
  applyLocale(current);
  notify();
}

/**
 * Brings a language up to what the one on screen has loaded. The page was drawn with those
 * namespaces, so anything less would put English, or a key, where a sentence was a moment ago;
 * and a test, where English is loaded whole, gets the other language whole.
 */
function loadLike(next: Locale): Promise<void> {
  return loadNamespaces(next, loadedNamespaces(snapshot()));
}

/**
 * Switches the console's language: fetches what the current page shows in it, then persists,
 * applies and re-renders every subscriber. Rejects when the catalog cannot be fetched, leaving the
 * current language in place, so the caller can say so. What the language still lacks is fetched
 * while idle, as at the first load.
 */
export async function setLocale(locale: Locale): Promise<void> {
  const request = ++latest;
  await loadLike(locale);
  if (request !== latest) return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Not persisted, still applied for this page: the operator sees what they picked.
  }
  current = locale;
  applyLocale(locale);
  notify();
  wakeIdlePreload();
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
    loadLike(next).then(
      () => {
        // Every subscription hears the event; the last one to ask applies it for all.
        if (request !== latest) return;
        current = next;
        applyLocale(next);
        notify();
        wakeIdlePreload();
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
