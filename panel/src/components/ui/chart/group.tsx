import { createContext, useContext, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

/**
 * Charts that read together: a grid of charts over the same window (the machine's CPU, memory,
 * network and disk) shares one crosshair, so the moment under the pointer in one is marked in
 * all of them, and one width of value axis, so that moment falls on the same pixel in each.
 *
 * Not uPlot's own `cursor.sync`: that is fed by the mouse alone, and the keyboard steps through
 * the samples too. The moment is shared as a time, not an index, so charts whose grids differ
 * still agree.
 */
export interface ChartGroupCursor {
  /** The moment marked, Unix seconds, or null for none. */
  at: number | null;
  /** The chart whose pointer or keyboard put it there: only that one shows the readout card. */
  source: string | null;
}

export interface ChartGroupStore {
  subscribe: (listener: () => void) => () => void;
  cursor: () => ChartGroupCursor;
  setCursor: (at: number | null, source: string) => void;
  /**
   * Reports the width a member's value axis needs; returns the width every member draws, the
   * widest reported. A member whose report raises it makes the others lay out again.
   */
  reportAxis: (member: string, width: number) => number;
  /** Forgets a member that left, so its width no longer counts. */
  leave: (member: string) => void;
  /** Called when the shared axis width changes, so a member lays itself out again. */
  onAxisChange: (listener: () => void) => () => void;
}

const NONE: ChartGroupCursor = { at: null, source: null };

export function createChartGroup(): ChartGroupStore {
  let cursor = NONE;
  const listeners = new Set<() => void>();
  const axisListeners = new Set<() => void>();
  const widths = new Map<string, number>();
  const widest = (): number => Math.max(0, ...widths.values());
  const notifyAxis = (): void => {
    // Deferred: a member reports from inside its own layout, and the others must not lay out
    // in the middle of it.
    queueMicrotask(() => {
      axisListeners.forEach((listener) => {
        listener();
      });
    });
  };
  return {
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    cursor: () => cursor,
    setCursor: (at, source) => {
      if (cursor.at === at && (cursor.source === source || at === null)) return;
      cursor = at === null ? NONE : { at, source };
      listeners.forEach((listener) => {
        listener();
      });
    },
    reportAxis: (member, width) => {
      const before = widest();
      widths.set(member, width);
      const after = widest();
      if (after !== before) notifyAxis();
      return after;
    },
    leave: (member) => {
      const before = widest();
      widths.delete(member);
      if (widest() !== before) notifyAxis();
    },
    onAxisChange: (listener) => {
      axisListeners.add(listener);
      return () => {
        axisListeners.delete(listener);
      };
    },
  };
}

const GroupContext = createContext<ChartGroupStore | null>(null);

/** Makes every Chart inside read together: one crosshair, one value-axis width. */
export function ChartGroup({ children }: { children: ReactNode }) {
  const [store] = useState(createChartGroup);
  return <GroupContext.Provider value={store}>{children}</GroupContext.Provider>;
}

/** The group a chart belongs to, or null when it stands alone. */
export function useChartGroupStore(): ChartGroupStore | null {
  return useContext(GroupContext);
}

const noSubscription = (): (() => void) => () => undefined;

/** The group's crosshair, re-rendering when it moves; nothing marked outside a group. */
export function useChartGroupCursor(store: ChartGroupStore | null): ChartGroupCursor {
  return useSyncExternalStore(store?.subscribe ?? noSubscription, store ? store.cursor : () => NONE);
}
