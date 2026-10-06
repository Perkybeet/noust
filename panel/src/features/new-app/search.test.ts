import { describe, expect, it } from "vitest";

import { validateNewAppSearch, wizardStart } from "./search";

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
    expect(Object.keys(unknown)).toEqual(["start"]);
    expect(unknown.start).toBeUndefined();
  });
});
