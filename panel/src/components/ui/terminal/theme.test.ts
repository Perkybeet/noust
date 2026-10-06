import { describe, expect, it } from "vitest";

import { contrastRatio, readThemeTokens } from "../../../lib/contrast";
import tokensCss from "../../../styles/tokens.css?raw";
import { THEME_TOKENS } from "./theme";

const TOKENS = readThemeTokens(tokensCss);
const THEMES = ["light", "dark"] as const;

function colour(theme: (typeof THEMES)[number], token: string): string {
  const value = TOKENS[theme][token.replace(/^--/, "")];
  if (value === undefined) throw new Error(`${token} is not a solid colour in the ${theme} theme`);
  return value;
}

describe("the terminal's colours", () => {
  it.each(THEMES)("are tokens that exist in the %s theme", (theme) => {
    for (const token of THEME_TOKENS) expect(() => colour(theme, token)).not.toThrow();
  });

  // What a program prints must be readable on the terminal's own ground, as the log viewer's is
  // (WCAG 1.4.3): the default text, the dimmed text, the three state colours and the three hues.
  it.each(THEMES)("keep every foreground readable on the ground in the %s theme", (theme) => {
    const ground = colour(theme, "--bg-sunken");
    for (const foreground of ["--text", "--text-faint", "--fail", "--ok", "--warn", "--ansi-blue", "--ansi-magenta", "--ansi-cyan"]) {
      expect(contrastRatio(colour(theme, foreground), ground), `${foreground} on the ground, ${theme}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(THEMES)("keep the default text readable on a program's backgrounds in the %s theme", (theme) => {
    for (const ground of ["--surface-active", "--fail-soft", "--ok-soft", "--warn-soft", "--selection"]) {
      expect(contrastRatio(colour(theme, "--text"), colour(theme, ground)), `text on ${ground}, ${theme}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(THEMES)("keep the character under a block cursor readable in the %s theme", (theme) => {
    // The cursor is the text colour, and the character under it takes the ground's.
    expect(contrastRatio(colour(theme, "--text"), colour(theme, "--bg-sunken"))).toBeGreaterThanOrEqual(4.5);
  });
});
