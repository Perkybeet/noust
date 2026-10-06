/**
 * Draws a terminal screen on a canvas.
 *
 * A canvas, not an element per cell and not xterm.js: the console's content security policy
 * allows no inline style and no string-to-DOM sink, and xterm injects a `<style>` element. The
 * grid is drawn cell by cell on a 2D context, on whole pixels, in the console's own mono font; only
 * the rows that changed are drawn again, unless the view moved or the theme changed.
 *
 * The renderer owns the canvas's size. The cell is measured from the font, `fit` says how many
 * columns and rows fit in a box, `resize` sets the canvas for them (in device pixels, so it is
 * sharp on a screen with a density of 2). What it draws is told to it, not read from the DOM:
 * the screen, the selection, how far back the view is scrolled, whether the terminal has the
 * focus. With no 2D context (a test's DOM, a browser without canvas) it measures with fixed
 * metrics and draws nothing.
 */

import { fixedColour, isTrueColour, trueColour, cssRgb } from "../../../lib/terminal/palette";
import type { Selection } from "../../../lib/terminal/selection";
import { Attr, DEFAULT } from "../../../lib/terminal/screen";
import type { Screen } from "../../../lib/terminal/screen";
import type { TerminalTheme } from "./theme";

export interface CellSize {
  width: number;
  height: number;
}

/** What the renderer is told to draw besides the screen. */
export interface View {
  /** How many lines the view is scrolled back into the scrollback; 0 follows the screen. */
  offset: number;
  selection: Selection;
  focused: boolean;
}

/** The size of a cell when there is no canvas to measure with. */
const FALLBACK_CELL: CellSize = { width: 8, height: 17 };

/** The line height, as a multiple of the font size. */
const LINE_HEIGHT = 1.3;

const BOLD_WEIGHT = "600";

/** Colours of the 256-colour palette, as CSS, built once. */
const FIXED: string[] = [];

function fixedCss(index: number): string {
  let css = FIXED[index];
  if (css === undefined) {
    const [red, green, blue] = fixedColour(index);
    css = cssRgb(red, green, blue);
    FIXED[index] = css;
  }
  return css;
}

export class TerminalRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly context: CanvasRenderingContext2D | null;
  private theme: TerminalTheme;
  private fontFamily: string;
  private fontSize: number;
  private ratio = 1;
  private cols = 0;
  private rows = 0;
  private cell: CellSize = FALLBACK_CELL;
  private ascent = 13;
  private lastCursorRow = -1;
  private lastOffset = 0;
  private lastFocused = false;
  private lastVersion = -1;
  private truecolour = new Map<number, string>();
  /** The font the context has now: setting it is not free, and a resize forgets it. */
  private currentFont = "";

  constructor(canvas: HTMLCanvasElement, theme: TerminalTheme, fontFamily: string, fontSize: number) {
    this.canvas = canvas;
    this.theme = theme;
    this.fontFamily = fontFamily;
    this.fontSize = fontSize;
    this.context = canvas.getContext("2d");
    this.measure();
  }

  /** The size of one cell, in CSS pixels. */
  get cellSize(): CellSize {
    return this.cell;
  }

  setTheme(theme: TerminalTheme): void {
    this.theme = theme;
    this.lastVersion = -1;
  }

  setFont(fontFamily: string, fontSize: number): void {
    this.fontFamily = fontFamily;
    this.fontSize = fontSize;
    this.measure();
    this.lastVersion = -1;
  }

  private font(bold: boolean, italic: boolean): string {
    return `${italic ? "italic " : ""}${bold ? BOLD_WEIGHT : "400"} ${String(this.fontSize)}px ${this.fontFamily}`;
  }

  /** Measures a cell from the font: its width is the width of a character, its height the line's. */
  private measure(): void {
    const context = this.context;
    if (context === null) {
      this.cell = FALLBACK_CELL;
      return;
    }
    context.font = this.font(false, false);
    this.currentFont = context.font;
    const metrics = context.measureText("0");
    const width = metrics.width > 0 ? metrics.width : FALLBACK_CELL.width;
    const height = Math.ceil(this.fontSize * LINE_HEIGHT);
    this.cell = { width, height };
    this.ascent = Math.round((height - this.fontSize) / 2 + this.fontSize * 0.8);
  }

  /** How many columns and rows fit in a box of this size (CSS pixels). */
  fit(width: number, height: number): { cols: number; rows: number } {
    return {
      cols: Math.max(2, Math.floor(width / this.cell.width)),
      rows: Math.max(1, Math.floor(height / this.cell.height)),
    };
  }

  /** Sets the canvas for a grid: CSS size from the cells, backing store from the display's density. */
  resize(cols: number, rows: number, ratio: number): void {
    this.cols = cols;
    this.rows = rows;
    this.ratio = ratio;
    const width = cols * this.cell.width;
    const height = rows * this.cell.height;
    this.canvas.width = Math.ceil(width * ratio);
    this.canvas.height = Math.ceil(height * ratio);
    // The size the page lays the canvas out at: CSSOM, which the content security policy allows.
    this.canvas.style.width = `${String(width)}px`;
    this.canvas.style.height = `${String(height)}px`;
    this.currentFont = "";
    this.lastVersion = -1;
  }

  /** The cell under a point of the page, or null when it is outside the grid. */
  cellAt(clientX: number, clientY: number): { col: number; row: number } | null {
    const box = this.canvas.getBoundingClientRect();
    const col = Math.floor((clientX - box.left) / this.cell.width);
    const row = Math.floor((clientY - box.top) / this.cell.height);
    if (col < 0 || row < 0 || col >= this.cols || row >= this.rows) return null;
    return { col, row };
  }

  private colour(value: number, foreground: boolean): string {
    if (value === DEFAULT) return foreground ? this.theme.fg : this.theme.bg;
    if (value < 16) return (foreground ? this.theme.ansiFg : this.theme.ansiBg)[value] ?? this.theme.fg;
    if (isTrueColour(value)) {
      let css = this.truecolour.get(value);
      if (css === undefined) {
        const [red, green, blue] = trueColour(value);
        css = cssRgb(red, green, blue);
        // A program that sweeps a gradient would otherwise keep every colour it ever drew.
        if (this.truecolour.size > 4_096) this.truecolour.clear();
        this.truecolour.set(value, css);
      }
      return css;
    }
    return fixedCss(Math.min(value, 255));
  }

  /**
   * Draws the screen. Only what changed is drawn, unless something it depends on did: the
   * view's offset or focus, the size, the theme, or the whole screen being marked.
   */
  draw(screen: Screen, view: View): void {
    const context = this.context;
    if (context === null) {
      screen.markDrawn();
      return;
    }
    const everything =
      screen.allDirty || view.offset !== this.lastOffset || view.focused !== this.lastFocused || this.lastVersion < 0 || view.offset > 0;
    if (!everything && screen.version === this.lastVersion) return;

    context.setTransform(this.ratio, 0, 0, this.ratio, 0, 0);
    const rows = Math.min(this.rows, screen.rows);
    const wanted = new Set<number>();
    if (everything) {
      for (let row = 0; row < rows; row++) wanted.add(row);
    } else {
      for (const row of screen.dirty) wanted.add(row);
      wanted.add(this.lastCursorRow);
      wanted.add(screen.y);
    }
    const firstLine = screen.scrollback.length - view.offset;
    for (const row of wanted) {
      if (row >= 0 && row < rows) this.drawRow(context, screen, view, row, firstLine + row);
    }
    this.lastCursorRow = screen.y;
    this.lastOffset = view.offset;
    this.lastFocused = view.focused;
    this.lastVersion = screen.version;
    screen.markDrawn();
  }

  private drawRow(context: CanvasRenderingContext2D, screen: Screen, view: View, row: number, index: number): void {
    const cell = this.cell;
    const line = screen.line(index);
    const y = row * cell.height;
    const absolute = screen.trimmed + index;
    const cursorHere = view.offset === 0 && screen.modes.cursorVisible && row === screen.y;
    const reverse = screen.modes.reverseVideo;

    context.fillStyle = reverse ? this.theme.fg : this.theme.bg;
    context.fillRect(0, y, this.cols * cell.width, cell.height);

    for (let x = 0; x < this.cols && x < line.cols; x++) {
      const attrs = line.attrs[x] ?? 0;
      if ((attrs & Attr.Continuation) !== 0) continue;
      const wide = (attrs & Attr.Wide) !== 0;
      const width = wide ? cell.width * 2 : cell.width;
      const isCursor = cursorHere && x === screen.x;
      const selected = view.selection.contains(absolute, x);

      const fg = line.fg[x] ?? DEFAULT;
      const bg = line.bg[x] ?? DEFAULT;
      let foreground = this.colour(fg, true);
      let ground = this.colour(bg, false);
      if (((attrs & Attr.Inverse) !== 0) !== reverse) {
        // Inverse swaps the two; a default ground becomes the theme's text, a default text its ground.
        foreground = bg === DEFAULT ? this.theme.bg : this.colour(bg, true);
        ground = fg === DEFAULT ? this.theme.fg : this.colour(fg, false);
      }
      let drawBlock = false;
      if (isCursor && screen.cursorShape === "block" && view.focused) {
        ground = this.theme.cursor;
        foreground = this.theme.cursorText;
        drawBlock = true;
      }
      const painted = ground !== this.theme.bg || selected || drawBlock;
      if (painted) {
        context.fillStyle = selected && !drawBlock ? this.theme.selection : ground;
        context.fillRect(x * cell.width, y, width, cell.height);
      }

      const text = line.chars[x] ?? " ";
      const visible = text !== " " && text !== "" && (attrs & Attr.Invisible) === 0;
      if (visible || (attrs & (Attr.Underline | Attr.Strike)) !== 0) {
        context.fillStyle = foreground;
        if ((attrs & Attr.Dim) !== 0) context.globalAlpha = 0.6;
        if (visible) {
          const font = this.font((attrs & Attr.Bold) !== 0, (attrs & Attr.Italic) !== 0);
          if (font !== this.currentFont) {
            context.font = font;
            this.currentFont = font;
          }
          context.fillText(text, x * cell.width, y + this.ascent);
        }
        if ((attrs & Attr.Underline) !== 0) context.fillRect(x * cell.width, y + cell.height - 2, width, 1);
        if ((attrs & Attr.Strike) !== 0) context.fillRect(x * cell.width, y + Math.floor(cell.height / 2), width, 1);
        context.globalAlpha = 1;
      }

      if (isCursor && !drawBlock) this.drawCursor(context, screen, view, x, y, width);
    }
    if (cursorHere && screen.x >= line.cols) this.drawCursor(context, screen, view, screen.x, y, cell.width);
  }

  /** A cursor that is not a filled block: an outline when the terminal lacks the focus, a line or a bar otherwise. */
  private drawCursor(context: CanvasRenderingContext2D, screen: Screen, view: View, x: number, y: number, width: number): void {
    const cell = this.cell;
    context.fillStyle = this.theme.cursor;
    if (!view.focused && screen.cursorShape === "block") {
      context.strokeStyle = this.theme.cursor;
      context.lineWidth = 1;
      context.strokeRect(x * cell.width + 0.5, y + 0.5, width - 1, cell.height - 1);
    } else if (screen.cursorShape === "underline") {
      context.fillRect(x * cell.width, y + cell.height - 2, width, 2);
    } else if (screen.cursorShape === "bar") {
      context.fillRect(x * cell.width, y, 2, cell.height);
    }
  }
}
