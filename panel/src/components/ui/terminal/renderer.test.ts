import { beforeEach, describe, expect, it } from "vitest";

import { Selection } from "../../../lib/terminal/selection";
import { Terminal } from "../../../lib/terminal/terminal";
import { TerminalRenderer } from "./renderer";
import type { View } from "./renderer";
import { CELL, installFakeCanvas } from "./testing";
import type { FakeContext } from "./testing";
import type { TerminalTheme } from "./theme";

const THEME: TerminalTheme = {
  fg: "fg",
  bg: "bg",
  cursor: "cursor",
  cursorText: "cursor-text",
  selection: "selection",
  ansiFg: Array.from({ length: 16 }, (_, index) => `fg-${String(index)}`),
  ansiBg: Array.from({ length: 16 }, (_, index) => `bg-${String(index)}`),
};

function view(overrides: Partial<View> = {}): View {
  return { offset: 0, selection: new Selection(), focused: true, ...overrides };
}

function open(cols = 10, rows = 4): { renderer: TerminalRenderer; term: Terminal; context: FakeContext } {
  const context = installFakeCanvas();
  const canvas = document.createElement("canvas");
  const renderer = new TerminalRenderer(canvas, THEME, "monospace", 13);
  renderer.resize(cols, rows, 1);
  return { renderer, term: new Terminal(cols, rows), context };
}

describe("TerminalRenderer", () => {
  beforeEach(() => {
    installFakeCanvas();
  });

  describe("size", () => {
    it("measures a cell from the font", () => {
      const renderer = new TerminalRenderer(document.createElement("canvas"), THEME, "monospace", 13);
      expect(renderer.cellSize).toEqual(CELL);
    });

    it("says how many columns and rows fit in a box", () => {
      const renderer = new TerminalRenderer(document.createElement("canvas"), THEME, "monospace", 13);
      expect(renderer.fit(800, 340)).toEqual({ cols: 100, rows: 20 });
      // Never smaller than something that can show a prompt.
      expect(renderer.fit(3, 3)).toEqual({ cols: 2, rows: 1 });
    });

    it("sizes the canvas in device pixels and lays it out in CSS pixels", () => {
      const canvas = document.createElement("canvas");
      const renderer = new TerminalRenderer(canvas, THEME, "monospace", 13);
      renderer.resize(10, 4, 2);
      expect(canvas.width).toBe(10 * CELL.width * 2);
      expect(canvas.height).toBe(4 * CELL.height * 2);
      expect(canvas.style.width).toBe(`${String(10 * CELL.width)}px`);
      expect(canvas.style.height).toBe(`${String(4 * CELL.height)}px`);
    });

    it("finds the cell under a point, and none outside the grid", () => {
      const { renderer } = open(10, 4);
      expect(renderer.cellAt(17, 35)).toEqual({ col: 2, row: 2 });
      expect(renderer.cellAt(-1, 0)).toBeNull();
      expect(renderer.cellAt(10 * CELL.width, 0)).toBeNull();
    });

    it("measures with fixed metrics and draws nothing without a canvas", () => {
      const unused = installFakeCanvas();
      const canvas = document.createElement("canvas");
      canvas.getContext = (() => null) as never;
      const renderer = new TerminalRenderer(canvas, THEME, "monospace", 13);
      const term = new Terminal(10, 4);
      term.write("hi");
      renderer.draw(term.screen, view());
      expect(unused.calls).toHaveLength(0);
      expect(renderer.fit(80, 34).cols).toBeGreaterThan(0);
    });
  });

  describe("drawing", () => {
    it("draws each character at its cell, on the text's baseline", () => {
      const { renderer, term, context: drawn } = open();
      term.write("hi");
      renderer.draw(term.screen, view());
      expect(drawn.texts()).toEqual(["h", "i"]);
      const [h, i] = drawn.calls.filter((call) => call.op === "fillText");
      expect(h?.args[1]).toBe(0);
      expect(i?.args[1]).toBe(CELL.width);
      expect(h?.args[2]).toBe(i?.args[2]);
    });

    it("draws in the theme's text colour, and a colour a program chose in that colour", () => {
      const { renderer, term, context: drawn } = open();
      term.write("a\u001b[31mb\u001b[0m");
      renderer.draw(term.screen, view());
      const [a, b] = drawn.calls.filter((call) => call.op === "fillText");
      expect(a?.fillStyle).toBe("fg");
      // Red is the console's fail colour, the same red a log shows.
      expect(b?.fillStyle).toBe("fg-1");
    });

    it("draws a true colour as that colour", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[38;2;10;20;30mx");
      renderer.draw(term.screen, view());
      expect(drawn.calls.find((call) => call.op === "fillText")?.fillStyle).toBe("rgb(10 20 30)");
    });

    it("paints a ground only where it is not the default", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[42mg\u001b[0m ");
      renderer.draw(term.screen, view({ focused: false }));
      const painted = drawn.calls.filter((call) => call.op === "fillRect" && call.fillStyle === "bg-2");
      expect(painted).toHaveLength(1);
      expect(painted[0]?.args.slice(0, 2)).toEqual([0, 0]);
    });

    it("swaps text and ground for inverse, and the default ground becomes the text colour", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[7mx");
      renderer.draw(term.screen, view({ focused: false }));
      const letter = drawn.calls.find((call) => call.op === "fillText");
      expect(letter?.fillStyle).toBe("bg");
      expect(drawn.calls.some((call) => call.op === "fillRect" && call.fillStyle === "fg" && call.args[0] === 0)).toBe(true);
    });

    it("draws bold in a heavier face, and a wide character over two cells", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[1mB\u001b[0m中");
      renderer.draw(term.screen, view());
      const fonts = drawn.calls.filter((call) => call.op === "fillText").map((call) => call.font);
      expect(fonts[0]).toContain("600");
      expect(fonts[1]).toContain("400");
    });

    it("underlines with a rule under the text", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[4mu");
      renderer.draw(term.screen, view({ focused: false }));
      const rule = drawn.calls.find((call) => call.op === "fillRect" && call.args[3] === 1);
      expect(rule?.args[1]).toBe(CELL.height - 2);
    });

    it("dims text instead of recolouring it", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[2md");
      renderer.draw(term.screen, view());
      expect(drawn.calls.find((call) => call.op === "fillText")?.alpha).toBeLessThan(1);
    });

    it("draws nothing for text a program hid", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[8msecret");
      renderer.draw(term.screen, view());
      expect(drawn.texts()).toEqual([]);
    });

    it("draws the selection under the text", () => {
      const { renderer, term, context: drawn } = open();
      term.write("abc");
      const selection = new Selection();
      selection.set({ line: 0, col: 0 }, { line: 0, col: 1 });
      renderer.draw(term.screen, view({ selection }));
      expect(drawn.calls.filter((call) => call.op === "fillRect" && call.fillStyle === "selection").length).toBeGreaterThan(0);
    });
  });

  describe("the cursor", () => {
    it("is a filled block while the terminal has the keyboard", () => {
      const { renderer, term, context: drawn } = open();
      term.write("a");
      renderer.draw(term.screen, view({ focused: true }));
      expect(drawn.calls.some((call) => call.op === "fillRect" && call.fillStyle === "cursor" && call.args[0] === CELL.width)).toBe(true);
    });

    it("is an outline when it does not", () => {
      const { renderer, term, context: drawn } = open();
      renderer.draw(term.screen, view({ focused: false }));
      expect(drawn.calls.some((call) => call.op === "strokeRect")).toBe(true);
    });

    it("is not drawn when a program hid it", () => {
      const { renderer, term, context: drawn } = open();
      term.write("\u001b[?25l");
      renderer.draw(term.screen, view({ focused: true }));
      expect(drawn.calls.some((call) => call.fillStyle === "cursor")).toBe(false);
    });

    it("is not drawn while the view is scrolled back", () => {
      const { renderer, term, context: drawn } = open(10, 2);
      term.write("a\r\nb\r\nc\r\nd");
      renderer.draw(term.screen, view({ offset: 1 }));
      expect(drawn.calls.some((call) => call.fillStyle === "cursor")).toBe(false);
    });
  });

  describe("only what changed", () => {
    it("draws the rows that changed and the cursor's, not the whole grid", () => {
      const { renderer, term, context: drawn } = open(10, 6);
      renderer.draw(term.screen, view());
      drawn.clear();
      term.write("\u001b[4;1Hx");
      renderer.draw(term.screen, view());
      // Row 3 changed, and row 0 is where the cursor was.
      expect(drawn.rowsPainted().sort((a, b) => a - b)).toEqual([0, 3 * CELL.height]);
    });

    it("draws everything again when the view moves, the focus changes or the theme does", () => {
      const { renderer, term, context: drawn } = open(10, 3);
      renderer.draw(term.screen, view({ focused: true }));
      drawn.clear();
      renderer.draw(term.screen, view({ focused: false }));
      expect(drawn.rowsPainted()).toHaveLength(3);
      drawn.clear();
      renderer.setTheme({ ...THEME, bg: "other" });
      renderer.draw(term.screen, view({ focused: false }));
      expect(drawn.rowsPainted()).toHaveLength(3);
    });

    it("does not draw again when nothing changed", () => {
      const { renderer, term, context: drawn } = open();
      renderer.draw(term.screen, view());
      drawn.clear();
      renderer.draw(term.screen, view());
      expect(drawn.calls).toHaveLength(0);
    });
  });
});
