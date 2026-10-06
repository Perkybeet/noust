/**
 * The terminal's colours, which are the console's: tokens, read as concrete colours for the canvas.
 *
 * A program's sixteen colours are mapped the way the log viewer maps them (`lib/ansi.ts`), so the
 * same red is the same red in a build log and in a shell: red is the fail colour, green the ok
 * colour, yellow the warn colour, blue, magenta and cyan the three `--ansi-*` hues, white the
 * text. Backgrounds use the soft grounds, which keep a program's text readable on them. The
 * other 240 colours are fixed by xterm's definition (`lib/terminal/palette.ts`); a program that
 * names one by number or as true colour meant exactly that colour.
 */

import { tokenReader } from "../../../lib/tokenColours";

export interface TerminalTheme {
  /** The default text. */
  fg: string;
  /** The default ground. */
  bg: string;
  cursor: string;
  /** Text under a block cursor. */
  cursorText: string;
  selection: string;
  /** The first sixteen colours as text. */
  ansiFg: readonly string[];
  /** The first sixteen colours as a ground. */
  ansiBg: readonly string[];
}

/** Black, red, green, yellow, blue, magenta, cyan, white, then bright black and the brights. */
const FOREGROUND: readonly string[] = [
  "--text-faint",
  "--fail",
  "--ok",
  "--warn",
  "--ansi-blue",
  "--ansi-magenta",
  "--ansi-cyan",
  "--text",
  "--text-faint",
  "--fail",
  "--ok",
  "--warn",
  "--ansi-blue",
  "--ansi-magenta",
  "--ansi-cyan",
  "--text",
];

const BACKGROUND: readonly string[] = [
  "--surface-active",
  "--fail-soft",
  "--ok-soft",
  "--warn-soft",
  "--surface-active",
  "--surface-active",
  "--surface-active",
  "--surface-active",
  "--surface-active",
  "--fail-soft",
  "--ok-soft",
  "--warn-soft",
  "--surface-active",
  "--surface-active",
  "--surface-active",
  "--surface-active",
];

/** The tokens the theme reads, so a test can hold them to tokens.css. */
export const THEME_TOKENS: readonly string[] = [
  ...new Set([...FOREGROUND, ...BACKGROUND, "--bg-sunken", "--text", "--selection"]),
];

/** Reads the theme as it is at `host`: the colour scheme in force there decides every token. */
export function readTerminalTheme(host: HTMLElement): TerminalTheme {
  const reader = tokenReader(host);
  const theme: TerminalTheme = {
    fg: reader.read("--text"),
    bg: reader.read("--bg-sunken"),
    cursor: reader.read("--text"),
    cursorText: reader.read("--bg-sunken"),
    selection: reader.read("--selection"),
    ansiFg: FOREGROUND.map((token) => reader.read(token)),
    ansiBg: BACKGROUND.map((token) => reader.read(token)),
  };
  reader.release();
  return theme;
}
