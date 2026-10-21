/**
 * The notices the server sent while this tab was open, newest first, kept in memory (NV-17). A toast
 * is gone in five seconds and a failure that came while the operator looked elsewhere would be gone
 * with it: the inbox's "Recent" keeps the last thirty, and says what it is - since the console was
 * opened, not a history. The history is Activity.
 */

import { useSyncExternalStore } from "react";

/** The backend's four-word vocabulary for a notice: busy, active, failed, idle. */
export type NoticeState = "busy" | "active" | "failed" | "idle";

export interface RecentNotice {
  /** Counts up for the life of the tab: a stable key. */
  id: number;
  text: string;
  state: NoticeState;
  /** When it arrived, in milliseconds. */
  at: number;
  /** The server it came from (null: this one). */
  node: string | null;
}

/** How many notices are kept. */
export const RECENT_NOTICES = 30;

let notices: readonly RecentNotice[] = [];
let counter = 0;
const listeners = new Set<() => void>();

/** Keeps a notice, dropping the oldest beyond the last thirty. */
export function recordNotice(text: string, state: NoticeState, node: string | null = null, at: number = Date.now()): void {
  counter += 1;
  notices = [{ id: counter, text, state, at, node }, ...notices].slice(0, RECENT_NOTICES);
  for (const listener of listeners) listener();
}

/** Forgets every notice. For tests, and for a sign-out. */
export function clearNotices(): void {
  notices = [];
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The notices kept, newest first, kept current as new ones arrive. */
export function useRecentNotices(): readonly RecentNotice[] {
  return useSyncExternalStore(
    subscribe,
    () => notices,
    () => notices,
  );
}
