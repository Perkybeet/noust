import { describe, expect, it } from "vitest";

import appCss from "./app.css?raw";
import tokensCss from "./tokens.css?raw";

/**
 * The ground under a veil (docs/DESIGN.md, 2.2 "Veil"): while a dialog, a drawer or the bar opened over the
 * page is up, the page ground is `--bg-veiled`, the tone the veil gives it, so the room the scrollbar keeps
 * (which a veil fixed to the window does not reach) is not a light strip beside it. The two tokens are one
 * decision written twice, and the rule that applies it is one line of app.css.
 */

/** The `--backdrop` of each theme: the stone and how much of it. */
function veil(): { light: { rgb: string; share: number }; dark: { rgb: string; share: number } } {
  const match = /--backdrop:\s*light-dark\(\s*rgb\((\d+ \d+ \d+) \/ ([\d.]+)\),\s*rgb\((\d+ \d+ \d+) \/ ([\d.]+)\)\s*\);/.exec(tokensCss);
  if (match === null) throw new Error("--backdrop is not `light-dark(rgb(r g b / a), rgb(r g b / a))` in tokens.css");
  const [, lightRgb, lightAlpha, darkRgb, darkAlpha] = match;
  return {
    light: { rgb: lightRgb ?? "", share: Math.round(Number(lightAlpha) * 100) },
    dark: { rgb: darkRgb ?? "", share: Math.round(Number(darkAlpha) * 100) },
  };
}

describe("the ground under a veil", () => {
  it("is the veil's own stone and share mixed into the ground, in each theme", () => {
    const { light, dark } = veil();
    const token = /--bg-veiled:\s*light-dark\(\s*color-mix\(in srgb, rgb\(([\d ]+)\) (\d+)%, var\(--bg\)\),\s*color-mix\(in srgb, rgb\(([\d ]+)\) (\d+)%, var\(--bg\)\)\s*\);/.exec(tokensCss);
    expect(token, "--bg-veiled is not `light-dark(color-mix(in srgb, rgb(...) N%, var(--bg)), ...)`").not.toBeNull();
    expect([token?.[1], Number(token?.[2]), token?.[3], Number(token?.[4])]).toEqual([light.rgb, light.share, dark.rgb, dark.share]);
  });

  it("is what the page takes while a veil is in the page, and only then", () => {
    const rule = /html:has\(\.bg-backdrop:not\(\[data-ending-style\]\)\)\s*\{\s*background:\s*var\(--bg-veiled\);\s*\}/.exec(appCss);
    expect(rule, "app.css has no rule that gives the ground the veil's tone while a veil is up").not.toBeNull();
    // The veil, wherever it is drawn, is `bg-backdrop`: the kit's dialogs and drawers and the main bar opened over the page.
    expect(appCss).toContain("--color-backdrop: var(--backdrop);");
  });
});
