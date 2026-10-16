import { describe, expect, it } from "vitest";

import { auditRegistry, check, completeness, expand, flattenEnglish, missing, placeholdersOf, pluralCategories, releaseBlocker, validCode } from "./community";
import type { CommunityLocale } from "./community";

const ENGLISH = new Map(
  flattenEnglish({
    nav: { apps: { label: "Applications" }, failed: { one: "{count} failed", other: "{count} failed" } },
    shell: { greeting: "Hello {name}, you have {count} apps" },
    time: { justNow: "just now" },
  }),
);

describe("English as the standard a contributed language is held to", () => {
  it("flattens namespaces to dotted keys and keeps plural forms whole", () => {
    expect([...ENGLISH.keys()]).toEqual(["nav.apps.label", "nav.failed", "shell.greeting", "time.justNow"]);
    expect(ENGLISH.get("nav.failed")).toEqual({ one: "{count} failed", other: "{count} failed" });
  });

  it("reads the placeholders of a message across all its forms", () => {
    expect(placeholdersOf("Hello {name}, you have {count} apps")).toEqual(["count", "name"]);
    expect(placeholdersOf({ one: "{count} app", other: "{count} apps of {owner}" })).toEqual(["count", "owner"]);
  });
});

describe("checking a contributed file", () => {
  it("accepts a good one, plural forms included, and what it leaves out", () => {
    const file = { "nav.apps.label": "Aplicações", "nav.failed": { one: "{count} falhou", other: "{count} falharam" } };
    expect(check(file, ENGLISH, "pt")).toEqual([]);
  });

  it("says a key English does not have is a typo or a renamed key", () => {
    const problems = check({ "nav.apps.labell": "x" }, ENGLISH, "pt");
    expect(problems.map((problem) => problem.key)).toEqual(["nav.apps.labell"]);
    expect(problems[0]?.message).toContain("English has no such key");
  });

  it("refuses empty text, so a half-filled file shows English instead of nothing", () => {
    expect(check({ "time.justNow": "   " }, ENGLISH, "pt")[0]?.message).toMatch(/empty/);
  });

  it("holds the placeholders exactly: Noust fills them in", () => {
    const problems = check({ "shell.greeting": "Olá {nome}, tem {count} apps" }, ENGLISH, "pt");
    expect(problems).toHaveLength(1);
    expect(problems[0]?.message).toContain("{count}, {nome}");
    expect(problems[0]?.message).toContain("{count}, {name}");
  });

  it("holds the shape: text where English has text, plural forms where it has them", () => {
    expect(check({ "nav.failed": "falhou" }, ENGLISH, "pt")[0]?.message).toMatch(/plural forms/);
    expect(check({ "time.justNow": { one: "a", other: "b" } }, ENGLISH, "pt")[0]?.message).toMatch(/plain text/);
  });

  it("requires one and other, and only the forms the language itself uses", () => {
    expect(check({ "nav.failed": { other: "{count} falharam" } }, ENGLISH, "pt")[0]?.message).toMatch(/"one" is missing/);
    // Japanese has no "one": a form it never selects would be a form nobody reads.
    expect(pluralCategories("ja")).toEqual(["other"]);
    expect(check({ "nav.failed": { one: "{count}", other: "{count}" } }, ENGLISH, "ja")[0]?.message).toMatch(/not one this language uses/);
    expect(pluralCategories("pt")).toContain("many");
    expect(check({ "nav.failed": { one: "{count}", many: "{count}", other: "{count}" } }, ENGLISH, "pt")).toEqual([]);
  });
});

describe("how much of English a file translates", () => {
  it("counts the keys it has, ignoring any English does not have", () => {
    expect(completeness({}, ENGLISH)).toBe(0);
    expect(completeness({ "nav.apps.label": "x", "time.justNow": "y", nothing: "z" }, ENGLISH)).toBe(0.5);
  });

  it("lists what is left, with what English says, so a translator knows where to go on", () => {
    expect(missing({ "nav.apps.label": "x" }, ENGLISH).map(([key]) => key)).toEqual(["nav.failed", "shell.greeting", "time.justNow"]);
  });

  it("blocks a release below 95%", () => {
    expect(releaseBlocker({ "nav.apps.label": "x" }, ENGLISH)).toMatch(/Only 25% of English is translated/);
    const all = Object.fromEntries([...ENGLISH].map(([key, message]) => [key, message]));
    expect(releaseBlocker(all, ENGLISH)).toBeNull();
  });
});

describe("expanding a file into the catalog the translator looks keys up in", () => {
  it("nests dotted keys and keeps plural forms as the typed catalogs shape them", () => {
    expect(expand({ "nav.apps.label": "Aplicações", "nav.failed": { one: "um", other: "{count}" }, "time.justNow": "agora" })).toEqual({
      nav: { apps: { label: "Aplicações" }, failed: { one: "um", other: "{count}" } },
      time: { justNow: "agora" },
    });
  });

  it("drops what is neither text nor plural forms instead of failing the page", () => {
    expect(expand({ "time.justNow": 3, "nav.apps.label": "ok" })).toEqual({ nav: { apps: { label: "ok" } } });
  });
});

describe("language codes", () => {
  it.each(["pt", "pt-BR", "fr", "de", "zh-CN"])("accepts %s", (code) => {
    expect(validCode(code)).toBe(true);
  });

  it.each(["en", "es", "PT", "portuguese", "pt_BR", "", "p"])("refuses %s", (code) => {
    expect(validCode(code)).toBe(false);
  });
});

describe("the registry and the files, held to each other", () => {
  const PT: CommunityLocale = { code: "pt", autonym: "Português", status: "draft" };
  const good = { "./community/pt.json": { "nav.apps.label": "Aplicações" } };

  it("is clean when each language has its file, a name and a valid file", () => {
    expect(auditRegistry([PT], good, ENGLISH)).toEqual([]);
    expect(auditRegistry([], {}, ENGLISH)).toEqual([]);
  });

  it("says a registered language without a file, and a file without a registration", () => {
    expect(auditRegistry([PT], {}, ENGLISH)[0]?.message).toMatch(/does not exist/);
    expect(auditRegistry([], good, ENGLISH)[0]?.message).toMatch(/nobody registered/);
  });

  it("refuses a bad code, a duplicate and a language with no name", () => {
    const problems = auditRegistry([{ code: "en", autonym: "English", status: "draft" }, PT, PT, { ...PT, code: "fr", autonym: " " }], { ...good, "./community/fr.json": {}, "./community/en.json": {} }, ENGLISH).map((problem) => problem.message);
    expect(problems.some((message) => message.includes("Is not a language code"))).toBe(true);
    expect(problems.some((message) => message.includes("registered twice"))).toBe(true);
    expect(problems.some((message) => message.includes("no autonym"))).toBe(true);
  });

  it("names the language in front of a file's problem", () => {
    const problems = auditRegistry([PT], { "./community/pt.json": { "time.justNow": "" } }, ENGLISH);
    expect(problems[0]?.key).toBe("pt: time.justNow");
  });

  it("refuses to release a language that is not nearly all translated", () => {
    const problems = auditRegistry([{ ...PT, status: "released" }], good, ENGLISH);
    expect(problems[0]?.message).toMatch(/Is released but Only 25%/);
  });
});
