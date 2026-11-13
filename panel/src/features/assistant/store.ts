/**
 * Whether the assistant's panel is open and what it was opened for. Tiny and free of the panel's own code on purpose: the top bar's
 * button, "Explain this" and the palette all ask it to open, and none of them may pull the panel's chunk into the first load. The
 * panel itself is loaded only once something here says it is open.
 */

import { useSyncExternalStore } from "react";

import type { PageRef } from "./model";

/** What the panel was asked to do when it opened: read a page, and ask a question at once or leave the box to the person. */
export interface PanelRequest {
  /** The page the panel reads, or none for "no page in particular". */
  page: PageRef | null;
  /** How the page is named to the person ("Deployment 14 of shop.example.com"), already in their language. */
  label: string | null;
  /** A question to send at once ("Explain this"); absent, the panel opens and waits for the person to ask. */
  question?: string;
  /** Distinguishes two requests that are the same, so asking for the same thing twice is a second request. */
  nonce: number;
}

interface PanelState {
  open: boolean;
  request: PanelRequest | null;
}

let state: PanelState = { open: false, request: null };
let nonce = 0;
const listeners = new Set<() => void>();

function set(next: PanelState): void {
  state = next;
  for (const listener of listeners) listener();
}

/** Opens the panel, on the page and the question given. Opening it again with nothing given leaves what it holds. */
export function openPanel(request?: Omit<PanelRequest, "nonce">): void {
  nonce += 1;
  set({ open: true, request: request === undefined ? state.request : { ...request, nonce } });
}

let claimed = 0;

/**
 * Whether the panel in hand is the first to answer the request with this nonce. The panel is drawn in three places by width and
 * remounts when the window crosses from one to another; the question of a request is asked once, not once per mount.
 */
export function claimRequest(requestNonce: number): boolean {
  if (requestNonce <= claimed) return false;
  claimed = requestNonce;
  return true;
}

export function closePanel(): void {
  set({ open: false, request: null });
}

export function togglePanel(): void {
  if (state.open) closePanel();
  else openPanel();
}

/** The panel's state, for whoever draws it or a button that says whether it is open. */
export function usePanel(): PanelState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => state,
  );
}

/** For tests: back to closed. */
export function resetPanel(): void {
  set({ open: false, request: null });
}
