/**
 * What the operator last chose in the selector, for the pages that are no server's: the Fleet ("All
 * servers" is a place of its own) and the instance's own pages (accounts, tokens, compliance: a page of
 * no server, which leaves the selector where it was). Leaving the fleet goes back to the server the
 * operator was on, and the sidebar's per-server destinations lead to it meanwhile, so opening the
 * instance's settings from web-2 never silently lands the operator on the central's own pages.
 *
 * Kept per tab (`sessionStorage`): two tabs on two servers is a legitimate way to work, and
 * each goes back to its own. A name the central no longer knows is dropped where it is read
 * (`knownServer`), not here, since the list of servers is a query.
 */

import { useSyncExternalStore } from "react";

const KEY = "noust.lastServer";

/** How "this server" is written, so that it is told apart from nothing remembered. */
const THIS_SERVER = "@this";

/** Before the server it was on: what the operator last chose was the whole fleet, and the server they came from is kept behind it. */
const ALL_OVER = "all|";

/** A remembered server: a node's name, or null for this one; `all` when the last choice was the whole fleet. */
export interface RememberedServer {
  node: string | null;
  all?: true;
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
  if (raw.startsWith(ALL_OVER)) return { node: parse(raw.slice(ALL_OVER.length))?.node ?? null, all: true };
  return raw.startsWith("node:") && raw.length > "node:".length ? { node: raw.slice("node:".length) } : null;
}

/** The server this tab was last on, or null when it has not been on one yet. */
export function lastServer(): RememberedServer | null {
  return parse(read());
}

/** Remembers the server this tab is on (null: this one). */
export function rememberServer(node: string | null): void {
  remember(node === null ? THIS_SERVER : `node:${node}`);
}

/** Remembers that this tab is on the whole fleet ("All servers"), and keeps the server it came from for the areas that have no fleet face. */
export function rememberAll(): void {
  const current = read();
  const server = current === null || current === "" ? THIS_SERVER : current.startsWith(ALL_OVER) ? current.slice(ALL_OVER.length) : current;
  remember(`${ALL_OVER}${server}`);
}

function remember(raw: string): void {
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
