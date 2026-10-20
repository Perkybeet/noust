import { useEffect, useSyncExternalStore } from "react";

/**
 * A view has one primary action (DESIGN 4, FM-2). The header's is the one the page is for; while a
 * `SaveBar` holds changes the primary is Save, and the header's yields: it is drawn as a secondary
 * until the changes are saved or discarded.
 *
 * The save bar and the header are far apart in the tree (the header is the layout's, the bar is a
 * subsection's), so what joins them is a count of the bars that hold changes, outside React: a
 * count and not a flag, so two bars (a page and a drawer's form) that overlap do not cancel each
 * other when one is closed.
 */
let holding = 0;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Called by whatever makes Save the view's primary action, while it has changes to save. */
export function useYieldPrimary(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    holding += 1;
    emit();
    return () => {
      holding -= 1;
      emit();
    };
  }, [active]);
}

/** Whether the header's primary action should be drawn as a secondary right now. */
export function usePrimaryYielded(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => holding > 0,
    () => false,
  );
}
