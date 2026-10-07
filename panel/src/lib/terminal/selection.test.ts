import { describe, expect, it } from "vitest";

import { Selection, lineAt, wordAt } from "./selection";
import { Terminal } from "./terminal";

function screenOf(output: string, cols = 20, rows = 4): Terminal {
  const term = new Terminal(cols, rows);
  term.write(output);
  return term;
}

describe("selecting text", () => {
  it("is empty until the pointer moves off its cell", () => {
    const selection = new Selection();
    expect(selection.empty).toBe(true);
    selection.start({ line: 0, col: 2 });
    expect(selection.empty).toBe(true);
    selection.extend({ line: 0, col: 2 });
    expect(selection.empty).toBe(true);
    selection.extend({ line: 0, col: 3 });
    expect(selection.empty).toBe(false);
  });

  it("reads the text between two points, in either direction", () => {
    const term = screenOf("hello world");
    const forward = new Selection();
    forward.set({ line: 0, col: 0 }, { line: 0, col: 4 });
    expect(forward.text(term.screen)).toBe("hello");
    const backward = new Selection();
    backward.set({ line: 0, col: 10 }, { line: 0, col: 6 });
    expect(backward.text(term.screen)).toBe("world");
  });

  it("joins lines with a newline and cuts the blanks at the end of each", () => {
    const term = screenOf("one\r\ntwo   \r\nthree");
    const selection = new Selection();
    selection.set({ line: 0, col: 0 }, { line: 2, col: 19 });
    expect(selection.text(term.screen)).toBe("one\ntwo\nthree");
  });

  it("does not break a line the window only wrapped", () => {
    const term = screenOf("abcdefghijklmno", 10, 3);
    const selection = new Selection();
    selection.set({ line: 0, col: 0 }, { line: 1, col: 4 });
    expect(selection.text(term.screen)).toBe("abcdefghijklmno");
  });

  it("reads wide characters once", () => {
    const term = screenOf("a世界b");
    const selection = new Selection();
    selection.set({ line: 0, col: 0 }, { line: 0, col: 5 });
    expect(selection.text(term.screen)).toBe("a世界b");
  });

  it("contains the cells between its ends, and none outside", () => {
    const selection = new Selection();
    selection.set({ line: 1, col: 3 }, { line: 2, col: 1 });
    expect(selection.contains(1, 2)).toBe(false);
    expect(selection.contains(1, 3)).toBe(true);
    expect(selection.contains(1, 19)).toBe(true);
    expect(selection.contains(2, 0)).toBe(true);
    expect(selection.contains(2, 1)).toBe(true);
    expect(selection.contains(2, 2)).toBe(false);
    expect(selection.contains(0, 5)).toBe(false);
    expect(selection.contains(3, 0)).toBe(false);
  });

  it("follows its text up the scrollback as output pushes it", () => {
    const term = screenOf("keep me\r\n2\r\n3\r\n4", 20, 3);
    const selection = new Selection();
    selection.set({ line: 0, col: 0 }, { line: 0, col: 6 });
    expect(selection.text(term.screen)).toBe("keep me");
    term.write("\r\n5\r\n6");
    expect(selection.text(term.screen)).toBe("keep me");
  });

  it("loses what the scrollback dropped, without a failure", () => {
    const term = new Terminal(20, 2, {}, 2);
    term.write("gone\r\n2\r\n3\r\n4\r\n5\r\n6");
    const selection = new Selection();
    selection.set({ line: 0, col: 0 }, { line: 0, col: 3 });
    expect(selection.text(term.screen)).toBe("");
  });

  it("selects the word under a point: letters, digits, paths and addresses", () => {
    const term = screenOf("cd /var/www/app.js now");
    const [from, to] = wordAt(term.screen, { line: 0, col: 8 });
    const selection = new Selection();
    selection.set(from, to);
    expect(selection.text(term.screen)).toBe("/var/www/app.js");
  });

  it("selects nothing wider than the point on a blank", () => {
    const term = screenOf("a b");
    expect(wordAt(term.screen, { line: 0, col: 1 })).toEqual([
      { line: 0, col: 1 },
      { line: 0, col: 1 },
    ]);
  });

  it("selects the line under a point", () => {
    const term = screenOf("first\r\nsecond");
    const [from, to] = lineAt(term.screen, { line: 1, col: 3 });
    const selection = new Selection();
    selection.set(from, to);
    expect(selection.text(term.screen)).toBe("second");
  });

  it("clears", () => {
    const selection = new Selection();
    selection.set({ line: 0, col: 0 }, { line: 0, col: 3 });
    selection.clear();
    expect(selection.empty).toBe(true);
    expect(selection.range()).toBeNull();
  });
});
