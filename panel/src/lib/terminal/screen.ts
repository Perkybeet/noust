/**
 * The screen of a terminal: a grid of cells, the cursor on it, the lines that scrolled off its
 * top, and everything the control sequences of xterm do to them.
 *
 * It knows nothing of bytes or of drawing. The parser (`parser.ts`) turns the program's output
 * into calls on this class; the renderer (`components/ui/terminal/renderer.ts`) reads the cells
 * back and says which rows changed. That split is what lets the sequences be tested as what
 * they do to a grid, in golden tests, and the drawing be tested as what it does to a canvas.
 *
 * A cell is a character (a string: a base character and the combining marks that joined it),
 * a foreground and a background colour, and attributes. Colours are one integer: `DEFAULT`
 * (-1) is the theme's, 0 to 255 an index of the 256-colour palette, and `TRUE_COLOUR | rgb`
 * a colour the program chose. A character two columns wide takes its first cell
 * (`Attr.Wide`) and leaves the second empty (`Attr.Continuation`).
 */

import { charWidth, translate } from "./charset";
import type { Charset } from "./charset";

/** The theme's own colour, for a foreground or a background. */
export const DEFAULT = -1;

/** Set on a colour that is a true colour: the low 24 bits are red, green and blue. */
export const TRUE_COLOUR = 0x1000000;

/** Cell attributes, one bit each. */
export const Attr = {
  Bold: 1,
  Dim: 2,
  Italic: 4,
  Underline: 8,
  Inverse: 16,
  Invisible: 32,
  Strike: 64,
  Blink: 128,
  /** The first cell of a character two columns wide. */
  Wide: 256,
  /** The second cell of a character two columns wide: it draws nothing of its own. */
  Continuation: 512,
} as const;

/** The most lines kept after they scroll off the top. */
export const DEFAULT_SCROLLBACK = 2_000;

/** The pen: what the next character written looks like. */
export interface Pen {
  fg: number;
  bg: number;
  attrs: number;
}

/** What the cursor looks like, which a program chooses with DECSCUSR. */
export type CursorShape = "block" | "underline" | "bar";

/** One line of cells. */
export class Row {
  chars: string[];
  fg: Int32Array;
  bg: Int32Array;
  attrs: Uint16Array;
  /** The line continued onto the next one by wrapping, not by a line feed. */
  wrapped = false;

  constructor(cols: number, bg: number = DEFAULT) {
    this.chars = new Array<string>(cols).fill(" ");
    this.fg = new Int32Array(cols).fill(DEFAULT);
    this.bg = new Int32Array(cols).fill(bg);
    this.attrs = new Uint16Array(cols);
  }

  get cols(): number {
    return this.chars.length;
  }

  /** Blanks cells `from` up to (not including) `to`, in the background the screen erases with. */
  erase(from: number, to: number, bg: number): void {
    const end = Math.min(to, this.cols);
    for (let x = Math.max(from, 0); x < end; x++) {
      this.chars[x] = " ";
      this.fg[x] = DEFAULT;
      this.bg[x] = bg;
      this.attrs[x] = 0;
    }
  }

  /** A copy that is `cols` wide: cut at the right, or padded with blanks. */
  resized(cols: number): Row {
    const next = new Row(cols);
    const keep = Math.min(cols, this.cols);
    for (let x = 0; x < keep; x++) {
      next.chars[x] = this.chars[x] ?? " ";
      next.fg[x] = this.fg[x] ?? DEFAULT;
      next.bg[x] = this.bg[x] ?? DEFAULT;
      next.attrs[x] = this.attrs[x] ?? 0;
    }
    // A wide character cut in two leaves its first half alone: that is a blank.
    const last = keep - 1;
    if (last >= 0 && ((next.attrs[last] ?? 0) & Attr.Wide) !== 0 && keep < this.cols) {
      next.erase(last, keep, DEFAULT);
    }
    next.wrapped = this.wrapped;
    return next;
  }

  /** The row's text: its cells' characters, wide characters once, trailing blanks cut. */
  text(): string {
    let out = "";
    for (let x = 0; x < this.cols; x++) {
      if (((this.attrs[x] ?? 0) & Attr.Continuation) !== 0) continue;
      out += this.chars[x] ?? " ";
    }
    return out.trimEnd();
  }
}

/** A buffer: the lines of one screen. The main screen alone keeps what scrolled off. */
interface Buffer {
  lines: Row[];
}

interface SavedCursor {
  x: number;
  y: number;
  pen: Pen;
  origin: boolean;
  pendingWrap: boolean;
  charsets: [Charset, Charset];
  shifted: boolean;
}

/** What a terminal tells whoever listens to it. */
export interface ScreenEvents {
  /** Bytes the terminal answers with, as if typed: the reply to a status or attribute query. */
  onResponse?: (data: string) => void;
  /** The title a program set. */
  onTitle?: (title: string) => void;
  /** The program rang the bell. */
  onBell?: () => void;
}

const MAX_TITLE = 200;

function clamp(value: number, low: number, high: number): number {
  return value < low ? low : value > high ? high : value;
}

/** The modes a program can switch, which change what keys send or what is drawn. */
export interface Modes {
  /** Wrap at the right edge (DECAWM, `?7`). */
  autoWrap: boolean;
  /** Insert instead of overwrite (IRM, `4`). */
  insert: boolean;
  /** Line feed also returns the carriage (LNM, `20`). */
  newline: boolean;
  /** Cursor addressing is relative to the scroll region (DECOM, `?6`). */
  origin: boolean;
  /** The cursor is drawn (DECTCEM, `?25`). */
  cursorVisible: boolean;
  /** Arrow keys send `ESC O A`, not `ESC [ A` (DECCKM, `?1`). */
  applicationCursor: boolean;
  /** The keypad sends application sequences (`ESC =`). */
  applicationKeypad: boolean;
  /** A paste is wrapped in markers so the shell knows it is one (`?2004`). */
  bracketedPaste: boolean;
  /** The whole screen is drawn with foreground and background swapped (DECSCNM, `?5`). */
  reverseVideo: boolean;
  /** The program asked for mouse reports (`?1000` and its relatives). Noust never sends them. */
  mouse: boolean;
}

export class Screen {
  cols: number;
  rows: number;
  readonly events: ScreenEvents;
  readonly maxScrollback: number;

  x = 0;
  y = 0;
  /** The cursor is at the right edge with a character written: the next one wraps first. */
  pendingWrap = false;
  readonly pen: Pen = { fg: DEFAULT, bg: DEFAULT, attrs: 0 };
  cursorShape: CursorShape = "block";
  title = "";
  readonly modes: Modes = {
    autoWrap: true,
    insert: false,
    newline: false,
    origin: false,
    cursorVisible: true,
    applicationCursor: false,
    applicationKeypad: false,
    bracketedPaste: false,
    reverseVideo: false,
    mouse: false,
  };

  scrollTop = 0;
  scrollBottom: number;
  /** Lines that scrolled off the top of the main screen, oldest first. */
  scrollback: Row[] = [];
  /** How many lines were dropped from the start of the scrollback: they keep numbering stable. */
  trimmed = 0;
  alt = false;

  /** Bumped by every change, so a renderer knows whether there is anything to draw. */
  version = 0;
  /** Rows changed since the renderer last cleared them; all of them when `allDirty`. */
  readonly dirty = new Set<number>();
  allDirty = true;

  private main: Buffer;
  private altBuffer: Buffer;
  private buffer: Buffer;
  private tabs: boolean[];
  private charsets: [Charset, Charset] = ["B", "B"];
  private shifted = false;
  private saved: SavedCursor | null = null;
  private altSaved: SavedCursor | null = null;

  constructor(cols = 80, rows = 24, events: ScreenEvents = {}, maxScrollback = DEFAULT_SCROLLBACK) {
    this.cols = Math.max(cols, 1);
    this.rows = Math.max(rows, 1);
    this.events = events;
    this.maxScrollback = maxScrollback;
    this.scrollBottom = this.rows - 1;
    this.main = { lines: this.blankLines() };
    this.altBuffer = { lines: this.blankLines() };
    this.buffer = this.main;
    this.tabs = this.defaultTabs();
  }

  // -- reading --------------------------------------------------------------------------

  /** The row on the screen (0 is the top of what is visible). */
  row(y: number): Row {
    return this.buffer.lines[y] ?? new Row(this.cols);
  }

  /** The row `index` counted from the oldest line of the scrollback, then the screen. */
  line(index: number): Row {
    const inScrollback = this.scrollback.length;
    return index < inScrollback ? (this.scrollback[index] ?? new Row(this.cols)) : this.row(index - inScrollback);
  }

  /** How many lines there are: the scrollback and the screen. */
  get lineCount(): number {
    return this.scrollback.length + this.rows;
  }

  /** The lines of the screen as text, top to bottom, trailing blanks cut. */
  textRows(): string[] {
    return this.buffer.lines.map((row) => row.text());
  }

  /** The whole scrollback and the screen as text, one line per row; blank lines at the foot are cut. */
  text(): string {
    const lines = [...this.scrollback.map((row) => row.text()), ...this.textRows()];
    while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
    return lines.join("\n");
  }

  // -- bookkeeping ----------------------------------------------------------------------

  private blankLines(): Row[] {
    return Array.from({ length: this.rows }, () => new Row(this.cols));
  }

  private defaultTabs(): boolean[] {
    return Array.from({ length: this.cols }, (_, x) => x > 0 && x % 8 === 0);
  }

  private touch(y: number): void {
    this.version++;
    if (!this.allDirty) this.dirty.add(y);
  }

  private touchAll(): void {
    this.version++;
    this.allDirty = true;
    this.dirty.clear();
  }

  /** Forgets what was dirty: the renderer has drawn it. */
  markDrawn(): void {
    this.allDirty = false;
    this.dirty.clear();
  }

  /** Marks the whole screen as needing a draw (the theme changed, the canvas was resized). */
  invalidate(): void {
    this.touchAll();
  }

  /** The erase background: what BCE blanks cells with. */
  private eraseBg(): number {
    return this.pen.bg;
  }

  // -- writing --------------------------------------------------------------------------

  /**
   * Writes printable text at the cursor.
   *
   * @param text Characters, already decoded: no control characters.
   */
  print(text: string): void {
    for (const char of text) {
      const code = char.codePointAt(0) ?? 0;
      const set = this.shifted ? this.charsets[1] : this.charsets[0];
      this.put(code < 0x80 ? translate(char, set) : char, code);
    }
  }

  private put(char: string, code: number): void {
    const width = charWidth(code);
    if (width === 0) {
      this.join(char);
      return;
    }
    if (this.pendingWrap && this.modes.autoWrap) this.wrapLine();
    if (width === 2 && this.x === this.cols - 1) {
      // A wide character does not fit in the last column: blank it and go on the next line.
      if (this.modes.autoWrap) {
        const row = this.row(this.y);
        row.erase(this.x, this.x + 1, this.eraseBg());
        this.wrapLine();
      } else {
        return;
      }
    }
    const row = this.row(this.y);
    if (this.modes.insert) this.shiftRight(row, this.x, width);
    this.clearWideOverlap(row, this.x, width);
    row.chars[this.x] = char;
    row.fg[this.x] = this.pen.fg;
    row.bg[this.x] = this.pen.bg;
    row.attrs[this.x] = this.pen.attrs | (width === 2 ? Attr.Wide : 0);
    if (width === 2) {
      row.chars[this.x + 1] = "";
      row.fg[this.x + 1] = this.pen.fg;
      row.bg[this.x + 1] = this.pen.bg;
      row.attrs[this.x + 1] = this.pen.attrs | Attr.Continuation;
    }
    this.touch(this.y);
    this.x += width;
    if (this.x >= this.cols) {
      this.x = this.cols - 1;
      this.pendingWrap = this.modes.autoWrap;
    }
  }

  /** A combining mark or joiner: it joins the character before the cursor. */
  private join(char: string): void {
    let x = this.pendingWrap ? this.x : this.x - 1;
    const row = this.row(this.y);
    if (x >= 0 && ((row.attrs[x] ?? 0) & Attr.Continuation) !== 0) x--;
    if (x < 0) return;
    row.chars[x] = (row.chars[x] ?? " ") + char;
    this.touch(this.y);
  }

  /** Overwriting half of a wide character blanks the other half. */
  private clearWideOverlap(row: Row, x: number, width: number): void {
    for (let at = x; at < x + width && at < this.cols; at++) {
      const attrs = row.attrs[at] ?? 0;
      if ((attrs & Attr.Continuation) !== 0 && at > 0) row.erase(at - 1, at, DEFAULT);
      if ((attrs & Attr.Wide) !== 0 && at + 1 < this.cols) row.erase(at + 1, at + 2, DEFAULT);
    }
  }

  private shiftRight(row: Row, x: number, by: number): void {
    for (let at = this.cols - 1; at >= x + by; at--) {
      row.chars[at] = row.chars[at - by] ?? " ";
      row.fg[at] = row.fg[at - by] ?? DEFAULT;
      row.bg[at] = row.bg[at - by] ?? DEFAULT;
      row.attrs[at] = row.attrs[at - by] ?? 0;
    }
  }

  private wrapLine(): void {
    this.row(this.y).wrapped = true;
    this.x = 0;
    this.pendingWrap = false;
    this.index();
  }

  // -- C0 controls ----------------------------------------------------------------------

  bell(): void {
    this.events.onBell?.();
  }

  backspace(): void {
    if (this.x > 0) this.x--;
    this.pendingWrap = false;
  }

  tab(count = 1): void {
    for (let i = 0; i < count; i++) {
      let next = this.x + 1;
      while (next < this.cols - 1 && this.tabs[next] !== true) next++;
      this.x = Math.min(next, this.cols - 1);
    }
    this.pendingWrap = false;
  }

  backTab(count = 1): void {
    for (let i = 0; i < count; i++) {
      let previous = this.x - 1;
      while (previous > 0 && this.tabs[previous] !== true) previous--;
      this.x = Math.max(previous, 0);
    }
    this.pendingWrap = false;
  }

  carriageReturn(): void {
    this.x = 0;
    this.pendingWrap = false;
  }

  /** LF, VT and FF. */
  lineFeed(): void {
    this.index();
    if (this.modes.newline) this.carriageReturn();
  }

  /** IND: down one line, scrolling at the bottom of the region. */
  index(): void {
    if (this.y === this.scrollBottom) this.scrollUp(1);
    else if (this.y < this.rows - 1) this.y++;
    this.pendingWrap = false;
  }

  /** RI: up one line, scrolling at the top of the region. */
  reverseIndex(): void {
    if (this.y === this.scrollTop) this.scrollDown(1);
    else if (this.y > 0) this.y--;
    this.pendingWrap = false;
  }

  /** NEL: the start of the next line. */
  nextLine(): void {
    this.index();
    this.carriageReturn();
  }

  shiftOut(): void {
    this.shifted = true;
  }

  shiftIn(): void {
    this.shifted = false;
  }

  designate(slot: 0 | 1, set: Charset): void {
    this.charsets[slot] = set;
  }

  // -- cursor ---------------------------------------------------------------------------

  /** CUP and friends: to a position on the screen, or in the region under DECOM. */
  moveTo(row: number, col: number): void {
    const top = this.modes.origin ? this.scrollTop : 0;
    const bottom = this.modes.origin ? this.scrollBottom : this.rows - 1;
    this.y = clamp(top + row, top, bottom);
    this.x = clamp(col, 0, this.cols - 1);
    this.pendingWrap = false;
  }

  moveBy(rows: number, cols: number): void {
    if (rows !== 0) {
      // Up and down stop at the region's edge when the cursor started inside it.
      const insideRegion = this.y >= this.scrollTop && this.y <= this.scrollBottom;
      const low = insideRegion ? this.scrollTop : 0;
      const high = insideRegion ? this.scrollBottom : this.rows - 1;
      this.y = clamp(this.y + rows, low, high);
    }
    if (cols !== 0) this.x = clamp(this.x + cols, 0, this.cols - 1);
    this.pendingWrap = false;
  }

  /** CHA and HPA: to a column. */
  setColumn(col: number): void {
    this.x = clamp(col, 0, this.cols - 1);
    this.pendingWrap = false;
  }

  /** VPA: to a line. */
  setLine(row: number): void {
    const top = this.modes.origin ? this.scrollTop : 0;
    const bottom = this.modes.origin ? this.scrollBottom : this.rows - 1;
    this.y = clamp(top + row, top, bottom);
    this.pendingWrap = false;
  }

  saveCursor(): void {
    this.saved = this.snapshot();
  }

  restoreCursor(): void {
    const saved = this.saved;
    if (saved === null) {
      this.x = 0;
      this.y = this.modes.origin ? this.scrollTop : 0;
      this.pendingWrap = false;
      return;
    }
    this.apply(saved);
  }

  private snapshot(): SavedCursor {
    return {
      x: this.x,
      y: this.y,
      pen: { ...this.pen },
      origin: this.modes.origin,
      pendingWrap: this.pendingWrap,
      charsets: [this.charsets[0], this.charsets[1]],
      shifted: this.shifted,
    };
  }

  private apply(saved: SavedCursor): void {
    this.x = clamp(saved.x, 0, this.cols - 1);
    this.y = clamp(saved.y, 0, this.rows - 1);
    this.pen.fg = saved.pen.fg;
    this.pen.bg = saved.pen.bg;
    this.pen.attrs = saved.pen.attrs;
    this.modes.origin = saved.origin;
    this.pendingWrap = saved.pendingWrap;
    this.charsets = [saved.charsets[0], saved.charsets[1]];
    this.shifted = saved.shifted;
  }

  // -- tab stops ------------------------------------------------------------------------

  setTabStop(): void {
    this.tabs[this.x] = true;
  }

  clearTabStop(all: boolean): void {
    if (all) this.tabs = this.tabs.map(() => false);
    else this.tabs[this.x] = false;
  }

  // -- scrolling ------------------------------------------------------------------------

  /** DECSTBM: the lines that scroll. An empty or inverted region is the whole screen. */
  setScrollRegion(top: number, bottom: number): void {
    const low = clamp(top, 0, this.rows - 1);
    const high = clamp(bottom, 0, this.rows - 1);
    if (low >= high) {
      this.scrollTop = 0;
      this.scrollBottom = this.rows - 1;
    } else {
      this.scrollTop = low;
      this.scrollBottom = high;
    }
    this.moveTo(0, 0);
  }

  /** SU, and what a line feed at the bottom does: lines move up, a blank comes in below. */
  scrollUp(count: number): void {
    const lines = this.buffer.lines;
    const n = clamp(count, 0, this.scrollBottom - this.scrollTop + 1);
    for (let i = 0; i < n; i++) {
      const [gone] = lines.splice(this.scrollTop, 1);
      lines.splice(this.scrollBottom, 0, new Row(this.cols, this.eraseBg()));
      if (gone !== undefined && !this.alt && this.scrollTop === 0 && this.scrollBottom === this.rows - 1) {
        this.pushScrollback(gone);
      }
    }
    if (n > 0) this.touchAll();
  }

  /** SD, and what a reverse index at the top does: lines move down, a blank comes in above. */
  scrollDown(count: number): void {
    const lines = this.buffer.lines;
    const n = clamp(count, 0, this.scrollBottom - this.scrollTop + 1);
    for (let i = 0; i < n; i++) {
      lines.splice(this.scrollBottom, 1);
      lines.splice(this.scrollTop, 0, new Row(this.cols, this.eraseBg()));
    }
    if (n > 0) this.touchAll();
  }

  private pushScrollback(row: Row): void {
    this.scrollback.push(row);
    const over = this.scrollback.length - this.maxScrollback;
    if (over > 0) {
      this.scrollback.splice(0, over);
      this.trimmed += over;
    }
  }

  // -- erasing and editing --------------------------------------------------------------

  /** ED: 0 from the cursor to the end, 1 from the start to the cursor, 2 everything, 3 the scrollback too. */
  eraseInDisplay(mode: number): void {
    const bg = this.eraseBg();
    if (mode === 0) {
      this.row(this.y).erase(this.x, this.cols, bg);
      for (let y = this.y + 1; y < this.rows; y++) this.row(y).erase(0, this.cols, bg);
    } else if (mode === 1) {
      for (let y = 0; y < this.y; y++) this.row(y).erase(0, this.cols, bg);
      this.row(this.y).erase(0, this.x + 1, bg);
    } else if (mode === 2 || mode === 3) {
      for (let y = 0; y < this.rows; y++) {
        const row = this.row(y);
        row.erase(0, this.cols, bg);
        row.wrapped = false;
      }
      if (mode === 3 && !this.alt) {
        this.trimmed += this.scrollback.length;
        this.scrollback = [];
      }
    }
    this.touchAll();
  }

  /** EL: 0 from the cursor to the end of the line, 1 from its start to the cursor, 2 all of it. */
  eraseInLine(mode: number): void {
    const row = this.row(this.y);
    const bg = this.eraseBg();
    if (mode === 0) row.erase(this.x, this.cols, bg);
    else if (mode === 1) row.erase(0, this.x + 1, bg);
    else if (mode === 2) row.erase(0, this.cols, bg);
    row.wrapped = false;
    this.touch(this.y);
  }

  /** ECH: blanks `count` cells from the cursor, leaving the rest where it is. */
  eraseChars(count: number): void {
    this.row(this.y).erase(this.x, this.x + Math.max(count, 1), this.eraseBg());
    this.touch(this.y);
  }

  /** ICH: opens `count` blank cells at the cursor, pushing the rest right. */
  insertChars(count: number): void {
    const row = this.row(this.y);
    const n = clamp(count, 1, this.cols - this.x);
    for (let at = this.cols - 1; at >= this.x + n; at--) {
      row.chars[at] = row.chars[at - n] ?? " ";
      row.fg[at] = row.fg[at - n] ?? DEFAULT;
      row.bg[at] = row.bg[at - n] ?? DEFAULT;
      row.attrs[at] = row.attrs[at - n] ?? 0;
    }
    row.erase(this.x, this.x + n, this.eraseBg());
    this.touch(this.y);
  }

  /** DCH: removes `count` cells at the cursor, pulling the rest left. */
  deleteChars(count: number): void {
    const row = this.row(this.y);
    const n = clamp(count, 1, this.cols - this.x);
    for (let at = this.x; at < this.cols - n; at++) {
      row.chars[at] = row.chars[at + n] ?? " ";
      row.fg[at] = row.fg[at + n] ?? DEFAULT;
      row.bg[at] = row.bg[at + n] ?? DEFAULT;
      row.attrs[at] = row.attrs[at + n] ?? 0;
    }
    row.erase(this.cols - n, this.cols, this.eraseBg());
    this.touch(this.y);
  }

  /** IL: opens `count` blank lines at the cursor, inside the region. */
  insertLines(count: number): void {
    if (this.y < this.scrollTop || this.y > this.scrollBottom) return;
    const lines = this.buffer.lines;
    const n = clamp(count, 1, this.scrollBottom - this.y + 1);
    for (let i = 0; i < n; i++) {
      lines.splice(this.scrollBottom, 1);
      lines.splice(this.y, 0, new Row(this.cols, this.eraseBg()));
    }
    this.x = 0;
    this.pendingWrap = false;
    this.touchAll();
  }

  /** DL: removes `count` lines at the cursor, inside the region. */
  deleteLines(count: number): void {
    if (this.y < this.scrollTop || this.y > this.scrollBottom) return;
    const lines = this.buffer.lines;
    const n = clamp(count, 1, this.scrollBottom - this.y + 1);
    for (let i = 0; i < n; i++) {
      lines.splice(this.y, 1);
      lines.splice(this.scrollBottom, 0, new Row(this.cols, this.eraseBg()));
    }
    this.x = 0;
    this.pendingWrap = false;
    this.touchAll();
  }

  /** DECALN: fills the screen with E, which programs use to test their own size. */
  alignmentTest(): void {
    for (let y = 0; y < this.rows; y++) {
      const row = this.row(y);
      row.erase(0, this.cols, DEFAULT);
      for (let x = 0; x < this.cols; x++) row.chars[x] = "E";
    }
    this.touchAll();
  }

  // -- pen ------------------------------------------------------------------------------

  resetPen(): void {
    this.pen.fg = DEFAULT;
    this.pen.bg = DEFAULT;
    this.pen.attrs = 0;
  }

  // -- modes ----------------------------------------------------------------------------

  /** Switches to or from the alternate screen (`?47`, `?1047`, `?1049`). */
  useAlternateScreen(enable: boolean, { save, clear }: { save: boolean; clear: boolean }): void {
    if (enable === this.alt) return;
    if (enable) {
      if (save) this.altSaved = this.snapshot();
      this.buffer = this.altBuffer;
      this.alt = true;
      if (clear) this.altBuffer.lines = this.blankLines();
    } else {
      if (clear) this.altBuffer.lines = this.blankLines();
      this.buffer = this.main;
      this.alt = false;
      if (save && this.altSaved !== null) this.apply(this.altSaved);
    }
    this.scrollTop = 0;
    this.scrollBottom = this.rows - 1;
    this.touchAll();
  }

  setTitle(title: string): void {
    // eslint-disable-next-line no-control-regex -- a title is text: control characters are dropped
    const clean = title.replace(/[\x00-\x1f\x7f-\x9f]/g, "").slice(0, MAX_TITLE);
    this.title = clean;
    this.events.onTitle?.(clean);
  }

  respond(data: string): void {
    this.events.onResponse?.(data);
  }

  /** DECSTR: back to the modes a program starts with, the screen's contents kept. */
  softReset(): void {
    this.modes.autoWrap = true;
    this.modes.insert = false;
    this.modes.newline = false;
    this.modes.origin = false;
    this.modes.cursorVisible = true;
    this.modes.applicationCursor = false;
    this.modes.applicationKeypad = false;
    this.modes.bracketedPaste = false;
    this.modes.reverseVideo = false;
    this.modes.mouse = false;
    this.cursorShape = "block";
    this.resetPen();
    this.charsets = ["B", "B"];
    this.shifted = false;
    this.scrollTop = 0;
    this.scrollBottom = this.rows - 1;
    this.saved = null;
    this.pendingWrap = false;
    this.touchAll();
  }

  /** RIS: as if the terminal had just been switched on, but for the scrollback, which is kept. */
  reset(): void {
    if (this.alt) this.useAlternateScreen(false, { save: false, clear: true });
    this.softReset();
    this.tabs = this.defaultTabs();
    this.main.lines = this.blankLines();
    this.x = 0;
    this.y = 0;
    this.title = "";
    this.touchAll();
  }

  // -- size -----------------------------------------------------------------------------

  /**
   * Changes the size. Lines are cut or padded, not reflowed: a program that cares redraws on
   * SIGWINCH. Growing taller brings lines back from the scrollback; shrinking pushes the top
   * lines into it, so what the cursor is on stays on screen.
   */
  resize(cols: number, rows: number): void {
    const nextCols = Math.max(cols, 1);
    const nextRows = Math.max(rows, 1);
    if (nextCols === this.cols && nextRows === this.rows) return;
    this.main.lines = this.main.lines.map((row) => row.resized(nextCols));
    this.altBuffer.lines = this.altBuffer.lines.map((row) => row.resized(nextCols));
    this.scrollback = this.scrollback.map((row) => row.resized(nextCols));

    for (const buffer of [this.main, this.altBuffer]) {
      const isMain = buffer === this.main;
      while (buffer.lines.length > nextRows) {
        // The bottom blank lines go first; past them the top lines move into the scrollback.
        const cursorBelow = isMain ? this.y >= nextRows : false;
        if (isMain && cursorBelow) {
          const gone = buffer.lines.shift();
          if (gone !== undefined) this.pushScrollback(gone);
          this.y--;
        } else {
          buffer.lines.pop();
        }
      }
      while (buffer.lines.length < nextRows) {
        const back = isMain ? this.scrollback.pop() : undefined;
        if (back !== undefined) {
          buffer.lines.unshift(back);
          this.y++;
        } else {
          buffer.lines.push(new Row(nextCols));
        }
      }
    }

    this.cols = nextCols;
    this.rows = nextRows;
    this.scrollTop = 0;
    this.scrollBottom = nextRows - 1;
    this.x = clamp(this.x, 0, nextCols - 1);
    this.y = clamp(this.y, 0, nextRows - 1);
    this.pendingWrap = false;
    this.tabs = this.defaultTabs();
    this.touchAll();
  }

  /** Throws away the lines that scrolled off. */
  clearScrollback(): void {
    this.trimmed += this.scrollback.length;
    this.scrollback = [];
    this.touchAll();
  }
}
