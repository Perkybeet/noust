/**
 * How many rows of a list the operator wants on a page, remembered per list in the browser.
 *
 * Coolify fixed it at 10, eight people said so within a week, and the control it then added was
 * clipped by its own card (checklist M-51). Here the choice is 20, 50, 100 or all, it is made
 * where the rows are, and it stays: each list (applications, databases, ...) has its own, under
 * `noust.pageSize.<list>` in `localStorage`. A list's id is a name the code gives it, never
 * something the operator typed.
 */

import { useCallback, useSyncExternalStore } from "react";

export type PageSize = 20 | 50 | 100 | "all";

/** The sizes offered, in the order the control lists them. */
export const PAGE_SIZES: readonly PageSize[] = [20, 50, 100, "all"];

/** 20: what DESIGN.md has always said a table pages at, now the starting point of a choice. */
export const DEFAULT_PAGE_SIZE: PageSize = 20;

/** The storage key of one list's page size. */
export function pageSizeKey(list: string): string {
  return `noust.pageSize.${list}`;
}

/** A stored value as a page size: one of the four offered, or the default for anything else. */
export function parsePageSize(raw: string | null): PageSize {
  if (raw === "all") return "all";
  const match = PAGE_SIZES.find((size) => String(size) === raw);
  return match ?? DEFAULT_PAGE_SIZE;
}

function readRaw(list: string): string | null {
  try {
    return window.localStorage.getItem(pageSizeKey(list));
  } catch {
    // Storage disabled (a privacy mode, an embedded browser): the default, for this page.
    return null;
  }
}

/** What could not be stored this session: the choice still holds until the page is closed. */
const unsaved = new Map<string, PageSize>();

function currentRaw(list: string): string | null {
  const held = unsaved.get(list);
  return held !== undefined ? String(held) : readRaw(list);
}

/** The page size remembered for a list; 20 until the operator chooses another. */
export function readPageSize(list: string): PageSize {
  return parsePageSize(currentRaw(list));
}

const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

/** Remembers a list's page size and tells every table of that list. */
export function writePageSize(list: string, size: PageSize): void {
  try {
    window.localStorage.setItem(pageSizeKey(list), String(size));
    unsaved.delete(list);
  } catch {
    // Not kept past this page: the table still shows what was chosen.
    unsaved.set(list, size);
  }
  notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent): void => {
    if (event.key === null || event.key.startsWith("noust.pageSize.")) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** A list's page size and its setter; every table of the list follows the one choice. */
export function usePageSize(list: string): readonly [PageSize, (size: PageSize) => void] {
  // The raw string is the snapshot: a primitive that is equal until the choice changes.
  const raw = useSyncExternalStore(subscribe, () => currentRaw(list), () => null);
  const set = useCallback((size: PageSize) => writePageSize(list, size), [list]);
  return [parsePageSize(raw), set] as const;
}
