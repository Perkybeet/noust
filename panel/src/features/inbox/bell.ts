import { useSyncExternalStore } from "react";

/**
 * Asking the bell to open, from somewhere that is not the bell: the head of Home's "Needs attention" says "See all 5", and
 * what it opens is the list the bell holds. The bell keeps its own open state, so what it listens to is a count of the times
 * it was asked, and opens when that moves. (The approvals module has an `openApprovals` of its own for its page: the two had
 * the same name once, and "See all" went to the wrong one.)
 */
let requests = 0;
const listeners = new Set<() => void>();

/** Asks the bell to open on what needs the operator. */
export function openBell(): void {
  requests += 1;
  for (const listener of listeners) listener();
}

/** How many times the bell has been asked to open: it opens when this changes after it was drawn. */
export function useBellRequests(): number {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => requests,
    () => 0,
  );
}
