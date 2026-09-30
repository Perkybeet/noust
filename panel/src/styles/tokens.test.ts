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
  ["on-accent", "fail-strong"],
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
    for (const token of ["font-sans", "font-mono", "radius-control", "radius-card", "shadow-overlay", "duration-fast", "duration-base", "ease-out"]) {
      expect(tokensCss).toMatch(new RegExp(`--${token}:`));
    }
    expect(tokensCss).toMatch(/--radius-control: 6px;/);
    expect(tokensCss).toMatch(/--radius-card: 10px;/);
    expect(tokensCss).toMatch(/--duration-fast: 120ms;/);
    expect(tokensCss).toMatch(/--duration-base: 180ms;/);
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

  it.each(THEMES)("keeps every surface and text colour achromatic in %s", (theme) => {
    const achromatic = [
      "bg",
      "bg-sunken",
      "surface",
      "surface-raised",
      "surface-hover",
      "surface-active",
      "border",
      "border-strong",
      "text",
      "text-muted",
      "text-faint",
      "idle",
      "idle-soft",
      "idle-border",
    ];
    for (const token of achromatic) {
      const [r, g, b] = parseHex(colour(theme, token));
      expect([g, b], `--${token} must be grey`).toEqual([r, r]);
    }
  });

  it("gives the dark theme luminance elevation: each surface step is lighter", () => {
    const steps = ["bg-sunken", "bg", "surface", "surface-raised"].map((t) => parseHex(colour("dark", t))[0]);
    expect([...steps].sort((a, b) => a - b)).toEqual(steps);
    expect(new Set(steps).size).toBe(steps.length);
  });

  it("drops motion to zero for people who ask for reduced motion", () => {
    expect(tokensCss).toMatch(/prefers-reduced-motion: reduce[\s\S]*--duration-fast: 0ms;[\s\S]*--duration-base: 0ms;/);
  });

  it("gives the mono stack an emoji-capable fallback, so a build log's own glyphs render", () => {
    // Deploy and build logs are CLI output verbatim (icons like 📦 and 🔨 included, see
    // CLAUDE.md's "a system error is never paraphrased"); JetBrains Mono has none of them, so
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
    const mono = stack("font-mono");
    expect(sans.slice(0, 3)).toEqual(["Mona Sans Variable", "Mona Sans Fallback", "Mona Sans Fallback DejaVu"]);
    expect(mono.slice(0, 4)).toEqual([
      "JetBrains Mono Variable",
      "JetBrains Mono Fallback",
      "JetBrains Mono Fallback Consolas",
      "JetBrains Mono Fallback Liberation",
    ]);
    const faces = [...fontsCss.matchAll(/@font-face\s*{([^}]*)}/g)].map((match) => match[1] ?? "");
    for (const family of [...sans.slice(1, 3), ...mono.slice(1, 4)]) {
      const face = faces.find((body) => body.includes(`font-family: "${family}";`));
      expect(face, family).toBeDefined();
      expect(face).toMatch(/src: local\(/);
      expect(face).toMatch(/size-adjust: \d+(\.\d+)?%;/);
      expect(face).toMatch(/ascent-override: \d+(\.\d+)?%;/);
      expect(face).toMatch(/descent-override: \d+(\.\d+)?%;/);
    }
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
    const pairs = [...TEXT_PAIRS, ...UI_PAIRS, ...TINT_BORDER_PAIRS, ...HAIRLINE_PAIRS, ...VIZ.map((v): [string, string] => [v, "surface"])];
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

  it("has four radii: chip, control, card and pill", () => {
    expect(declared("radius-chip")).toBe("4px");
    expect(declared("radius-control")).toBe("6px");
    expect(declared("radius-card")).toBe("10px");
    expect(declared("radius-pill")).toBe("999px");
  });

  it("sizes controls from three heights that grow for a coarse pointer", () => {
    expect(declared("control-sm")).toBe("1.75rem");
    expect(declared("control-md")).toBe("2rem");
    expect(declared("control-lg")).toBe("2.5rem");
    expect(tokensCss).toMatch(
      /@media \(pointer: coarse\)\s*{\s*:root\s*{\s*--control-sm: 2.25rem;\s*--control-md: 2.5rem;\s*--control-lg: 2.75rem;/,
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
    expect(declared("width-auth")).toBe("25rem");
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
