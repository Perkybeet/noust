/**
 * Home's grid: a CSS grid of 12 columns (4 on a phone) whose cells are the widgets, each placed through
 * the CSSOM (React's `style`, which the Content Security Policy allows) where `pack` says. Arranging is
 * three ways of one thing, and none needs the pointer (WCAG 2.5.7): a widget is dragged, or lifted with
 * Space from its handle and moved with the arrow keys, or moved and resized from the menu of its size.
 * Its corner drags its size to the nearest one it takes. Every change is announced, and a widget that
 * changes place slides from where it was (transform only, in the console's move duration; with reduced
 * motion the durations are zero and it is instant).
 */
import { Check, GripVertical, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, RefObject } from "react";

import { announce } from "../../app/Announcer";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Menu, MenuGroup, MenuItem, MenuSeparator } from "../ui/Menu";
import { REDUCED_MOTION } from "../ui/useMediaQuery";
import { SizeGlyph } from "./SizeGlyph";
import { WidgetFrame } from "./WidgetFrame";
import { cellsAfterDrag, cellsOf } from "./dragMath";
import type { Geometry } from "./dragMath";
import { GAP_PX, ROW_PX, SIZE_ORDER, moveBy, nearestSize, reorder, slotsOf } from "./model";
import type { Placed, SizeKey, Slot } from "./model";
import type { FaceOf } from "./types";
import { useLatest } from "./useLatest";
import type { DragState, WidgetBoard } from "./useWidgetBoard";

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

/** The floating copy of what is being dragged: following the pointer, then sliding into its place. */
export function DragGhost({ drag, faceOf }: { drag: DragState; faceOf: FaceOf }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    const target = drag.dropping ?? { x: drag.x, y: drag.y };
    element.style.transform = `translate(${String(target.x)}px, ${String(target.y)}px)${drag.dropping !== undefined ? "" : " rotate(0.6deg)"}`;
    element.style.width = `${String(drag.width)}px`;
    element.style.height = `${String(drag.height)}px`;
    element.style.transition = drag.dropping !== undefined ? "transform var(--duration-move) var(--ease-standard)" : "none";
  }, [drag]);
  const face = faceOf(drag.item);
  if (face === null) return null;
  return (
    <div ref={ref} aria-hidden="true" className="pointer-events-none fixed top-0 left-0 z-overlay">
      <WidgetFrame face={face} size={drag.item.size} editing />
    </div>
  );
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
  const { gridRef, drag, startMove, remove, settling, retiring, columns } = board;
  const slots = slotsOf(layout, columns);
  const [lifted, setLifted] = useState<{ id: string; from: number } | null>(null);
  const [resizing, setResizing] = useState<string | null>(null);
  // The handle that keeps the keyboard after the widget moves in the document, or after a neighbour goes.
  const focusHandle = useRef<string | null>(null);
  const layoutRef = useLatest(layout);

  useFlip(gridRef, `${columns}:${layout.map((item) => `${item.id}.${item.size}`).join(",")}`);

  useEffect(() => {
    const id = focusHandle.current;
    if (id === null) return;
    focusHandle.current = null;
    const handle = [...(gridRef.current?.querySelectorAll<HTMLElement>("[data-handle]") ?? [])].find((element) => element.dataset["handle"] === id);
    if (handle !== undefined) handle.focus();
    else gridRef.current?.focus();
  });

  const titleOf = (item: Placed): string => faceOf(item)?.title ?? item.type;
  const sizeName = (size: SizeKey): string => t(`widgets.sizes.${size}`);

  const move = (item: Placed, by: number): void => {
    const next = moveBy(layoutRef.current, item.id, by);
    const position = next.findIndex((placed) => placed.id === item.id);
    if (position === layoutRef.current.findIndex((placed) => placed.id === item.id)) return;
    focusHandle.current = item.id;
    onLayoutChange(next);
    announce(t("widgets.grid.position", { title: titleOf(item), position: position + 1, count: next.length }));
  };

  const resize = (item: Placed, size: SizeKey): void => {
    if (size === item.size) return;
    onLayoutChange(layoutRef.current.map((placed) => (placed.id === item.id ? { ...placed, size } : placed)));
    announce(t("widgets.grid.resized", { title: titleOf(item), size: sizeName(size).toLocaleLowerCase(t.locale) }));
  };

  const take = (item: Placed): void => {
    // Focus goes to the widget that comes to be where this one was, or to the one before it, never to nowhere.
    const index = layoutRef.current.findIndex((placed) => placed.id === item.id);
    focusHandle.current = (layoutRef.current[index + 1] ?? layoutRef.current[index - 1])?.id ?? null;
    remove(item.id);
  };

  const onHandleKey = (item: Placed, event: ReactKeyboardEvent<HTMLButtonElement>): void => {
    const index = layoutRef.current.findIndex((placed) => placed.id === item.id);
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (lifted?.id === item.id) {
        setLifted(null);
        announce(t("widgets.grid.dropped", { title: titleOf(item), position: index + 1 }));
      } else {
        setLifted({ id: item.id, from: index });
        announce(t("widgets.grid.lifted", { title: titleOf(item) }));
      }
      return;
    }
    if (lifted?.id !== item.id) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      move(item, -1);
    } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      move(item, 1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      focusHandle.current = item.id;
      onLayoutChange(reorder(layoutRef.current, item.id, lifted.from));
      setLifted(null);
      announce(t("widgets.grid.cancelled", { title: titleOf(item) }));
    }
  };

  const startResize = (item: Placed, event: ReactPointerEvent<HTMLElement>): void => {
    const grid = gridRef.current;
    const face = faceOf(item);
    if (grid === null || face === null || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const box = grid.getBoundingClientRect();
    const geometry: Geometry = { left: box.left, top: box.top, width: box.width, columns };
    const start = { x: event.clientX, y: event.clientY, cells: cellsOf(item.size) };
    let current = item.size;
    setResizing(item.id);
    document.documentElement.classList.add("widget-dragging");
    const onMove = (pointer: PointerEvent): void => {
      const cells = cellsAfterDrag(start.cells, geometry, pointer.clientX - start.x, pointer.clientY - start.y);
      const size = nearestSize(face.sizes, cells.w, cells.h);
      if (size === current) return;
      current = size;
      onLayoutChange(layoutRef.current.map((placed) => (placed.id === item.id ? { ...placed, size } : placed)));
    };
    const onUp = (): void => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.documentElement.classList.remove("widget-dragging");
      setResizing(null);
      if (current !== item.size) announce(t("widgets.grid.resized", { title: titleOf(item), size: sizeName(current).toLocaleLowerCase(t.locale) }));
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  return (
    <div
      ref={gridRef}
      role="group"
      aria-label={t("widgets.grid.label")}
      tabIndex={-1}
      className="grid min-w-0 outline-none"
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
        return (
          <div
            key={item.id}
            data-cell={item.id}
            style={cellStyle(slot)}
            className={cx("relative min-w-0", item.id === settling && "animate-settle", item.id === retiring && "animate-retire")}
            onPointerDown={
              editing && !held
                ? (event) => {
                    const target = event.target as HTMLElement;
                    // A finger starts a drag from the handle only: on the widget it is scrolling the page.
                    if (event.pointerType !== "mouse" || target.closest("button, a, [role=menuitem], [data-resize]") !== null) return;
                    startMove(item.id, event, event.currentTarget);
                  }
                : undefined
            }
          >
            {held ? (
              <div aria-hidden="true" className="elev-carved-fine h-full rounded-card bg-bg-sunken" />
            ) : (
              <WidgetFrame
                face={face}
                size={item.size}
                editing={editing}
                className={cx(editing && "cursor-grab", isLifted && "outline-2 outline-offset-2 outline-focus", resizing === item.id && "border-accent")}
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
                        onPointerDown={(event) => startMove(item.id, event, event.currentTarget.closest<HTMLElement>("[data-cell]") ?? event.currentTarget)}
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
                            <MenuItem key={size} icon={<SizeGlyph size={size} className="text-fg" />} onClick={() => resize(item, size)}>
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
                        <MenuItem onClick={() => move(item, -1)} disabled={index === 0}>
                          {t("widgets.grid.earlier")}
                        </MenuItem>
                        <MenuItem onClick={() => move(item, 1)} disabled={index === layout.length - 1}>
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
            )}
            {editing && !held && face.sizes.length > 1 ? (
              <div
                data-resize=""
                aria-hidden="true"
                onPointerDown={(event) => startResize(item, event)}
                className="absolute right-1 bottom-1 flex size-6 cursor-nwse-resize touch-none items-end justify-end rounded-chip p-1 text-fg-faint hover:text-fg"
              >
                <svg viewBox="0 0 10 10" className="size-2.5">
                  <path d="M9 1 1 9M9 5 5 9M9 8.5 8.5 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                </svg>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
