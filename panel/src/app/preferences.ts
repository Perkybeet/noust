/**
 * What a person chooses about the frame of the console, kept the way the theme is (app/theme.ts):
 * per browser, read before anything is drawn, followed across tabs. Two choices:
 *
 * - whether the main bar is folded to icons, which only matters where there is room to choose
 *   (app/navLayout.ts: from 1440 px; below it the bar is always folded);
 * - the width of the page, comfortable or the whole width there is.
 *
 * There is no choice of presentation any more: the frame is always the main bar and the context
 * column (4.2), and a browser that saved "one bar" in 4.1 simply carries a key nobody reads.
 *
 * A default is stored as no key at all, so a browser that never chose carries nothing.
 */

import { useSyncExternalStore } from "react";

export type RailFold = "expanded" | "folded";
export type PageWidth = "comfortable" | "full";

export interface Preferences {
  rail: RailFold;
  width: PageWidth;
}

export const RAIL_KEY = "noust.rail";
export const WIDTH_KEY = "noust.width";

const DEFAULTS: Preferences = { rail: "expanded", width: "comfortable" };

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
    if (event.key !== null && ![RAIL_KEY, WIDTH_KEY].includes(event.key)) return;
    current = readPreferences();
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** The two choices, current. */
export function usePreferences(): Preferences {
  return useSyncExternalStore(subscribe, snapshot);
}
