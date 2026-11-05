import { describe, expect, it, vi } from "vitest";

import { isOn, markOffered, notifyWhenHidden, shouldOffer, turnOn } from "./browserNotice";
import type { Browser, Permission } from "./browserNotice";

function browser(permission: Permission, over: Partial<Browser> = {}): Browser & { store: Map<string, string>; shown: [string, string][] } {
  const store = new Map<string, string>();
  const shown: [string, string][] = [];
  return {
    store,
    shown,
    storage: { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => void store.set(key, value) },
    permission: () => permission,
    ask: () => Promise.resolve(permission === "default" ? "granted" : permission),
    show: (title, body) => void shown.push([title, body]),
    hidden: () => true,
    ...over,
  };
}

describe("the browser's notification", () => {
  it("is offered once, and only to a browser that has not been asked", () => {
    const fresh = browser("default");
    expect(shouldOffer(fresh)).toBe(true);
    markOffered(fresh);
    expect(shouldOffer(fresh)).toBe(false);
    expect(shouldOffer(browser("granted"))).toBe(false);
    expect(shouldOffer(browser("denied"))).toBe(false);
    expect(shouldOffer(browser("unsupported"))).toBe(false);
  });

  it("is never on by itself: a permission the browser already gave is not the person's yes", () => {
    const granted = browser("granted");
    expect(isOn(granted)).toBe(false);
    expect(notifyWhenHidden("Deploy: done", "body", vi.fn(), granted)).toBe(false);
    expect(granted.shown).toEqual([]);
  });

  it("turns on when the person says yes and the browser agrees", async () => {
    const asked = browser("default");
    await expect(turnOn(asked)).resolves.toBe("granted");
    expect(asked.store.get("noust.tasks.notify")).toBe("on");
  });

  it("stays off when the browser refuses", async () => {
    const refused = browser("default", { ask: () => Promise.resolve("denied") });
    await expect(turnOn(refused)).resolves.toBe("denied");
    expect(refused.store.has("noust.tasks.notify")).toBe(false);
  });

  it("shows the notification only when it is on and the tab is hidden", async () => {
    const on = browser("default");
    await turnOn(on);
    // The permission the browser holds now is the one it gave.
    const granted: typeof on = { ...on, permission: () => "granted" };
    expect(notifyWhenHidden("Deploy: done", "You started it", vi.fn(), granted)).toBe(true);
    expect(granted.shown).toEqual([["Deploy: done", "You started it"]]);
    const visible: typeof on = { ...granted, hidden: () => false, shown: [] };
    expect(notifyWhenHidden("Deploy: done", "You started it", vi.fn(), visible)).toBe(false);
    expect(visible.shown).toEqual([]);
  });

  it("stops when the browser took the permission back", async () => {
    const on = browser("default");
    await turnOn(on);
    expect(isOn({ ...on, permission: () => "denied" })).toBe(false);
  });
});
