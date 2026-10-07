/**
 * What a character is worth on a terminal screen: how many columns it takes, and what the
 * DEC line-drawing set turns a letter into.
 *
 * The widths follow Unicode's East Asian Width (wide and fullwidth are two columns) and
 * treat combining marks, joiners and variation selectors as zero, which is what a program
 * that lays out a table with `wcwidth` assumes. The ranges are the ones that matter in
 * practice (CJK, Hangul, fullwidth forms, the emoji blocks), not the whole database: a
 * character outside them is one column, as it is in every terminal that has no table of its
 * own either.
 */

/** Inclusive code point ranges that take two columns. */
const WIDE: readonly (readonly [number, number])[] = [
  [0x1100, 0x115f], // Hangul Jamo
  [0x231a, 0x231b], // watch, hourglass
  [0x2329, 0x232a],
  [0x23e9, 0x23ec],
  [0x23f0, 0x23f0],
  [0x23f3, 0x23f3],
  [0x25fd, 0x25fe],
  [0x2614, 0x2615],
  [0x2648, 0x2653],
  [0x267f, 0x267f],
  [0x2693, 0x2693],
  [0x26a1, 0x26a1],
  [0x26aa, 0x26ab],
  [0x26bd, 0x26be],
  [0x26c4, 0x26c5],
  [0x26ce, 0x26ce],
  [0x26d4, 0x26d4],
  [0x26ea, 0x26ea],
  [0x26f2, 0x26f3],
  [0x26f5, 0x26f5],
  [0x26fa, 0x26fa],
  [0x26fd, 0x26fd],
  [0x2705, 0x2705],
  [0x270a, 0x270b],
  [0x2728, 0x2728],
  [0x274c, 0x274c],
  [0x274e, 0x274e],
  [0x2753, 0x2755],
  [0x2757, 0x2757],
  [0x2795, 0x2797],
  [0x27b0, 0x27b0],
  [0x27bf, 0x27bf],
  [0x2b1b, 0x2b1c],
  [0x2b50, 0x2b50],
  [0x2b55, 0x2b55],
  [0x2e80, 0x303e], // CJK radicals, Kangxi, CJK symbols
  [0x3041, 0x33ff], // Hiragana, Katakana, Bopomofo, CJK compatibility
  [0x3400, 0x4dbf], // CJK extension A
  [0x4e00, 0x9fff], // CJK unified ideographs
  [0xa000, 0xa4cf], // Yi
  [0xa960, 0xa97f], // Hangul Jamo extended A
  [0xac00, 0xd7a3], // Hangul syllables
  [0xf900, 0xfaff], // CJK compatibility ideographs
  [0xfe10, 0xfe19], // vertical forms
  [0xfe30, 0xfe6f], // CJK compatibility forms
  [0xff00, 0xff60], // fullwidth forms
  [0xffe0, 0xffe6],
  [0x16fe0, 0x16fe4],
  [0x17000, 0x18cd5], // Tangut
  [0x1b000, 0x1b2ff], // Kana supplement
  [0x1f004, 0x1f004],
  [0x1f0cf, 0x1f0cf],
  [0x1f18e, 0x1f18e],
  [0x1f191, 0x1f19a],
  [0x1f200, 0x1f320], // enclosed ideographic, then the pictographs
  [0x1f32d, 0x1f335],
  [0x1f337, 0x1f37c],
  [0x1f37e, 0x1f393],
  [0x1f3a0, 0x1f3ca],
  [0x1f3cf, 0x1f3d3],
  [0x1f3e0, 0x1f3f0],
  [0x1f3f4, 0x1f3f4],
  [0x1f3f8, 0x1f43e],
  [0x1f440, 0x1f440],
  [0x1f442, 0x1f4fc],
  [0x1f4ff, 0x1f53d],
  [0x1f54b, 0x1f54e],
  [0x1f550, 0x1f567],
  [0x1f57a, 0x1f57a],
  [0x1f595, 0x1f596],
  [0x1f5a4, 0x1f5a4],
  [0x1f5fb, 0x1f64f],
  [0x1f680, 0x1f6c5],
  [0x1f6cc, 0x1f6cc],
  [0x1f6d0, 0x1f6d2],
  [0x1f6d5, 0x1f6d7],
  [0x1f6dc, 0x1f6df],
  [0x1f6eb, 0x1f6ec],
  [0x1f6f4, 0x1f6fc],
  [0x1f7e0, 0x1f7eb],
  [0x1f7f0, 0x1f7f0],
  [0x1f90c, 0x1f93a],
  [0x1f93c, 0x1f945],
  [0x1f947, 0x1f9ff],
  [0x1fa70, 0x1fa7c],
  [0x1fa80, 0x1fa88],
  [0x1fa90, 0x1fabd],
  [0x1fabf, 0x1fac5],
  [0x1face, 0x1fadb],
  [0x1fae0, 0x1fae8],
  [0x1faf0, 0x1faf8],
  [0x20000, 0x2fffd], // CJK extensions B and on
  [0x30000, 0x3fffd],
];

/** Inclusive ranges that take no column of their own: they join the character before. */
const ZERO: readonly (readonly [number, number])[] = [
  [0x0300, 0x036f], // combining diacritical marks
  [0x0483, 0x0489],
  [0x0591, 0x05bd],
  [0x0610, 0x061a],
  [0x064b, 0x065f],
  [0x0670, 0x0670],
  [0x06d6, 0x06dc],
  [0x0900, 0x0902],
  [0x093c, 0x093c],
  [0x0e31, 0x0e31],
  [0x0e34, 0x0e3a],
  [0x0e47, 0x0e4e],
  [0x1ab0, 0x1aff],
  [0x1dc0, 0x1dff],
  [0x200b, 0x200f], // zero width space, joiners, direction marks
  [0x202a, 0x202e],
  [0x2060, 0x2064],
  [0x20d0, 0x20ff],
  [0xfe00, 0xfe0f], // variation selectors
  [0xfe20, 0xfe2f],
  [0xfeff, 0xfeff],
  [0xe0100, 0xe01ef],
];

function inRanges(code: number, ranges: readonly (readonly [number, number])[]): boolean {
  let low = 0;
  let high = ranges.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const range = ranges[middle];
    if (range === undefined) return false;
    if (code < range[0]) high = middle - 1;
    else if (code > range[1]) low = middle + 1;
    else return true;
  }
  return false;
}

/**
 * How many columns a code point takes.
 *
 * @returns 0 for a combining mark, joiner or variation selector; 2 for a wide or fullwidth
 *   character; 1 otherwise.
 */
export function charWidth(code: number): 0 | 1 | 2 {
  if (code < 0x300) return 1;
  if (inRanges(code, ZERO)) return 0;
  if (code >= 0x1100 && inRanges(code, WIDE)) return 2;
  return 1;
}

/**
 * DEC Special Graphics: what `ESC ( 0` turns the characters `_` to `~` into. Programs that
 * draw frames with the terminal's own line set (`dialog`, `mc`, `htop`, `ncurses` in general)
 * send these letters instead of the box-drawing characters themselves.
 */
const DEC_SPECIAL: Readonly<Record<string, string>> = {
  _: " ",
  "`": "◆",
  a: "▒",
  b: "␉",
  c: "␌",
  d: "␍",
  e: "␊",
  f: "°",
  g: "±",
  h: "␤",
  i: "␋",
  j: "┘",
  k: "┐",
  l: "┌",
  m: "└",
  n: "┼",
  o: "⎺",
  p: "⎻",
  q: "─",
  r: "⎼",
  s: "⎽",
  t: "├",
  u: "┤",
  v: "┴",
  w: "┬",
  x: "│",
  y: "≤",
  z: "≥",
  "{": "π",
  "|": "≠",
  "}": "£",
  "~": "·",
};

/** The character set a G0 or G1 slot holds: ASCII (`B`) or the DEC line-drawing set (`0`). */
export type Charset = "B" | "0";

/** What a character of ASCII is under a character set. */
export function translate(char: string, set: Charset): string {
  if (set === "B") return char;
  return DEC_SPECIAL[char] ?? char;
}
