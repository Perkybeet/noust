/**
 * The rest of the active language, fetched while the browser has nothing better to do.
 *
 * The first paint loads what the page needs (the chrome, and what the router was told the route
 * draws); everything else is a handful of small chunks that nobody has asked for yet. Once the
 * page is up they are fetched a few at a time, when the browser is idle, so that by the time an
 * operator clicks a link its texts are here and the navigation waits for nothing. On a connection
 * that asked to save data they are not fetched at all: a page then waits for its own.
 */

import { NAMESPACES, areNamespacesLoaded, loadNamespaces } from "./catalogs";
import type { Locale } from "./types";

/** How many chunks are in flight at once: a few, so a page's own requests are never queued behind them. */
const BATCH = 6;

/** Runs `work` when the browser is idle, or soon after where it has no such thing (Safari). */
export type Schedule = (work: () => void) => void;

interface Connection {
  readonly saveData?: boolean;
}

function savingData(): boolean {
  return (navigator as Navigator & { connection?: Connection }).connection?.saveData === true;
}

const whenIdle: Schedule = (work) => {
  if (typeof window.requestIdleCallback === "function") {
    // A deadline, so a page that never goes idle (a log streaming in) still gets its namespaces.
    window.requestIdleCallback(work, { timeout: 4000 });
  } else {
    window.setTimeout(work, 250);
  }
};

let activeLocale: (() => Locale) | null = null;
let schedule: Schedule = whenIdle;
let waiting = false;

function step(): void {
  if (activeLocale === null) {
    waiting = false;
    return;
  }
  const locale = activeLocale();
  const todo = NAMESPACES.filter((namespace) => !areNamespacesLoaded(locale, [namespace])).slice(0, BATCH);
  if (todo.length === 0) {
    waiting = false;
    return;
  }
  loadNamespaces(locale, todo).then(
    () => {
      schedule(step);
    },
    (error: unknown) => {
      // Stops quietly: whatever a page needs, it asks for itself, and that is where a failure shows.
      waiting = false;
      console.error(`Could not preload the ${locale} texts`, error);
    },
  );
}

/**
 * Starts fetching the rest of the language `locale()` names, batch by batch, while idle. Call it
 * once, after the first paint.
 *
 * @param locale - Read at every step, so a language switched meanwhile is the one fetched.
 * @param scheduler - When to run each step; the browser's idle time unless a test says otherwise.
 */
export function startIdlePreload(locale: () => Locale, scheduler: Schedule = whenIdle): void {
  activeLocale = locale;
  schedule = scheduler;
  wakeIdlePreload();
}

/** Starts the loop again if it had finished, because the language changed and has namespaces to fetch. */
export function wakeIdlePreload(): void {
  if (activeLocale === null || waiting || savingData()) return;
  waiting = true;
  schedule(step);
}

/** Stops preloading. For tests. */
export function stopIdlePreload(): void {
  activeLocale = null;
  waiting = false;
  schedule = whenIdle;
}
