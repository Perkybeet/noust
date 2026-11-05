import { cx } from "../../lib/cx";

/**
 * A small, steady wobble between -0.5 and 0.5: the same drawing every time, never two stones alike.
 * Nothing here is random, so a snapshot, a screenshot and a second render all agree.
 */
function jitter(seed: number): number {
  const x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x) - 0.5;
}

/** The greys of the stone, as the tokens give them in each theme: a course is never one flat tone. */
const TONES = ["fill-surface", "fill-bg-column", "fill-surface-raised", "fill-bg", "fill-surface", "fill-bg-column"];

const PEN = "fill-none stroke-il-stroke [stroke-linecap:round] [stroke-linejoin:round]";
const PEN_LIGHT = "fill-none stroke-il-stroke-2 [stroke-linecap:round] [stroke-linejoin:round]";

/** A number as the picture draws it: one decimal is below what a pen can tell apart. */
const fix = (value: number): string => value.toFixed(1);

interface StoneProps {
  x: number;
  y: number;
  w: number;
  h: number;
  /** Which wobble this stone has: the same seed always draws the same stone. */
  seed: number;
}

/**
 * One flat stone of dry wall: an irregular hexagon in pen, with the lit lip along its top. The light
 * comes from above, so every stone catches it the same way and the wall reads as one.
 */
function Stone({ x, y, w, h, seed }: StoneProps) {
  const j = (n: number, scale = 2): number => jitter(seed * 7 + n) * scale;
  const corners: [number, number][] = [
    [x + j(1), y + j(2)],
    [x + w * 0.45 + j(3, 4), y - 1 + j(4)],
    [x + w + j(5), y + 0.8 + j(6)],
    [x + w + 1 + j(7), y + h + j(8)],
    [x + w * 0.55 + j(9, 4), y + h + 1 + j(10)],
    [x - 0.8 + j(11), y + h + j(12)],
  ];
  const [a, b, c] = corners as [[number, number], [number, number], [number, number]];
  const lip = `M${fix(a[0] + 2.5)} ${fix(a[1] + 2)} L${fix(b[0])} ${fix(b[1] + 2)} L${fix(c[0] - 2.5)} ${fix(c[1] + 2)}`;
  const tone = TONES[Math.floor(Math.abs(jitter(seed + 0.5)) * 2 * TONES.length) % TONES.length] ?? "fill-surface";
  return (
    <g>
      <polygon points={corners.map(([px, py]) => `${fix(px)},${fix(py)}`).join(" ")} className={cx(tone, "stroke-il-stroke")} strokeWidth={1.8} strokeLinejoin="round" />
      <path d={lip} className="fill-none stroke-edge" strokeWidth={1.6} strokeLinecap="round" />
    </g>
  );
}

function Grass({ x, y, seed }: { x: number; y: number; seed: number }) {
  const lean = jitter(seed) * 3;
  const d = `M${String(x)} ${String(y)} l${fix(-2.5 + lean)} -8 M${String(x + 3.5)} ${String(y)} l${fix(1 + lean)} -11 M${String(x + 7)} ${String(y)} l${fix(3.5 + lean)} -7`;
  return <path d={d} className={PEN_LIGHT} strokeWidth={1.6} />;
}

/** The far wall, course by course from the floor: the bank rises inland, so each course starts further in. */
const COURSES = [
  { y: 162, h: 20, from: 118 },
  { y: 143, h: 19, from: 146 },
  { y: 125, h: 18, from: 184 },
  { y: 108, h: 17, from: 228 },
  { y: 92, h: 16, from: 272 },
];

/** Where the far wall meets the end wall, which is taller. */
const END_WALL_X = 336;

/** The stones of the far wall, laid left to right with joints that do not line up from one course to the next. */
function farWall(): StoneProps[] {
  const stones: StoneProps[] = [];
  COURSES.forEach((course, row) => {
    let x = course.from + (row % 2) * 11;
    let n = 0;
    while (x < END_WALL_X - 14) {
      const length = Math.min(END_WALL_X - x, 34 + Math.abs(jitter(row * 31 + n)) * 40);
      stones.push({ x, y: course.y, w: length - 3, h: course.h - 2.5, seed: row * 17 + n });
      x += length;
      n += 1;
    }
  });
  return stones;
}

const END_WALL: Omit<StoneProps, "seed">[] = [
  { x: 338, y: 160, w: 40, h: 21 },
  { x: 340, y: 139, w: 36, h: 20 },
  { x: 337, y: 119, w: 41, h: 19 },
  { x: 341, y: 100, w: 35, h: 18 },
  { x: 338, y: 82, w: 39, h: 17 },
];

/** The low wall in front of the hull: the boat is inside the hollow, not beside a wall. */
const NEAR_WALL = [
  { x: 132, w: 44 },
  { x: 178, w: 52 },
  { x: 232, w: 38 },
  { x: 272, w: 48 },
  { x: 322, w: 30 },
];

const PEBBLES: [number, number, number][] = [
  [76, 194, 4],
  [88, 191, 3],
  [100, 188, 4.5],
  [111, 186, 3],
  [84, 199, 3],
];

const TUFTS: [number, number][] = [
  [126, 156],
  [168, 136],
  [214, 116],
  [262, 98],
  [356, 74],
  [382, 74],
];

export interface NoustSideProps {
  className?: string | undefined;
  /** Says what is drawn, when the page does not say it. Without it the drawing is decoration. */
  label?: string;
}

/**
 * A noust from the side, for the screens before the console: the hollow cut into the shore where a boat is
 * kept out of the weather. The sea, the shingle rising, the far wall laid in courses of flat stones that
 * climb to the end wall, the boat drawn up inside on its keel, two oars leaning on the end wall and grass
 * on the bank above. Drawn as by hand (each stone a little different, a pen's outline) in the greys of the
 * stone, with the hull in the accent as the one thing that is not grey.
 *
 * Every colour is a token, so one drawing serves both themes; it loads nothing and carries no style.
 */
export function NoustSide({ className, label }: NoustSideProps) {
  const a11y = label !== undefined ? ({ role: "img", "aria-label": label } as const) : ({ "aria-hidden": true } as const);
  return (
    <svg viewBox="0 0 400 230" focusable="false" xmlns="http://www.w3.org/2000/svg" className={cx("block", className)} {...a11y}>
      {/* The bank behind the wall, rising inland. */}
      <path d="M112 160 C170 132 250 96 336 80 C360 76 382 75 398 76" className={PEN} strokeWidth={2.2} />
      {/* The ground: the sea's edge, the shingle, the floor of the noust, and the water. */}
      <path d="M2 198 C24 197 46 198 66 197 C88 195 104 190 120 184 L380 182" className={PEN} strokeWidth={2.4} />
      <path d="M8 210 q9 -5 18 0 t18 0 t18 0 M30 222 q8 -4 16 0 t16 0 t16 0" className={PEN_LIGHT} strokeWidth={1.8} />
      {PEBBLES.map(([px, py, r]) => (
        <ellipse key={`${String(px)}-${String(py)}`} cx={px} cy={py} rx={r} ry={r * 0.62} className="fill-bg-column stroke-il-stroke-2" strokeWidth={1.3} />
      ))}
      {farWall().map((stone) => (
        <Stone key={`far-${String(stone.seed)}`} {...stone} />
      ))}
      {END_WALL.map((stone, i) => (
        <Stone key={`end-${String(i)}`} {...stone} seed={90 + i} />
      ))}
      {/* Two oars leaning on the end wall, blades up. */}
      <path d="M327 96 L306 180 M335 98 L317 180" className={PEN} strokeWidth={2.6} />
      <path d="M324 84 l8 2 l-3 15 l-8 -2 Z M332 86 l8 2 l-3 15 l-8 -2 Z" className="fill-bg-column stroke-il-stroke" strokeWidth={1.6} strokeLinejoin="round" />
      {/* The boat, drawn up on its keel: a double-ender, its planks overlapping. */}
      <path
        d="M146 124 C152 156 176 179 216 181 L262 181 C300 179 320 154 324 120 C302 135 268 143 236 143 C204 143 170 137 146 124 Z"
        className="fill-accent stroke-accent-joint"
        strokeWidth={2.2}
        strokeLinejoin="round"
      />
      <path d="M154 141 C188 158 284 158 318 139 M166 157 C200 170 272 170 306 156" className="fill-none stroke-accent-joint" strokeWidth={1.7} strokeLinecap="round" />
      <path d="M146 124 l-5 -10 M324 120 l5 -10" className="fill-none stroke-accent-joint" strokeWidth={3.2} strokeLinecap="round" />
      {NEAR_WALL.map((stone, i) => (
        <Stone key={`near-${String(i)}`} x={stone.x} y={166} w={stone.w - 3} h={16} seed={600 + i} />
      ))}
      {TUFTS.map(([gx, gy], i) => (
        <Grass key={`${String(gx)}-${String(gy)}`} x={gx} y={gy} seed={i + 1} />
      ))}
    </svg>
  );
}
