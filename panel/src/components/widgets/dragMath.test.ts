import { describe, expect, it } from "vitest";

import { cellsAfterDrag, cellsOf, columnWidth, indexAt, nearGrid, pixelsOf, rectOf } from "./dragMath";
import type { Geometry } from "./dragMath";
import { GAP_PX, ROW_PX } from "./model";
import type { Placed, SizeKey } from "./model";

/** 12 columns of 60px and 11 gaps of 16: 896px wide, starting 100 from the left and 50 from the top. */
const DESKTOP: Geometry = { left: 100, top: 50, width: 12 * 60 + 11 * GAP_PX, columns: 12 };
const PHONE: Geometry = { left: 0, top: 0, width: 4 * 70 + 3 * GAP_PX, columns: 4 };

function item(id: string, size: SizeKey): Placed {
  return { id, type: id, size, settings: {} };
}

describe("where the grid's places are on screen", () => {
  it("measures a column without its gap", () => {
    expect(columnWidth(DESKTOP)).toBe(60);
    expect(columnWidth(PHONE)).toBe(70);
  });

  it("puts a place at its column and row, and gives it the room of its cells and the gaps between them", () => {
    expect(rectOf({ x: 0, y: 0, w: 3, h: 2 }, DESKTOP)).toEqual({ left: 100, top: 50, width: 3 * 60 + 2 * GAP_PX, height: 2 * ROW_PX + GAP_PX });
    expect(rectOf({ x: 6, y: 2, w: 6, h: 4 }, DESKTOP)).toEqual({
      left: 100 + 6 * (60 + GAP_PX),
      top: 50 + 2 * (ROW_PX + GAP_PX),
      width: 6 * 60 + 5 * GAP_PX,
      height: 4 * ROW_PX + 3 * GAP_PX,
    });
  });

  it("knows the room a size takes without placing it", () => {
    expect(pixelsOf({ w: 12, h: 4 }, DESKTOP)).toEqual({ width: 896, height: 4 * ROW_PX + 3 * GAP_PX });
  });
});

describe("indexAt", () => {
  const layout = [item("a", "medium"), item("b", "medium"), item("c", "wide")];
  // a: left half of the first row, b: right half of it, c: the whole second band.
  const a = rectOf({ x: 0, y: 0, w: 6, h: 2 }, DESKTOP);
  const b = rectOf({ x: 6, y: 0, w: 6, h: 2 }, DESKTOP);
  const c = rectOf({ x: 0, y: 2, w: 12, h: 2 }, DESKTOP);

  it("puts the dragged widget before a widget when the pointer is on its first half and after it on its second", () => {
    expect(indexAt(layout, "c", DESKTOP, a.left + 10, a.top + 10)).toBe(0);
    expect(indexAt(layout, "c", DESKTOP, a.left + a.width - 10, a.top + 10)).toBe(1);
    expect(indexAt(layout, "c", DESKTOP, b.left + 10, b.top + 10)).toBe(1);
    expect(indexAt(layout, "c", DESKTOP, b.left + b.width - 10, b.top + 10)).toBe(2);
  });

  it("splits a widget as wide as the grid into an upper and a lower half", () => {
    expect(indexAt(layout, "a", DESKTOP, c.left + 300, c.top + 10)).toBe(1);
    expect(indexAt(layout, "a", DESKTOP, c.left + 300, c.top + c.height - 10)).toBe(2);
  });

  it("asks for nothing over the widget being dragged itself, or in a gap", () => {
    expect(indexAt(layout, "a", DESKTOP, a.left + 10, a.top + 10)).toBeNull();
    expect(indexAt(layout, "c", DESKTOP, a.left + a.width + 4, a.top + 10)).toBeNull();
  });

  it("puts the widget last when the pointer is under the last of them, a little way down", () => {
    expect(indexAt(layout, "a", DESKTOP, c.left + 100, c.top + c.height + 40)).toBe(2);
    expect(indexAt(layout, "a", DESKTOP, c.left + 100, c.top + c.height + 400)).toBeNull();
    expect(indexAt(layout, "a", DESKTOP, DESKTOP.left - 30, c.top + c.height + 40)).toBeNull();
  });

  it("reads the places of the phone's grid when that is the grid", () => {
    const small = [item("a", "small"), item("b", "small"), item("c", "medium")];
    const right = rectOf({ x: 2, y: 0, w: 2, h: 2 }, PHONE);
    expect(indexAt(small, "c", PHONE, right.left + 10, right.top + 10)).toBe(1);
    expect(indexAt(small, "c", PHONE, right.left + right.width - 5, right.top + 10)).toBe(2);
  });
});

describe("nearGrid", () => {
  it("is where a widget dragged from the gallery starts to belong to the panel: the grid and a margin round it", () => {
    const bottom = 600;
    expect(nearGrid(DESKTOP, bottom, 300, 300)).toBe(true);
    expect(nearGrid(DESKTOP, bottom, DESKTOP.left - 20, 300)).toBe(true);
    expect(nearGrid(DESKTOP, bottom, 20, 300)).toBe(false);
    expect(nearGrid(DESKTOP, bottom, 300, bottom + 1000)).toBe(false);
  });
});

describe("cellsAfterDrag", () => {
  it("grows a widget by the cells the corner has travelled", () => {
    const start = cellsOf("small");
    const grown = cellsAfterDrag(start, DESKTOP, 2 * (60 + GAP_PX), ROW_PX + GAP_PX);
    expect(grown.w).toBeCloseTo(start.w + 2);
    expect(grown.h).toBeCloseTo(start.h + 1);
  });

  it("shrinks it when the corner goes back", () => {
    const grown = cellsAfterDrag(cellsOf("wide"), DESKTOP, -(60 + GAP_PX) * 3, 0);
    expect(grown.w).toBeCloseTo(9);
  });

  it("counts a phone's cell as two thirds of one of its columns", () => {
    const grown = cellsAfterDrag(cellsOf("small"), PHONE, 1.5 * (70 + GAP_PX), 0);
    expect(grown.w).toBeCloseTo(3 + 1.5 * 1.5);
  });
});
