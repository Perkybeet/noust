import { describe, expect, it } from "vitest";

import {
  CATEGORIES,
  CATEGORY_LABELS,
  categoryLabelKey,
  dayOf,
  filtersOf,
  installedState,
  isCategory,
  isNarrowed,
  pageOf,
  previewNote,
  tagCounts,
  validateCatalogSearch,
  versionLabel,
} from "./model";
import type { RecipeSummary } from "../../api/queries/recipes";
import { popularRecipes } from "./popular";
import { VAULTWARDEN, WORDPRESS } from "./testFixtures";

describe("the catalogue's filters in the address", () => {
  it("keeps what is well formed and drops the rest instead of failing", () => {
    expect(validateCatalogSearch({ q: "  password manager ", category: "security", tag: "bitwarden", kind: "compose" })).toEqual({
      q: "password manager",
      category: "security",
      tag: "bitwarden",
      kind: "compose",
    });
    // A hand-edited or stale link still opens the catalogue, just less filtered.
    expect(validateCatalogSearch({ q: "", category: "Not A Category!", tag: "UPPER", kind: "everything" })).toEqual({});
    expect(validateCatalogSearch({ q: 5, category: ["ai"], tag: null, kind: undefined })).toEqual({});
  });

  it("cuts a search that is far longer than any name", () => {
    const search = validateCatalogSearch({ q: "x".repeat(500) });
    expect(search.q).toHaveLength(100);
  });

  it("knows whether anything narrows the list", () => {
    expect(isNarrowed({})).toBe(false);
    for (const search of [{ q: "a" }, { category: "ai" }, { tag: "ide" }, { kind: "native" as const }]) expect(isNarrowed(search)).toBe(true);
  });

  it("hands the server the filters it has, which are the only search there is", () => {
    expect(filtersOf({ q: "wiki", kind: "native" })).toEqual({ q: "wiki", category: undefined, tag: undefined, kind: "native" });
  });
});

describe("the closed list of categories", () => {
  it("has a word for each one, and names a category it does not know as the server wrote it", () => {
    for (const category of CATEGORIES) {
      expect(isCategory(category)).toBe(true);
      expect(categoryLabelKey(category)).toBe(CATEGORY_LABELS[category]);
    }
    expect(isCategory("networking")).toBe(false);
    expect(categoryLabelKey("networking")).toBeNull();
  });
});

describe("the tags the filter offers", () => {
  it("lists the most used first and then alphabetically, so the order never jumps", () => {
    const counts = tagCounts([{ tags: ["ide", "editor"] }, { tags: ["editor", "ai"] }, { tags: ["ai", "editor"] }, { tags: [] }]);
    expect(counts).toEqual([
      { tag: "editor", count: 3 },
      { tag: "ai", count: 2 },
      { tag: "ide", count: 1 },
    ]);
    expect(tagCounts([])).toEqual([]);
  });
});

describe("a page of the catalogue", () => {
  it("is the slice pageWindow numbers from 1", () => {
    expect(pageOf(["a", "b", "c", "d", "e"], 2, 4)).toEqual(["b", "c", "d"]);
    expect(pageOf(["a"], 0, 0)).toEqual([]);
  });
});

describe("a recipe's version and date", () => {
  it("prints the application's version, and the recipe's own revision only when it was fixed alone", () => {
    expect(versionLabel("1.37.4", 1)).toBe("v1.37.4");
    expect(versionLabel("1.37.4", 2)).toBe("v1.37.4-r2");
    // A version that is not numbers (a commit, a name) is not given a v.
    expect(versionLabel("2026.10", 1)).toBe("v2026.10");
    expect(versionLabel("stable", 1)).toBe("stable");
    expect(versionLabel(null, 1)).toBeNull();
    expect(versionLabel("", 3)).toBeNull();
    expect(versionLabel(undefined, undefined)).toBeNull();
  });

  it("reads a day as the reader's own, whatever their zone", () => {
    const day = dayOf("2026-10-06");
    expect(day?.getFullYear()).toBe(2026);
    expect(day?.getMonth()).toBe(9);
    expect(day?.getDate()).toBe(6);
    expect(dayOf("2026-10-06T23:59:00Z")?.getDate()).toBe(6);
    expect(dayOf("last week")).toBeNull();
    expect(dayOf(null)).toBeNull();
  });
});

describe("a recipe's notes before it is installed", () => {
  it("say what the operator will be told, with the address as a placeholder", () => {
    expect(previewNote("Open {{ url }}/wp-admin now, then run noust restart {{ domain }}.")).toBe("Open https://<domain>/wp-admin now, then run noust restart <domain>.");
    expect(previewNote("The token is {{ vars.ADMIN_TOKEN }}")).toBe("The token is <ADMIN_TOKEN>");
    expect(previewNote("Nothing to fill in.")).toBe("Nothing to fill in.");
  });
});

describe("where an installed recipe stands", () => {
  const behind = { update_available: true, reason: null, latest_version: "1.38.0" };
  const current = { update_available: false, reason: null, latest_version: "1.37.4" };
  const gone = { update_available: false, reason: "The recipe is no longer in the catalogue.", latest_version: null };
  const listedOnly = { update_available: false, reason: "Listed as unavailable", latest_version: "1.0" };

  it("is behind when the catalogue is ahead and can be deployed, current when it is the same, stuck when it cannot move", () => {
    expect(installedState(behind)).toBe("behind");
    expect(installedState(current)).toBe("current");
    expect(installedState(gone)).toBe("stuck");
    expect(installedState(listedOnly)).toBe("stuck");
    expect(installedState({ update_available: false, reason: null })).toBe("stuck");
  });
});

describe("the popular recipes", () => {
  const recipe = (name: string, patch: Partial<RecipeSummary> = {}): RecipeSummary => ({ ...VAULTWARDEN, name, title: name, available: true, ...patch });

  it("are the ones most people start with, in their order, then whatever else is available", () => {
    const items = [recipe("umami"), recipe("n8n"), { ...WORDPRESS, available: true }, recipe("uptime-kuma"), recipe("plausible"), recipe("ghost")];
    expect(popularRecipes(items).map((item) => item.name)).toEqual(["uptime-kuma", "ghost", "wordpress", "n8n"]);
  });

  it("leave out what this release cannot deploy and the demo, which is offered on its own", () => {
    const items = [recipe("hello"), recipe("ghost", { available: false }), recipe("umami"), recipe("plausible")];
    expect(popularRecipes(items).map((item) => item.name)).toEqual(["umami", "plausible"]);
  });

  it("are four at most, and none before the catalogue has been read", () => {
    expect(popularRecipes(Array.from({ length: 12 }, (_, index) => recipe(`app-${String(index)}`)))).toHaveLength(4);
    expect(popularRecipes(undefined)).toEqual([]);
  });
});
