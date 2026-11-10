/**
 * Arranging a panel with a pointer: moving a widget by dragging it, bringing one in from the gallery to the place it
 * is wanted, changing a widget's size by an edge or its corner, and the short stories of adding and removing one (a
 * new widget settles, a removed one lifts and goes). No library: pointer events on the window, the grid's own
 * geometry and nothing injected into the page, which is what lets it run under the console's Content Security Policy.
 *
 * The page owns the layout; this reads it and asks for the next one (`onLayoutChange`), and keeps what the pointer
 * is holding (`drag`, `resize`), so the gallery, which is somewhere else on the page, can start a drag of its own
 * (`startAdd`) onto the same grid.
 *
 * Three things a pointer needs that a stream of move events does not give it:
 *
 * - The page scrolls under a widget held near the top or the bottom of the window for as long as it is held there,
 *   once a frame (`edgeSpeed`), and not once a move: a hand that rests at the edge has not moved.
 * - A finger lifts a widget by pressing and holding it, and from then on the page does not scroll under it; before
 *   that it is only scrolling the page, and nothing is lifted by accident.
 * - Every change a person made on purpose can be undone with the toast that says it was made (`remember`).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

import { announce } from "../../app/Announcer";
import { useT } from "../../i18n";
import { toast } from "../ui/toast";
import { REDUCED_MOTION } from "../ui/useMediaQuery";
import { edgeSpeed, indexAt, nearGrid, rectOf, resizeTarget } from "./dragMath";
import type { Geometry, ResizeHandle } from "./dragMath";
import { COLUMNS, PHONE_COLUMNS, reorder, slotsOf } from "./model";
import type { Placed, SizeKey } from "./model";
import type { FaceOf } from "./types";
import { useLatest } from "./useLatest";

/** What the pointer is holding: a widget of the panel being moved, or one from the gallery being brought in. */
export interface DragState {
  kind: "move" | "add";
  item: Placed;
  /** The top left of the copy that follows the pointer when it was lifted, and where inside it the pointer took hold. The pointer moves the copy itself from there. */
  x: number;
  y: number;
  dx: number;
  dy: number;
  width: number;
  /** For `add` the copy is the gallery's card and takes the height of what it says, so there is none to give. */
  height: number;
  /** For `add`: the widget is part of the layout only while the pointer is over the panel. */
  inGrid: boolean;
  /** Set when the pointer is let go: where the copy slides to before it goes. */
  dropping?: { x: number; y: number };
  /** The layout when the drag began, which Escape puts back. */
  origin: readonly Placed[];
}

/** An edge or a corner held to change a widget's size: where it began and the size it will have if it is let go now. */
export interface ResizeState {
  id: string;
  handle: ResizeHandle;
  from: SizeKey;
  to: SizeKey;
}

/** What an undo puts back: the layout as it was, and what was done, for the words of the toast. */
export type UndoKind = "moved" | "resized" | "removed";

export interface WidgetBoardOptions {
  layout: readonly Placed[];
  onLayoutChange: (next: readonly Placed[]) => void;
  faceOf: FaceOf;
  /** Four columns instead of twelve. */
  phone: boolean;
  /** The panel is being arranged, which is when a finger on the page may have to be kept from scrolling it. */
  enabled?: boolean;
}

export interface WidgetBoard {
  gridRef: RefObject<HTMLDivElement | null>;
  drag: DragState | null;
  resize: ResizeState | null;
  /**
   * Takes hold of a widget of the panel. `cell` is the element that holds it. A finger on the widget has to hold it for a
   * moment first; one on its handle (`grip`) takes it at once, as a mouse does.
   */
  startMove: (id: string, event: ReactPointerEvent<HTMLElement>, cell: HTMLElement, options?: { grip?: boolean }) => void;
  /** Takes hold of a widget that is not in the panel yet: the gallery's. */
  startAdd: (item: Placed, event: ReactPointerEvent<HTMLElement>) => void;
  /** Takes hold of an edge or the corner of a widget. */
  startResize: (id: string, handle: ResizeHandle, event: ReactPointerEvent<HTMLElement>) => void;
  /** Puts a widget at the end of the panel and says so. */
  add: (item: Placed) => void;
  /** Takes a widget out, after it has lifted away. */
  remove: (id: string) => void;
  /** Says a change was made and offers to undo it: the layout as it was before. */
  remember: (kind: UndoKind, item: Placed, before: readonly Placed[]) => void;
  /** Takes the offer to undo away, for a change that comes after it and would be lost by taking it. */
  forgetUndo: () => void;
  /** Says which widget's handle should have the keyboard once the panel is drawn again (null: none in particular). */
  focusLater: (id: string | null) => void;
  /** The widget whose handle was asked for, once: what draws the panel gives it the keyboard and the request is spent. */
  takeFocus: () => string | null;
  /** The widget that has just been added, for its entrance; the one that is leaving, for its exit. */
  settling: string | null;
  retiring: string | null;
  columns: number;
}

/** How long the copy takes to slide into its place when it is let go, and how long a removed widget lifts. */
const DROP_MS = 210;
const RETIRE_MS = 140;
const SETTLE_MS = 300;

/** How long a finger holds a widget before it is lifted, and how far it may drift meanwhile: further and it is a scroll. */
const HOLD_MS = 380;
const HOLD_SLOP_PX = 8;

/** How long the toast that offers to undo stays, longer than a toast that only says something: the hand has to get to it. */
const UNDO_MS = 8000;
const UNDO_ID = "panel-undo";

/** A finger that has pressed a widget and is being waited on. */
interface Hold {
  release: () => void;
}

/** Where a resize began, for the distance the pointer has gone since: the scroll counts, or a page that moves under a still hand would not. */
interface ResizeStart {
  x: number;
  y: number;
  scrollY: number;
  from: SizeKey;
  handle: ResizeHandle;
  allowed: readonly SizeKey[];
}

export function useWidgetBoard({ layout, onLayoutChange, faceOf, phone, enabled = true }: WidgetBoardOptions): WidgetBoard {
  const t = useT();
  const columns = phone ? PHONE_COLUMNS : COLUMNS;
  const gridRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [resize, setResize] = useState<ResizeState | null>(null);
  const [settling, setSettling] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<string | null>(null);
  const layoutRef = useLatest(layout);
  const faceRef = useLatest(faceOf);
  const changeRef = useLatest(onLayoutChange);
  const textRef = useLatest(t);
  // What the pointer is holding, as it is now: a move event can come before React has drawn the one before it, so the
  // state alone would let a widget from the gallery be put in the panel twice.
  const dragLive = useRef<DragState | null>(null);
  const resizeLive = useRef<ResizeState | null>(null);
  const resizeStart = useRef<ResizeStart | null>(null);
  const pointer = useRef({ x: 0, y: 0 });
  const hold = useRef<Hold | null>(null);
  const focusAfter = useRef<string | null>(null);
  const focusLater = useCallback((id: string | null) => {
    focusAfter.current = id;
  }, []);
  const takeFocus = useCallback((): string | null => {
    const id = focusAfter.current;
    focusAfter.current = null;
    return id;
  }, []);
  const timers = useRef(new Set<number>());

  const later = useCallback((run: () => void, ms: number) => {
    const timer = window.setTimeout(() => {
      timers.current.delete(timer);
      run();
    }, ms);
    timers.current.add(timer);
  }, []);

  const releaseHold = useCallback(() => {
    hold.current?.release();
    hold.current = null;
  }, []);

  useEffect(() => {
    const held = timers.current;
    return () => {
      for (const timer of held) window.clearTimeout(timer);
      held.clear();
      releaseHold();
      document.documentElement.classList.remove("widget-dragging");
      // An offer to undo is for the panel that is on screen: it must not outlive it and put back a layout of another page.
      toast.dismiss(UNDO_ID);
    };
  }, [releaseHold]);

  const titleOf = useCallback((item: Placed): string => faceRef.current(item)?.title ?? item.type, [faceRef]);

  const geometry = useCallback((): Geometry | null => {
    const grid = gridRef.current;
    if (grid === null) return null;
    const rect = grid.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width, columns };
  }, [columns]);

  const setDragNow = useCallback((next: DragState | null) => {
    dragLive.current = next;
    setDrag(next);
  }, []);
  const setResizeNow = useCallback((next: ResizeState | null) => {
    resizeLive.current = next;
    setResize(next);
  }, []);

  /** The layout is changed here and the ref that the next move event reads is brought up to date, not left for the next draw. */
  const commit = useCallback(
    (next: readonly Placed[]) => {
      layoutRef.current = next;
      changeRef.current(next);
    },
    [changeRef, layoutRef],
  );

  const forgetUndo = useCallback(() => toast.dismiss(UNDO_ID), []);

  const remember = useCallback(
    (kind: UndoKind, item: Placed, before: readonly Placed[]) => {
      const say = textRef.current;
      const title = titleOf(item);
      toast.info(say(`widgets.grid.${kind}Toast`, { title }), {
        id: UNDO_ID,
        timeout: UNDO_MS,
        action: {
          label: say("widgets.grid.undo"),
          onClick: () => {
            toast.dismiss(UNDO_ID);
            commit(before);
            // The widget a removal put back is where the keyboard was before it went; the one that was moved is still where its handle is.
            focusLater(item.id);
            if (kind === "removed") {
              setSettling(item.id);
              later(() => setSettling(null), SETTLE_MS);
            }
            announce(textRef.current("widgets.grid.undone", { title }));
          },
        },
      });
    },
    [commit, focusLater, later, textRef, titleOf],
  );

  /** A widget that is held for as long as the pointer is, and nothing in the document changes until it moves. */
  const lift = useCallback(
    (state: DragState, x: number, y: number) => {
      pointer.current = { x, y };
      document.documentElement.classList.add("widget-dragging");
      setDragNow(state);
    },
    [setDragNow],
  );

  /** What a pointer at (x, y) asks of the layout while something is held: where it goes, and whether it is in the panel at all. */
  const applyDrag = useCallback(
    (x: number, y: number) => {
      const current = dragLive.current;
      const where = geometry();
      const grid = gridRef.current;
      if (current === null || where === null || grid === null || current.dropping !== undefined) return;
      let layoutNow = layoutRef.current;
      let inGrid = current.inGrid;
      const over = nearGrid(where, grid.getBoundingClientRect().bottom, x, y);
      if (current.kind === "add") {
        if (over && !inGrid) {
          layoutNow = [...layoutNow, current.item];
          inGrid = true;
        } else if (!over && inGrid) {
          layoutNow = layoutNow.filter((item) => item.id !== current.item.id);
          inGrid = false;
        }
      }
      if (over && (current.kind === "move" || inGrid)) {
        const index = indexAt(layoutNow, current.item.id, where, x, y);
        if (index !== null) layoutNow = reorder(layoutNow, current.item.id, index);
      }
      if (layoutNow !== layoutRef.current && layoutNow.map((item) => item.id).join() !== layoutRef.current.map((item) => item.id).join()) commit(layoutNow);
      if (inGrid !== current.inGrid) setDragNow({ ...current, inGrid });
    },
    [commit, geometry, layoutRef, setDragNow],
  );

  const applyResize = useCallback(
    (x: number, y: number) => {
      const start = resizeStart.current;
      const current = resizeLive.current;
      const where = geometry();
      if (start === null || current === null || where === null) return;
      // Down is further down the page than the window says when the page has scrolled since the hand took hold.
      const to = resizeTarget(start.allowed, start.from, start.handle, where, x - start.x, y - start.y + (window.scrollY - start.scrollY));
      if (to !== current.to) setResizeNow({ ...current, to });
    },
    [geometry, setResizeNow],
  );

  const finishDrag = useCallback(
    (keep: boolean) => {
      const current = dragLive.current;
      if (current === null || current.dropping !== undefined) return;
      document.documentElement.classList.remove("widget-dragging");
      const now = layoutRef.current;
      if (!keep) {
        // Escape puts everything back: a widget moved is where it was, one brought in is not there.
        commit(current.kind === "add" ? now.filter((item) => item.id !== current.item.id) : current.origin);
        announce(t("widgets.grid.cancelled", { title: titleOf(current.item) }));
        setDragNow(null);
        return;
      }
      if (current.kind === "add" && !current.inGrid) {
        setDragNow(null);
        return;
      }
      const index = now.findIndex((item) => item.id === current.item.id);
      announce(t("widgets.grid.dropped", { title: titleOf(current.item), position: index + 1 }));
      const arrive = (): void => {
        if (current.kind !== "add") return;
        // The widget the card stood for settles where the panel made room for it.
        setSettling(current.item.id);
        later(() => setSettling(null), SETTLE_MS);
      };
      if (current.kind === "add") forgetUndo();
      else if (current.origin.map((item) => item.id).join() !== now.map((item) => item.id).join()) remember("moved", current.item, current.origin);
      const where = geometry();
      const slot = slotsOf(now, columns)[index];
      if (window.matchMedia(REDUCED_MOTION).matches || where === null || slot === undefined) {
        setDragNow(null);
        arrive();
        return;
      }
      // What is in the hand slides into the place: a widget as it is, the gallery's card to the corner of the room the widget will take.
      const target = rectOf(slot, where);
      setDragNow({ ...current, dropping: { x: target.left, y: target.top }, ...(current.kind === "move" ? { width: target.width, height: target.height } : {}) });
      later(() => {
        setDragNow(null);
        arrive();
      }, DROP_MS);
    },
    [columns, commit, forgetUndo, geometry, later, layoutRef, remember, setDragNow, t, titleOf],
  );

  const finishResize = useCallback(
    (keep: boolean) => {
      const current = resizeLive.current;
      if (current === null) return;
      document.documentElement.classList.remove("widget-dragging");
      resizeStart.current = null;
      setResizeNow(null);
      if (!keep || current.to === current.from) return;
      const before = layoutRef.current;
      const item = before.find((placed) => placed.id === current.id);
      if (item === undefined) return;
      commit(before.map((placed) => (placed.id === current.id ? { ...placed, size: current.to } : placed)));
      announce(t("widgets.grid.resized", { title: titleOf(item), size: t(`widgets.sizes.${current.to}`).toLocaleLowerCase(t.locale) }));
      remember("resized", item, before);
    },
    [commit, layoutRef, remember, setResizeNow, t, titleOf],
  );

  // The handlers a window listener holds: it is added once and reads these, so it never acts on what the page was.
  const handlers = useLatest({ applyDrag, applyResize, finishDrag, finishResize });
  const active = (drag !== null && drag.dropping === undefined) || resize !== null;
  const activeRef = useLatest(active);
  useEffect(() => {
    if (!active) return;
    const onMove = (event: PointerEvent): void => {
      pointer.current = { x: event.clientX, y: event.clientY };
      if (resizeLive.current !== null) handlers.current.applyResize(event.clientX, event.clientY);
      else handlers.current.applyDrag(event.clientX, event.clientY);
    };
    const onUp = (): void => {
      if (resizeLive.current !== null) handlers.current.finishResize(true);
      else handlers.current.finishDrag(true);
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (resizeLive.current !== null) handlers.current.finishResize(false);
      else handlers.current.finishDrag(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    window.addEventListener("keydown", onKey);

    // Near an edge of the window the page scrolls under what is held, a frame at a time and by how far the edge is
    // from the pointer, for as long as the pointer stays there. What the page then holds under the pointer is asked again.
    let frame = 0;
    let last: number | null = null;
    let carry = 0;
    const tick = (now: number): void => {
      frame = window.requestAnimationFrame(tick);
      const elapsed = last === null ? 0 : Math.min(now - last, 50);
      last = now;
      const speed = edgeSpeed(pointer.current.y, window.innerHeight);
      if (speed === 0) {
        carry = 0;
        return;
      }
      // A frame moves the page by a fraction of a pixel on a fast screen: what is left over is kept for the next one.
      carry += (speed * elapsed) / 1000;
      const whole = Math.trunc(carry);
      if (whole === 0) return;
      carry -= whole;
      window.scrollBy(0, whole);
      if (resizeLive.current !== null) handlers.current.applyResize(pointer.current.x, pointer.current.y);
      else handlers.current.applyDrag(pointer.current.x, pointer.current.y);
    };
    frame = window.requestAnimationFrame(tick);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("keydown", onKey);
    };
  }, [active, handlers]);

  // A finger that has lifted a widget must not also scroll the page. The browser decides at the first move, so the
  // listener is there before the finger is: it is not passive, and it only cancels while something is held.
  useEffect(() => {
    if (!enabled) return;
    const keepStill = (event: TouchEvent): void => {
      if (activeRef.current && event.cancelable) event.preventDefault();
    };
    window.addEventListener("touchmove", keepStill, { passive: false });
    return () => window.removeEventListener("touchmove", keepStill);
  }, [activeRef, enabled]);

  /**
   * A finger is waited on: if it stays where it is for `HOLD_MS`, `lifted` is called with where it is, and if it goes
   * anywhere before that, or is lifted, it was a tap or a scroll and nothing happens.
   */
  const waitForHold = useCallback(
    (event: ReactPointerEvent<HTMLElement>, lifted: (x: number, y: number) => void) => {
      releaseHold();
      const at = { x: event.clientX, y: event.clientY };
      const id = event.pointerId;
      const onMove = (move: PointerEvent): void => {
        if (move.pointerId !== id) return;
        if (Math.hypot(move.clientX - at.x, move.clientY - at.y) > HOLD_SLOP_PX) releaseHold();
      };
      const onEnd = (end: PointerEvent): void => {
        if (end.pointerId === id) releaseHold();
      };
      const timer = window.setTimeout(() => {
        releaseHold();
        // A short buzz where the device has one, so the hand knows the widget is up before it sees it.
        if ("vibrate" in navigator) navigator.vibrate(12);
        lifted(at.x, at.y);
      }, HOLD_MS);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onEnd);
      window.addEventListener("pointercancel", onEnd);
      hold.current = {
        release: () => {
          window.clearTimeout(timer);
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", onEnd);
          window.removeEventListener("pointercancel", onEnd);
        },
      };
    },
    [releaseHold],
  );

  const startMove = useCallback(
    (id: string, event: ReactPointerEvent<HTMLElement>, cell: HTMLElement, options?: { grip?: boolean }) => {
      const item = layoutRef.current.find((placed) => placed.id === id);
      if (item === undefined || event.button !== 0) return;
      const take = (x: number, y: number): void => {
        const rect = cell.getBoundingClientRect();
        lift({ kind: "move", item, x: rect.left, y: rect.top, dx: x - rect.left, dy: y - rect.top, width: rect.width, height: rect.height, inGrid: true, origin: layoutRef.current }, x, y);
      };
      if (event.pointerType === "touch" && options?.grip !== true) {
        waitForHold(event, take);
        return;
      }
      event.preventDefault();
      take(event.clientX, event.clientY);
    },
    [layoutRef, lift, waitForHold],
  );

  const startAdd = useCallback(
    (item: Placed, event: ReactPointerEvent<HTMLElement>) => {
      const where = geometry();
      if (where === null || event.button !== 0) return;
      const card = event.currentTarget.getBoundingClientRect();
      // What follows the pointer is the card that was taken, where it was taken: the panel shows the size.
      const take = (x: number, y: number): void => lift({ kind: "add", item, x: card.left, y: card.top, dx: x - card.left, dy: y - card.top, width: card.width, height: 0, inGrid: false, origin: layoutRef.current }, x, y);
      if (event.pointerType === "touch") {
        waitForHold(event, take);
        return;
      }
      event.preventDefault();
      take(event.clientX, event.clientY);
    },
    [geometry, layoutRef, lift, waitForHold],
  );

  const startResize = useCallback(
    (id: string, handle: ResizeHandle, event: ReactPointerEvent<HTMLElement>) => {
      const item = layoutRef.current.find((placed) => placed.id === id);
      const face = item === undefined ? null : faceRef.current(item);
      if (item === undefined || face === null || event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      pointer.current = { x: event.clientX, y: event.clientY };
      resizeStart.current = { x: event.clientX, y: event.clientY, scrollY: window.scrollY, from: item.size, handle, allowed: face.sizes };
      document.documentElement.classList.add("widget-dragging");
      setResizeNow({ id, handle, from: item.size, to: item.size });
    },
    [faceRef, layoutRef, setResizeNow],
  );

  const add = useCallback(
    (item: Placed) => {
      forgetUndo();
      commit([...layoutRef.current, item]);
      setSettling(item.id);
      later(() => setSettling(null), SETTLE_MS);
      announce(t("widgets.grid.added", { title: titleOf(item) }));
      // The new widget goes to the end of a panel that may be longer than the window: it is brought into view.
      later(() => {
        const cell = [...(gridRef.current?.querySelectorAll<HTMLElement>("[data-cell]") ?? [])].find((element) => element.dataset["cell"] === item.id);
        if (typeof cell?.scrollIntoView === "function") cell.scrollIntoView({ block: "nearest", behavior: window.matchMedia(REDUCED_MOTION).matches ? "auto" : "smooth" });
      }, 0);
    },
    [commit, forgetUndo, later, layoutRef, t, titleOf],
  );

  const remove = useCallback(
    (id: string) => {
      const item = layoutRef.current.find((placed) => placed.id === id);
      if (item === undefined) return;
      const before = layoutRef.current;
      setRetiring(id);
      later(() => {
        commit(layoutRef.current.filter((placed) => placed.id !== id));
        setRetiring(null);
        announce(t("widgets.grid.removed", { title: titleOf(item) }));
        remember("removed", item, before);
      }, RETIRE_MS);
    },
    [commit, later, layoutRef, remember, t, titleOf],
  );

  return { gridRef, drag, resize, startMove, startAdd, startResize, add, remove, remember, forgetUndo, focusLater, takeFocus, settling, retiring, columns };
}
