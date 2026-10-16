/**
 * A language contributed as data is offered, previewed and chosen for people by different rules
 * (checklist M-63): released ones are in the switch and are matched from the browser's languages,
 * regional tag first; a draft is reachable only by choosing it, so nobody lands in a language that
 * is still being translated.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

// The setup file has already loaded the real registry (empty); a fresh module graph reads this one.
vi.doMock("../i18n/communityLocales", () => ({
  COMMUNITY_LOCALES: [
    { code: "pt", autonym: "Português", status: "released" },
    { code: "pt-BR", autonym: "Português do Brasil", status: "released" },
    { code: "fr", autonym: "Français", status: "draft" },
  ],
}));
vi.resetModules();

const { LOCALE_CHOICES, LOCALE_STORAGE_KEY, browserLocale, readLocale } = await import("./locale");
const { LOCALES } = await import("../i18n/types");

describe("a language that is only a file", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("is a known language, drafts included", () => {
    expect(LOCALES).toEqual(["en", "es", "pt", "pt-BR", "fr"]);
  });

  it("is offered in the switch under its own name once released, and a draft is not", () => {
    expect(LOCALE_CHOICES.map((choice) => [choice.value, choice.label])).toEqual([
      ["en", "English"],
      ["es", "Español"],
      ["pt", "Português"],
      ["pt-BR", "Português do Brasil"],
    ]);
  });

  it("is matched from the browser's languages, the regional translation before the general one", () => {
    expect(browserLocale(["pt-BR", "en"])).toBe("pt-BR");
    expect(browserLocale(["pt-br"])).toBe("pt-BR");
    expect(browserLocale(["pt-PT", "en"])).toBe("pt");
    expect(browserLocale(["es-MX", "pt"])).toBe("es");
  });

  it("is never chosen for somebody while it is a draft", () => {
    expect(browserLocale(["fr-FR", "fr"])).toBe("en");
  });

  it("can be previewed by choosing it, which is how a translator sees a draft", () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "fr");
    expect(readLocale()).toBe("fr");
  });

  it("is ignored when the stored choice names a language that does not exist", () => {
    vi.stubGlobal("navigator", { languages: ["pt"], language: "pt" });
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "klingon");
    expect(readLocale()).toBe("pt");
  });
});
