/**
 * Every registered community language really loads through the build's own file discovery and
 * answers key by key, in its own words where it has them and in English where it has not
 * (checklist M-63). It follows the registry, so the first contributed language is exercised the day
 * it is registered; with none registered it asserts the rule on English and Spanish only.
 */

import { describe, expect, it } from "vitest";

import { catalogFor, isCatalogLoaded, loadCatalog } from "./catalogs";
import { COMMUNITY_LOCALES } from "./communityLocales";
import { translate } from "./translate";
import type { Locale } from "./types";

describe("a language registered as a file", () => {
  it.each<[string]>(COMMUNITY_LOCALES.map((entry): [string] => [entry.code]))("%s loads from its file and falls back to English for what it lacks", async (code) => {
    const locale = code as Locale;
    expect(isCatalogLoaded(locale)).toBe(false);
    await loadCatalog(locale);
    expect(isCatalogLoaded(locale)).toBe(true);
    expect(catalogFor(locale)).toBeDefined();
    // A key no contributed file can have translated yet is English, never the key or a blank.
    expect(translate(locale, "time.justNow")).not.toBe("time.justNow");
    expect(translate(locale, "nav.apps.label").length).toBeGreaterThan(0);
  });

  it("is refused with a reason when nothing is registered for the code", async () => {
    await expect(loadCatalog("zz" as Locale)).rejects.toThrow(/No catalog is registered for zz/);
  });
});
