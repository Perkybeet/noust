/**
 * A 2D context that records what is drawn on it, for tests: jsdom has no canvas. It measures
 * every character as 8 pixels wide, so a line height of 17 (a 13px font at 1.3) gives cells of
 * 8 by 17 and a box of 800 by 340 holds 100 columns by 20 rows.
 */

import { vi } from "vitest";

import { Terminal } from "../../../lib/terminal/terminal";
import type { ScreenSource } from "./controller";

export interface DrawCall {
  op: "fillRect" | "fillText" | "strokeRect";
  /** The numbers or the text it was given. */
  args: (number | string)[];
  fillStyle: string;
  strokeStyle: string;
  font: string;
  alpha: number;
}

export const CELL = { width: 8, height: 17 } as const;

export class FakeContext {
  calls: DrawCall[] = [];
  font = "";
  fillStyle = "";
  strokeStyle = "";
  lineWidth = 1;
  globalAlpha = 1;
  transform: number[] = [];

  setTransform(...values: number[]): void {
    this.transform = values;
  }

  measureText(text: string): { width: number } {
    return { width: CELL.width * text.length };
  }

  private record(op: DrawCall["op"], args: (number | string)[]): void {
    this.calls.push({ op, args, fillStyle: this.fillStyle, strokeStyle: this.strokeStyle, font: this.font, alpha: this.globalAlpha });
  }

  fillRect(x: number, y: number, width: number, height: number): void {
    this.record("fillRect", [x, y, width, height]);
  }

  strokeRect(x: number, y: number, width: number, height: number): void {
    this.record("strokeRect", [x, y, width, height]);
  }

  fillText(text: string, x: number, y: number): void {
    this.record("fillText", [text, x, y]);
  }

  /** The characters drawn, in the order they were drawn. */
  texts(): string[] {
    return this.calls.filter((call) => call.op === "fillText").map((call) => String(call.args[0]));
  }

  /** The rows (by their top edge) that had their ground painted. */
  rowsPainted(): number[] {
    const tops = this.calls.filter((call) => call.op === "fillRect" && call.args[0] === 0).map((call) => Number(call.args[1]));
    return [...new Set(tops)];
  }

  clear(): void {
    this.calls = [];
  }
}

/** Gives every canvas this context; undone with the test's mocks. */
export function installFakeCanvas(): FakeContext {
  const context = new FakeContext();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((() => context) as never);
  return context;
}

/** A box of a given size: jsdom lays nothing out. */
export function sizeOf(element: HTMLElement, width: number, height: number): void {
  Object.defineProperty(element, "clientWidth", { configurable: true, value: width });
  Object.defineProperty(element, "clientHeight", { configurable: true, value: height });
}

/** An emulator and the way to tell its watchers it changed, as the page's session provides one. */
export function makeSource(cols = 80, rows = 24, onResponse?: (data: string) => void): ScreenSource & { notify: () => void; write: (data: string) => void } {
  const listeners = new Set<() => void>();
  const terminal = new Terminal(cols, rows, onResponse !== undefined ? { onResponse } : {});
  const notify = (): void => {
    for (const listener of listeners) listener();
  };
  return {
    terminal,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    notify,
    write: (data) => {
      terminal.write(data);
      notify();
    },
  };
}
