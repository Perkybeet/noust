import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import {
  markFor,
  secrecyActionLabel,
  secrecyChoice,
  secrecyKind,
  secrecyLine,
  secrecyMarkAnnouncement,
  secrecyStateLabel,
} from "./secrecy";
import type { EnvSecrecy, SecrecyChoice } from "./secrecy";

function verdict(overrides: Partial<EnvSecrecy> = {}): EnvSecrecy {
  return { secret: false, reason: "plain", marked: false, ...overrides };
}

describe("secrecyLine", () => {
  it("names a value that matches a vendor's own token shape", () => {
    expect(secrecyLine(verdict({ secret: true, reason: "value: stripe" }))).toBe("Hidden: its value looks like a Stripe key");
    expect(secrecyLine(verdict({ secret: true, reason: "value: github token" }))).toBe("Hidden: its value looks like a GitHub token");
  });

  it("names an operator's own mark, in either direction", () => {
    expect(secrecyLine(verdict({ secret: true, reason: "marked secret", marked: true }))).toBe("Hidden: marked secret by you");
    expect(secrecyLine(verdict({ secret: false, reason: "marked not secret", marked: true }))).toBe("Shown: marked not secret by you");
  });

  it("names a name-only verdict", () => {
    expect(secrecyLine(verdict({ secret: true, reason: "name" }))).toBe("Hidden: its name suggests a secret");
  });

  it("names a credential embedded in a URL", () => {
    expect(secrecyLine(verdict({ secret: true, reason: "url credentials" }))).toBe("Hidden: the URL carries credentials");
  });

  it("names a plain value", () => {
    expect(secrecyLine(verdict({ secret: false, reason: "plain" }))).toBe("Shown: nothing about it looks like a secret");
  });

  it("shows an unrecognised reason verbatim rather than guessing", () => {
    expect(secrecyLine(verdict({ secret: true, reason: "something new" }))).toBe("Hidden: something new");
  });
});

describe("secrecyChoice / markFor", () => {
  it("reads automatic from an unmarked verdict", () => {
    expect(secrecyChoice(verdict({ marked: false }))).toBe("auto");
  });

  it("reads the operator's own choice from a marked verdict", () => {
    expect(secrecyChoice(verdict({ marked: true, secret: true }))).toBe("secret");
    expect(secrecyChoice(verdict({ marked: true, secret: false }))).toBe("not-secret");
  });

  it("round-trips through markFor", () => {
    const choices: readonly SecrecyChoice[] = ["secret", "not-secret", "auto"];
    expect(choices.map(markFor)).toEqual([true, false, null]);
  });
});

describe("secrecyStateLabel / secrecyActionLabel", () => {
  it("describes the three states and the three actions distinctly", () => {
    expect(secrecyStateLabel("secret")).toBe("always hidden");
    expect(secrecyStateLabel("not-secret")).toBe("always shown");
    expect(secrecyStateLabel("auto")).toBe("decided automatically");
    expect(secrecyActionLabel("secret")).toBe("Yes, always hide it");
    expect(secrecyActionLabel("not-secret")).toBe("No, always show it");
    expect(secrecyActionLabel("auto")).toBe("Decide automatically");
  });
});

describe("secrecyKind", () => {
  it("names the type, and in a few words what decided it", () => {
    expect(secrecyKind(verdict({ secret: true, reason: "name" }))).toEqual({ secret: true, label: "Secret", reason: "by its name" });
    expect(secrecyKind(verdict({ secret: true, reason: "value: stripe" }))).toEqual({ secret: true, label: "Secret", reason: "by its value" });
    expect(secrecyKind(verdict({ secret: false, reason: "marked not secret", marked: true }))).toEqual({ secret: false, label: "Plain", reason: "marked by you" });
    expect(secrecyKind(verdict({ secret: false, reason: "plain" }))).toEqual({ secret: false, label: "Plain", reason: null });
  });

  it("shows a reason it does not know verbatim", () => {
    expect(secrecyKind(verdict({ secret: true, reason: "entropy of 4.2 bits" })).reason).toBe("entropy of 4.2 bits");
  });
});

describe("secrecyMarkAnnouncement", () => {
  it("announces each direction of a mark, and clearing one", () => {
    expect(secrecyMarkAnnouncement("SESSION_SECRET", true)).toBe("SESSION_SECRET is now always hidden");
    expect(secrecyMarkAnnouncement("PUBLIC_KEY", false)).toBe("PUBLIC_KEY is now always shown");
    expect(secrecyMarkAnnouncement("API_URL", null)).toBe("API_URL is now classified automatically");
  });
});

describe("in Spanish", () => {
  it("says the same things in Spanish", async () => {
    await setLocale("es");
    expect(secrecyLine(verdict({ secret: true, reason: "value: stripe" }), "es")).toBe("Oculto: su valor parece una clave de Stripe");
    expect(secrecyLine(verdict({ secret: false, reason: "plain" }), "es")).toBe("Mostrado: nada en él parece un secreto");
    expect(secrecyStateLabel("secret", "es")).toBe("siempre oculto");
    expect(secrecyActionLabel("not-secret", "es")).toBe("No, mostrarlo siempre");
    expect(secrecyMarkAnnouncement("API_URL", null, "es")).toBe("API_URL ahora se clasifica automáticamente");
  });
});
