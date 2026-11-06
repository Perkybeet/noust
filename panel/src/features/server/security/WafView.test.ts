import { describe, expect, it } from "vitest";

import { blocksToday, wafLevelOf } from "./WafView";

describe("the web firewall of a site", () => {
  it("reads the level a site's policy gives it, and takes anything it does not name for off", () => {
    expect(wafLevelOf("block")).toBe("block");
    expect(wafLevelOf("detect")).toBe("detect");
    expect(wafLevelOf("off")).toBe("off");
    expect(wafLevelOf("")).toBe("off");
    expect(wafLevelOf(null)).toBe("off");
    expect(wafLevelOf("paranoid")).toBe("off");
  });

  it("counts what the rules refused since the start of today, and not what they only wrote down or refused yesterday", () => {
    const now = new Date(2026, 10, 5, 15, 30);
    const today = new Date(2026, 10, 5, 9, 0).toISOString();
    const yesterday = new Date(2026, 10, 4, 23, 59).toISOString();
    expect(
      blocksToday(
        [
          { blocked: true, time: today },
          { blocked: true, time: today },
          { blocked: false, time: today },
          { blocked: true, time: yesterday },
          { blocked: true, time: "not a time" },
        ],
        now,
      ),
    ).toBe(2);
    expect(blocksToday([], now)).toBe(0);
  });
});
