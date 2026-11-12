import { describe, expect, it } from "vitest";

import { EDGE_PX, SCROLL_PX_PER_S, cellsAfterDrag, cellsOf, columnWidth, edgeSpeed, handlesFor, indexAt, localRect, nearGrid, pixelsOf, rectOf, resizeTarget } from "./dragMath";
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

const ALL: SizeKey[] = ["small", "medium", "large", "tall", "wide", "xlarge"];
const COLUMN = 60 + GAP_PX;
const BAND = ROW_PX + GAP_PX;

describe("handlesFor", () => {
  it("gives an edge only where the widget takes another size along it, and the corner when it takes any other", () => {
    // A small one (3 x 2) can be wider (medium, wide) and taller (tall).
    expect(handlesFor(ALL, "small")).toEqual(["right", "bottom", "corner"]);
    // A medium one that only takes medium and large can be taller but never wider.
    expect(handlesFor(["medium", "large"], "medium")).toEqual(["bottom", "corner"]);
    // One that takes two sizes of the same height can be wider and not taller.
    expect(handlesFor(["small", "medium"], "small")).toEqual(["right", "corner"]);
  });

  it("gives nothing to a widget that takes one size only", () => {
    expect(handlesFor(["small"], "small")).toEqual([]);
  });

  it("gives the corner to a widget whose other sizes are neither wider nor taller along a straight line", () => {
    // medium (6 x 2) and tall (3 x 4): no size keeps the height or the width, so only the corner reaches the other.
    expect(handlesFor(["medium", "tall"], "medium")).toEqual(["corner"]);
  });
});

describe("resizeTarget", () => {
  it("snaps the right edge to the nearest width at the same height, whatever the pointer does vertically", () => {
    // From small, three columns more is a medium one even if the pointer has gone two rows down.
    expect(resizeTarget(ALL, "small", "right", DESKTOP, 3 * COLUMN, 2 * BAND)).toBe("medium");
    expect(resizeTarget(ALL, "small", "right", DESKTOP, 9 * COLUMN, 0)).toBe("wide");
  });

  it("snaps the bottom edge to the nearest height at the same width, whatever the pointer does sideways", () => {
    expect(resizeTarget(ALL, "medium", "bottom", DESKTOP, 6 * COLUMN, 2 * BAND)).toBe("large");
    expect(resizeTarget(ALL, "large", "bottom", DESKTOP, 0, -2 * BAND)).toBe("medium");
  });

  it("lets the corner go to any size, the nearest to where both measures have got to", () => {
    expect(resizeTarget(ALL, "small", "corner", DESKTOP, 3 * COLUMN, 2 * BAND)).toBe("large");
    expect(resizeTarget(ALL, "small", "corner", DESKTOP, 9 * COLUMN, 2 * BAND)).toBe("xlarge");
  });

  it("keeps the size when the edge has nothing to snap to", () => {
    expect(resizeTarget(["medium", "large"], "medium", "right", DESKTOP, 6 * COLUMN, 0)).toBe("medium");
  });

  it("is where it began for a drag that has not gone anywhere", () => {
    for (const handle of ["right", "bottom", "corner"] as const) expect(resizeTarget(ALL, "large", handle, DESKTOP, 0, 0)).toBe("large");
  });
});

describe("localRect", () => {
  it("places a slot inside the grid, from its own top left corner and not the window's", () => {
    expect(localRect({ x: 3, y: 1, w: 3, h: 2 }, DESKTOP)).toEqual({ left: 3 * COLUMN, top: BAND, width: 3 * 60 + 2 * GAP_PX, height: 2 * ROW_PX + GAP_PX });
  });
});

describe("edgeSpeed", () => {
  const HEIGHT = 900;

  it("does not scroll while the pointer is away from both edges", () => {
    expect(edgeSpeed(HEIGHT / 2, HEIGHT)).toBe(0);
    expect(edgeSpeed(HEIGHT - EDGE_PX, HEIGHT)).toBe(0);
    expect(edgeSpeed(EDGE_PX + 20, HEIGHT)).toBe(0);
  });

  it("scrolls down at the bottom and up at the top, signed so", () => {
    expect(edgeSpeed(HEIGHT - 10, HEIGHT)).toBeGreaterThan(0);
    expect(edgeSpeed(30, HEIGHT)).toBeLessThan(0);
  });

  it("goes faster the nearer the edge, and at the edge itself at the top speed", () => {
    const slow = edgeSpeed(HEIGHT - EDGE_PX + 5, HEIGHT);
    const quick = edgeSpeed(HEIGHT - 5, HEIGHT);
    expect(quick).toBeGreaterThan(slow);
    expect(edgeSpeed(HEIGHT, HEIGHT)).toBe(SCROLL_PX_PER_S);
    expect(edgeSpeed(HEIGHT + 400, HEIGHT)).toBe(SCROLL_PX_PER_S);
    expect(edgeSpeed(0, HEIGHT)).toBe(-SCROLL_PX_PER_S);
  });

  it("never crawls: the first pixel inside the zone already moves the page", () => {
    expect(edgeSpeed(HEIGHT - EDGE_PX + 1, HEIGHT)).toBeGreaterThan(SCROLL_PX_PER_S * 0.19);
  });
});
