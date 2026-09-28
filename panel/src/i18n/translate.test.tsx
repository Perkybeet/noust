import { render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { bindT, loadCatalog, translate, translateRich } from ".";
import type { MessageKey, PlainKey } from ".";
import * as en from "./en";

describe("translate", () => {
  beforeAll(async () => {
    await loadCatalog("es");
  });

  it("returns the text of a key in each language", () => {
    expect(translate("en", "nav.apps.label")).toBe("Applications");
    expect(translate("es", "nav.apps.label")).toBe("Aplicaciones");
  });

  it("fills placeholders", () => {
    expect(translate("en", "time.duration.hoursMinutes", { hours: 1, minutes: 12 })).toBe("1h 12m");
    expect(translate("es", "time.duration.hoursMinutes", { hours: 1, minutes: 12 })).toBe("1 h 12 min");
  });

  it("leaves a placeholder without a value visible instead of dropping it", () => {
    // Only reachable around the types, from a key built at runtime.
    const loose = translate as (locale: "en", key: string, params?: Record<string, string>) => string;
    expect(loose("en", "time.duration.hoursMinutes", { hours: "1" })).toBe("1h {minutes}m");
  });

  it("binds to a language for components", () => {
    const t = bindT("es");
    expect(t("nav.settings.label")).toBe("Ajustes");
    expect(t.locale).toBe("es");
  });

  it("renders elements in placeholders without building HTML", () => {
    render(<p>{translateRich("en", "time.duration.seconds", { value: <b>14</b> })}</p>);
    expect(screen.getByText("14").tagName).toBe("B");
    expect(screen.getByText("14").parentElement?.textContent).toBe("14s");
  });

  it("types keys and parameters from the English catalog", () => {
    const t = bindT("en");
    // @ts-expect-error: a key that does not exist is a compile error.
    t("nav.nowhere.label");
    // @ts-expect-error: a message with placeholders needs its parameters.
    t("time.duration.hoursMinutes");
    // @ts-expect-error: and all of them.
    t("time.duration.hoursMinutes", { hours: 1 });
    // @ts-expect-error: a plain message takes none.
    t("nav.apps.label", { count: 1 });
    const key: PlainKey = "nav.cron.label";
    expect(t(key)).toBe("Cron");
    const anyKey: MessageKey = "time.justNow";
    expect(anyKey).toBe("time.justNow");
  });
});

describe("plurals and fallbacks", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("picks the plural form with the language's rules, falling back to other", async () => {
    // A catalog with a plural, as a namespace would declare one.
    const catalogs = await import("./catalogs");
    const english = catalogs.catalogFor("en") as unknown as Record<string, unknown>;
    const spanish = catalogs.catalogFor("es") as unknown as Record<string, unknown>;
    english["zz"] = { apps: { one: "{count} application", other: "{count} applications" } };
    spanish["zz"] = { apps: { one: "{count} aplicación", other: "{count} aplicaciones" } };
    try {
      const loose = translate as (locale: "en" | "es", key: string, params: { count: number }) => string;
      expect(loose("en", "zz.apps", { count: 1 })).toBe("1 application");
      expect(loose("en", "zz.apps", { count: 3 })).toBe("3 applications");
      expect(loose("es", "zz.apps", { count: 1 })).toBe("1 aplicación");
      expect(loose("es", "zz.apps", { count: 0 })).toBe("0 aplicaciones");
      // CLDR says "many" for a million in Spanish; a catalog without it uses other.
      expect(loose("es", "zz.apps", { count: 1_000_000 })).toBe("1000000 aplicaciones");
    } finally {
      delete english["zz"];
      delete spanish["zz"];
    }
  });

  it("falls back to English for a key Spanish lacks, and says so once", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const catalogs = await import("./catalogs");
    const english = catalogs.catalogFor("en") as unknown as Record<string, unknown>;
    english["zz"] = { only: "Only in English" };
    try {
      const loose = translate as (locale: "es", key: string) => string;
      expect(loose("es", "zz.only")).toBe("Only in English");
      expect(loose("es", "zz.only")).toBe("Only in English");
      expect(warn).toHaveBeenCalledTimes(1);
    } finally {
      delete english["zz"];
    }
  });

  it("keeps the English catalog as the module exports it", () => {
    expect(Object.keys(en)).not.toContain("zz");
  });
});
