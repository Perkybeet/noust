import { describe, expect, it } from "vitest";

import { readAllowedSources, recipesAllowed, withRecipes } from "./allowed";

describe("where code may come from", () => {
  it("reads the list from the configuration, and nothing from what is not a list", () => {
    expect(readAllowedSources({ security: { allowed_sources: ["github.com/acme", "local"] } })).toEqual(["github.com/acme", "local"]);
    expect(readAllowedSources({ security: {} })).toEqual([]);
    expect(readAllowedSources({ security: { allowed_sources: "github.com/acme" } })).toEqual([]);
    expect(readAllowedSources(null)).toEqual([]);
  });

  it("allows the recipes where there is no list, and where the list says recipes or local", () => {
    expect(recipesAllowed([])).toBe(true);
    expect(recipesAllowed(["github.com/acme"])).toBe(false);
    expect(recipesAllowed(["github.com/acme", "Recipes"])).toBe(true);
    expect(recipesAllowed(["local"])).toBe(true);
  });

  it("adds the word to the entries the server has, once, and keeps them in order", () => {
    expect(withRecipes(["github.com/acme"])).toEqual(["github.com/acme", "recipes"]);
    expect(withRecipes(["github.com/acme", "recipes"])).toEqual(["github.com/acme", "recipes"]);
    // `local` already allowed them, but the offer is for the word the catalog is named by.
    expect(withRecipes(["local"])).toEqual(["local", "recipes"]);
  });
});
