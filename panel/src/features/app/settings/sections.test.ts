import { describe, expect, it } from "vitest";

import { APP_SETTINGS_SCREENS } from "../../nav/app";
import { SETTINGS_GROUPS, SETTINGS_SECTIONS } from "./sections";

const ROOT = "/apps/$domain/settings";

describe("the subsections of an application's settings", () => {
  it("are the register's, in its order, so the side list, the palette and the icons are one list", () => {
    const drawn = SETTINGS_SECTIONS.slice(1);
    expect(drawn.map((section) => [section.path, section.label, section.group])).toEqual(APP_SETTINGS_SCREENS.map((screen) => [screen.to.slice(ROOT.length), screen.label, screen.group]));
    // An icon the register gives a destination reaches the list; none is drawn here.
    expect(drawn.map((section) => section.icon)).toEqual(APP_SETTINGS_SCREENS.map((screen) => screen.icon));
  });

  it("open with General, which has no group, and set Delete apart at the end", () => {
    const [first, ...rest] = SETTINGS_SECTIONS;
    expect(first).toMatchObject({ path: "", label: "appSettings.nav.general" });
    expect(first?.group).toBeUndefined();
    expect(SETTINGS_SECTIONS.at(-1)).toMatchObject({ path: "/delete", danger: true });
    expect(rest.filter((section) => section.danger === true)).toHaveLength(1);
  });

  it("are listed only under the groups the settings have", () => {
    for (const section of SETTINGS_SECTIONS.slice(1)) expect(SETTINGS_GROUPS).toContain(section.group);
  });
});
