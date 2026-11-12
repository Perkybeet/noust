/**
 * Home's grid: a CSS grid of 12 columns (4 on a phone) whose cells are the widgets, each placed through
 * the CSSOM (React's `style`, which the Content Security Policy allows) where `pack` says. Arranging is
 * three ways of one thing, and none needs the pointer (WCAG 2.5.7): a widget is dragged, or lifted with
 * Space from its handle and moved with the arrow keys (and made bigger or smaller with Shift and them), or moved
 * and resized from the menu of its size. Its edges and its corner drag its size to the nearest one it takes, with
 * an outline of the size it will have. Every change is announced, and can be undone; a widget that changes place
 * slides from where it was (transform only, in the console's move duration; with reduced motion the durations are
 * zero and it is instant).
 */
import { Check, GripVertical, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, RefObject } from "react";

import { announce } from "../../app/Announcer";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Menu, MenuGroup, MenuItem, MenuSeparator } from "../ui/Menu";
import { REDUCED_MOTION } from "../ui/useMediaQuery";
import { DropPlace, Grip, SizeOutline } from "./ArrangeParts";
import { SizeGlyph } from "./SizeGlyph";
import { WidgetFrame } from "./WidgetFrame";
import { handlesFor, localRect } from "./dragMath";
import { GAP_PX, ROW_PX, SIZE_ORDER, moveBy, sameLayout, slotsOf, stepSize } from "./model";
import type { Axis, Placed, SizeKey, Slot } from "./model";
import type { FaceOf } from "./types";
import { useLatest } from "./useLatest";
import type { DragState, ResizeState, WidgetBoard } from "./useWidgetBoard";

/** Whether the grid is the phone's four columns, where only the corner is a grip: a finger down an edge would be scrolling the page. */
function onPhone(columns: number): boolean {
  return columns < 12;
}

/** The place of a cell on the grid: explicit, so what is drawn is what the layout says. */
export function cellStyle(slot: Slot): CSSProperties {
  return { gridColumn: `${String(slot.x + 1)} / span ${String(slot.w)}`, gridRow: `${String(slot.y + 1)} / span ${String(slot.h)}` };
}

export function gridStyle(columns: number): CSSProperties {
  return { gridTemplateColumns: `repeat(${String(columns)}, minmax(0, 1fr))`, gridAutoRows: `${String(ROW_PX)}px`, gap: `${String(GAP_PX)}px` };
}

/** Slides every cell from where it was to where it is when the layout changes: transform only. */
function useFlip(gridRef: RefObject<HTMLDivElement | null>, signature: string): void {
  const previous = useRef(new Map<string, { x: number; y: number }>());
  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (grid === null) return;
    const origin = grid.getBoundingClientRect();
    const reduced = window.matchMedia(REDUCED_MOTION).matches;
    const next = new Map<string, { x: number; y: number }>();
    for (const cell of grid.querySelectorAll<HTMLElement>("[data-cell]")) {
      const id = cell.dataset["cell"] ?? "";
      cell.style.transition = "none";
      cell.style.transform = "";
      const box = cell.getBoundingClientRect();
      const rect = { x: box.left - origin.left, y: box.top - origin.top };
      next.set(id, rect);
      const was = previous.current.get(id);
      if (was === undefined || reduced) continue;
      const dx = was.x - rect.x;
      const dy = was.y - rect.y;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      cell.style.transform = `translate(${String(dx)}px, ${String(dy)}px)`;
      requestAnimationFrame(() => {
        cell.style.transition = "transform var(--duration-move) var(--ease-standard)";
        cell.style.transform = "";
      });
    }
    previous.current = next;
  }, [gridRef, signature]);
}

/** What the copy in the hand looks like: a little lifted, and a little turned, as a card picked off a table is. */
function liftedTransform(x: number, y: number, sliding: boolean): string {
  return `translate(${String(x)}px, ${String(y)}px)${sliding ? "" : " rotate(0.6deg) scale(1.01)"}`;
}

/**
 * The copy of what is being dragged: a widget of the panel is lifted as it is, and follows the pointer, then slides
 * into its place; one from the gallery is the card that was taken, since the panel shows the size it will have where it
 * will be. The pointer moves it from here (a window listener, written to the CSSOM), not through the state of the page:
 * a drag that redrew every widget at every move would not keep up with a hand.
 */
export function DragGhost({ drag, faceOf }: { drag: DragState; faceOf: FaceOf }) {
  const ref = useRef<HTMLDivElement>(null);
  const sliding = drag.dropping !== undefined;
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    const target = drag.dropping ?? { x: drag.x, y: drag.y };
    element.style.transform = liftedTransform(target.x, target.y, drag.dropping !== undefined);
    element.style.width = `${String(drag.width)}px`;
    if (drag.height > 0) element.style.height = `${String(drag.height)}px`;
    element.style.transition = drag.dropping !== undefined ? "transform var(--duration-move) var(--ease-standard)" : "none";
  }, [drag.dropping, drag.x, drag.y, drag.width, drag.height]);
  useEffect(() => {
    if (sliding) return;
    const follow = (event: PointerEvent): void => {
      const element = ref.current;
      if (element !== null) element.style.transform = liftedTransform(event.clientX - drag.dx, event.clientY - drag.dy, false);
    };
    window.addEventListener("pointermove", follow);
    return () => window.removeEventListener("pointermove", follow);
  }, [sliding, drag.dx, drag.dy]);
  const face = faceOf(drag.item);
  if (face === null) return null;
  return (
    <div ref={ref} aria-hidden="true" className="pointer-events-none fixed top-0 left-0 z-overlay">
      {drag.kind === "add" ? (
        <div className="flex items-center gap-2.5 rounded-card border border-accent bg-surface-raised px-3 py-2.5 text-13 font-medium text-fg">
          <face.icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
          <span className="min-w-0 truncate">{face.title}</span>
        </div>
      ) : (
        <WidgetFrame face={face} size={drag.item.size} editing className="outline-2 outline-accent" />
      )}
    </div>
  );
}

/**
 * The size an edge or the corner that is held will give the widget if it is let go now: the outline of the room it
 * will take where it will be (it may be put in another place by what the others leave), and its name. The widget keeps
 * the size it has until then, so what is around it does not jump with every size it passes on its way.
 */
function ResizeOutline({ resize, layout, columns, gridRef }: { resize: ResizeState; layout: readonly Placed[]; columns: number; gridRef: RefObject<HTMLDivElement | null> }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const grid = gridRef.current;
    const element = ref.current;
    if (grid === null || element === null) return;
    const next = layout.map((placed) => (placed.id === resize.id ? { ...placed, size: resize.to } : placed));
    const slot = slotsOf(next, columns)[next.findIndex((placed) => placed.id === resize.id)];
    if (slot === undefined) return;
    const rect = localRect(slot, { left: 0, top: 0, width: grid.getBoundingClientRect().width, columns });
    element.style.left = `${String(rect.left)}px`;
    element.style.top = `${String(rect.top)}px`;
    element.style.width = `${String(rect.width)}px`;
    element.style.height = `${String(rect.height)}px`;
  }, [resize.id, resize.to, layout, columns, gridRef]);
  return <SizeOutline ref={ref} size={resize.to} />;
}

export interface WidgetGridProps {
  board: WidgetBoard;
  layout: readonly Placed[];
  onLayoutChange: (next: readonly Placed[]) => void;
  faceOf: FaceOf;
  /** The panel is being arranged: each widget draws its controls and can be dragged. */
  editing: boolean;
}

/** The panel: its widgets in their places, and while it is arranged, everything it takes to move, resize and remove them. */
export function WidgetGrid({ board, layout, onLayoutChange, faceOf, editing }: WidgetGridProps) {
  const t = useT();
  const helpId = useId();
  const { gridRef, drag, resize: resizing, startMove, startResize, remove, remember, focusLater, takeFocus, settling, retiring, columns } = board;
  const slots = slotsOf(layout, columns);
  // The layout as it was when the widget was lifted, which Escape puts back and the toast that follows the drop undoes.
  const [lifted, setLifted] = useState<{ id: string; before: readonly Placed[] } | null>(null);
  const layoutRef = useLatest(layout);

  useFlip(gridRef, `${columns}:${layout.map((item) => `${item.id}.${item.size}`).join(",")}`);

  useEffect(() => {
    // A long press on a finger is a lift, and the menu a browser opens on it would take the widget away again.
    const grid = gridRef.current;
    if (!editing || grid === null) return;
    const keepMenuAway = (event: Event): void => event.preventDefault();
    grid.addEventListener("contextmenu", keepMenuAway);
    return () => grid.removeEventListener("contextmenu", keepMenuAway);
  }, [editing, gridRef]);

  useEffect(() => {
    // The handle that keeps the keyboard after the widget moves in the document, after a neighbour goes, or after an undo.
    const id = takeFocus();
    if (id === null) return;
    const handle = [...(gridRef.current?.querySelectorAll<HTMLElement>("[data-handle]") ?? [])].find((element) => element.dataset["handle"] === id);
    if (handle !== undefined) handle.focus();
    else gridRef.current?.focus();
  });

  const titleOf = (item: Placed): string => faceOf(item)?.title ?? item.type;
  const sizeName = (size: SizeKey): string => t(`widgets.sizes.${size}`);

  const move = (item: Placed, by: number, undoable: boolean): void => {
    const before = layoutRef.current;
    const next = moveBy(before, item.id, by);
    const position = next.findIndex((placed) => placed.id === item.id);
    if (position === before.findIndex((placed) => placed.id === item.id)) return;
    focusLater(item.id);
    onLayoutChange(next);
    announce(t("widgets.grid.position", { title: titleOf(item), position: position + 1, count: next.length }));
    if (undoable) remember("moved", item, before);
  };

  const resize = (item: Placed, size: SizeKey, undoable: boolean): void => {
    if (size === item.size) return;
    const before = layoutRef.current;
    onLayoutChange(before.map((placed) => (placed.id === item.id ? { ...placed, size } : placed)));
    announce(t("widgets.grid.resized", { title: titleOf(item), size: sizeName(size).toLocaleLowerCase(t.locale) }));
    if (undoable) remember("resized", item, before);
  };

  const take = (item: Placed): void => {
    // Focus goes to the widget that comes to be where this one was, or to the one before it, never to nowhere.
    const index = layoutRef.current.findIndex((placed) => placed.id === item.id);
    focusLater((layoutRef.current[index + 1] ?? layoutRef.current[index - 1])?.id ?? null);
    remove(item.id);
  };

  /** One step wider, narrower, taller or shorter, or the word that there is none: the keyboard is told, never left waiting. */
  const step = (item: Placed, axis: Axis, direction: 1 | -1): void => {
    const to = stepSize(faceOf(item)?.sizes ?? [], item.size, axis, direction);
    if (to !== null) {
      resize(item, to, false);
      return;
    }
    const none = axis === "w" ? (direction === 1 ? "noWider" : "noNarrower") : direction === 1 ? "noTaller" : "noShorter";
    announce(t(`widgets.grid.${none}`, { title: titleOf(item) }));
  };

  const drop = (item: Placed, before: readonly Placed[]): void => {
    const now = layoutRef.current;
    if (sameLayout(before, now)) return;
    const was = before.find((placed) => placed.id === item.id);
    remember(was !== undefined && now.find((placed) => placed.id === item.id)?.size !== was.size ? "resized" : "moved", item, before);
  };

  const onHandleKey = (item: Placed, event: ReactKeyboardEvent<HTMLButtonElement>): void => {
    const index = layoutRef.current.findIndex((placed) => placed.id === item.id);
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (lifted?.id === item.id) {
        setLifted(null);
        announce(t("widgets.grid.dropped", { title: titleOf(item), position: index + 1 }));
        drop(item, lifted.before);
      } else {
        setLifted({ id: item.id, before: layoutRef.current });
        announce(t("widgets.grid.lifted", { title: titleOf(item) }));
      }
      return;
    }
    if (lifted?.id !== item.id) return;
    const horizontal = event.key === "ArrowLeft" || event.key === "ArrowRight";
    const vertical = event.key === "ArrowUp" || event.key === "ArrowDown";
    if ((horizontal || vertical) && event.shiftKey) {
      event.preventDefault();
      step(item, horizontal ? "w" : "h", event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      move(item, -1, false);
    } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      move(item, 1, false);
    } else if (event.key === "Escape") {
      event.preventDefault();
      focusLater(item.id);
      onLayoutChange(lifted.before);
      setLifted(null);
      announce(t("widgets.grid.cancelled", { title: titleOf(item) }));
    }
  };

  return (
    <div
      ref={gridRef}
      role="group"
      aria-label={t("widgets.grid.label")}
      tabIndex={-1}
      // Positioned, for the outline of a size that is being chosen, which is drawn inside it.
      className="relative grid min-w-0 outline-none"
      style={gridStyle(columns)}
      data-editing={editing ? "" : undefined}
      data-widget-grid=""
    >
      {editing ? (
        <p id={helpId} className="sr-only">
          {t("widgets.grid.moveHelp")}
        </p>
      ) : null}
      {layout.map((item, index) => {
        const face = faceOf(item);
        const slot = slots[index];
        if (face === null || slot === undefined) return null;
        const held = drag !== null && drag.item.id === item.id;
        const isLifted = lifted?.id === item.id;
        const title = face.title;
        const handles = editing && !held ? handlesFor(face.sizes, item.size) : [];
        const grips = onPhone(columns) ? handles.filter((handle) => handle === "corner") : handles;
        return (
          <div
            key={item.id}
            data-cell={item.id}
            style={cellStyle(slot)}
            className={cx("relative min-w-0", editing && "select-none", item.id === settling && "animate-settle", item.id === retiring && "animate-retire")}
            onPointerDown={
              editing && !held
                ? (event) => {
                    const target = event.target as HTMLElement;
                    // A finger holds the widget for a moment before it is lifted (the board waits), because until then it is scrolling the page.
                    if (target.closest("button, a, [role=menuitem], [data-resize], [data-resize-edge]") !== null) return;
                    startMove(item.id, event, event.currentTarget);
                  }
                : undefined
            }
          >
            {held ? <DropPlace size={item.size} /> : null}
            {/* Held, the widget is unseen and not gone: a finger sends its touch events to the element it pressed, and from one that has left the page they go nowhere, so the page would scroll under the hand. */}
            <div className={cx("h-full", held && "invisible")}>
              <WidgetFrame
                face={face}
                size={item.size}
                editing={editing}
                className={cx(editing && "cursor-grab", isLifted && "outline-2 outline-offset-2 outline-focus")}
                controls={
                  editing ? (
                    <div className="flex shrink-0 items-center gap-0.5">
                      <button
                        type="button"
                        data-handle={item.id}
                        aria-label={t("widgets.grid.move", { title })}
                        aria-pressed={isLifted}
                        aria-describedby={helpId}
                        onKeyDown={(event) => onHandleKey(item, event)}
                        onBlur={() => {
                          if (lifted?.id === item.id) setLifted(null);
                        }}
                        onPointerDown={(event) => startMove(item.id, event, event.currentTarget.closest<HTMLElement>("[data-cell]") ?? event.currentTarget, { grip: true })}
                        className="flex size-7 cursor-grab touch-none items-center justify-center rounded-control text-fg-muted hover:bg-bg-sunken hover:text-fg"
                      >
                        <GripVertical aria-hidden="true" className="size-icon-sm" />
                      </button>
                      <Menu
                        align="end"
                        trigger={
                          <button
                            type="button"
                            aria-label={t("widgets.grid.sizeButton", { title, size: sizeName(item.size) })}
                            className="flex h-7 min-w-7 cursor-pointer items-center justify-center rounded-control px-1 text-fg-muted hover:bg-bg-sunken hover:text-fg"
                          >
                            <SizeGlyph size={item.size} />
                          </button>
                        }
                      >
                        <MenuGroup label={t("widgets.grid.sizeGroup")}>
                          {SIZE_ORDER.filter((size) => face.sizes.includes(size)).map((size) => (
                            <MenuItem key={size} icon={<SizeGlyph size={size} className="text-fg" />} onClick={() => resize(item, size, true)}>
                              <span className="flex items-center justify-between gap-3">
                                {sizeName(size)}
                                {size === item.size ? (
                                  <>
                                    <Check aria-hidden="true" className="size-icon-sm text-fg" />
                                    <span className="sr-only">{t("widgets.grid.current")}</span>
                                  </>
                                ) : null}
                              </span>
                            </MenuItem>
                          ))}
                        </MenuGroup>
                        <MenuSeparator />
                        <MenuItem onClick={() => move(item, -1, true)} disabled={index === 0}>
                          {t("widgets.grid.earlier")}
                        </MenuItem>
                        <MenuItem onClick={() => move(item, 1, true)} disabled={index === layout.length - 1}>
                          {t("widgets.grid.later")}
                        </MenuItem>
                      </Menu>
                      <button
                        type="button"
                        aria-label={t("widgets.grid.remove", { title })}
                        onClick={() => take(item)}
                        className="flex size-7 cursor-pointer items-center justify-center rounded-control text-fg-muted hover:bg-bg-sunken hover:text-fail"
                      >
                        <X aria-hidden="true" className="size-icon-sm" />
                      </button>
                    </div>
                  ) : undefined
                }
              />
            </div>
            {grips.map((handle) => (
              <Grip key={handle} handle={handle} active={resizing?.id === item.id && resizing.handle === handle} onStart={(which, event) => startResize(item.id, which, event)} />
            ))}
          </div>
        );
      })}
      {resizing !== null ? <ResizeOutline resize={resizing} layout={layout} columns={columns} gridRef={gridRef} /> : null}
    </div>
  );
}
