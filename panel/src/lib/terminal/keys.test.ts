import { describe, expect, it } from "vitest";

import { encodeKey } from "./keys";
import type { KeyInput } from "./keys";

function press(key: string, held: Partial<Omit<KeyInput, "key">> = {}, application = false): string | null {
  return encodeKey(
    { key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...held },
    { applicationCursor: application },
  );
}

describe("what a key sends", () => {
  it("sends printable characters as they are", () => {
    expect(press("a")).toBe("a");
    expect(press("A", { shiftKey: true })).toBe("A");
    expect(press("ñ")).toBe("ñ");
    expect(press("世")).toBe("世");
    expect(press("€")).toBe("€");
    expect(press(" ")).toBe(" ");
  });

  it("sends Enter as a carriage return, Backspace as DEL, Tab as tab, Escape as escape", () => {
    expect(press("Enter")).toBe("\r");
    expect(press("Backspace")).toBe("\x7f");
    expect(press("Tab")).toBe("\t");
    expect(press("Escape")).toBe("\x1b");
  });

  it("sends Shift+Tab as back tab", () => {
    expect(press("Tab", { shiftKey: true })).toBe("\x1b[Z");
  });

  it("sends the arrows, and their application forms once the program asked", () => {
    expect(press("ArrowUp")).toBe("\x1b[A");
    expect(press("ArrowDown")).toBe("\x1b[B");
    expect(press("ArrowRight")).toBe("\x1b[C");
    expect(press("ArrowLeft")).toBe("\x1b[D");
    expect(press("ArrowUp", {}, true)).toBe("\x1bOA");
    expect(press("ArrowLeft", {}, true)).toBe("\x1bOD");
    expect(press("Home")).toBe("\x1b[H");
    expect(press("End")).toBe("\x1b[F");
    expect(press("Home", {}, true)).toBe("\x1bOH");
    expect(press("End", {}, true)).toBe("\x1bOF");
  });

  it("puts the modifier into the sequence: shift 2, alt 3, ctrl 5, ctrl+shift 6, ctrl+alt 7", () => {
    expect(press("ArrowRight", { shiftKey: true })).toBe("\x1b[1;2C");
    expect(press("ArrowRight", { altKey: true })).toBe("\x1b[1;3C");
    expect(press("ArrowRight", { ctrlKey: true })).toBe("\x1b[1;5C");
    expect(press("ArrowRight", { ctrlKey: true, shiftKey: true })).toBe("\x1b[1;6C");
    expect(press("ArrowRight", { ctrlKey: true, altKey: true })).toBe("\x1b[1;7C");
    // A modifier wins over application mode: the program asked for plain arrows.
    expect(press("ArrowRight", { ctrlKey: true }, true)).toBe("\x1b[1;5C");
  });

  it("sends the editing keys", () => {
    expect(press("Insert")).toBe("\x1b[2~");
    expect(press("Delete")).toBe("\x1b[3~");
    expect(press("PageUp")).toBe("\x1b[5~");
    expect(press("PageDown")).toBe("\x1b[6~");
    expect(press("Delete", { ctrlKey: true })).toBe("\x1b[3;5~");
  });

  it("sends the function keys", () => {
    expect(press("F1")).toBe("\x1bOP");
    expect(press("F2")).toBe("\x1bOQ");
    expect(press("F3")).toBe("\x1bOR");
    expect(press("F4")).toBe("\x1bOS");
    expect(press("F5")).toBe("\x1b[15~");
    expect(press("F6")).toBe("\x1b[17~");
    expect(press("F7")).toBe("\x1b[18~");
    expect(press("F8")).toBe("\x1b[19~");
    expect(press("F9")).toBe("\x1b[20~");
    expect(press("F10")).toBe("\x1b[21~");
    expect(press("F11")).toBe("\x1b[23~");
    expect(press("F12")).toBe("\x1b[24~");
    expect(press("F1", { shiftKey: true })).toBe("\x1b[1;2P");
    expect(press("F5", { ctrlKey: true })).toBe("\x1b[15;5~");
  });

  it("sends Ctrl+letter as the control character", () => {
    expect(press("c", { ctrlKey: true })).toBe("\x03");
    expect(press("C", { ctrlKey: true })).toBe("\x03");
    expect(press("d", { ctrlKey: true })).toBe("\x04");
    expect(press("z", { ctrlKey: true })).toBe("\x1a");
    expect(press("l", { ctrlKey: true })).toBe("\x0c");
    expect(press("a", { ctrlKey: true })).toBe("\x01");
  });

  it("sends the control characters of the punctuation", () => {
    expect(press("[", { ctrlKey: true })).toBe("\x1b");
    expect(press("\\", { ctrlKey: true })).toBe("\x1c");
    expect(press("]", { ctrlKey: true })).toBe("\x1d");
    expect(press("^", { ctrlKey: true })).toBe("\x1e");
    expect(press("_", { ctrlKey: true })).toBe("\x1f");
    expect(press("@", { ctrlKey: true })).toBe("\x00");
    expect(press(" ", { ctrlKey: true })).toBe("\x00");
    expect(press("?", { ctrlKey: true })).toBe("\x7f");
  });

  it("sends Alt+key as the key after an escape", () => {
    expect(press("b", { altKey: true })).toBe("\x1bb");
    expect(press("f", { altKey: true })).toBe("\x1bf");
    expect(press("Backspace", { altKey: true })).toBe("\x1b\x7f");
    expect(press("Enter", { altKey: true })).toBe("\x1b\r");
    expect(press("x", { altKey: true, ctrlKey: true })).toBe("\x1b\x18");
  });

  it("sends Ctrl+Backspace as backspace", () => {
    expect(press("Backspace", { ctrlKey: true })).toBe("\x08");
  });

  it("leaves Ctrl+Shift+C and Ctrl+Shift+V to the console: copy and paste", () => {
    expect(press("C", { ctrlKey: true, shiftKey: true })).toBeNull();
    expect(press("V", { ctrlKey: true, shiftKey: true })).toBeNull();
    // Ctrl+C alone stays an interrupt.
    expect(press("c", { ctrlKey: true })).toBe("\x03");
  });

  it("leaves what is the browser's or the system's: Cmd+key, and bare modifiers", () => {
    expect(press("c", { metaKey: true })).toBeNull();
    expect(press("v", { metaKey: true })).toBeNull();
    for (const key of ["Shift", "Control", "Alt", "Meta", "CapsLock", "Dead", "Unidentified", "AltGraph"]) {
      expect(press(key)).toBeNull();
    }
  });

  it("sends nothing for a key it has no meaning for", () => {
    expect(press("ContextMenu")).toBeNull();
    expect(press("MediaPlayPause")).toBeNull();
    expect(press("1", { ctrlKey: true })).toBeNull();
  });
});
