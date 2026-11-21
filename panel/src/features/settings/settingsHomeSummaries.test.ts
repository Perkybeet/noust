import { beforeAll, describe, expect, it } from "vitest";

import { translate } from "../../i18n";
import { loadCatalog } from "../../i18n/catalogs";
import type { PlainKey } from "../../i18n";
import { SETTINGS_SCREENS, groupSettings } from "../nav/settings";

/**
 * A door of Settings is drawn from the register and its line is looked up by the section's name (`SettingsHome.tsx` builds the key
 * from data, so the compiler cannot check it): a section added to the register without its line drew the key itself, as text, on
 * the page every operator opens Settings on, for two sections of 4.3.
 */
const DOORS = groupSettings(SETTINGS_SCREENS).flatMap((group) => group.sections);
const keyOf = (id: string): PlainKey => `settingsHome.summary.${id.replace(/^settings\./, "")}` as PlainKey;

// Spanish is not here until it is loaded, and asked cold it answers with the English it falls back to: that would pass a missing line.
beforeAll(() => loadCatalog("es"));

describe("the line of every door of Settings", () => {
  it("reads every section of the register (so the next test is not about nothing)", () => {
    expect(DOORS.length).toBeGreaterThan(20);
  });

  it.each(DOORS.map((door) => [door.id] as const))("%s has its line in English and in Spanish, and neither is its own key", (id) => {
    const key = keyOf(id);
    for (const language of ["en", "es"] as const) {
      const line = translate(language, key);
      expect(line, `${key} in ${language}`).not.toBe(key);
      expect(line.trim(), `${key} in ${language}`).not.toBe("");
    }
    expect(translate("es", key), `${key} in es is the English of en`).not.toBe(translate("en", key));
  });
});
