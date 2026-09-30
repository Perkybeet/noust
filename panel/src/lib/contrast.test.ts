import { describe, expect, it } from "vitest";

import { contrastRatio, deltaE, hueDistance, oklab, oklch, parseHex, readThemeTokens } from "./contrast";

describe("contrast", () => {
  it("measures the extremes of the WCAG scale", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#777777")).toBe(1);
  });

  it("is symmetric", () => {
    expect(contrastRatio("#6a45d5", "#ffffff")).toBeCloseTo(contrastRatio("#ffffff", "#6a45d5"), 10);
  });

  it("matches a published reference pair", () => {
    // #767676 on white is the classic lightest grey that passes AA: 4.54:1.
    expect(contrastRatio("#767676", "#ffffff")).toBeCloseTo(4.54, 2);
  });

  it("parses short and long hex and rejects anything else", () => {
    expect(parseHex("#fff")).toEqual([255, 255, 255]);
    expect(parseHex("#1a2B3c")).toEqual([26, 43, 60]);
    expect(() => parseHex("rgb(0 0 0)")).toThrow("Not a hex colour");
  });

  it("reads light-dark pairs and plain hex from the :root block only", () => {
    const tokens = readThemeTokens(`
      :root {
        --bg: light-dark(#ffffff, #000000);
        --on: #ffffff;
        --shadow: 0 1px 2px rgb(0 0 0 / 0.1);
      }
      [data-theme="dark"] { --bg: #123456; }
    `);
    expect(tokens.light).toEqual({ bg: "#ffffff", on: "#ffffff" });
    expect(tokens.dark).toEqual({ bg: "#000000", on: "#ffffff" });
  });
});

describe("OKLab and colour-vision distance", () => {
  it("places white, black and a primary where Björn Ottosson's reference puts them", () => {
    const [wl, wa, wb] = oklab("#ffffff");
    expect(wl).toBeCloseTo(1, 3);
    expect(Math.abs(wa)).toBeLessThan(1e-3);
    expect(Math.abs(wb)).toBeLessThan(1e-3);
    expect(oklab("#000000")[0]).toBeCloseTo(0, 5);
    // sRGB red, from the reference table of the OKLab post.
    const [rl, ra, rb] = oklab("#ff0000");
    expect(rl).toBeCloseTo(0.628, 3);
    expect(ra).toBeCloseTo(0.2249, 3);
    expect(rb).toBeCloseTo(0.1258, 3);
  });

  it("reads OKLCH lightness, chroma and hue", () => {
    const [l, c, h] = oklch("#ff0000");
    expect(l).toBeCloseTo(0.628, 3);
    expect(c).toBeCloseTo(0.2577, 3);
    expect(h).toBeCloseTo(29.23, 1);
    expect(oklch("#777777")[1]).toBeLessThan(1e-3);
  });

  it("measures hue distance the short way round the circle", () => {
    expect(hueDistance(350, 10)).toBeCloseTo(20, 10);
    expect(hueDistance(10, 350)).toBeCloseTo(20, 10);
    expect(hueDistance(90, 270)).toBeCloseTo(180, 10);
  });

  it("gives zero distance for one colour and grows with difference", () => {
    expect(deltaE("#0274c7", "#0274c7")).toBe(0);
    expect(deltaE("#000000", "#ffffff")).toBeCloseTo(100, 0);
    expect(deltaE("#0274c7", "#9b2673")).toBeGreaterThan(deltaE("#0274c7", "#1f5fbf"));
  });

  it("collapses red and green for a deuteranope, as Machado et al. predict", () => {
    // Pure red and green are far apart to normal vision and close once simulated.
    const normal = deltaE("#c12c24", "#16784a");
    const deutan = deltaE("#c12c24", "#16784a", "deutan");
    expect(normal).toBeGreaterThan(20);
    expect(deutan).toBeLessThan(normal / 2);
  });

  it("matches the dataviz validator on the proposed series pair", () => {
    // design-system.md Annex B: 13.7 (deutan, light) and 25.0 (normal vision, light).
    const worst = Math.min(deltaE("#0274c7", "#9b2673", "protan"), deltaE("#0274c7", "#9b2673", "deutan"));
    expect(worst).toBeCloseTo(13.7, 0);
    expect(deltaE("#0274c7", "#9b2673")).toBeCloseTo(25.0, 0);
  });
});
