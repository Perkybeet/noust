/**
 * The terminal's behaviour on a page: what it does with a key, a paste, a wheel, a drag and a
 * change of size, and when it draws.
 *
 * It is plain TypeScript over three elements (the box that holds it, the canvas, and a text area
 * that is where the keyboard and the clipboard land), so `TerminalScreen` stays a thin component
 * and the behaviour is tested by dispatching events at it. The emulator and the renderer do the
 * rest.
 *
 * What keys do: the program gets what xterm would send (`lib/terminal/keys.ts`); Ctrl+Shift+C
 * copies the selection and Ctrl+Shift+V (or the browser's paste) pastes, because Ctrl+C has to
 * stay an interrupt; Shift+PageUp and Shift+PageDown scroll the view back. A wheel scrolls the
 * scrollback, or, on the alternate screen where there is none, sends the arrows (`less` and
 * `man` scroll with them). Input is never sent when the session is over.
 */

import { copyText } from "../../../lib/clipboard";
import { encodeKey } from "../../../lib/terminal/keys";
import { lineAt, wordAt } from "../../../lib/terminal/selection";
import { Selection } from "../../../lib/terminal/selection";
import type { Point } from "../../../lib/terminal/selection";
import type { Terminal } from "../../../lib/terminal/terminal";
import { TerminalRenderer } from "./renderer";
import { readTerminalTheme } from "./theme";

/** What the page needs to know about the terminal's content: the emulator and when it changed. */
export interface ScreenSource {
  terminal: Terminal;
  /** Calls the listener after every write to the emulator; returns how to stop. */
  subscribe: (listener: () => void) => () => void;
}

export interface Elements {
  /** The box the terminal fills; its size decides the grid. */
  container: HTMLElement;
  canvas: HTMLCanvasElement;
  /** Where the keyboard, the input method and the clipboard land. */
  input: HTMLTextAreaElement;
}

export interface Callbacks {
  /** Bytes to send to the program: a key, a paste, a reply to a query. */
  onInput: (data: string) => void;
  /** The grid changed size: the program has to be told. */
  onResize: (cols: number, rows: number) => void;
  /** The view moved, so the page can offer a way back to the newest output. */
  onScrolled: (offset: number) => void;
  /** The focus moved in or out of the terminal. */
  onFocusChange: (focused: boolean) => void;
  /** Text was copied (or could not be). */
  onCopied: (text: string, ok: boolean) => void;
}

/** The mono size the console uses for system output (DESIGN.md: system output is 12px mono). */
const FONT_SIZE = 13;

/** Lines one notch of a mouse wheel scrolls. */
const WHEEL_LINES = 3;

/**
 * How long after Escape a Tab leaves the terminal instead of reaching the shell. The terminal
 * keeps Tab for completion, so without a way out the keyboard would be trapped in it (WCAG 2.1.2).
 */
const LEAVE_WINDOW_MS = 1000;

/** How fast the box may be resized, in frames: a drag of the window is not a series of resizes. */
const SETTLE_MS = 80;

export class TerminalController {
  private readonly elements: Elements;
  private readonly source: ScreenSource;
  private readonly callbacks: Callbacks;
  private renderer: TerminalRenderer | null = null;
  private readonly selection = new Selection();
  private offset = 0;
  private escapedAt = Number.NEGATIVE_INFINITY;
  private focused = false;
  private composing = false;
  private disabled = false;
  private frame = 0;
  private settle: ReturnType<typeof setTimeout> | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private stopSource: (() => void) | null = null;
  private stopThemeWatch: (() => void) | null = null;
  private pointerDown = false;
  private pushedLines = 0;
  private readonly cleanups: (() => void)[] = [];

  constructor(elements: Elements, source: ScreenSource, callbacks: Callbacks) {
    this.elements = elements;
    this.source = source;
    this.callbacks = callbacks;
  }

  // -- lifecycle ------------------------------------------------------------------------

  start(): void {
    const { container, canvas } = this.elements;
    const style = getComputedStyle(container);
    const theme = readTerminalTheme(container);
    this.renderer = new TerminalRenderer(canvas, theme, style.fontFamily, FONT_SIZE);
    this.pushedLines = this.lines();

    this.resizeObserver = new ResizeObserver(() => {
      this.scheduleFit();
    });
    this.resizeObserver.observe(container);
    this.stopSource = this.source.subscribe(() => {
      this.follow();
      this.schedule();
    });

    this.on(this.elements.input, "keydown", (event) => {
      this.keydown(event as KeyboardEvent);
    });
    this.on(this.elements.input, "input", () => {
      this.typed();
    });
    this.on(this.elements.input, "compositionstart", () => {
      this.composing = true;
    });
    this.on(this.elements.input, "compositionend", (event) => {
      this.composing = false;
      const data = (event as CompositionEvent).data;
      this.elements.input.value = "";
      if (data !== "") this.send(data);
    });
    this.on(this.elements.input, "paste", (event) => {
      this.paste(event as ClipboardEvent);
    });
    this.on(this.elements.input, "focus", () => {
      this.setFocused(true);
    });
    this.on(this.elements.input, "blur", () => {
      this.setFocused(false);
    });
    this.on(container, "wheel", (event) => {
      this.wheel(event as WheelEvent);
    }, { passive: false });
    this.on(this.elements.canvas, "pointerdown", (event) => {
      this.pointerStart(event as PointerEvent);
    });
    this.on(this.elements.canvas, "pointermove", (event) => {
      this.pointerMove(event as PointerEvent);
    });
    this.on(this.elements.canvas, "pointerup", (event) => {
      this.pointerEnd(event as PointerEvent);
    });
    this.on(this.elements.canvas, "pointercancel", () => {
      this.pointerDown = false;
    });
    this.on(container, "mousedown", (event) => {
      // Clicking anywhere in the box is clicking in the terminal: the keyboard goes to it.
      if ((event as MouseEvent).button === 0) this.focus();
    });

    this.watchTheme(container);
    this.fit();
    this.schedule();
  }

  stop(): void {
    for (const cleanup of this.cleanups.splice(0)) cleanup();
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.stopSource?.();
    this.stopSource = null;
    this.stopThemeWatch?.();
    this.stopThemeWatch = null;
    if (this.settle !== null) clearTimeout(this.settle);
    this.settle = null;
    if (this.frame !== 0) cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.renderer = null;
  }

  private on(target: EventTarget, type: string, handler: (event: Event) => void, options?: AddEventListenerOptions): void {
    target.addEventListener(type, handler, options);
    this.cleanups.push(() => {
      target.removeEventListener(type, handler, options);
    });
  }

  /** Canvas pixels do not follow CSS: the theme is read again when the console's changes. */
  private watchTheme(container: HTMLElement): void {
    const reread = (): void => {
      this.renderer?.setTheme(readTerminalTheme(container));
      this.source.terminal.screen.invalidate();
      this.schedule();
    };
    const observer = new MutationObserver(reread);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", reread);
    this.stopThemeWatch = () => {
      observer.disconnect();
      scheme.removeEventListener("change", reread);
    };
  }

  // -- state the page sets --------------------------------------------------------------

  /** Once the session is over nothing typed is sent. */
  setDisabled(disabled: boolean): void {
    this.disabled = disabled;
  }

  focus(): void {
    this.elements.input.focus({ preventScroll: true });
  }

  /** The text of the selection, or the empty string. */
  selectedText(): string {
    return this.selection.text(this.source.terminal.screen);
  }

  /** Copies the selection, or the whole screen when nothing is selected. */
  copy(): void {
    const screen = this.source.terminal.screen;
    const text = this.selection.empty ? screen.textRows().join("\n").trimEnd() : this.selection.text(screen);
    if (text === "") return;
    copyText(text).then(
      () => {
        this.callbacks.onCopied(text, true);
      },
      () => {
        this.callbacks.onCopied(text, false);
      },
    );
  }

  /** Jumps to the newest output. */
  scrollToBottom(): void {
    this.scrollTo(0);
  }

  get scrolledBack(): number {
    return this.offset;
  }

  // -- size and drawing -----------------------------------------------------------------

  private scheduleFit(): void {
    if (this.settle !== null) clearTimeout(this.settle);
    this.settle = setTimeout(() => {
      this.settle = null;
      this.fit();
    }, SETTLE_MS);
  }

  private fit(): void {
    const renderer = this.renderer;
    if (renderer === null) return;
    const { container } = this.elements;
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width <= 0 || height <= 0) return;
    const { cols, rows } = renderer.fit(width, height);
    const screen = this.source.terminal.screen;
    const ratio = window.devicePixelRatio || 1;
    if (cols !== screen.cols || rows !== screen.rows) {
      this.source.terminal.resize(cols, rows);
      this.callbacks.onResize(cols, rows);
    }
    renderer.resize(cols, rows, ratio);
    screen.invalidate();
    this.clampOffset();
    this.schedule();
  }

  private schedule(): void {
    if (this.frame !== 0) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.renderer?.draw(this.source.terminal.screen, { offset: this.offset, selection: this.selection, focused: this.focused });
    });
  }

  private setFocused(focused: boolean): void {
    this.focused = focused;
    this.source.terminal.screen.invalidate();
    this.schedule();
    this.callbacks.onFocusChange(focused);
  }

  // -- scrolling ------------------------------------------------------------------------

  private lines(): number {
    const screen = this.source.terminal.screen;
    return screen.trimmed + screen.scrollback.length;
  }

  /** New output keeps a scrolled-back view on the same lines, and a view at the bottom there. */
  private follow(): void {
    const total = this.lines();
    const added = total - this.pushedLines;
    this.pushedLines = total;
    if (this.offset > 0 && added > 0) {
      this.offset += added;
      this.clampOffset();
      this.callbacks.onScrolled(this.offset);
    } else if (this.offset > 0) {
      this.clampOffset();
    }
  }

  private clampOffset(): void {
    const max = this.source.terminal.screen.scrollback.length;
    if (this.offset > max) this.offset = max;
    if (this.offset < 0) this.offset = 0;
  }

  private scrollTo(offset: number): void {
    const before = this.offset;
    this.offset = offset;
    this.clampOffset();
    if (this.offset !== before) {
      this.source.terminal.screen.invalidate();
      this.callbacks.onScrolled(this.offset);
      this.schedule();
    }
  }

  private wheel(event: WheelEvent): void {
    const screen = this.source.terminal.screen;
    const renderer = this.renderer;
    if (renderer === null) return;
    const lines = event.deltaMode === 1 ? event.deltaY : (event.deltaY / renderer.cellSize.height) * (WHEEL_LINES / 3);
    const steps = Math.sign(lines) * Math.max(1, Math.round(Math.abs(lines)));
    if (steps === 0) return;
    if (screen.alt) {
      // Nothing to scroll on the alternate screen: what a pager understands is the arrows.
      event.preventDefault();
      const key = steps < 0 ? "A" : "B";
      const prefix = screen.modes.applicationCursor ? "\x1bO" : "\x1b[";
      this.send(`${prefix}${key}`.repeat(Math.min(Math.abs(steps), 10)));
      return;
    }
    if (screen.scrollback.length === 0 && this.offset === 0) return;
    event.preventDefault();
    this.scrollTo(this.offset - steps);
  }

  // -- keyboard and clipboard -----------------------------------------------------------

  private send(data: string): void {
    if (this.disabled || data === "") return;
    if (this.offset > 0) this.scrollTo(0);
    this.callbacks.onInput(data);
  }

  private keydown(event: KeyboardEvent): void {
    // An input method is composing: its keys are not the program's, and its result arrives whole.
    // Browsers that report the first key of a composition before it starts name it "Process".
    if (event.isComposing || this.composing || event.key === "Process") return;

    // The way out for a keyboard: Escape, then Tab, moves the focus on. The Escape itself still
    // reaches the program, so a vi user who pressed it loses nothing.
    if (event.key === "Tab" && !event.ctrlKey && !event.altKey && !event.metaKey && performance.now() - this.escapedAt < LEAVE_WINDOW_MS) {
      this.escapedAt = Number.NEGATIVE_INFINITY;
      return;
    }
    if (event.key === "Escape") this.escapedAt = performance.now();

    const copy = event.ctrlKey && event.shiftKey && !event.altKey && event.key.toLowerCase() === "c";
    const paste = event.ctrlKey && event.shiftKey && !event.altKey && event.key.toLowerCase() === "v";
    if (copy || (event.metaKey && !event.ctrlKey && event.key.toLowerCase() === "c" && !this.selection.empty)) {
      event.preventDefault();
      this.copy();
      return;
    }
    if (paste) {
      // The browser's own paste event delivers the text; let it happen on the text area.
      return;
    }
    if (event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {
      const screen = this.source.terminal.screen;
      if (!screen.alt && (event.key === "PageUp" || event.key === "PageDown" || event.key === "Home" || event.key === "End")) {
        event.preventDefault();
        if (event.key === "PageUp") this.scrollTo(this.offset + Math.max(1, screen.rows - 1));
        else if (event.key === "PageDown") this.scrollTo(this.offset - Math.max(1, screen.rows - 1));
        else if (event.key === "Home") this.scrollTo(screen.scrollback.length);
        else this.scrollTo(0);
        return;
      }
    }

    const data = encodeKey(event, { applicationCursor: this.source.terminal.screen.modes.applicationCursor });
    if (data === null) return;
    event.preventDefault();
    if (!this.selection.empty) {
      this.selection.clear();
      this.source.terminal.screen.invalidate();
    }
    this.send(data);
  }

  /** Text that arrived without a key press the mapping handled: a virtual keyboard, a dead key's result. */
  private typed(): void {
    if (this.composing) return;
    const input = this.elements.input;
    const text = input.value;
    input.value = "";
    if (text !== "") this.send(text);
  }

  private paste(event: ClipboardEvent): void {
    event.preventDefault();
    const text = event.clipboardData?.getData("text") ?? "";
    if (text === "") return;
    this.send(this.source.terminal.paste(text));
  }

  // -- the pointer ----------------------------------------------------------------------

  private pointOf(event: PointerEvent): Point | null {
    const cell = this.renderer?.cellAt(event.clientX, event.clientY) ?? null;
    if (cell === null) return null;
    const screen = this.source.terminal.screen;
    return { line: screen.trimmed + screen.scrollback.length - this.offset + cell.row, col: cell.col };
  }

  private pointerStart(event: PointerEvent): void {
    if (event.button !== 0) return;
    const point = this.pointOf(event);
    if (point === null) return;
    const screen = this.source.terminal.screen;
    if (event.detail >= 3) {
      const [from, to] = lineAt(screen, point);
      this.selection.set(from, to);
    } else if (event.detail === 2) {
      const [from, to] = wordAt(screen, point);
      this.selection.set(from, to);
    } else {
      this.selection.start(point);
      this.pointerDown = true;
      this.elements.canvas.setPointerCapture(event.pointerId);
    }
    screen.invalidate();
    this.schedule();
  }

  private pointerMove(event: PointerEvent): void {
    if (!this.pointerDown) return;
    const point = this.pointOf(event);
    if (point === null) return;
    this.selection.extend(point);
    this.source.terminal.screen.invalidate();
    this.schedule();
  }

  private pointerEnd(event: PointerEvent): void {
    if (!this.pointerDown) return;
    this.pointerDown = false;
    if (this.elements.canvas.hasPointerCapture(event.pointerId)) this.elements.canvas.releasePointerCapture(event.pointerId);
    if (this.selection.empty) {
      this.selection.clear();
      this.source.terminal.screen.invalidate();
      this.schedule();
    }
  }
}
