import { describe, expect, it } from "vitest";

import { COLUMNS, PHONE_COLUMNS, SIZE_ORDER, SIZES, fromDocument, moveBy, nearestSize, newId, pack, reorder, sameLayout, sizeOf, slotsOf, spansFor, toDocument } from "./model";
import type { Placed, SizeKey } from "./model";

function item(id: string, size: SizeKey, type = id): Placed {
  return { id, type, size, settings: {} };
}

/** A small deterministic generator, so a failing layout can be reproduced. */
function random(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state * 1_664_525 + 1_013_904_223) % 4_294_967_296;
    return state / 4_294_967_296;
  };
}

const EVERY: (type: string) => readonly SizeKey[] = () => SIZE_ORDER;

describe("the six sizes", () => {
  it("are all different, inside the grid, and found again by their own width and height", () => {
    for (const key of SIZE_ORDER) {
      const { w, h } = SIZES[key];
      expect(w).toBeLessThanOrEqual(COLUMNS);
      expect(sizeOf(w, h)).toBe(key);
    }
    expect(new Set(SIZE_ORDER.map((key) => `${String(SIZES[key].w)}x${String(SIZES[key].h)}`)).size).toBe(SIZE_ORDER.length);
    expect(sizeOf(5, 2)).toBeNull();
  });

  it("take, on a phone's four columns, half the row or all of it", () => {
    expect(spansFor("small", PHONE_COLUMNS)).toEqual({ w: 2, h: 2 });
    expect(spansFor("tall", PHONE_COLUMNS)).toEqual({ w: 2, h: 4 });
    expect(spansFor("medium", PHONE_COLUMNS)).toEqual({ w: 4, h: 2 });
    expect(spansFor("xlarge", PHONE_COLUMNS)).toEqual({ w: 4, h: 4 });
    expect(spansFor("large", COLUMNS)).toEqual({ w: 6, h: 4 });
  });
});

describe("nearestSize", () => {
  it("takes the allowed size closest to where a corner was dragged", () => {
    expect(nearestSize(["small", "medium", "wide"], 7, 2)).toBe("medium");
    expect(nearestSize(["small", "medium", "wide"], 11, 2)).toBe("wide");
    expect(nearestSize(["small", "medium", "large"], 6, 3.4)).toBe("large");
  });

  it("never answers a size the widget does not take", () => {
    expect(nearestSize(["small"], 12, 4)).toBe("small");
    expect(nearestSize(["medium", "wide"], 3, 2)).toBe("medium");
  });

  it("goes to the smaller size on a tie", () => {
    expect(nearestSize(["small", "medium"], 4.5, 2)).toBe("small");
  });
});

describe("pack", () => {
  it("lays widgets out left to right and then down", () => {
    const slots = slotsOf([item("a", "medium"), item("b", "medium"), item("c", "wide")], COLUMNS);
    expect(slots).toEqual([
      { x: 0, y: 0, w: 6, h: 2 },
      { x: 6, y: 0, w: 6, h: 2 },
      { x: 0, y: 2, w: 12, h: 2 },
    ]);
  });

  it("lets a narrow widget fill a gap a wide one left above it", () => {
    const slots = slotsOf([item("a", "large"), item("b", "wide"), item("c", "medium")], COLUMNS);
    expect(slots[1]).toEqual({ x: 0, y: 4, w: 12, h: 2 });
    expect(slots[2]).toEqual({ x: 6, y: 0, w: 6, h: 2 });
  });

  it("puts two quarter widgets side by side on a phone and a medium one under them", () => {
    const slots = slotsOf([item("a", "small"), item("b", "small"), item("c", "medium")], PHONE_COLUMNS);
    expect(slots).toEqual([
      { x: 0, y: 0, w: 2, h: 2 },
      { x: 2, y: 0, w: 2, h: 2 },
      { x: 0, y: 2, w: 4, h: 2 },
    ]);
  });

  it("never overlaps two widgets and never leaves the grid", () => {
    const next = random(7);
    for (let round = 0; round < 60; round += 1) {
      const layout = Array.from({ length: 1 + Math.floor(next() * 25) }, (_, index) => item(`w${String(index)}`, SIZE_ORDER[Math.floor(next() * SIZE_ORDER.length)] ?? "small"));
      for (const columns of [COLUMNS, PHONE_COLUMNS]) {
        const slots = slotsOf(layout, columns);
        const cells = new Set<string>();
        for (const slot of slots) {
          expect(slot.x + slot.w).toBeLessThanOrEqual(columns);
          for (let y = slot.y; y < slot.y + slot.h; y += 1) {
            for (let x = slot.x; x < slot.x + slot.w; x += 1) {
              const key = `${String(x)},${String(y)}`;
              expect(cells.has(key), `two widgets on ${key}`).toBe(false);
              cells.add(key);
            }
          }
        }
      }
    }
  });

  it("answers nothing for nothing", () => {
    expect(pack([], COLUMNS)).toEqual([]);
  });
});

describe("reorder and moveBy", () => {
  const layout = [item("a", "small"), item("b", "small"), item("c", "small")];

  it("puts a widget at a place among the others", () => {
    expect(reorder(layout, "a", 2).map((p) => p.id)).toEqual(["b", "c", "a"]);
    expect(reorder(layout, "c", 0).map((p) => p.id)).toEqual(["c", "a", "b"]);
  });

  it("clamps a place past either end", () => {
    expect(reorder(layout, "a", 99).map((p) => p.id)).toEqual(["b", "c", "a"]);
    expect(reorder(layout, "c", -4).map((p) => p.id)).toEqual(["c", "a", "b"]);
  });

  it("leaves the layout as it was for a widget it does not hold", () => {
    expect(reorder(layout, "z", 0).map((p) => p.id)).toEqual(["a", "b", "c"]);
    expect(moveBy(layout, "z", 1).map((p) => p.id)).toEqual(["a", "b", "c"]);
  });

  it("moves a widget a place earlier or later and stops at the ends", () => {
    expect(moveBy(layout, "b", 1).map((p) => p.id)).toEqual(["a", "c", "b"]);
    expect(moveBy(layout, "b", -1).map((p) => p.id)).toEqual(["b", "a", "c"]);
    expect(moveBy(layout, "a", -1).map((p) => p.id)).toEqual(["a", "b", "c"]);
    expect(moveBy(layout, "c", 1).map((p) => p.id)).toEqual(["a", "b", "c"]);
  });

  it("does not change the layout it is given", () => {
    const before = layout.map((p) => p.id);
    reorder(layout, "a", 2);
    moveBy(layout, "a", 1);
    expect(layout.map((p) => p.id)).toEqual(before);
  });
});

describe("newId", () => {
  it("is the type for the first widget of it and numbers the others", () => {
    expect(newId("apps", [])).toBe("apps");
    expect(newId("apps", ["apps"])).toBe("apps-2");
    expect(newId("apps", ["apps", "apps-2"])).toBe("apps-3");
    expect(newId("apps", ["apps-2"])).toBe("apps");
  });

  it("makes ids the server accepts: lower case letters, digits and hyphens, at most 40", () => {
    const id = newId("a-widget-type", ["a-widget-type", "a-widget-type-2"]);
    expect(id).toMatch(/^[a-z0-9][a-z0-9-]{0,39}$/);
  });
});

describe("the stored document", () => {
  it("is version 1 and gives every widget the place it is drawn at", () => {
    const layout = [item("attention", "wide"), item("apps", "medium"), item("work", "small")];
    const document = toDocument(layout);
    expect(document.version).toBe(1);
    expect(document.widgets).toEqual([
      { id: "attention", type: "attention", x: 0, y: 0, w: 12, h: 2, settings: {} },
      { id: "apps", type: "apps", x: 0, y: 2, w: 6, h: 2, settings: {} },
      { id: "work", type: "work", x: 6, y: 2, w: 3, h: 2, settings: {} },
    ]);
  });

  it("keeps a widget's settings and nothing the server would refuse", () => {
    const document = toDocument([{ id: "chart", type: "chart", size: "large", settings: { metric: "cpu", follow: true } }]);
    expect(document.widgets[0]?.settings).toEqual({ metric: "cpu", follow: true });
    for (const widget of document.widgets) {
      expect(Object.keys(widget).sort()).toEqual(["h", "id", "settings", "type", "w", "x", "y"]);
      expect(widget.x + widget.w).toBeLessThanOrEqual(COLUMNS);
    }
  });

  it("comes back drawn exactly where it was stored, for any layout", () => {
    const next = random(21);
    const where = (document: ReturnType<typeof toDocument>) => new Map(document.widgets.map((widget) => [widget.id, `${String(widget.x)},${String(widget.y)},${String(widget.w)},${String(widget.h)}`]));
    for (let round = 0; round < 80; round += 1) {
      const layout = Array.from({ length: Math.floor(next() * 30) }, (_, index) => item(`w${String(index)}`, SIZE_ORDER[Math.floor(next() * SIZE_ORDER.length)] ?? "small"));
      const back = fromDocument(toDocument(layout), EVERY);
      expect(back, `round ${String(round)}`).not.toBeNull();
      expect(back?.length).toBe(layout.length);
      // The order may be read again (a narrow widget that filled a gap above a wide one now comes before it), the picture may not.
      expect(where(toDocument(back ?? [])), `round ${String(round)}`).toEqual(where(toDocument(layout)));
    }
  });

  it("comes back in the same order when nothing filled a gap", () => {
    const layout = [item("a", "wide"), item("b", "medium"), item("c", "medium"), item("d", "large"), item("e", "large"), item("f", "xlarge")];
    expect(sameLayout(fromDocument(toDocument(layout), EVERY) ?? [], layout)).toBe(true);
  });

  it("leaves out a widget this console does not know and one the role may not see", () => {
    const document = toDocument([item("a", "small"), item("b", "small", "from-the-future"), item("c", "small")]);
    const kept = fromDocument(document, (type) => (type === "from-the-future" ? null : SIZE_ORDER));
    expect(kept?.map((p) => p.id)).toEqual(["a", "c"]);
  });

  it("puts a widget on the nearest size it takes when the document has one it does not", () => {
    const document = toDocument([item("a", "xlarge")]);
    expect(fromDocument(document, () => ["small", "medium"])?.[0]?.size).toBe("medium");
    expect(fromDocument({ version: 1, widgets: [{ id: "a", type: "a", x: 0, y: 0, w: 5, h: 3, settings: {} }] }, () => SIZE_ORDER)?.[0]?.size).toBe("medium");
  });

  it("reads the widgets in the order they sit, whatever order they were stored in", () => {
    const document = {
      version: 1,
      widgets: [
        { id: "c", type: "c", x: 0, y: 2, w: 12, h: 2, settings: {} },
        { id: "b", type: "b", x: 6, y: 0, w: 6, h: 2, settings: {} },
        { id: "a", type: "a", x: 0, y: 0, w: 6, h: 2, settings: {} },
      ],
    };
    expect(fromDocument(document, EVERY)?.map((p) => p.id)).toEqual(["a", "b", "c"]);
  });

  it("is no layout when it is not version 1, and skips a widget that is damaged", () => {
    expect(fromDocument(null, EVERY)).toBeNull();
    expect(fromDocument({ version: 2, widgets: [] }, EVERY)).toBeNull();
    expect(fromDocument({ version: 1 }, EVERY)).toBeNull();
    expect(fromDocument("text", EVERY)).toBeNull();
    const damaged = {
      version: 1,
      widgets: [
        { id: "ok", type: "ok", x: 0, y: 0, w: 3, h: 2, settings: {} },
        { id: "no-place", type: "x", w: 3, h: 2, settings: {} },
        { id: 7, type: "x", x: 0, y: 0, w: 3, h: 2, settings: {} },
        { id: "ok", type: "ok", x: 3, y: 0, w: 3, h: 2, settings: {} },
        "text",
        null,
      ],
    };
    expect(fromDocument(damaged, EVERY)?.map((p) => p.id)).toEqual(["ok"]);
  });

  it("keeps only scalar settings", () => {
    const document = { version: 1, widgets: [{ id: "a", type: "a", x: 0, y: 0, w: 3, h: 2, settings: { metric: "cpu", n: 3, on: false, nested: { x: 1 }, list: [1], nothing: null } }] };
    expect(fromDocument(document, EVERY)?.[0]?.settings).toEqual({ metric: "cpu", n: 3, on: false });
  });
});

describe("sameLayout", () => {
  it("tells a change of order, size or setting from no change", () => {
    const a = [item("a", "small"), item("b", "medium")];
    expect(sameLayout(a, [item("a", "small"), item("b", "medium")])).toBe(true);
    expect(sameLayout(a, [item("b", "medium"), item("a", "small")])).toBe(false);
    expect(sameLayout(a, [item("a", "medium"), item("b", "medium")])).toBe(false);
    expect(sameLayout(a, [item("a", "small")])).toBe(false);
    expect(sameLayout([{ ...item("a", "small"), settings: { m: "cpu" } }], [{ ...item("a", "small"), settings: { m: "memory" } }])).toBe(false);
  });
});
