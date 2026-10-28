import { describe, expect, it } from "vitest";

import { RAIL_FOLDED_ABOVE, SINGLE_BELOW, WIDE_FROM, layoutFor, tierOf } from "./navLayout";
import type { Preferences } from "./preferences";

const DEFAULTS: Preferences = { presentation: "double", rail: "expanded", width: "comfortable" };

describe("the widths where the frame changes", () => {
  it("names the tier of a width, at the edges the design gives (1024, 1280, 1440)", () => {
    expect(tierOf(390)).toBe("phone");
    expect(tierOf(1023)).toBe("phone");
    expect(tierOf(1024)).toBe("narrow");
    expect(tierOf(1279)).toBe("narrow");
    expect(tierOf(1280)).toBe("middle");
    expect(tierOf(1439)).toBe("middle");
    expect(tierOf(1440)).toBe("wide");
    expect(tierOf(2560)).toBe("wide");
    expect([SINGLE_BELOW, RAIL_FOLDED_ABOVE, WIDE_FROM]).toEqual([1280, 1280, 1440]);
  });
});

describe("the layout the preferences and the width give", () => {
  it("is one bar below 1280 px whatever the preference says: two columns need about 1410 px", () => {
    for (const width of [390, 1024, 1279]) {
      expect(layoutFor(width, DEFAULTS).presentation, `${String(width)}`).toBe("single");
    }
  });

  it("is a phone below 1024 px: a bar at the foot and the menu in a drawer", () => {
    expect(layoutFor(390, DEFAULTS)).toMatchObject({ phone: true, presentation: "single" });
    expect(layoutFor(1024, DEFAULTS).phone).toBe(false);
  });

  it("folds the main bar from 1280 to 1439 px and unfolds it above the content, never by use", () => {
    const layout = layoutFor(1366, DEFAULTS);
    expect(layout).toMatchObject({ presentation: "double", rail: "folded", overlay: true });
    // A saved "unfolded" is not what is on screen here: there is no room, and it opens over the page.
    expect(layoutFor(1366, { ...DEFAULTS, rail: "expanded" }).rail).toBe("folded");
  });

  it("gives the preference the last word from 1440 px", () => {
    expect(layoutFor(1440, DEFAULTS)).toMatchObject({ presentation: "double", rail: "expanded", overlay: false });
    expect(layoutFor(1440, { ...DEFAULTS, rail: "folded" })).toMatchObject({ presentation: "double", rail: "folded", overlay: false });
    expect(layoutFor(1920, { ...DEFAULTS, presentation: "single" })).toMatchObject({ presentation: "single" });
  });

  it("honours a saved one bar from 1280 px, and then has no rail to fold", () => {
    const layout = layoutFor(1366, { ...DEFAULTS, presentation: "single" });
    expect(layout).toMatchObject({ presentation: "single", overlay: false });
    expect(layout.rail).toBe("expanded");
  });
});
