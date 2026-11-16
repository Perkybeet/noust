import { describe, expect, it } from "vitest";

import { PHONE_BELOW, WIDE_FROM, layoutFor, tierOf } from "./navLayout";
import type { Preferences } from "./preferences";

const DEFAULTS: Preferences = { rail: "expanded", width: "comfortable" };

describe("the widths where the frame changes", () => {
  it("names the tier of a width, at the edges the design gives (1024 and 1200)", () => {
    expect(tierOf(390)).toBe("phone");
    expect(tierOf(1023)).toBe("phone");
    expect(tierOf(1024)).toBe("middle");
    expect(tierOf(1199)).toBe("middle");
    expect(tierOf(1200)).toBe("wide");
    expect(tierOf(1280)).toBe("wide");
    expect(tierOf(1366)).toBe("wide");
    expect(tierOf(1440)).toBe("wide");
    expect(tierOf(2560)).toBe("wide");
    expect([PHONE_BELOW, WIDE_FROM]).toEqual([1024, 1200]);
  });
});

describe("the layout the preferences and the width give", () => {
  it("is a phone below 1024 px: the areas at the foot, and the column a selector in the page", () => {
    expect(layoutFor(390, DEFAULTS)).toMatchObject({ phone: true, tier: "phone", overlay: false });
    expect(layoutFor(1023, DEFAULTS).phone).toBe(true);
    expect(layoutFor(1024, DEFAULTS).phone).toBe(false);
  });

  it("folds the main bar from 1024 to 1199 px and unfolds it above the content, never by use", () => {
    for (const width of [1024, 1100, 1199]) {
      expect(layoutFor(width, DEFAULTS), String(width)).toMatchObject({ rail: "folded", overlay: true, phone: false });
      // A saved "unfolded" is not what is on screen here: there is no room, and it opens over the page.
      expect(layoutFor(width, { ...DEFAULTS, rail: "expanded" }).rail, String(width)).toBe("folded");
    }
  });

  it("draws the bar with its words from 1200 px, so a laptop of 1280 or 1366 px reads it (item 157)", () => {
    for (const width of [1200, 1280, 1366, 1439, 1440, 1920]) {
      expect(layoutFor(width, DEFAULTS), String(width)).toMatchObject({ rail: "expanded", overlay: false, phone: false });
    }
  });

  it("gives the saved fold the last word from 1200 px: whoever wants icons folds the bar and the browser remembers", () => {
    for (const width of [1200, 1280, 1366, 1440, 1920]) {
      expect(layoutFor(width, { ...DEFAULTS, rail: "folded" }), String(width)).toMatchObject({ rail: "folded", overlay: false });
    }
  });

  it("has no presentation to choose: the frame is the same at every desktop width", () => {
    expect(Object.keys(layoutFor(1440, DEFAULTS)).sort()).toEqual(["overlay", "phone", "rail", "tier"]);
  });
});
