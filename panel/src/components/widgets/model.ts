/**
 * What Home's grid holds and how it is placed, as pure functions so every rule is tested without a page.
 *
 * A panel is an ordered list of widgets, each with one of six sizes on a grid of 12 columns. The order is
 * the only thing a person arranges: where each widget sits is a consequence of it (`pack`), the way the
 * words of a paragraph fall into lines, so there are no gaps for a person to mend and the reading order
 * is the order the keyboard moves things in. The server keeps a panel as the document of
 * `noust.validators.dashboards` (version 1: every widget with its column, row, width and height), which is
 * what `toDocument` writes and `fromDocument` reads back into an order.
 */

/** The six shapes a widget may take, named by what they look like. */
export type SizeKey = "small" | "medium" | "large" | "tall" | "wide" | "xlarge";

export interface Size {
  /** Columns of the grid of 12. */
  w: number;
  /** Rows of the grid, each `ROW_PX` high. */
  h: number;
}

export const SIZES: Readonly<Record<SizeKey, Size>> = {
  small: { w: 3, h: 2 },
  medium: { w: 6, h: 2 },
  large: { w: 6, h: 4 },
  tall: { w: 3, h: 4 },
  wide: { w: 12, h: 2 },
  xlarge: { w: 12, h: 4 },
};

/** The order the sizes are offered in: by the room they take. */
export const SIZE_ORDER: readonly SizeKey[] = ["small", "medium", "large", "tall", "wide", "xlarge"];

/** Columns of the grid on a desktop and on a phone. */
export const COLUMNS = 12;
export const PHONE_COLUMNS = 4;

/** One row of the grid in pixels, and the gap between cells: a widget two rows high is two rows and a gap. */
export const ROW_PX = 80;
export const GAP_PX = 16;

/** The most widgets a panel holds; the server's own limit, read from `GET /api/auth/dashboards` when it is known. */
export const MAX_WIDGETS = 40;

/** What a widget keeps beside its place: a few scalars (the metric a chart draws). */
export type Settings = Readonly<Record<string, string | number | boolean>>;

/** One widget of a panel. `id` is unique inside the panel and is what the server keeps. */
export interface Placed {
  id: string;
  type: string;
  size: SizeKey;
  settings: Settings;
}

/** Where a widget sits on the grid, from 0. */
export interface Slot {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * How many columns and rows a size takes on a grid of `columns` columns. A phone's four columns have two
 * widths: a quarter-width widget (3 of 12) takes half the row, anything wider takes all of it.
 */
export function spansFor(size: SizeKey, columns: number): Size {
  const { w, h } = SIZES[size];
  if (columns === COLUMNS) return { w, h };
  return { w: w <= 3 ? Math.ceil(columns / 2) : columns, h };
}

/**
 * A size in the units a person counts it in: a width in quarters of the panel and a height in bands of two rows.
 * Every size is 1, 2 or 4 wide and 1 or 2 high ("2 × 1" is a medium one), so the label of a size that is being
 * dragged to says how much of the panel it will take without a word about columns.
 */
export function sizeUnits(size: SizeKey): Size {
  const { w, h } = SIZES[size];
  return { w: w / 3, h: h / 2 };
}

/** Which measure of a widget is changed: its width or its height. */
export type Axis = "w" | "h";

/**
 * The size one step along an axis from `size`, among the sizes the widget takes, with the other measure kept: the
 * next wider or the next taller (`direction` 1), the next narrower or shorter (`direction` -1). Null when there is
 * none, which is when the keyboard says that the widget cannot get any bigger or smaller that way.
 */
export function stepSize(allowed: readonly SizeKey[], size: SizeKey, axis: Axis, direction: 1 | -1): SizeKey | null {
  const other: Axis = axis === "w" ? "h" : "w";
  const here = SIZES[size];
  let best: SizeKey | null = null;
  for (const key of SIZE_ORDER) {
    if (!allowed.includes(key)) continue;
    const candidate = SIZES[key];
    if (candidate[other] !== here[other] || (candidate[axis] - here[axis]) * direction <= 0) continue;
    const better = best === null || (candidate[axis] - SIZES[best][axis]) * direction < 0;
    if (better) best = key;
  }
  return best;
}

/** The size that has exactly this width and height, or null when none has. */
export function sizeOf(w: number, h: number): SizeKey | null {
  return SIZE_ORDER.find((key) => SIZES[key].w === w && SIZES[key].h === h) ?? null;
}

/**
 * The size `allowed` has that is nearest to a width and a height in cells, which need not be whole: a
 * corner dragged to somewhere between two sizes lands on the closer. A row weighs double a column, since a
 * widget is easier to make a little wider than a little taller. Ties go to the smaller size.
 */
export function nearestSize(allowed: readonly SizeKey[], w: number, h: number): SizeKey {
  let best = allowed[0] ?? "medium";
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const key of SIZE_ORDER) {
    if (!allowed.includes(key)) continue;
    const size = SIZES[key];
    const distance = (size.w - w) ** 2 + ((size.h - h) * 2) ** 2;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = key;
    }
  }
  return best;
}

/**
 * Places the widgets, in order, each at the first place from the top left where it fits (the dense
 * packing CSS grids call `grid-auto-flow: dense`, done here so that what is drawn and what is stored
 * are one thing). A narrow widget after a wide one may fill a gap above it.
 */
export function pack(spans: readonly Size[], columns: number): Slot[] {
  const taken: boolean[][] = [];
  const free = (x: number, y: number, w: number, h: number): boolean => {
    for (let row = y; row < y + h; row += 1) {
      for (let column = x; column < x + w; column += 1) if (taken[row]?.[column] === true) return false;
    }
    return true;
  };
  return spans.map(({ w: wanted, h }) => {
    const w = Math.min(wanted, columns);
    for (let y = 0; ; y += 1) {
      for (let x = 0; x + w <= columns; x += 1) {
        if (!free(x, y, w, h)) continue;
        for (let row = y; row < y + h; row += 1) {
          const cells = taken[row] ?? (taken[row] = []);
          for (let column = x; column < x + w; column += 1) cells[column] = true;
        }
        return { x, y, w, h };
      }
    }
  });
}

/** The widgets' places for a layout, on a grid of `columns`. */
export function slotsOf(layout: readonly Placed[], columns: number): Slot[] {
  return pack(
    layout.map((item) => spansFor(item.size, columns)),
    columns,
  );
}

/** The layout with the widget `id` taken out and put back at `index` among the others (clamped). */
export function reorder(layout: readonly Placed[], id: string, index: number): Placed[] {
  const item = layout.find((placed) => placed.id === id);
  if (item === undefined) return [...layout];
  const rest = layout.filter((placed) => placed.id !== id);
  const at = Math.max(0, Math.min(index, rest.length));
  return [...rest.slice(0, at), item, ...rest.slice(at)];
}

/** The widget `id` moved `by` places along the order (negative is earlier); the same layout when it cannot. */
export function moveBy(layout: readonly Placed[], id: string, by: number): Placed[] {
  const from = layout.findIndex((placed) => placed.id === id);
  if (from < 0) return [...layout];
  return reorder(layout, id, Math.max(0, Math.min(layout.length - 1, from + by)));
}

/** A widget's id in a panel: its type, and `type-2`, `type-3` for a second and a third of the same. */
export function newId(type: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  if (!used.has(type)) return type;
  for (let n = 2; ; n += 1) {
    const candidate = `${type}-${String(n)}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** One widget of the stored document. */
export interface DocumentWidget {
  id: string;
  type: string;
  x: number;
  y: number;
  w: number;
  h: number;
  settings: Settings;
}

/** The document the server keeps for a panel: closed, version 1. */
export interface LayoutDocument {
  version: 1;
  widgets: DocumentWidget[];
}

/** The document of a layout, with each widget where `pack` puts it on the grid of 12. */
export function toDocument(layout: readonly Placed[]): LayoutDocument {
  const slots = slotsOf(layout, COLUMNS);
  return {
    version: 1,
    widgets: layout.map((item, index) => {
      const slot = slots[index] ?? { x: 0, y: 0, w: SIZES[item.size].w, h: SIZES[item.size].h };
      return { id: item.id, type: item.type, x: slot.x, y: slot.y, w: slot.w, h: slot.h, settings: { ...item.settings } };
    }),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function scalarSettings(value: unknown): Settings {
  if (!isRecord(value)) return {};
  const kept: Record<string, string | number | boolean> = {};
  for (const [name, setting] of Object.entries(value)) {
    if (typeof setting === "string" || typeof setting === "number" || typeof setting === "boolean") kept[name] = setting;
  }
  return kept;
}

/** What the console knows of a widget type, to read a document: the sizes it takes, or null when it does not exist here. */
export type KnownWidgets = (type: string) => readonly SizeKey[] | null;

/**
 * The layout a stored document holds. The server accepts any well-formed widget type, so what this
 * console does not know (a widget a newer console drew, or one this person's role may not see) is left out
 * here, and a size the widget does not take becomes the nearest it does. Widgets come back in the order
 * they sit on the grid, left to right and top to bottom. Anything that is not a version 1 document is no
 * layout at all (null), so a damaged copy never replaces the default.
 */
export function fromDocument(document: unknown, known: KnownWidgets): Placed[] | null {
  if (!isRecord(document) || document["version"] !== 1 || !Array.isArray(document["widgets"])) return null;
  const read: { placed: Placed; x: number; y: number }[] = [];
  const seen = new Set<string>();
  for (const raw of document["widgets"] as unknown[]) {
    if (!isRecord(raw)) continue;
    const id = raw["id"];
    const type = raw["type"];
    const x = integer(raw["x"]);
    const y = integer(raw["y"]);
    const w = integer(raw["w"]);
    const h = integer(raw["h"]);
    if (typeof id !== "string" || typeof type !== "string" || x === null || y === null || w === null || h === null || seen.has(id)) continue;
    const allowed = known(type);
    if (allowed === null || allowed.length === 0) continue;
    const exact = sizeOf(w, h);
    const size = exact !== null && allowed.includes(exact) ? exact : nearestSize(allowed, w, h);
    seen.add(id);
    read.push({ placed: { id, type, size, settings: scalarSettings(raw["settings"]) }, x, y });
  }
  return read.sort((a, b) => a.y - b.y || a.x - b.x).map((entry) => entry.placed);
}

/** Whether two layouts are the same widgets in the same order at the same sizes with the same settings. */
export function sameLayout(a: readonly Placed[], b: readonly Placed[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((item, index) => {
    const other = b[index];
    if (item.id !== other?.id || item.type !== other.type || item.size !== other.size) return false;
    const mine = Object.entries(item.settings);
    return mine.length === Object.keys(other.settings).length && mine.every(([name, value]) => other.settings[name] === value);
  });
}
