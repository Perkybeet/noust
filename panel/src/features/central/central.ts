/**
 * What the console knows about this Noust as a central: its role, and whether the secrets
 * that reach its servers are sealed and still locked. Read from GET /api/auth/session, which
 * carries `central: {role, sealed, locked}`; a backend that sends none is a plain server with
 * nothing sealed, which is exactly how it behaves.
 */

import { useQuery } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import { isApiError, request } from "../../api/client";
import { authKeys, sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";

/** `server` deploys here and may manage a fleet too; `hub` only manages the fleet. */
export type CentralRole = "server" | "hub";

export interface CentralInfo {
  role: CentralRole;
  /** The node keys and tokens are encrypted at rest under a passphrase. */
  sealed: boolean;
  /** Sealed and not unlocked since the last start: no tunnel opens. */
  locked: boolean;
}

/** What a central that says nothing about itself is: a server, nothing sealed. */
export const PLAIN_SERVER: CentralInfo = { role: "server", sealed: false, locked: false };

type WireCentral = NonNullable<SessionInfo["central"]>;

function fromWire(central: WireCentral | null | undefined): CentralInfo {
  if (central === null || central === undefined) return PLAIN_SERVER;
  // A role this console does not know deploys, like a server: hiding pages it has would be worse.
  return { role: central.role === "hub" ? "hub" : "server", sealed: central.sealed, locked: central.locked };
}

/** Reads the central's state from a session answer. */
export function centralOf(session: SessionInfo | undefined): CentralInfo {
  return fromWire(session?.central);
}

/** The central's state, from the session query every signed-in page already holds. */
export function useCentral(): CentralInfo {
  const { data } = useQuery(sessionQuery());
  return centralOf(data);
}

/**
 * Unlocks the sealed secrets with the passphrase. The session answer is updated in place, so
 * every screen that reads `central.locked` changes at once, and read again to be sure.
 *
 * @throws ApiError 403 for a wrong passphrase, 423 when another unlock holds the seal.
 */
export async function unlockCentral(queryClient: QueryClient, passphrase: string): Promise<CentralInfo> {
  const answer = await request("post", "/api/central/unlock", { body: { passphrase } });
  const current = queryClient.getQueryData<SessionInfo>(authKeys.session);
  if (current !== undefined) queryClient.setQueryData<SessionInfo>(authKeys.session, { ...current, central: answer });
  void queryClient.invalidateQueries({ queryKey: authKeys.session });
  return fromWire(answer);
}

/** The unlock endpoint's refusal of the passphrase: not sudo mode asked for, nor declined. */
export function isWrongPassphrase(error: unknown): boolean {
  if (!isApiError(error)) return false;
  return error.error === "wrong_passphrase" || (error.status === 403 && !error.error.startsWith("elevation_"));
}

/** The 409 a hub answers when asked for something it does not do (deploy, serve, back up). */
export function isHubRoleError(error: unknown): boolean {
  return isApiError(error) && error.error === "hub_role";
}

/** The 423 a locked central answers for anything that needs a server's tunnel. */
export function isCentralLockedError(error: unknown): boolean {
  return isApiError(error) && (error.status === 423 || error.error === "central_locked");
}

/**
 * Where a hub opens when the address is the console's start (`/`): the fleet. A hub has no server of its own to begin on, so
 * with nothing chosen yet in this tab it starts on All servers; once the operator has chosen anything (the hub itself
 * included) `/` is that server's home and nothing is redirected. It is the only thing the console decides by the role: what
 * a hub can do is `session.central.features`, asked by `offers` (features.ts), never by the role.
 *
 * @param session The signed-in session.
 * @param pathname The address being opened, without `/n/...`.
 * @param node The server the address names, or null.
 * @param chosen Whether this tab has already chosen a server or All servers in the selector.
 */
export function startsOnFleet(session: SessionInfo, pathname: string, node: string | null, chosen: boolean): boolean {
  return pathname === "/" && node === null && !chosen && centralOf(session).role === "hub";
}

/** Where a tab that chose "continue locked" remembers it, until the tab is closed. */
const CONTINUE_LOCKED_KEY = "noust.central.continue-locked";

/** Whether the operator chose to use the console locked in this tab. */
export function continuedLocked(): boolean {
  try {
    return window.sessionStorage.getItem(CONTINUE_LOCKED_KEY) === "1";
  } catch {
    // Storage disabled: the lock screen shows again on the next load, which is the safe side.
    return false;
  }
}

/** Remembers, for this tab, that the operator chose to continue without unlocking. */
export function rememberContinuedLocked(): void {
  try {
    window.sessionStorage.setItem(CONTINUE_LOCKED_KEY, "1");
  } catch {
    // Not kept: only means the lock screen comes back on a reload.
  }
}
