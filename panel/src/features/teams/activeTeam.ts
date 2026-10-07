/**
 * The team the console works in, for a credential that has no account to remember it: the
 * access token and a central's fleet token (`PUT /api/teams/active` answers 400 for them, and
 * answers it for an account, which the server remembers across browsers).
 *
 * Kept in the browser (`localStorage`), per server: a node's teams are not this server's, so
 * each has its own choice. A team that no longer exists is dropped where it is read
 * (`useOrganization`), not here, since the list of teams is a query.
 */

import { useSyncExternalStore } from "react";

const PREFIX = "noust.activeTeam.";

function key(node: string | null): string {
  return `${PREFIX}${node ?? "@this"}`;
}

/** What could not be stored this session: the choice holds until the page is closed. */
const held = new Map<string, string | null>();

const listeners = new Set<() => void>();

function read(node: string | null): string | null {
  if (held.has(key(node))) return held.get(key(node)) ?? null;
  try {
    return window.localStorage.getItem(key(node));
  } catch {
    return null;
  }
}

/** The team a browser last chose on a server (null: every team, or never chose). */
export function readLocalActiveTeam(node: string | null): string | null {
  const raw = read(node);
  return raw === null || raw === "" ? null : raw;
}

/** Remembers the team (null: every team in view) on a server. */
export function writeLocalActiveTeam(node: string | null, team: string | null): void {
  held.delete(key(node));
  try {
    if (team === null) window.localStorage.removeItem(key(node));
    else window.localStorage.setItem(key(node), team);
  } catch {
    held.set(key(node), team);
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The remembered team of a server, kept current when it is changed. */
export function useLocalActiveTeam(node: string | null): string | null {
  return useSyncExternalStore(
    subscribe,
    () => readLocalActiveTeam(node),
    () => null,
  );
}
