/**
 * The arithmetic of arranging a panel with a pointer, apart from the page so it can be tested: where a
 * place of the grid is on screen, which place of the order a pointer is asking for, and how many cells a
 * dragged corner has grown a widget to. Positions come from the layout (`slotsOf`), never from measuring
 * the cells, which are in the middle of sliding while a person drags and would answer where they were.
 */
import { GAP_PX, ROW_PX, SIZES, slotsOf } from "./model";
import type { Placed, Size, SizeKey, Slot } from "./model";

/** The grid as the browser laid it out: where it starts, how wide it is, and how many columns it has. */
export interface Geometry {
  left: number;
  top: number;
  width: number;
  columns: number;
}

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** One column's width, without its gap. */
export function columnWidth(geometry: Geometry): number {
  return (geometry.width - GAP_PX * (geometry.columns - 1)) / geometry.columns;
}

/** Where a place of the grid is on screen. */
export function rectOf(slot: Slot, geometry: Geometry): Rect {
  const column = columnWidth(geometry);
  return {
    left: geometry.left + slot.x * (column + GAP_PX),
    top: geometry.top + slot.y * (ROW_PX + GAP_PX),
    width: slot.w * column + (slot.w - 1) * GAP_PX,
    height: slot.h * ROW_PX + (slot.h - 1) * GAP_PX,
  };
}

/** The room a size takes on screen, for the copy that follows the pointer. */
export function pixelsOf(size: Size, geometry: Geometry): { width: number; height: number } {
  const { width, height } = rectOf({ x: 0, y: 0, w: size.w, h: size.h }, geometry);
  return { width, height };
}

/** How far down the grid a pointer is still asking to put something at the end of it, past its last row. */
const BELOW_PX = 160;

/**
 * Where among the others the widget `id` goes if it is dropped with the pointer at (x, y): before or
 * after the widget the pointer is over (the half of it nearer the start of the line, or, for a widget as
 * wide as the grid, the upper half), or at the end when the pointer is under the last of them. Null when
 * the pointer is not asking for anything, which leaves the order as it is.
 */
export function indexAt(layout: readonly Placed[], id: string, geometry: Geometry, x: number, y: number): number | null {
  const slots = slotsOf(layout, geometry.columns);
  const rects = layout.map((item, index) => ({ item, rect: rectOf(slots[index] ?? { x: 0, y: 0, w: 1, h: 1 }, geometry) }));
  const others = rects.filter((entry) => entry.item.id !== id);
  for (let index = 0; index < others.length; index += 1) {
    const entry = others[index];
    if (entry === undefined) continue;
    const { left, top, width, height } = entry.rect;
    if (x < left || x > left + width || y < top || y > top + height) continue;
    const wide = width > geometry.width * 0.75;
    const after = wide ? y > top + height / 2 : x > left + width / 2;
    return after ? index + 1 : index;
  }
  const bottom = rects.reduce((lowest, entry) => Math.max(lowest, entry.rect.top + entry.rect.height), geometry.top);
  if (x >= geometry.left && x <= geometry.left + geometry.width && y > bottom && y <= bottom + BELOW_PX) return others.length;
  return null;
}

/** Whether a pointer is over the grid or close to it, which is where a widget from the gallery starts to be part of it. */
export function nearGrid(geometry: Geometry, bottom: number, x: number, y: number): boolean {
  const margin = 24;
  return x >= geometry.left - margin && x <= geometry.left + geometry.width + margin && y >= geometry.top - margin && y <= bottom + BELOW_PX + margin;
}

/** The cells a corner dragged by (dx, dy) pixels has grown a widget of `start` to: not whole, so the nearest size can be asked for. */
export function cellsAfterDrag(start: { w: number; h: number }, geometry: Geometry, dx: number, dy: number): { w: number; h: number } {
  const column = columnWidth(geometry) + GAP_PX;
  // On a phone's four columns a quarter-width widget (3 cells of 12) takes two of them: a cell is two thirds of one.
  const perCell = geometry.columns === 12 ? column : (column * 2) / 3;
  return { w: start.w + dx / perCell, h: start.h + dy / (ROW_PX + GAP_PX) };
}

/** The width and height of a size on the grid of twelve: where a drag starts from. */
export function cellsOf(size: SizeKey): { w: number; h: number } {
  return { ...SIZES[size] };
}
