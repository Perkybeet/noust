/**
 * The server the operator was last on, for the pages that are no server's: the Fleet ("All
 * servers") and the central's own settings ("This central"). Leaving one of them goes back to
 * that server, and the sidebar's per-server destinations lead to it meanwhile, so opening the
 * central's settings from web-2 never silently lands the operator on the central's own pages.
 *
 * Kept per tab (`sessionStorage`): two tabs on two servers is a legitimate way to work, and
 * each goes back to its own. A name the central no longer knows is dropped where it is read
 * (`knownServer`), not here, since the list of servers is a query.
 */

import { useSyncExternalStore } from "react";

const KEY = "noust.lastServer";

/** How "this server" is written, so that it is told apart from nothing remembered. */
const THIS_SERVER = "@this";

/** A remembered server: a node's name, or null for this one. */
export interface RememberedServer {
  node: string | null;
}

const listeners = new Set<() => void>();

function read(): string | null {
  try {
    return window.sessionStorage.getItem(KEY);
  } catch {
    // Storage disabled: nothing is remembered, and the pages fall back to this server.
    return null;
  }
}

function parse(raw: string | null): RememberedServer | null {
  if (raw === null || raw === "") return null;
  if (raw === THIS_SERVER) return { node: null };
  return raw.startsWith("node:") && raw.length > "node:".length ? { node: raw.slice("node:".length) } : null;
}

/** The server this tab was last on, or null when it has not been on one yet. */
export function lastServer(): RememberedServer | null {
  return parse(read());
}

/** Remembers the server this tab is on (null: this one). */
export function rememberServer(node: string | null): void {
  const raw = node === null ? THIS_SERVER : `node:${node}`;
  if (read() === raw) return;
  try {
    window.sessionStorage.setItem(KEY, raw);
  } catch {
    // Not kept past a reload: the pages then fall back to this server, which is safe.
  }
  for (const listener of listeners) listener();
}

/** Forgets it. For tests, and for a server the central removed. */
export function forgetServer(): void {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // Nothing was kept.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The server this tab was last on, kept current as the operator moves. */
export function useLastServer(): RememberedServer | null {
  const raw = useSyncExternalStore(subscribe, read, () => null);
  return parse(raw);
}
