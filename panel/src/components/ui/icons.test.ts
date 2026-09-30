import { describe, expect, it } from "vitest";

import { ICON_SIZE, ICONS } from "./icons";

describe("semantic icons", () => {
  it("gives each meaning its own icon: one icon, one meaning", () => {
    const meanings = Object.entries(ICONS).filter(([name]) => name !== "dismiss");
    const icons = meanings.map(([, icon]) => icon);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("names the four severities a message can have", () => {
    for (const severity of ["info", "success", "warning", "error"] as const) expect(ICONS[severity]).toBeDefined();
  });

  it("offers the five token sizes and nothing between", () => {
    expect(Object.values(ICON_SIZE)).toEqual(["size-icon-xs", "size-icon-sm", "size-icon-md", "size-icon-lg", "size-icon-xl"]);
  });
});
