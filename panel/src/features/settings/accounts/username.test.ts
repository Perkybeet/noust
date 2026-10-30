import { describe, expect, it } from "vitest";

import { isUsername, suggestUsername } from "./username";

describe("the account name an email proposes", () => {
  it("is what is before the @, in lower case", () => {
    expect(suggestUsername("lucia.ferrer@example.com")).toBe("lucia.ferrer");
    expect(suggestUsername("Marta.Ibanez@Example.com")).toBe("marta.ibanez");
  });
  it("takes accents off and turns what the rule refuses into one dot", () => {
    expect(suggestUsername("Lucía Ferrer+ops@example.com")).toBe("lucia.ferrer.ops");
    expect(suggestUsername("ñandú__test@example.com")).toBe("nandu__test");
  });
  it("does not start or end with a separator, and is never longer than the server takes", () => {
    expect(suggestUsername("..a-b..@example.com")).toBe("a-b");
    expect(suggestUsername(`${"a".repeat(80)}@example.com`)).toHaveLength(64);
  });
  it("proposes nothing for what is not an address yet", () => {
    expect(suggestUsername("")).toBe("");
    expect(suggestUsername("@example.com")).toBe("");
  });
});

describe("a name the server takes", () => {
  it("is two to sixty-four letters, digits, dots, underscores and hyphens, starting with a letter or digit", () => {
    expect(isUsername("lucia.ferrer")).toBe(true);
    expect(isUsername("a")).toBe(false);
    expect(isUsername("-ab")).toBe(false);
    expect(isUsername("Juan Angel")).toBe(false);
    expect(isUsername("juan\n")).toBe(false);
  });
});
