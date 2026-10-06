import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TerminalController } from "./controller";
import { installFakeCanvas, makeSource, sizeOf } from "./testing";
import type { FakeContext } from "./testing";

/** Captures the observer the controller makes, so a test can say the box changed. */
class ObserverStub {
  static latest: ObserverStub | null = null;
  readonly callback: () => void;
  constructor(callback: () => void) {
    this.callback = callback;
    ObserverStub.latest = this;
  }
  observe = (): void => undefined;
  unobserve = (): void => undefined;
  disconnect = (): void => undefined;
}

interface Mounted {
  controller: TerminalController;
  source: ReturnType<typeof makeSource>;
  context: FakeContext;
  container: HTMLElement;
  canvas: HTMLCanvasElement;
  input: HTMLTextAreaElement;
  sent: string[];
  callbacks: {
    onInput: ReturnType<typeof vi.fn>;
    onResize: ReturnType<typeof vi.fn>;
    onScrolled: ReturnType<typeof vi.fn>;
    onFocusChange: ReturnType<typeof vi.fn>;
    onCopied: ReturnType<typeof vi.fn>;
  };
}

function mount(width = 800, height = 340): Mounted {
  const context = installFakeCanvas();
  const container = document.createElement("div");
  const canvas = document.createElement("canvas");
  const input = document.createElement("textarea");
  container.append(canvas, input);
  document.body.append(container);
  sizeOf(container, width, height);
  const source = makeSource();
  const sent: string[] = [];
  const callbacks = {
    onInput: vi.fn((data: string) => sent.push(data)),
    onResize: vi.fn(),
    onScrolled: vi.fn(),
    onFocusChange: vi.fn(),
    onCopied: vi.fn(),
  };
  const controller = new TerminalController({ container, canvas, input }, source, callbacks);
  controller.start();
  return { controller, source, context, container, canvas, input, sent, callbacks };
}

function press(input: HTMLElement, key: string, modifiers: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...modifiers });
  input.dispatchEvent(event);
  return event;
}

function lines(count: number): string {
  return Array.from({ length: count }, (_, index) => `line ${String(index)}`).join("\r\n");
}

describe("TerminalController", () => {
  let mounted: Mounted;
  const original = window.ResizeObserver;

  beforeEach(() => {
    vi.useFakeTimers();
    // The test setup defines it as a plain writable property, which stubGlobal cannot redefine.
    window.ResizeObserver = ObserverStub as never;
    mounted = mount();
  });
  afterEach(() => {
    mounted.controller.stop();
    mounted.container.remove();
    vi.useRealTimers();
    window.ResizeObserver = original;
    ObserverStub.latest = null;
  });

  describe("size", () => {
    it("fits the grid to its box and tells the program", () => {
      expect(mounted.callbacks.onResize).toHaveBeenCalledWith(100, 20);
      expect(mounted.source.terminal.screen.cols).toBe(100);
      expect(mounted.source.terminal.screen.rows).toBe(20);
    });

    it("follows the box, once it has settled", () => {
      sizeOf(mounted.container, 400, 170);
      ObserverStub.latest?.callback();
      ObserverStub.latest?.callback();
      expect(mounted.callbacks.onResize).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(100);
      expect(mounted.callbacks.onResize).toHaveBeenLastCalledWith(50, 10);
      expect(mounted.source.terminal.screen.cols).toBe(50);
    });

    it("does not resize the program when the box did not change the grid", () => {
      ObserverStub.latest?.callback();
      vi.advanceTimersByTime(100);
      expect(mounted.callbacks.onResize).toHaveBeenCalledTimes(1);
    });

    it("waits for a box that has a size", () => {
      mounted.controller.stop();
      const empty = mount(0, 0);
      expect(empty.callbacks.onResize).not.toHaveBeenCalled();
      empty.controller.stop();
      empty.container.remove();
    });
  });

  describe("drawing", () => {
    it("draws what the program printed, on the next frame", () => {
      mounted.source.write("hello");
      vi.advanceTimersByTime(20);
      expect(mounted.context.texts().join("")).toContain("hello");
    });

    it("draws once for a burst of output", () => {
      vi.advanceTimersByTime(20);
      mounted.context.clear();
      mounted.source.write("a");
      mounted.source.write("b");
      mounted.source.write("c");
      vi.advanceTimersByTime(20);
      expect(mounted.context.texts().join("")).toBe("abc");
    });
  });

  describe("the keyboard", () => {
    it("sends control characters as a terminal does, and keeps the browser from acting on them", () => {
      const interrupt = press(mounted.input, "c", { ctrlKey: true });
      expect(mounted.sent).toEqual(["\u0003"]);
      expect(interrupt.defaultPrevented).toBe(true);
      press(mounted.input, "Enter");
      press(mounted.input, "Backspace");
      expect(mounted.sent).toEqual(["\u0003", "\r", "\u007f"]);
    });

    it("sends arrows in the form the program asked for", () => {
      press(mounted.input, "ArrowUp");
      mounted.source.write("\u001b[?1h");
      press(mounted.input, "ArrowUp");
      expect(mounted.sent).toEqual(["\u001b[A", "\u001bOA"]);
    });

    it("sends a printable key as it is typed", () => {
      const letter = press(mounted.input, "e");
      expect(letter.defaultPrevented).toBe(true);
      expect(mounted.sent).toEqual(["e"]);
      press(mounted.input, "é");
      expect(mounted.sent).toEqual(["e", "é"]);
    });

    it("lets text that has no key arrive through the text area, as a virtual keyboard sends it", () => {
      const unknown = press(mounted.input, "Unidentified");
      expect(unknown.defaultPrevented).toBe(false);
      mounted.input.value = "hola";
      mounted.input.dispatchEvent(new Event("input"));
      expect(mounted.sent).toEqual(["hola"]);
      expect(mounted.input.value).toBe("");
    });

    it("sends what an input method composed, whole, and not its keystrokes", () => {
      mounted.input.dispatchEvent(new CompositionEvent("compositionstart"));
      press(mounted.input, "Process");
      mounted.input.value = "に";
      mounted.input.dispatchEvent(new Event("input"));
      expect(mounted.sent).toEqual([]);
      mounted.input.dispatchEvent(new CompositionEvent("compositionend", { data: "日本" }));
      expect(mounted.sent).toEqual(["日本"]);
    });

    it("sends nothing once the session is over, and again when it is not", () => {
      mounted.controller.setDisabled(true);
      press(mounted.input, "Enter");
      expect(mounted.sent).toEqual([]);
      mounted.controller.setDisabled(false);
      press(mounted.input, "Enter");
      expect(mounted.sent).toEqual(["\r"]);
    });

    it("leaves with Escape and then Tab, which is the way out of a terminal that keeps Tab", () => {
      const alone = press(mounted.input, "Tab");
      expect(alone.defaultPrevented).toBe(true);
      expect(mounted.sent).toEqual(["\t"]);

      press(mounted.input, "Escape");
      const out = press(mounted.input, "Tab");
      // The escape reached the shell; the Tab did not, and the browser moves the focus.
      expect(out.defaultPrevented).toBe(false);
      expect(mounted.sent).toEqual(["\t", "\u001b"]);

      press(mounted.input, "Escape");
      vi.advanceTimersByTime(1_500);
      const late = press(mounted.input, "Tab");
      expect(late.defaultPrevented).toBe(true);
    });

    it("stops listening when it is stopped", () => {
      mounted.controller.stop();
      press(mounted.input, "Enter");
      expect(mounted.sent).toEqual([]);
    });
  });

  describe("the clipboard", () => {
    function paste(text: string): Event {
      const event = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "clipboardData", { value: { getData: () => text } });
      mounted.input.dispatchEvent(event);
      return event;
    }

    it("pastes what the browser delivers, and takes the paste over from the text area", () => {
      const event = paste("ls -l");
      expect(event.defaultPrevented).toBe(true);
      expect(mounted.sent).toEqual(["ls -l"]);
    });

    it("marks a paste as one when the shell asked to be told", () => {
      mounted.source.write("\u001b[?2004h");
      paste("rm -rf /\nstill typed");
      expect(mounted.sent[0]?.startsWith("\u001b[200~")).toBe(true);
      expect(mounted.sent[0]?.endsWith("\u001b[201~")).toBe(true);
    });

    it("never lets a paste end the bracket early", () => {
      mounted.source.write("\u001b[?2004h");
      paste("a\u001b[201~b");
      expect(mounted.sent[0]?.split("\u001b[201~")).toHaveLength(2);
    });

    it("does not paste into an ended session", () => {
      mounted.controller.setDisabled(true);
      paste("x");
      expect(mounted.sent).toEqual([]);
    });

    describe("copying", () => {
      let written: string[];
      beforeEach(() => {
        written = [];
        Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
        Object.defineProperty(navigator, "clipboard", {
          configurable: true,
          value: {
            writeText: (text: string) => {
              written.push(text);
              return Promise.resolve();
            },
          },
        });
      });

      it("copies the whole screen when nothing is selected, with Ctrl+Shift+C and not Ctrl+C", async () => {
        mounted.source.write("one\r\ntwo");
        const event = press(mounted.input, "C", { ctrlKey: true, shiftKey: true });
        expect(event.defaultPrevented).toBe(true);
        await vi.advanceTimersByTimeAsync(0);
        expect(written).toEqual(["one\ntwo"]);
        expect(mounted.callbacks.onCopied).toHaveBeenCalledWith("one\ntwo", true);
        // Ctrl+C is still an interrupt, never a copy.
        expect(mounted.sent).toEqual([]);
      });

      it("copies a word picked with a double click", async () => {
        mounted.source.write("alpha beta");
        // Cells are 8 by 17: x=44 is the column of "beta"'s first letters.
        mounted.canvas.dispatchEvent(new MouseEvent("pointerdown", { clientX: 52, clientY: 4, button: 0, detail: 2, bubbles: true }));
        press(mounted.input, "C", { ctrlKey: true, shiftKey: true });
        await vi.advanceTimersByTimeAsync(0);
        expect(written).toEqual(["beta"]);
      });

      it("says when the browser refused", async () => {
        Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: () => Promise.reject(new Error("no")) } });
        mounted.source.write("text");
        press(mounted.input, "C", { ctrlKey: true, shiftKey: true });
        await vi.advanceTimersByTimeAsync(0);
        expect(mounted.callbacks.onCopied).toHaveBeenCalledWith("text", false);
      });
    });
  });

  describe("scrolling back", () => {
    function wheel(deltaY: number): WheelEvent {
      const event = new WheelEvent("wheel", { deltaY, deltaMode: 1, bubbles: true, cancelable: true });
      mounted.container.dispatchEvent(event);
      return event;
    }

    it("goes back through the output with the wheel, and says how far", () => {
      mounted.source.write(lines(40));
      const event = wheel(-3);
      expect(event.defaultPrevented).toBe(true);
      expect(mounted.callbacks.onScrolled).toHaveBeenLastCalledWith(3);
      expect(mounted.controller.scrolledBack).toBe(3);
    });

    it("goes back by a page with Shift+Page Up, and to the newest with Shift+End", () => {
      mounted.source.write(lines(60));
      press(mounted.input, "PageUp", { shiftKey: true });
      expect(mounted.controller.scrolledBack).toBe(19);
      press(mounted.input, "End", { shiftKey: true });
      expect(mounted.controller.scrolledBack).toBe(0);
    });

    it("does not go back further than there is output", () => {
      mounted.source.write(lines(25));
      wheel(-100);
      expect(mounted.controller.scrolledBack).toBe(mounted.source.terminal.screen.scrollback.length);
    });

    it("keeps the view on the same lines while new output arrives", () => {
      mounted.source.write(lines(40));
      wheel(-3);
      mounted.source.write("\r\nmore\r\nmore");
      expect(mounted.controller.scrolledBack).toBe(5);
    });

    it("jumps to the newest output when something is typed", () => {
      mounted.source.write(lines(40));
      wheel(-3);
      press(mounted.input, "Enter");
      expect(mounted.controller.scrolledBack).toBe(0);
      expect(mounted.callbacks.onScrolled).toHaveBeenLastCalledWith(0);
    });

    it("turns the wheel into arrows for a full-screen program, which has no scrollback", () => {
      mounted.source.write("\u001b[?1049h");
      const event = wheel(2);
      expect(event.defaultPrevented).toBe(true);
      expect(mounted.sent).toEqual(["\u001b[B\u001b[B"]);
    });

    it("leaves the wheel to the page when there is nothing to scroll", () => {
      const event = wheel(3);
      expect(event.defaultPrevented).toBe(false);
    });

    it("can be told to go to the newest output", () => {
      mounted.source.write(lines(40));
      wheel(-3);
      mounted.controller.scrollToBottom();
      expect(mounted.controller.scrolledBack).toBe(0);
    });
  });

  describe("focus", () => {
    it("tells the page when the terminal has the keyboard", () => {
      mounted.input.dispatchEvent(new FocusEvent("focus"));
      expect(mounted.callbacks.onFocusChange).toHaveBeenLastCalledWith(true);
      mounted.input.dispatchEvent(new FocusEvent("blur"));
      expect(mounted.callbacks.onFocusChange).toHaveBeenLastCalledWith(false);
    });

    it("takes the keyboard when the box is clicked", () => {
      mounted.container.dispatchEvent(new MouseEvent("mousedown", { button: 0, bubbles: true }));
      expect(document.activeElement).toBe(mounted.input);
    });
  });
});
