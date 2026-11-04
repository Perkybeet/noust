/**
 * Arranging a panel with a pointer: moving a widget by dragging it, bringing one in from the gallery by
 * dragging it onto the panel, and the short stories of adding and removing one (a new widget settles, a
 * removed one lifts and goes). No library: pointer events on the window, the grid's own geometry and
 * nothing injected into the page, which is what lets it run under the console's Content Security Policy.
 *
 * The page owns the layout; this reads it and asks for the next one (`onLayoutChange`), and keeps what
 * the pointer is holding (`drag`), so the gallery, which is somewhere else on the page, can start a drag
 * of its own (`startAdd`) onto the same grid.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

import { announce } from "../../app/Announcer";
import { useT } from "../../i18n";
import { REDUCED_MOTION } from "../ui/useMediaQuery";
import { indexAt, nearGrid, pixelsOf, rectOf } from "./dragMath";
import type { Geometry } from "./dragMath";
import { COLUMNS, PHONE_COLUMNS, reorder, slotsOf, spansFor } from "./model";
import type { Placed } from "./model";
import type { FaceOf } from "./types";
import { useLatest } from "./useLatest";

/** What the pointer is holding: a widget of the panel being moved, or one from the gallery being brought in. */
export interface DragState {
  kind: "move" | "add";
  item: Placed;
  /** The top left of the copy that follows the pointer, and where inside it the pointer took hold. */
  x: number;
  y: number;
  dx: number;
  dy: number;
  width: number;
  height: number;
  /** For `add`: the widget is part of the layout only while the pointer is over the panel. */
  inGrid: boolean;
  /** Set when the pointer is let go: where the copy slides to before it goes. */
  dropping?: { x: number; y: number };
  /** The layout when the drag began, which Escape puts back. */
  origin: readonly Placed[];
}

export interface WidgetBoardOptions {
  layout: readonly Placed[];
  onLayoutChange: (next: readonly Placed[]) => void;
  faceOf: FaceOf;
  /** Four columns instead of twelve. */
  phone: boolean;
}

export interface WidgetBoard {
  gridRef: RefObject<HTMLDivElement | null>;
  drag: DragState | null;
  /** Takes hold of a widget of the panel. `cell` is the element that holds it. */
  startMove: (id: string, event: ReactPointerEvent<HTMLElement>, cell: HTMLElement) => void;
  /** Takes hold of a widget that is not in the panel yet: the gallery's. */
  startAdd: (item: Placed, event: ReactPointerEvent<HTMLElement>) => void;
  /** Puts a widget at the end of the panel and says so. */
  add: (item: Placed) => void;
  /** Takes a widget out, after it has lifted away. */
  remove: (id: string) => void;
  /** The widget that has just been added, for its entrance; the one that is leaving, for its exit. */
  settling: string | null;
  retiring: string | null;
  columns: number;
}

/** How long the copy takes to slide into its place when it is let go, and how long a removed widget lifts. */
const DROP_MS = 210;
const RETIRE_MS = 140;
const SETTLE_MS = 300;

/** How near the edge of the window a drag has to be for the page to scroll under it, and by how much. */
const EDGE_PX = 60;
const SCROLL_PX = 14;

export function useWidgetBoard({ layout, onLayoutChange, faceOf, phone }: WidgetBoardOptions): WidgetBoard {
  const t = useT();
  const columns = phone ? PHONE_COLUMNS : COLUMNS;
  const gridRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [settling, setSettling] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<string | null>(null);
  const layoutRef = useLatest(layout);
  const dragRef = useLatest(drag);
  const faceRef = useLatest(faceOf);
  const changeRef = useLatest(onLayoutChange);
  const timers = useRef(new Set<number>());

  const later = useCallback((run: () => void, ms: number) => {
    const timer = window.setTimeout(() => {
      timers.current.delete(timer);
      run();
    }, ms);
    timers.current.add(timer);
  }, []);
  useEffect(() => {
    const held = timers.current;
    return () => {
      for (const timer of held) window.clearTimeout(timer);
      held.clear();
      document.documentElement.classList.remove("widget-dragging");
    };
  }, []);

  const titleOf = useCallback((item: Placed): string => faceRef.current(item)?.title ?? item.type, [faceRef]);

  const geometry = useCallback((): Geometry | null => {
    const grid = gridRef.current;
    if (grid === null) return null;
    const rect = grid.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width, columns };
  }, [columns]);

  const finish = useCallback(
    (commit: boolean) => {
      const current = dragRef.current;
      if (current === null || current.dropping !== undefined) return;
      document.documentElement.classList.remove("widget-dragging");
      const now = layoutRef.current;
      if (!commit) {
        // Escape puts everything back: a widget moved is where it was, one brought in is not there.
        changeRef.current(current.kind === "add" ? now.filter((item) => item.id !== current.item.id) : current.origin);
        announce(t("widgets.grid.cancelled", { title: titleOf(current.item) }));
        setDrag(null);
        return;
      }
      if (current.kind === "add" && !current.inGrid) {
        setDrag(null);
        return;
      }
      const index = now.findIndex((item) => item.id === current.item.id);
      announce(t("widgets.grid.dropped", { title: titleOf(current.item), position: index + 1 }));
      const where = geometry();
      const slot = slotsOf(now, columns)[index];
      if (window.matchMedia(REDUCED_MOTION).matches || where === null || slot === undefined) {
        setDrag(null);
        return;
      }
      const target = rectOf(slot, where);
      setDrag({ ...current, dropping: { x: target.left, y: target.top }, width: target.width, height: target.height });
      later(() => setDrag(null), DROP_MS);
    },
    [changeRef, columns, dragRef, geometry, later, layoutRef, t, titleOf],
  );

  const active = drag !== null && drag.dropping === undefined;
  useEffect(() => {
    if (!active) return;
    const onMove = (event: PointerEvent): void => {
      const current = dragRef.current;
      const where = geometry();
      const grid = gridRef.current;
      if (current === null || where === null || grid === null || current.dropping !== undefined) return;
      const next: DragState = { ...current, x: event.clientX - current.dx, y: event.clientY - current.dy };
      let layoutNow = layoutRef.current;
      const over = nearGrid(where, grid.getBoundingClientRect().bottom, event.clientX, event.clientY);
      if (current.kind === "add") {
        if (over && !current.inGrid) {
          layoutNow = [...layoutNow, current.item];
          next.inGrid = true;
        } else if (!over && current.inGrid) {
          layoutNow = layoutNow.filter((item) => item.id !== current.item.id);
          next.inGrid = false;
        }
      }
      if (over && (current.kind === "move" || next.inGrid)) {
        const index = indexAt(layoutNow, current.item.id, where, event.clientX, event.clientY);
        if (index !== null) layoutNow = reorder(layoutNow, current.item.id, index);
      }
      if (layoutNow !== layoutRef.current && layoutNow.map((item) => item.id).join() !== layoutRef.current.map((item) => item.id).join()) changeRef.current(layoutNow);
      setDrag(next);
      // Near an edge of the window the page scrolls under the widget, so a long panel can be arranged top to bottom.
      if (event.clientY > window.innerHeight - EDGE_PX) window.scrollBy(0, SCROLL_PX);
      else if (event.clientY < EDGE_PX + 20) window.scrollBy(0, -SCROLL_PX);
    };
    const onUp = (): void => finish(true);
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("keydown", onKey);
    };
  }, [active, changeRef, dragRef, finish, geometry, layoutRef]);

  const startMove = useCallback(
    (id: string, event: ReactPointerEvent<HTMLElement>, cell: HTMLElement) => {
      const item = layoutRef.current.find((placed) => placed.id === id);
      if (item === undefined || event.button !== 0) return;
      event.preventDefault();
      const rect = cell.getBoundingClientRect();
      document.documentElement.classList.add("widget-dragging");
      setDrag({
        kind: "move",
        item,
        x: rect.left,
        y: rect.top,
        dx: event.clientX - rect.left,
        dy: event.clientY - rect.top,
        width: rect.width,
        height: rect.height,
        inGrid: true,
        origin: layoutRef.current,
      });
    },
    [layoutRef],
  );

  const startAdd = useCallback(
    (item: Placed, event: ReactPointerEvent<HTMLElement>) => {
      const where = geometry();
      if (where === null || event.button !== 0) return;
      event.preventDefault();
      const { width, height } = pixelsOf(spansFor(item.size, columns), where);
      document.documentElement.classList.add("widget-dragging");
      setDrag({
        kind: "add",
        item,
        x: event.clientX - 24,
        y: event.clientY - 20,
        dx: 24,
        dy: 20,
        width: Math.min(width, where.width),
        height,
        inGrid: false,
        origin: layoutRef.current,
      });
    },
    [columns, geometry, layoutRef],
  );

  const add = useCallback(
    (item: Placed) => {
      changeRef.current([...layoutRef.current, item]);
      setSettling(item.id);
      later(() => setSettling(null), SETTLE_MS);
      announce(t("widgets.grid.added", { title: titleOf(item) }));
    },
    [changeRef, later, layoutRef, t, titleOf],
  );

  const remove = useCallback(
    (id: string) => {
      const item = layoutRef.current.find((placed) => placed.id === id);
      if (item === undefined) return;
      setRetiring(id);
      later(() => {
        changeRef.current(layoutRef.current.filter((placed) => placed.id !== id));
        setRetiring(null);
        announce(t("widgets.grid.removed", { title: titleOf(item) }));
      }, RETIRE_MS);
    },
    [changeRef, later, layoutRef, t, titleOf],
  );

  return { gridRef, drag, startMove, startAdd, add, remove, settling, retiring, columns };
}
