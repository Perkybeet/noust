import { describe, expect, it } from "vitest";

import iconMono from "../../assets/brand/noust-icon-mono.svg?raw";
import icon from "../../assets/brand/noust-icon.svg?raw";
import markMono from "../../assets/brand/noust-mark-mono.svg?raw";
import mark from "../../assets/brand/noust-mark.svg?raw";
import wordmarkMono from "../../assets/brand/noust-wordmark-mono.svg?raw";
import wordmark from "../../assets/brand/noust-wordmark.svg?raw";

/**
 * The owner asked for care with the symmetry of the mark: two stacks of stones, a hull between them, a
 * ground line under all three. This holds the files to it, in the drawing's own units and before any
 * screen is involved: every point a path passes through or steers by has its mirror about the mark's axis.
 * What the browser then does with the pixels is access-slab.spec.ts's to check.
 */

type Point = [number, number];

/** Every endpoint and control point of the paths of a drawing, absolute (the brand files write no other). */
function pointsOf(d: string): Point[] {
  const found: Point[] = [];
  let x = 0;
  let y = 0;
  // A command letter and the numbers that follow it, up to the next letter.
  for (const [, command = "", args = ""] of d.matchAll(/([A-Za-z])([^A-Za-z]*)/g)) {
    const n = (args.match(/-?\d*\.?\d+(?:e-?\d+)?/g) ?? []).map(Number);
    const take = (count: number, each: (values: number[]) => void): void => {
      for (let i = 0; i + count <= n.length; i += count) each(n.slice(i, i + count));
    };
    if (command === "M" || command === "L") {
      take(2, ([nx = 0, ny = 0]) => {
        [x, y] = [nx, ny];
        found.push([x, y]);
      });
    } else if (command === "H") {
      take(1, ([nx = 0]) => {
        x = nx;
        found.push([x, y]);
      });
    } else if (command === "V") {
      take(1, ([ny = 0]) => {
        y = ny;
        found.push([x, y]);
      });
    } else if (command === "C") {
      take(6, ([x1 = 0, y1 = 0, x2 = 0, y2 = 0, nx = 0, ny = 0]) => {
        found.push([x1, y1], [x2, y2], [nx, ny]);
        [x, y] = [nx, ny];
      });
    } else if (command === "A") {
      // rx ry rotation large-arc sweep x y: the arc's own shape is not a point; its end is.
      take(7, ([, , , , , nx = 0, ny = 0]) => {
        found.push([nx, ny]);
        [x, y] = [nx, ny];
      });
    } else if (command !== "Z" && command !== "z") {
      throw new Error(`the brand files use no "${command}" command: teach pointsOf before drawing with it`);
    }
  }
  return found;
}

/** The `d` of each path of the first `count` paths of a file (the mark, in a wordmark that goes on to spell the name). */
function paths(source: string, count = Infinity): string[] {
  return [...source.matchAll(/<path\b[^>]*?\bd="([^"]+)"/g)].slice(0, count).map((match) => match[1] ?? "");
}

function mirrorMissing(all: Point[], axis: number): Point[] {
  const key = ([x, y]: Point): string => `${x.toFixed(3)},${y.toFixed(3)}`;
  const have = new Set(all.map(key));
  return all.filter(([x, y]) => !have.has(key([2 * axis - x, y])));
}

const FILES: [string, string, number, number][] = [
  // [name, source, how many paths are the mark, the mark's axis]
  ["noust-mark", mark, 3, 70],
  ["noust-mark-mono", markMono, 3, 70],
  ["noust-wordmark", wordmark, 3, 70],
  ["noust-wordmark-mono", wordmarkMono, 3, 70],
  ["noust-icon", icon, 2, 16],
  ["noust-icon-mono", iconMono, 2, 16],
];

describe("the noust mark is mirror-symmetric as drawn", () => {
  it.each(FILES)("%s: every point has its mirror about the axis", (_name, source, count, axis) => {
    const all = paths(source, count).flatMap(pointsOf);
    expect(all.length).toBeGreaterThan(20);
    expect(mirrorMissing(all, axis)).toEqual([]);
  });

  it.each(FILES)("%s: the drawing is centred on its own canvas", (_name, source, _count, axis) => {
    const width = Number(/viewBox="0 0 (\d+) \d+"/.exec(source)?.[1]);
    // The wordmark is wider than its mark: the mark is centred on its own 140 units, which are the canvas's first.
    expect(axis).toBe(width === 254 ? 70 : width / 2);
  });

  it("the mono files draw the same mark as the coloured ones, to the unit", () => {
    expect(paths(markMono)).toEqual(paths(mark));
    expect(paths(wordmarkMono)).toEqual(paths(wordmark));
    expect(paths(iconMono)).toEqual(paths(icon));
  });

  it("knows what a symmetric drawing is: a point without its mirror is found", () => {
    // A stack nudged by a quarter of a unit is the kind of slip that reads as a crooked logo.
    const crooked = ["M8 19H36M12 11H32M16 3H28", "M104 19H132M108 11H128M112.25 3H124"].flatMap(pointsOf);
    expect(mirrorMissing(crooked, 70)).not.toEqual([]);
  });
});
