/**
 * Whether Home is being customised: the one thing the page of Home and its column have to agree on.
 * While it is, the column is the gallery of widgets and the page is the panel in edit mode (4.2 design,
 * 3.3); when it is not, the column lists the panels. A page and a column are two places in the frame,
 * so the state is not either's: it lives here, outside React, and both read it.
 *
 * It is not saved and not in the address: leaving Home ends it, so a person never comes back to a
 * half-edited page they did not mean to open (the frame closes it, `app/Shell.tsx`).
 */

import { useSyncExternalStore } from "react";

let customising = false;
const listeners = new Set<() => void>();

/** Starts or ends customising Home. Called by the page of Home, which owns the buttons that do it. */
export function setHomeCustomising(value: boolean): void {
  if (customising === value) return;
  customising = value;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Whether Home is being customised now. */
export function useHomeCustomising(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => customising,
    () => false,
  );
}
