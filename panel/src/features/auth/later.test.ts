import { beforeEach, describe, expect, it } from "vitest";

import { LATER_KEY, RETURNS_AFTER_MS, factorOffer, offerTo, putOff, returnOf, standing } from "./later";

const DAY = 24 * 60 * 60 * 1000;
const T0 = Date.UTC(2026, 10, 13, 9, 0, 0);

beforeEach(() => {
  window.localStorage.clear();
});

describe("what this browser was told later about (item 158)", () => {
  it("returns a week after it was put off, which is the 7 days the offer is held for", () => {
    expect(RETURNS_AFTER_MS).toBe(7 * DAY);
  });

  it("offers what was never put off, as the first sign-in does", () => {
    expect(standing("account", T0)).toBe("new");
    expect(offerTo("account", T0)).toBe(true);
    // Offering a new one spends nothing: it is made every time until it is put off.
    expect(offerTo("account", T0 + DAY)).toBe(true);
  });

  it("does not offer it again on every sign-in once it was put off", () => {
    putOff("account", T0);
    expect(standing("account", T0)).toBe("waiting");
    for (const days of [0, 1, 3, 6]) expect(offerTo("account", T0 + days * DAY), `${String(days)} days later`).toBe(false);
  });

  it("offers it once more a week later, and only once", () => {
    putOff("account", T0);
    expect(standing("account", T0 + 7 * DAY - 1)).toBe("waiting");
    expect(standing("account", T0 + 7 * DAY)).toBe("due");
    expect(offerTo("account", T0 + 7 * DAY)).toBe(true);
    // The return is spent as it is made: the next sign-in, a minute or a month on, does not make it again.
    expect(offerTo("account", T0 + 7 * DAY + 60_000)).toBe(false);
    expect(offerTo("account", T0 + 40 * DAY)).toBe(false);
    expect(standing("account", T0 + 40 * DAY)).toBe("spent");
  });

  it("does not bring it back a third time when it is put off again on its return", () => {
    putOff("account", T0);
    expect(offerTo("account", T0 + 8 * DAY)).toBe(true);
    putOff("account", T0 + 8 * DAY);
    expect(standing("account", T0 + 8 * DAY)).toBe("spent");
    expect(offerTo("account", T0 + 30 * DAY)).toBe(false);
    expect(offerTo("account", T0 + 400 * DAY)).toBe(false);
  });

  it("only returns what was put off: an account that never met the offer is not told about it at sign-in", () => {
    const offer = factorOffer("lucia");
    expect(returnOf(offer, T0)).toBe(false);
    expect(returnOf(offer, T0 + 30 * DAY)).toBe(false);
  });

  it("returns a second factor that was put off, a week later, once", () => {
    const offer = factorOffer("lucia");
    putOff(offer, T0);
    expect(returnOf(offer, T0 + 3 * DAY)).toBe(false);
    expect(returnOf(offer, T0 + 7 * DAY)).toBe(true);
    expect(returnOf(offer, T0 + 8 * DAY)).toBe(false);
  });

  it("keeps one account's second factor apart from another's on the same browser", () => {
    putOff(factorOffer("lucia"), T0);
    expect(returnOf(factorOffer("marcos"), T0 + 10 * DAY)).toBe(false);
    expect(returnOf(factorOffer("lucia"), T0 + 10 * DAY)).toBe(true);
    // And the first account's offer is a thing of its own.
    expect(standing("account", T0 + 10 * DAY)).toBe("new");
  });

  it("is kept in the browser the way the fold of the bar is: one key, readable after a reload", () => {
    putOff("account", T0);
    const stored = window.localStorage.getItem(LATER_KEY);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored ?? "{}")).toEqual({ account: { at: T0, returned: false } });
  });

  it("reads what is there defensively: damaged storage is as if nothing was put off", () => {
    for (const damaged of ["not json", "null", "[]", '{"account":"yesterday"}', '{"account":{"at":"x","returned":false}}', '{"account":{"at":1}}']) {
      window.localStorage.setItem(LATER_KEY, damaged);
      expect(standing("account", T0), damaged).toBe("new");
    }
  });

  it("never takes a username for a property of the record: whatever it holds, it is one entry and no path into the others", () => {
    putOff(factorOffer("__proto__"), T0);
    putOff(factorOffer("constructor"), T0);
    expect(standing("account", T0)).toBe("new");
    expect(Object.keys(JSON.parse(window.localStorage.getItem(LATER_KEY) ?? "{}") as Record<string, unknown>)).toEqual(["factor:__proto__", "factor:constructor"]);
  });

  it("works when the browser keeps nothing: the offer is made every time and nothing throws", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("storage is disabled");
      },
    });
    try {
      expect(() => {
        putOff("account", T0);
      }).not.toThrow();
      expect(offerTo("account", T0)).toBe(true);
      expect(returnOf(factorOffer("lucia"), T0)).toBe(false);
    } finally {
      if (original !== undefined) Object.defineProperty(window, "localStorage", original);
    }
  });
});
