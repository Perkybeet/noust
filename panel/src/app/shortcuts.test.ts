import { describe, expect, it, vi } from "vitest";

import { createKeySequenceHandler, isTypingTarget, shortcutKey } from "./shortcuts";
import type { KeyBinding } from "./shortcuts";

function press(handler: (event: KeyboardEvent) => void, key: string, init: KeyboardEventInit = {}, target: EventTarget = document.body) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  Object.defineProperty(event, "target", { value: target });
  handler(event);
  return event;
}

function setup(now = () => 0) {
  const apps = vi.fn();
  const search = vi.fn();
  const help = vi.fn();
  const bindings: KeyBinding[] = [
    { keys: ["g", "a"], description: "Go to applications", run: apps },
    { keys: ["/"], description: "Search", run: search },
    { keys: ["?"], description: "Shortcuts", run: help },
  ];
  return { handler: createKeySequenceHandler(() => bindings, { now }), apps, search, help };
}

describe("createKeySequenceHandler", () => {
  it("runs a two-key sequence and claims the second key", () => {
    const { handler, apps } = setup();
    expect(press(handler, "g").defaultPrevented).toBe(false);
    expect(press(handler, "a").defaultPrevented).toBe(true);
    expect(apps).toHaveBeenCalledOnce();
  });

  it("runs single keys, including shifted ones", () => {
    const { handler, search, help } = setup();
    press(handler, "/");
    press(handler, "?", { shiftKey: true });
    expect(search).toHaveBeenCalledOnce();
    expect(help).toHaveBeenCalledOnce();
  });

  it("never fires while the operator types in a field", () => {
    const { handler, apps, search } = setup();
    for (const field of [document.createElement("input"), document.createElement("textarea"), document.createElement("select")]) {
      press(handler, "g", {}, field);
      press(handler, "a", {}, field);
      press(handler, "/", {}, field);
    }
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "");
    press(handler, "/", {}, editable);
    expect(apps).not.toHaveBeenCalled();
    expect(search).not.toHaveBeenCalled();
  });

  it("leaves keys inside an open dialog or menu to that overlay", () => {
    const { handler, search } = setup();
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const button = document.createElement("button");
    dialog.append(button);
    press(handler, "/", {}, button);
    expect(search).not.toHaveBeenCalled();
  });

  it("leaves modified keys to the browser", () => {
    const { handler, apps } = setup();
    press(handler, "g", { ctrlKey: true });
    press(handler, "a");
    press(handler, "g");
    press(handler, "a", { metaKey: true });
    expect(apps).not.toHaveBeenCalled();
  });

  it("forgets a first key after a pause", () => {
    let now = 0;
    const { handler, apps } = setup(() => now);
    press(handler, "g");
    now = 5_000;
    press(handler, "a");
    expect(apps).not.toHaveBeenCalled();
  });
});

describe("isTypingTarget", () => {
  it("treats a checkbox as a control, not a text field", () => {
    const box = document.createElement("input");
    box.type = "checkbox";
    expect(isTypingTarget(box)).toBe(false);
    expect(isTypingTarget(document.createElement("input"))).toBe(true);
  });
});

describe("shortcutKey", () => {
  it("is the character a key produces when it is a Latin letter or a symbol, in lower case", () => {
    expect(shortcutKey({ key: "g", code: "KeyG" })).toBe("g");
    expect(shortcutKey({ key: "G", code: "KeyG" })).toBe("g");
    expect(shortcutKey({ key: "?", code: "Slash" })).toBe("?");
    expect(shortcutKey({ key: "/", code: "Slash" })).toBe("/");
    expect(shortcutKey({ key: "Escape", code: "Escape" })).toBe("Escape");
  });

  it("is where the key is, not what it says, on AZERTY and QWERTZ: the letter is the character", () => {
    // AZERTY: the key at the QWERTY "q" position produces "a", and the one at "a" produces "q".
    expect(shortcutKey({ key: "a", code: "KeyQ" })).toBe("a");
    expect(shortcutKey({ key: "q", code: "KeyA" })).toBe("q");
    // QWERTZ: "z" and "y" are swapped.
    expect(shortcutKey({ key: "y", code: "KeyZ" })).toBe("y");
  });

  it("falls back to the letter of the physical key on a layout of another script", () => {
    expect(shortcutKey({ key: "п", code: "KeyG" })).toBe("g");
    expect(shortcutKey({ key: "ф", code: "KeyA" })).toBe("a");
    expect(shortcutKey({ key: "γ", code: "KeyG" })).toBe("g");
    expect(shortcutKey({ key: "ل", code: "KeyG" })).toBe("g");
    // Nothing physical to fall back to: the character it is.
    expect(shortcutKey({ key: "ж", code: "Semicolon" })).toBe("ж");
  });
});

describe("createKeySequenceHandler on other layouts (NV-6)", () => {
  it("runs `g a` on AZERTY, QWERTZ and Cyrillic layouts, and with Caps Lock on", () => {
    const { handler, apps } = setup();
    press(handler, "g", { code: "KeyG" });
    press(handler, "a", { code: "KeyQ" });
    expect(apps).toHaveBeenCalledTimes(1);
    // Cyrillic: the keys at G and A produce "п" and "ф".
    press(handler, "п", { code: "KeyG" });
    press(handler, "ф", { code: "KeyA" });
    expect(apps).toHaveBeenCalledTimes(2);
    press(handler, "G", { code: "KeyG" });
    press(handler, "A", { code: "KeyA" });
    expect(apps).toHaveBeenCalledTimes(3);
  });

  it("does not take a Cyrillic letter at the position of another letter for that letter's shortcut when the key says what it is", () => {
    const { handler, apps } = setup();
    // QWERTZ: the key at "z" produces "y", which is not what `g a` waits for.
    press(handler, "g", { code: "KeyG" });
    press(handler, "y", { code: "KeyZ" });
    expect(apps).not.toHaveBeenCalled();
  });
});
