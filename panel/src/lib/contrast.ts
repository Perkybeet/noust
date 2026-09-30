/**
 * WCAG 2.2 contrast arithmetic, OKLab distance (with colour-vision deficiency simulated) and a
 * reader for tokens.css.
 *
 * The tokens are the single source of every colour, so contrast and distinguishability are
 * verified on them rather than on rendered pages: a failing pair is caught before any
 * component uses it.
 */

export type Rgb = readonly [number, number, number];
export type Theme = "light" | "dark";
export type ThemeTokens = Record<Theme, Record<string, string>>;

/** Parses `#rgb` or `#rrggbb` into 0-255 channels. */
export function parseHex(hex: string): Rgb {
  const raw = hex.trim().replace(/^#/, "");
  const full = raw.length === 3 ? raw.replace(/./g, (c) => c + c) : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) {
    throw new Error(`Not a hex colour: ${hex}`);
  }
  return [0, 2, 4].map((i) => Number.parseInt(full.slice(i, i + 2), 16)) as unknown as Rgb;
}

/** Relative luminance as defined by WCAG 2.x. */
export function relativeLuminance([r, g, b]: Rgb): number {
  const lin = (c: number): number => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** Contrast ratio between two hex colours, from 1 to 21. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(parseHex(a));
  const lb = relativeLuminance(parseHex(b));
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** sRGB channel (0-255) to linear light (0-1). */
function linear(channel: number): number {
  const s = channel / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

type Vector = readonly [number, number, number];
type Matrix = readonly [Vector, Vector, Vector];

function apply(matrix: Matrix, [x, y, z]: Vector): Vector {
  return [
    matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
    matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
    matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z,
  ];
}

/** Linear sRGB to OKLab (Ottosson 2020): lightness 0-1 and the a, b opponent axes. */
function oklabFromLinear(rgb: Vector): Vector {
  const [l, m, s] = apply(
    [
      [0.4122214708, 0.5363325363, 0.0514459929],
      [0.2119034982, 0.6806995451, 0.1073969566],
      [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    rgb,
  ).map(Math.cbrt) as unknown as Vector;
  return apply(
    [
      [0.2104542553, 0.793617785, -0.0040720468],
      [1.9779984951, -2.428592205, 0.4505937099],
      [0.0259040371, 0.7827717662, -0.808675766],
    ],
    [l, m, s],
  );
}

/**
 * Colour-vision deficiency at full severity, Machado, Oliveira and Fernandes (2009), applied
 * to linear RGB. The same model the dataviz validator is calibrated on: the thresholds of the
 * token tests (a distance of 8 between series) only mean something with this simulation.
 */
const MACHADO: Record<"protan" | "deutan" | "tritan", Matrix> = {
  protan: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deutan: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritan: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
};

export type Deficiency = keyof typeof MACHADO;

function linearOf(hex: string, deficiency?: Deficiency): Vector {
  const rgb = parseHex(hex).map(linear) as unknown as Vector;
  if (deficiency === undefined) return rgb;
  const clamp = (c: number): number => Math.min(1, Math.max(0, c));
  return apply(MACHADO[deficiency], rgb).map(clamp) as unknown as Vector;
}

/** A hex colour in OKLab: `[L, a, b]`. */
export function oklab(hex: string): Vector {
  return oklabFromLinear(linearOf(hex));
}

/** A hex colour in OKLCH: `[L, C, h]`, hue in degrees 0-360. */
export function oklch(hex: string): Vector {
  const [l, a, b] = oklab(hex);
  const hue = ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360;
  return [l, Math.hypot(a, b), hue];
}

/** Degrees between two hues, the short way round: 0-180. */
export function hueDistance(a: number, b: number): number {
  const d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}

/**
 * Perceptual distance between two colours: Euclidean in OKLab, times 100, as seen with normal
 * vision or, given a deficiency, as someone with it sees them. The dataviz rule of thumb: 15
 * apart for normal vision, 8 for protanopia and deuteranopia.
 */
export function deltaE(a: string, b: string, deficiency?: Deficiency): number {
  const [l1, a1, b1] = oklabFromLinear(linearOf(a, deficiency));
  const [l2, a2, b2] = oklabFromLinear(linearOf(b, deficiency));
  return 100 * Math.hypot(l1 - l2, a1 - a2, b1 - b2);
}

const DECLARATION = /--([a-z0-9-]+)\s*:\s*([^;]+);/g;
const LIGHT_DARK = /^light-dark\(\s*(#[0-9a-fA-F]{3,6})\s*,\s*(#[0-9a-fA-F]{3,6})\s*\)$/;
const HEX = /^#[0-9a-fA-F]{3,6}$/;

/** The body of the first `:root { ... }` rule, braces balanced. */
function rootBlock(css: string): string {
  const open = css.indexOf("{", css.indexOf(":root"));
  if (open === -1) return "";
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  return css.slice(open + 1);
}

/**
 * Reads the solid colour tokens of the `:root` block of tokens.css for both themes.
 *
 * Only hex values are returned: `light-dark(a, b)` yields `a` for light and `b` for dark, a
 * bare hex applies to both. Translucent values (shadows, backdrop) are not text grounds and
 * are skipped.
 */
export function readThemeTokens(css: string): ThemeTokens {
  const root = rootBlock(css);
  const tokens: ThemeTokens = { light: {}, dark: {} };
  for (const match of root.matchAll(DECLARATION)) {
    const [, name, rawValue] = match;
    if (name === undefined || rawValue === undefined) continue;
    const value = rawValue.trim();
    const pair = LIGHT_DARK.exec(value);
    if (pair?.[1] !== undefined && pair[2] !== undefined) {
      tokens.light[name] = pair[1];
      tokens.dark[name] = pair[2];
    } else if (HEX.test(value)) {
      tokens.light[name] = value;
      tokens.dark[name] = value;
    }
  }
  return tokens;
}
