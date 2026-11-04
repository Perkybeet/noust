/**
 * The panels a person saved, as this browser holds them: the cache of what the server keeps for an account (so Home
 * draws at once, from the first frame, and the server's answer only confirms it) and the whole copy for the credential
 * that has no account (the access token: `GET /api/auth/dashboards` answers it 400, the same as the active team).
 *
 * One store for the page and for the column, which are different parts of the frame and arrange the same panel: a
 * change made in one is in the other at the same moment. It lives in memory and in `localStorage`, and the other
 * tabs of the browser are told (`storage`). A change is kept in memory at once, however often it comes (a widget being
 * dragged changes the panel at every move) and written a moment after it stops.
 */

import { useSyncExternalStore } from "react";

import type { SavedPanel } from "./panels";

const KEY = "noust.panels";

/** What the browser holds: the panels, and whether it has ever heard the server's answer (so that none is "none", not "not asked yet"). */
export interface PanelCache {
  version: 1;
  /** The server's list was read at least once, or this browser is the only place the panels are. */
  settled: boolean;
  panels: readonly SavedPanel[];
}

const EMPTY: PanelCache = { version: 1, settled: false, panels: [] };

/** How long a run of changes is waited out before it is written to `localStorage`. */
export const WRITE_DELAY_MS = 300;

let snapshot: PanelCache | null = null;
let timer: number | null = null;
const listeners = new Set<() => void>();

function isPanel(value: unknown): value is SavedPanel {
  if (typeof value !== "object" || value === null) return false;
  const panel = value as Record<string, unknown>;
  return typeof panel["id"] === "string" && typeof panel["name"] === "string" && (panel["scope"] === "server" || panel["scope"] === "fleet") && typeof panel["layout"] === "object" && panel["layout"] !== null && typeof panel["dirty"] === "boolean";
}

/** What `localStorage` holds, or nothing: a copy that is damaged is a copy there is not, never a reason for Home not to draw. */
function load(): PanelCache {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw === null) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<PanelCache> | null;
    if (parsed?.version !== 1 || !Array.isArray(parsed.panels)) return EMPTY;
    return { version: 1, settled: parsed.settled === true, panels: parsed.panels.filter(isPanel) };
  } catch {
    // A browser that refuses storage, or text that is not JSON: the panels are what the server says, and the defaults.
    return EMPTY;
  }
}

function write(): void {
  timer = null;
  if (snapshot === null) return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(snapshot));
  } catch {
    // A full or refused storage: the copy in memory holds for as long as the page does.
  }
}

/** Writes at once what is waiting to be written: before the page goes, and in tests. */
export function flushPanelStore(): void {
  if (timer === null) return;
  window.clearTimeout(timer);
  write();
}

function notify(): void {
  for (const listener of listeners) listener();
}

function current(): PanelCache {
  snapshot ??= load();
  return snapshot;
}

function set(next: PanelCache): void {
  snapshot = next;
  if (timer !== null) window.clearTimeout(timer);
  timer = window.setTimeout(write, WRITE_DELAY_MS);
  notify();
}

/** The cache, which is the same object until something changes. */
export function readPanelCache(): PanelCache {
  return current();
}

/** Keeps a panel, replacing the one of that id; a new one goes last. Not yet known to the server until `markClean`. */
export function keepPanel(panel: Omit<SavedPanel, "dirty">): void {
  const { panels, settled } = current();
  const saved: SavedPanel = { ...panel, dirty: true };
  const known = panels.some((candidate) => candidate.id === panel.id);
  set({ version: 1, settled, panels: known ? panels.map((candidate) => (candidate.id === panel.id ? saved : candidate)) : [...panels, saved] });
}

/** Forgets a panel. */
export function dropPanel(id: string): void {
  const { panels, settled } = current();
  if (!panels.some((candidate) => candidate.id === id)) return;
  set({ version: 1, settled, panels: panels.filter((candidate) => candidate.id !== id) });
}

/** The server has kept the panel as it is here now: a later read of the server may replace it. Only if it has not changed since. */
export function markClean(id: string, layout: SavedPanel["layout"]): void {
  const { panels, settled } = current();
  const found = panels.find((candidate) => candidate.id === id);
  if (found?.layout !== layout) return;
  set({ version: 1, settled, panels: panels.map((candidate) => (candidate.id === id ? { ...candidate, dirty: false } : candidate)) });
}

/**
 * What the server said, taken in: its panels, in its order, except the ones changed here that it has not got yet (those
 * stay as they are, and are sent again). From now on an empty list means the account has none.
 */
export function takeServerPanels(fromServer: readonly Omit<SavedPanel, "dirty">[]): void {
  const { panels } = current();
  const mine = new Map(panels.filter((panel) => panel.dirty).map((panel) => [panel.id, panel]));
  const merged = [...fromServer.map((panel): SavedPanel => mine.get(panel.id) ?? { ...panel, dirty: false })];
  for (const [id, panel] of mine) if (!merged.some((candidate) => candidate.id === id)) merged.push(panel);
  set({ version: 1, settled: true, panels: merged });
}

/** This browser is the only place the panels are (the access token): what it holds is all there is. */
export function settleHere(): void {
  const { panels, settled } = current();
  if (!settled) set({ version: 1, settled: true, panels });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The panels this browser holds, current as they change, here and in the other tabs. */
export function usePanelCache(): PanelCache {
  return useSyncExternalStore(subscribe, readPanelCache, () => EMPTY);
}

if (typeof window !== "undefined") {
  // Another tab changed them: this one reads its copy again and draws it.
  window.addEventListener("storage", (event) => {
    if (event.key !== KEY && event.key !== null) return;
    snapshot = load();
    notify();
  });
  // A page that goes with a change still waiting to be written writes it first.
  window.addEventListener("pagehide", flushPanelStore);
}

/** Forgets everything held in memory and in the browser, for the tests of whoever uses the store. */
export function resetPanelStore(): void {
  if (timer !== null) window.clearTimeout(timer);
  timer = null;
  snapshot = null;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing to forget.
  }
  notify();
}
