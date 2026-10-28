/**
 * What a person chooses about the frame of the console, kept the way the theme is (app/theme.ts):
 * per browser, read before anything is drawn, followed across tabs. Three choices:
 *
 * - the presentation of the navigation: the double bar (a main bar of areas and a second column
 *   with the context) or one bar (the same entries of the second level as tabs or a list in the page);
 * - whether the main bar is folded to icons, which only matters where there is room to choose
 *   (app/navLayout.ts);
 * - the width of the page, comfortable or the whole width there is.
 *
 * A default is stored as no key at all, so a browser that never chose carries nothing.
 */

import { useSyncExternalStore } from "react";

export type NavPresentation = "double" | "single";
export type RailFold = "expanded" | "folded";
export type PageWidth = "comfortable" | "full";

export interface Preferences {
  presentation: NavPresentation;
  rail: RailFold;
  width: PageWidth;
}

export const NAV_KEY = "noust.nav";
export const RAIL_KEY = "noust.rail";
export const WIDTH_KEY = "noust.width";

const DEFAULTS: Preferences = { presentation: "double", rail: "expanded", width: "comfortable" };

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return allowed.find((candidate) => candidate === value) ?? fallback;
  } catch {
    // Storage can be disabled (privacy modes, some embedded browsers): the default decides.
    return fallback;
  }
}

/** The choices as the browser holds them now. */
export function readPreferences(): Preferences {
  return {
    presentation: stored(NAV_KEY, ["double", "single"], DEFAULTS.presentation),
    rail: stored(RAIL_KEY, ["expanded", "folded"], DEFAULTS.rail),
    width: stored(WIDTH_KEY, ["comfortable", "full"], DEFAULTS.width),
  };
}

const listeners = new Set<() => void>();
let current: Preferences | null = null;

function snapshot(): Preferences {
  current ??= readPreferences();
  return current;
}

function write(key: string, value: string, fallback: string): void {
  try {
    if (value === fallback) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Not persisted, still applied for this page: the person sees what they picked.
  }
}

function change(next: Partial<Preferences>): void {
  current = { ...snapshot(), ...next };
  for (const listener of listeners) listener();
}

export function setNavPresentation(value: NavPresentation): void {
  write(NAV_KEY, value, DEFAULTS.presentation);
  change({ presentation: value });
}

export function setRailFold(value: RailFold): void {
  write(RAIL_KEY, value, DEFAULTS.rail);
  change({ rail: value });
}

export function setPageWidth(value: PageWidth): void {
  write(WIDTH_KEY, value, DEFAULTS.width);
  change({ width: value });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab changed a choice: follow it, so two tabs of one console never disagree.
  const onStorage = (event: StorageEvent): void => {
    if (event.key !== null && ![NAV_KEY, RAIL_KEY, WIDTH_KEY].includes(event.key)) return;
    current = readPreferences();
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** The three choices, current. */
export function usePreferences(): Preferences {
  return useSyncExternalStore(subscribe, snapshot);
}
