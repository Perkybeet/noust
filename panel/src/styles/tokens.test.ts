import { describe, expect, it } from "vitest";

import { contrastRatio, deltaE, hueDistance, oklch, parseHex, readThemeTokens } from "../lib/contrast";
import appCss from "./app.css?raw";
import fontsCss from "./fonts.css?raw";
import tokensCss from "./tokens.css?raw";

const TOKENS = readThemeTokens(tokensCss);
const THEMES = ["light", "dark"] as const;

const GROUNDS = ["bg", "bg-sunken", "surface", "surface-raised", "surface-hover"];
const STATES = ["ok", "warn", "fail", "idle"];

/** Text and icons on the grounds they are drawn on: 4.5:1 (WCAG 1.4.3). */
const TEXT_PAIRS: [string, string][] = [
  ...["text", "text-muted", "text-faint"].flatMap((fg) => GROUNDS.map((bg): [string, string] => [fg, bg])),
  ["text", "surface-active"],
  ["text-muted", "surface-active"],
  ...["bg", "bg-sunken", "surface", "surface-raised", "accent-soft"].map((bg): [string, string] => ["accent-text", bg]),
  ["on-accent", "accent"],
  ["on-accent", "accent-hover"],
  ["on-fail", "fail-strong"],
  // The ground of the frame: the rail and the second column are stone of their own, and what is written on them
  // is text, muted text, the accent of the current item and, over a hovered item, the same.
  ...["bg-rail", "bg-column", "rail-hover"].flatMap((bg): [string, string][] => [
    ["text", bg],
    ["text-muted", bg],
  ]),
  ["accent-text", "bg-rail"],
  ["accent-text", "bg-column"],
  ["text-faint", "bg-column"],
  // A field's ground deepens under the pointer, and text is typed on it.
  ["text", "bg-sunken-deep"],
  ["text-muted", "bg-sunken-deep"],
  ["text-faint", "bg-sunken-deep"],
  ...STATES.flatMap((state) =>
    ["bg", "bg-sunken", "surface", "surface-raised", `${state}-soft`].map((bg): [string, string] => [state, bg]),
  ),
  // A Notice or a tinted block: its content in text and text-muted on the state's soft ground.
  ...["text", "text-muted"].flatMap((fg) =>
    ["ok-soft", "warn-soft", "fail-soft", "idle-soft"].map((bg): [string, string] => [fg, bg]),
  ),
  ["text", "match"],
  ["text", "selection"],
  // Tooltips are inverted: the page ground as text on the text colour.
  ["bg", "text"],
  ...["ansi-blue", "ansi-magenta", "ansi-cyan"].flatMap((fg) =>
    ["bg-sunken", "surface"].map((bg): [string, string] => [fg, bg]),
  ),
];

/** Boundaries and indicators of controls: 3:1 (WCAG 1.4.11). */
const UI_PAIRS: [string, string][] = [
  ...["border-strong", "focus", "accent"].flatMap((fg) =>
    ["bg", "bg-sunken", "surface", "surface-raised"].map((bg): [string, string] => [fg, bg]),
  ),
  ["on-accent", "border-strong"],
  ["warn", "warn-soft"],
  ["fail", "fail-soft"],
  ["text-muted", "surface-active"],
  // The fill of a meter that is not a problem, on its carved track, and the strokes of an illustration.
  ["il-stroke", "bg-sunken"],
  ["il-stroke", "surface"],
];

/**
 * The edges that give a height (docs/DESIGN.md 2.6): the joint under a raised piece, a seated panel and
 * a carved field has to be seen against the stone it sits on, and the light edge above it only has to
 * exist. The two edges of the accent and of the failure are the same, against their own fill.
 */
const JOINT_PAIRS: [string, string][] = ["bg", "surface", "surface-raised", "bg-sunken"].map((bg): [string, string] => ["joint", bg]);
const JOINT_MIN = 1.15;
const EDGE_PAIRS: [string, string][] = [
  ["accent-joint", "accent"],
  ["accent-edge", "accent"],
  ["fail-joint", "fail-strong"],
  ["fail-edge", "fail-strong"],
];
const EDGE_MIN = 1.3;
/**
 * What a field is found by (WCAG 1.4.11, docs/DESIGN.md A-10): a field has no line of its own, so the edge of its
 * hollow has 3:1 against what is around it. A hollow has two edges, the shadow along the top inside and the lit lip
 * below, and a theme can make only one of them strong: in light the shadow, in dark the lip, since nothing is darker
 * than the dark page by 3:1 (black on it is 1.3, on a raised surface 1.8). What surrounds a field is the page, a panel
 * (a card, a dialog) and a raised surface (a menu, a popover).
 */
const CARVED_SURROUNDINGS = ["bg", "surface", "surface-raised"];
const CARVED_PAIRS: [string, string][] = ["carved-shade", "carved-lip"].flatMap((edge) => CARVED_SURROUNDINGS.map((ground): [string, string] => [edge, ground]));
const CARVED_MIN = 3;
/** The edge each theme makes strong. */
const STRONG_EDGE = { light: "carved-shade", dark: "carved-lip" } as const;

const LIGHT_EDGE_PAIRS: [string, string][] = ["bg", "surface"].map((bg): [string, string] => ["edge", bg]);
const LIGHT_EDGE_MIN = 1.05;
/** The strokes of an illustration read against the page and against a panel; the second is the lighter one, for what is behind. */
const ILLUSTRATION_PAIRS: [string, string, number][] = [
  ["il-stroke", "bg", 3],
  ["il-stroke", "surface", 3],
  ["il-stroke-2", "bg", 1.5],
  ["il-stroke-2", "surface", 1.5],
];

/**
 * The border of a tinted block (a Notice, a failed job): decorative, since the block also
 * carries a glyph and words (WCAG 1.4.11 does not ask for 3:1), but it must be seen against
 * both the soft ground it bounds and the surface around it.
 */
const TINT_BORDER_PAIRS: [string, string][] = STATES.flatMap((state) =>
  [`${state}-soft`, "surface", "bg"].map((bg): [string, string] => [`${state}-border`, bg]),
);
const TINT_BORDER_MIN = 1.4;

/** Hairlines separate content: not a contrast requirement, but never invisible. */
const HAIRLINE_PAIRS: [string, string][] = ["bg", "bg-sunken", "surface", "surface-raised"].map((bg): [string, string] => [
  "border",
  bg,
]);
const HAIRLINE_MIN = 1.1;

/** The chart series family: identity of a series inside a plot, never state or action. */
const VIZ = ["viz-1", "viz-2"];
const VIZ_GROUNDS = ["bg", "bg-sunken", "surface", "surface-raised", "surface-hover"];

/**
 * Tokens that carry a colour but are not read as text, a control or a mark: translucent
 * overlays have no single ground to measure against.
 */
const UNPAIRED: readonly string[] = [];

function colour(theme: (typeof THEMES)[number], token: string): string {
  const value = TOKENS[theme][token];
  if (value === undefined) throw new Error(`--${token} is not a solid colour in the ${theme} theme`);
  return value;
}

describe("design tokens", () => {
  it("declares every token the pages rely on, for both themes", () => {
    const required = [
      "bg",
      "bg-sunken",
      "surface",
      "surface-raised",
      "border",
      "border-strong",
      "text",
      "text-muted",
      "text-faint",
      "accent",
      "accent-hover",
      "accent-soft",
      "accent-text",
      "bg-rail",
      "bg-column",
      "bg-sunken-deep",
      "edge",
      "joint",
      "ok",
      "ok-soft",
      "warn",
      "warn-soft",
      "fail",
      "fail-soft",
      "idle",
      "idle-soft",
      "focus",
    ];
    for (const theme of THEMES) {
      for (const token of required) expect(TOKENS[theme], `--${token} (${theme})`).toHaveProperty(token);
    }
    for (const token of [
      "font-sans",
      "font-display",
      "font-mono",
      "radius-control",
      "radius-card",
      "elev-carved",
      "elev-seated",
      "elev-raised",
      "duration-press",
      "duration-move",
      "duration-enter",
      "ease-standard",
    ]) {
      expect(tokensCss).toMatch(new RegExp(`--${token}:`));
    }
    expect(tokensCss).toMatch(/--radius-control: 9px;/);
    expect(tokensCss).toMatch(/--radius-card: 14px;/);
    expect(tokensCss).toMatch(/--duration-move: 200ms;/);
  });

  it.each(THEMES.flatMap((theme) => TEXT_PAIRS.map(([fg, bg]) => [fg, bg, theme] as const)))(
    "%s on %s meets 4.5:1 in %s",
    (fg, bg, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(THEMES.flatMap((theme) => UI_PAIRS.map(([fg, bg]) => [fg, bg, theme] as const)))(
    "%s against %s meets 3:1 in %s",
    (fg, bg, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(3);
    },
  );

  it.each(THEMES)("draws the icon of an item of the frame, which is a graphic and not text, at 3:1 on the bar, the column and a hovered item in %s", (theme) => {
    // The icons are `text-faint`, which is 4.5:1 on the page and on every panel; on the darkest stone of the bar it is
    // 4.4 in light, which is all that a graphic needs (WCAG 1.4.11) and more than the 3:1 it is held to here.
    for (const ground of ["bg-rail", "bg-column", "rail-hover"]) {
      expect(contrastRatio(colour(theme, "text-faint"), colour(theme, ground)), `text-faint on ${ground}`).toBeGreaterThanOrEqual(3);
    }
  });

  it.each(THEMES)("tells the current item of the frame by a piece of its own: its tone is not the bar's, the column's or a hovered item's in %s", (theme) => {
    // C-11: the current item is the raised piece (the sliding indicator draws it in `surface`, with its light edge and
    // its joint). What it is told apart from is the stone of the main bar, the second column and an item the pointer is
    // on; its label (`text`) and its icon (`accent-text`) are held on it at 4.5:1 by the text pairs above.
    for (const ground of ["bg-rail", "bg-column", "rail-hover"]) {
      expect(contrastRatio(colour(theme, "surface"), colour(theme, ground)), `surface against ${ground}`).toBeGreaterThan(1.1);
    }
    expect(colour(theme, "surface")).not.toBe(colour(theme, "surface-raised"));
  });

  it.each(THEMES)("keeps every surface, edge and text colour a stone in %s: grey with a trace of green, never a hue", (theme) => {
    // The page is not pure grey and not coloured: its chroma stays under 0.02 (OKLCH) and, where there is any, its
    // hue is the one of the stone (yellow-green to blue-green). Anything above that is a colour, and a colour on a
    // surface is a state, an action or a series, not a tone.
    const stones = [
      "bg-rail",
      "bg-column",
      "bg",
      "bg-sunken",
      "bg-sunken-deep",
      "surface",
      "surface-raised",
      "surface-hover",
      "surface-active",
      "rail-hover",
      "edge",
      "joint",
      "border",
      "border-strong",
      "text",
      "text-muted",
      "text-faint",
      "idle",
      "idle-soft",
      "idle-border",
      "il-stroke",
      "il-stroke-2",
    ];
    for (const token of stones) {
      const [, chroma, hue] = oklch(colour(theme, token));
      expect(chroma, `--${token} is too coloured to be a stone`).toBeLessThan(0.02);
      if (chroma > 0.004) {
        expect(hue, `--${token} is not the stone's hue`).toBeGreaterThanOrEqual(100);
        expect(hue, `--${token} is not the stone's hue`).toBeLessThanOrEqual(200);
      }
    }
  });

  it("gives the dark theme height by luminance: the rail is the darkest stone and each step up is lighter", () => {
    const steps = ["bg-rail", "bg-column", "bg", "bg-sunken", "surface", "surface-raised"].map((t) => parseHex(colour("dark", t))[0]);
    expect([...steps].sort((a, b) => a - b)).toEqual(steps);
    expect(new Set(steps).size).toBe(steps.length);
  });

  it("gives the light theme the same order the other way: the rail is the darkest stone and the raised piece the lightest", () => {
    const steps = ["bg-rail", "bg-sunken-deep", "bg-column", "bg-sunken", "bg", "surface", "surface-raised"].map((t) => parseHex(colour("light", t))[0]);
    expect([...steps].sort((a, b) => a - b)).toEqual(steps);
    expect(new Set(steps).size).toBe(steps.length);
  });

  it.each(THEMES.flatMap((theme) => JOINT_PAIRS.map(([fg, bg]) => [fg, bg, theme] as const)))(
    "the joint under a height (%s) is seen against %s in %s",
    (fg, bg, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(JOINT_MIN);
    },
  );

  it.each(THEMES.flatMap((theme) => EDGE_PAIRS.map(([fg, bg]) => [fg, bg, theme] as const)))(
    "the edge of the accent and of the failure (%s) is seen against %s in %s",
    (fg, bg, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(EDGE_MIN);
    },
  );

  it.each(THEMES.flatMap((theme) => CARVED_SURROUNDINGS.map((ground) => [theme, ground] as const)))(
    "a field is found by the strong edge of its hollow: 3:1 against %s's %s (A-10)",
    (theme, ground) => {
      const edge = STRONG_EDGE[theme];
      expect(contrastRatio(colour(theme, edge), colour(theme, ground)), `--${edge} on --${ground}`).toBeGreaterThanOrEqual(CARVED_MIN);
    },
  );

  it("builds the carved height from both edges: the shadow along the top inside and the lit lip below", () => {
    const carved = /--elev-carved:\s*([^;]+);/.exec(tokensCss)?.[1] ?? "";
    // The hollow has its shadow above, because the light comes from above: an inset layer pushed down from the top.
    expect(carved).toMatch(/inset 0 [1-9]\d*px \d+px var\(--carved-shade\)/);
    // And its lit lip below, outside the field.
    expect(carved).toMatch(/(?<!inset )0 [1-9]\d*px 0 var\(--carved-lip\)/);
    // The fine one, for what nobody presses (a track, a meter), keeps the soft joint: only a field needs the strong edge.
    expect(/--elev-carved-fine:\s*([^;]+);/.exec(tokensCss)?.[1]).toContain("var(--joint)");
  });

  it("explains why dark makes the lip strong and not the shadow: not even black is 3:1 below the dark page's tones", () => {
    for (const ground of CARVED_SURROUNDINGS) {
      expect(contrastRatio("#000000", colour("dark", ground)), `black on --${ground}`).toBeLessThan(CARVED_MIN);
    }
    // In light the lip is white, which cannot be 3:1 on the light page either: each theme has exactly one edge that can.
    for (const ground of CARVED_SURROUNDINGS) {
      expect(contrastRatio("#ffffff", colour("light", ground)), `white on --${ground}`).toBeLessThan(CARVED_MIN);
    }
    expect(contrastRatio(colour("dark", "carved-shade"), colour("dark", "surface-raised"))).toBeLessThan(CARVED_MIN);
    expect(contrastRatio(colour("light", "carved-lip"), colour("light", "bg"))).toBeLessThan(CARVED_MIN);
  });

  it.each(THEMES.flatMap((theme) => LIGHT_EDGE_PAIRS.map(([fg, bg]) => [fg, bg, theme] as const)))(
    "the light edge above a height (%s) exists against %s in %s",
    (fg, bg, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(LIGHT_EDGE_MIN);
    },
  );

  it.each(THEMES.flatMap((theme) => ILLUSTRATION_PAIRS.map(([fg, bg, min]) => [fg, bg, min, theme] as const)))(
    "the stroke of an illustration (%s) reads against %s at %s:1 in %s",
    (fg, bg, min, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(min);
    },
  );

  it("takes the white of a danger button's text from its own token, which holds on the failure fill in both themes", () => {
    for (const theme of THEMES) expect(contrastRatio(colour(theme, "on-fail"), colour(theme, "fail-strong"))).toBeGreaterThanOrEqual(4.5);
  });

  it("makes the height out of hard edges: no blurred shadow, nothing blurrier than 2px", () => {
    const heights = [...tokensCss.matchAll(/^\s*--elev-[a-z-]+:\s*([^;]+);/gm)].map((match) => match[1] ?? "");
    expect(heights.length).toBeGreaterThanOrEqual(10);
    for (const value of heights) {
      for (const layer of value.split(/,(?![^(]*\))/)) {
        const numbers = [...layer.replace(/var\([^)]*\)/g, "").matchAll(/(?<![\w.-])(-?\d+(?:\.\d+)?)(?:px)?(?![\w.%-])/g)].map((m) => Number(m[1]));
        const [, , blur] = numbers;
        expect(blur ?? 0, `a blurred layer in ${value}`).toBeLessThanOrEqual(2);
        expect(layer, `a translucent black in ${value}`).not.toMatch(/rgba?\(\s*0[ ,]+0[ ,]+0/);
      }
    }
  });

  it("drops motion to zero for people who ask for reduced motion", () => {
    for (const token of ["press", "fast", "move", "enter", "large"]) {
      expect(tokensCss, `--duration-${token}`).toMatch(new RegExp(`prefers-reduced-motion: reduce[\\s\\S]*--duration-${token}: 0ms;`));
    }
  });

  it("gives each curve its job: what enters decelerates, what leaves accelerates in less time, what moves goes by the standard curve", () => {
    expect(tokensCss).toMatch(/--ease-enter: cubic-bezier\(0, 0, 0\.2, 1\);/);
    expect(tokensCss).toMatch(/--ease-exit: cubic-bezier\(0\.4, 0, 1, 1\);/);
    expect(tokensCss).toMatch(/--ease-standard: cubic-bezier\(0\.4, 0, 0\.2, 1\);/);
    const ms = (token: string): number => Number(new RegExp(`--duration-${token}: (\\d+)ms;`).exec(tokensCss)?.[1]);
    // Frequent answers take about 100ms, what appears 200 to 300, the big ones up to 400; a way out is shorter than the way in.
    expect(ms("press")).toBe(100);
    expect(ms("fast")).toBeLessThan(ms("enter"));
    expect(ms("move")).toBeGreaterThanOrEqual(180);
    expect(ms("move")).toBeLessThanOrEqual(220);
    expect(ms("enter")).toBeGreaterThanOrEqual(200);
    expect(ms("enter")).toBeLessThanOrEqual(300);
    expect(ms("large")).toBeLessThanOrEqual(400);
  });

  it("gives the mono stack an emoji-capable fallback, so a build log's own glyphs render", () => {
    // Deploy and build logs are CLI output verbatim (icons like 📦 and 🔨 included, see
    // CONTRIBUTING.md's "a system error is never paraphrased"); JetBrains Mono has none of them, so
    // an emoji font has to follow it in the stack or they draw as tofu boxes.
    const match = /--font-mono:\s*([^;]+);/.exec(tokensCss);
    expect(match).not.toBeNull();
    const stack = match?.[1] ?? "";
    expect(stack).toContain('"JetBrains Mono Variable"');
    expect(stack).toMatch(/"Noto Color Emoji"|"Apple Color Emoji"|"Segoe UI Emoji"/);
  });

  it("follows each real font with its metric-matched fallbacks, each one declared and scaled", () => {
    // Text drawn before the fonts arrive takes the room it will keep, so the swap moves nothing.
    const stack = (token: string): string[] =>
      (new RegExp(`--${token}:\\s*([^;]+);`).exec(tokensCss)?.[1] ?? "").split(",").map((family) => family.trim().replace(/"/g, ""));
    const sans = stack("font-sans");
    const display = stack("font-display");
    const mono = stack("font-mono");
    expect(sans.slice(0, 3)).toEqual(["Atkinson Hyperlegible Next Variable", "Atkinson Hyperlegible Next Fallback", "Atkinson Hyperlegible Next Fallback DejaVu"]);
    expect(display.slice(0, 3)).toEqual(["Bricolage Grotesque Variable", "Bricolage Grotesque Fallback", "Bricolage Grotesque Fallback DejaVu"]);
    expect(mono.slice(0, 4)).toEqual([
      "JetBrains Mono Variable",
      "JetBrains Mono Fallback",
      "JetBrains Mono Fallback Consolas",
      "JetBrains Mono Fallback Liberation",
    ]);
    const faces = [...fontsCss.matchAll(/@font-face\s*{([^}]*)}/g)].map((match) => match[1] ?? "");
    for (const family of [...sans.slice(1, 3), ...display.slice(1, 3), ...mono.slice(1, 4)]) {
      const own = faces.filter((body) => body.includes(`font-family: "${family}";`));
      expect(own.length, family).toBeGreaterThan(0);
      for (const face of own) {
        expect(face).toMatch(/src: local\(/);
        expect(face).toMatch(/size-adjust: \d+(\.\d+)?%;/);
        expect(face).toMatch(/ascent-override: \d+(\.\d+)?%;/);
        expect(face).toMatch(/descent-override: \d+(\.\d+)?%;/);
      }
    }
    // The titles' fallback is told twice, for the narrow widths of a page title and for the rest.
    const bricolage = faces.filter((body) => body.includes('font-family: "Bricolage Grotesque Fallback";'));
    expect(bricolage.map((face) => /font-stretch: ([\d% ]+);/.exec(face)?.[1]).sort()).toEqual(["75% 84%", "85% 100%"]);
  });

  it("packages the two typefaces of the interface with their licences and never asks a CDN for them", () => {
    for (const family of ["Atkinson Hyperlegible Next Variable", "Bricolage Grotesque Variable"]) {
      const sources = [...fontsCss.matchAll(new RegExp(`font-family: "${family}";[^}]*?src: url\\("([^"]+)"\\) format\\("woff2-variations"\\)`, "g"))].map((m) => m[1] ?? "");
      expect(sources.length, family).toBeGreaterThan(0);
      for (const source of sources) expect(source).toMatch(/^\.\.\/assets\/fonts\/[a-z-]+\/[a-z-]+\.woff2$/);
    }
    expect(fontsCss).not.toMatch(/https?:\/\//);
    expect(fontsCss).not.toMatch(/url\(["']?data:/);
  });

  it.each(THEMES.flatMap((theme) => TINT_BORDER_PAIRS.map(([fg, bg]) => [fg, bg, theme] as const)))(
    "tinted border %s is visible against %s in %s",
    (fg, bg, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(TINT_BORDER_MIN);
    },
  );

  it.each(THEMES.flatMap((theme) => HAIRLINE_PAIRS.map(([fg, bg]) => [fg, bg, theme] as const)))(
    "hairline %s is visible against %s in %s",
    (fg, bg, theme) => {
      expect(contrastRatio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(HAIRLINE_MIN);
    },
  );
});

describe("chart series colours", () => {
  it.each(THEMES.flatMap((theme) => VIZ.flatMap((viz) => VIZ_GROUNDS.map((bg) => [viz, bg, theme] as const))))(
    "%s reads as a graphical mark on %s in %s (3:1, WCAG 1.4.11)",
    (viz, bg, theme) => {
      expect(contrastRatio(colour(theme, viz), colour(theme, bg))).toBeGreaterThanOrEqual(3);
    },
  );

  it.each(THEMES)("keeps the series apart for normal vision and for protanopia and deuteranopia in %s", (theme) => {
    const [a, b] = VIZ.map((token) => colour(theme, token)) as [string, string];
    expect(deltaE(a, b), "normal vision").toBeGreaterThanOrEqual(15);
    expect(deltaE(a, b, "protan"), "protanopia").toBeGreaterThanOrEqual(8);
    expect(deltaE(a, b, "deutan"), "deuteranopia").toBeGreaterThanOrEqual(8);
  });

  it.each(THEMES)("never looks like a state or the accent in %s: 35 degrees of hue from each", (theme) => {
    for (const viz of VIZ) {
      const hue = oklch(colour(theme, viz))[2];
      for (const other of ["accent", "ok", "warn", "fail"]) {
        const distance = hueDistance(hue, oklch(colour(theme, other))[2]);
        expect(distance, `--${viz} against --${other}`).toBeGreaterThanOrEqual(35);
      }
    }
  });

  it.each(THEMES)("sits in the lightness band and above the chroma floor of a series colour in %s", (theme) => {
    const [low, high] = theme === "light" ? [0.43, 0.77] : [0.48, 0.67];
    for (const viz of VIZ) {
      const [lightness, chroma] = oklch(colour(theme, viz));
      expect(lightness, `--${viz} lightness`).toBeGreaterThanOrEqual(low);
      expect(lightness, `--${viz} lightness`).toBeLessThanOrEqual(high);
      expect(chroma, `--${viz} chroma`).toBeGreaterThanOrEqual(0.1);
    }
  });
});

/** `--color-x: var(--y)` lines of the `@theme inline` block of app.css, as x -> y. */
function themeColours(css: string): Map<string, string> {
  const start = css.indexOf("@theme inline");
  const block = css.slice(start, css.indexOf("\n}", start));
  return new Map([...block.matchAll(/--color-([a-z0-9-]+):\s*var\(--([a-z0-9-]+)\)/g)].map((m) => [m[1] ?? "", m[2] ?? ""]));
}

describe("token parity", () => {
  const utilities = themeColours(appCss);
  const declared = new Set([...tokensCss.matchAll(/^\s*--([a-z0-9-]+):/gm)].map((m) => m[1] ?? ""));
  const colours = Object.keys(TOKENS.light);

  it("points every colour utility at a token tokens.css declares", () => {
    expect(utilities.size).toBeGreaterThan(30);
    for (const [utility, token] of utilities) expect(declared, `--color-${utility} -> --${token}`).toContain(token);
  });

  it("gives every colour token a utility, so no component reaches for var(--...) by hand", () => {
    const targets = new Set(utilities.values());
    for (const token of colours) expect(targets, `--${token} has no --color-* in app.css`).toContain(token);
  });

  it("verifies every colour token in at least one pair: a colour is born with its contrast checked", () => {
    const pairs = [
      ...TEXT_PAIRS,
      ...UI_PAIRS,
      ...TINT_BORDER_PAIRS,
      ...HAIRLINE_PAIRS,
      ...JOINT_PAIRS,
      ...EDGE_PAIRS,
      ...LIGHT_EDGE_PAIRS,
      ...CARVED_PAIRS,
      ...ILLUSTRATION_PAIRS.map(([fg, bg]): [string, string] => [fg, bg]),
      ...VIZ.map((v): [string, string] => [v, "surface"]),
    ];
    const paired = new Set(pairs.flat());
    for (const token of colours) {
      if (UNPAIRED.includes(token)) continue;
      expect(paired, `--${token} is in no verified pair`).toContain(token);
    }
  });
});

/** The value of a token as written in the :root block (the first declaration). */
function declared(token: string): string | undefined {
  return new RegExp(`--${token}:\\s*([^;]+);`).exec(tokensCss)?.[1]?.trim();
}

describe("token values the system is built on", () => {
  it("keeps the type scale to its seven sizes", () => {
    const sizes = [...tokensCss.matchAll(/--fs-(\d+):/g)].map((m) => Number(m[1]));
    expect(sizes).toEqual([12, 13, 14, 16, 18, 24, 32]);
  });

  it("has five radii by hierarchy: pebble, piece, slab, wall and pill", () => {
    expect(declared("radius-chip")).toBe("6px");
    expect(declared("radius-control")).toBe("9px");
    expect(declared("radius-card")).toBe("14px");
    expect(declared("radius-wall")).toBe("22px");
    expect(declared("radius-pill")).toBe("999px");
  });

  it("sizes controls from three heights that grow for a coarse pointer", () => {
    expect(declared("control-sm")).toBe("2rem");
    expect(declared("control-md")).toBe("2.25rem");
    expect(declared("control-lg")).toBe("2.75rem");
    expect(tokensCss).toMatch(
      /@media \(pointer: coarse\)\s*{\s*:root\s*{\s*--control-sm: 2.5rem;\s*--control-md: 2.75rem;\s*--control-lg: 3rem;/,
    );
  });

  it("names every stacking layer", () => {
    expect(declared("z-sticky")).toBe("30");
    expect(declared("z-backdrop")).toBe("40");
    expect(declared("z-overlay")).toBe("50");
    expect(declared("z-toast")).toBe("60");
    expect(declared("z-skip")).toBe("70");
  });

  it("names the reading measures and the width of every template, dialog and drawer", () => {
    expect(declared("measure")).toBe("68ch");
    expect(declared("measure-help")).toBe("52ch");
    expect(declared("width-page")).toBe("100rem");
    expect(declared("width-wizard")).toBe("46rem");
    expect(declared("width-settings-nav")).toBe("12.5rem");
    expect(declared("width-settings")).toBe("55rem");
    expect(declared("width-auth")).toBe("62rem");
    expect(declared("width-dialog-sm")).toBe("27.5rem");
    expect(declared("width-dialog-md")).toBe("35rem");
    expect(declared("width-dialog-lg")).toBe("45rem");
    expect(declared("width-dialog-xl")).toBe("80rem");
    expect(declared("width-drawer-md")).toBe("30rem");
    expect(declared("width-drawer-lg")).toBe("45rem");
    expect(declared("width-search")).toBe("18rem");
  });

  it("draws icons at five sizes and nothing between", () => {
    expect(declared("icon-xs")).toBe("0.75rem");
    expect(declared("icon-sm")).toBe("0.875rem");
    expect(declared("icon-md")).toBe("1rem");
    expect(declared("icon-lg")).toBe("1.25rem");
    expect(declared("icon-xl")).toBe("1.5rem");
  });

  it("exposes the named sizes to Tailwind, so no component writes a raw number", () => {
    for (const line of [
      "--radius-chip: var(--radius-chip);",
      "--spacing-control-sm: var(--control-sm);",
      "--spacing-control-md: var(--control-md);",
      "--spacing-control-lg: var(--control-lg);",
      "--spacing-icon-md: var(--icon-md);",
      "--container-measure: var(--measure);",
      "--container-page: var(--width-page);",
      "--container-dialog-sm: var(--width-dialog-sm);",
    ]) {
      expect(appCss).toContain(line);
    }
    for (const layer of ["sticky", "backdrop", "overlay", "toast", "skip"]) {
      expect(appCss).toMatch(new RegExp(`@utility z-${layer} {\\s*z-index: var\\(--z-${layer}\\);`));
    }
  });

  it("keeps the page from moving sideways when a scrollbar comes and goes", () => {
    expect(appCss).toMatch(/html\s*{[^}]*scrollbar-gutter: stable;/);
  });
});
