/**
 * What is selected on the screen, and the text of it.
 *
 * A point is a line and a column, the line counted from the first line the session ever
 * scrolled off (`Screen.trimmed` plus the line's index), so a selection stays on the same text
 * while output pushes it up the scrollback, and it is dropped quietly if that text is trimmed
 * away. The text is what a person would copy from a terminal: lines joined with a newline,
 * except where the program's own line was only wrapped by the width of the window, trailing
 * blanks cut.
 */

import { Attr } from "./screen";
import type { Row, Screen } from "./screen";

export interface Point {
  /** Counted from the first line of the whole session, which `Screen.trimmed` keeps stable. */
  line: number;
  col: number;
}

function before(a: Point, b: Point): boolean {
  return a.line < b.line || (a.line === b.line && a.col <= b.col);
}

/** A selection: where the press began, and where it is now. */
export class Selection {
  anchor: Point | null = null;
  focus: Point | null = null;

  /** Begins a selection at a point. */
  start(point: Point): void {
    this.anchor = point;
    this.focus = point;
  }

  /** Extends it to a point. */
  extend(point: Point): void {
    if (this.anchor !== null) this.focus = point;
  }

  /** Selects a span at once (a word, a line). */
  set(from: Point, to: Point): void {
    this.anchor = from;
    this.focus = to;
  }

  clear(): void {
    this.anchor = null;
    this.focus = null;
  }

  /** Whether nothing is selected: no press, or a press that did not move off its cell. */
  get empty(): boolean {
    return this.anchor === null || this.focus === null || (this.anchor.line === this.focus.line && this.anchor.col === this.focus.col);
  }

  /** The two ends in reading order, or null when there is no selection. */
  range(): [Point, Point] | null {
    if (this.anchor === null || this.focus === null || this.empty) return null;
    return before(this.anchor, this.focus) ? [this.anchor, this.focus] : [this.focus, this.anchor];
  }

  /** Whether a cell is inside the selection. `line` is absolute, as in `Point`. */
  contains(line: number, col: number): boolean {
    const range = this.range();
    if (range === null) return false;
    const [from, to] = range;
    if (line < from.line || line > to.line) return false;
    if (line === from.line && col < from.col) return false;
    if (line === to.line && col > to.col) return false;
    return true;
  }

  /** The selected text, as it would be copied from a terminal. */
  text(screen: Screen): string {
    const range = this.range();
    if (range === null) return "";
    const [from, to] = range;
    let out = "";
    for (let line = from.line; line <= to.line; line++) {
      const index = line - screen.trimmed;
      if (index < 0 || index >= screen.lineCount) continue;
      const row = screen.line(index);
      const first = line === from.line ? from.col : 0;
      const last = line === to.line ? to.col : row.cols - 1;
      let text = cells(row, first, last);
      // Blank cells at the end of a line are the screen's, not the program's.
      if (line !== to.line || last >= row.cols - 1) text = text.trimEnd();
      out += text;
      if (line !== to.line && !row.wrapped) out += "\n";
    }
    return out;
  }
}

function cells(row: Row, from: number, to: number): string {
  let out = "";
  for (let x = Math.max(from, 0); x <= to && x < row.cols; x++) {
    if (((row.attrs[x] ?? 0) & Attr.Continuation) !== 0) continue;
    out += row.chars[x] ?? " ";
  }
  return out;
}

const WORD = /[\p{L}\p{N}_./:@%+~#=?&-]/u;

/** The span of the word under a point (a double click): letters, digits and what makes a path or an address. */
export function wordAt(screen: Screen, point: Point): [Point, Point] {
  const index = point.line - screen.trimmed;
  if (index < 0 || index >= screen.lineCount) return [point, point];
  const row = screen.line(index);
  const isWord = (x: number): boolean => WORD.test(row.chars[x] ?? " ");
  if (!isWord(point.col)) return [point, point];
  let start = point.col;
  let end = point.col;
  while (start > 0 && isWord(start - 1)) start--;
  while (end < row.cols - 1 && isWord(end + 1)) end++;
  return [
    { line: point.line, col: start },
    { line: point.line, col: end },
  ];
}

/** The span of the whole line under a point (a triple click). */
export function lineAt(screen: Screen, point: Point): [Point, Point] {
  return [
    { line: point.line, col: 0 },
    { line: point.line, col: screen.cols - 1 },
  ];
}
