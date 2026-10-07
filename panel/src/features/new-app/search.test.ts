import { describe, expect, it } from "vitest";

import { validateNewAppSearch, wizardRecipe, wizardStart } from "./search";

describe("the wizard's search", () => {
  it("knows the three ways in besides its default", () => {
    expect(wizardStart("recipe")).toBe("recipe");
    expect(wizardStart("import")).toBe("import");
    expect(wizardStart("adopt")).toBe("adopt");
  });

  it("opens the default for anything else: a stale link, a guess, a repeated parameter", () => {
    for (const value of ["", "github", "manual", "RECIPE", "recipe,import", 1, true, null, undefined, ["recipe"], { start: "recipe" }]) {
      expect(wizardStart(value), JSON.stringify(value)).toBeUndefined();
    }
  });

  it("replaces a start it does not know with an explicit undefined, which is what the router needs to drop the raw one", () => {
    expect(validateNewAppSearch({ start: "recipe" })).toEqual({ start: "recipe" });
    const unknown = validateNewAppSearch({ start: "nonsense" });
    // Every key the route knows is named, so the router drops the raw value of any of them.
    expect(Object.keys(unknown)).toEqual(["start", "team", "project", "environment"]);
    expect(unknown.start).toBeUndefined();
  });

  it("names a recipe to open only by what a recipe's name can be", () => {
    expect(wizardRecipe("wordpress")).toBe("wordpress");
    expect(wizardRecipe("uptime-kuma")).toBe("uptime-kuma");
    for (const value of ["", "WordPress", "../etc", "a b", "-start", "x".repeat(41), 7, null, undefined, ["wordpress"]]) {
      expect(wizardRecipe(value), JSON.stringify(value)).toBeUndefined();
    }
  });

  it("carries the recipe a link names, and an explicit undefined for one that cannot be", () => {
    expect(validateNewAppSearch({ start: "recipe", recipe: "wordpress" })).toEqual({ start: "recipe", recipe: "wordpress" });
    expect(Object.keys(validateNewAppSearch({ start: "recipe" }))).toEqual(["start", "team", "project", "environment"]);
    const bad = validateNewAppSearch({ start: "recipe", recipe: "../etc" });
    expect(Object.keys(bad)).toEqual(["start", "team", "project", "environment", "recipe"]);
    expect(bad.recipe).toBeUndefined();
  });

  it("reads where the new application goes, from a project's page, and drops anything that is not a short name", () => {
    expect(validateNewAppSearch({ team: "acme", project: "web", environment: "staging" })).toEqual({ start: undefined, team: "acme", project: "web", environment: "staging" });
    const bad = validateNewAppSearch({ team: "Acme Corp", project: "../web", environment: 3 });
    expect(bad.team).toBeUndefined();
    expect(bad.project).toBeUndefined();
    expect(bad.environment).toBeUndefined();
  });
});
