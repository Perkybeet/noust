import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { bindT } from "../../../i18n/useT";
import { componentName, dependentWords, dependentsSentence, namesOf, neverReason, purposeOf } from "./words";

const en = bindT("en");

describe("what depends on a component, in words", () => {
  it("counts by kind, in the singular and the plural", () => {
    expect(dependentWords(en, { kind: "site", names: ["a.example.com"] })).toBe("1 site");
    expect(dependentWords(en, { kind: "tunnel", names: ["oficina", "nas-casa"] })).toBe("2 tunnels");
    expect(dependentWords(en, { kind: "published_port", names: ["25565"] })).toBe("1 published port");
  });

  it("says it in Spanish with the plural Spanish has", async () => {
    await setLocale("es");
    const es = bindT("es");
    expect(dependentWords(es, { kind: "tunnel", names: ["a", "b"] })).toBe("2 túneles");
    expect(dependentWords(es, { kind: "database", names: ["a", "b", "c"] })).toBe("3 bases de datos");
    expect(dependentWords(es, { kind: "site", names: ["a"] })).toBe("1 sitio");
  });

  it("says an engine that could not be asked as what it is, and a kind it has no words for by the name it came with", () => {
    expect(dependentWords(en, { kind: "unreadable", names: ["postgresql"] })).toBe("Cannot tell what it holds");
    expect(dependentWords(en, { kind: "spaceship", names: ["a", "b"] })).toBe("spaceship: 2");
  });

  it("puts every kind in one list", () => {
    expect(dependentsSentence(en, [{ kind: "site", names: ["a", "b", "c", "d"] }, { kind: "certificate", names: ["a", "b"] }])).toBe("4 sites and 2 certificates");
  });

  it("lists the first names and counts the rest", () => {
    expect(namesOf(["a", "b", "c"], 2)).toEqual({ shown: ["a", "b"], more: 1 });
    expect(namesOf(["a"], 8)).toEqual({ shown: ["a"], more: 0 });
  });
});

describe("a component's name and what it is for", () => {
  it("is the console's own for a known one and the id for one it has never heard of", async () => {
    await setLocale("es");
    expect(componentName(en, "nginx_stream_module")).toBe("nginx stream module");
    expect(componentName(bindT("es"), "nginx_stream_module")).toBe("Módulo stream de nginx");
    expect(componentName(en, "brand_new")).toBe("brand_new");
  });

  it("is the server's sentence, by its code, with the English it sent beside", () => {
    expect(purposeOf({ purpose: "Keeps the clock right", purpose_code: "software.chrony.purpose" })).toEqual({
      coded: { code: "software.chrony.purpose", params: {} },
      text: "Keeps the clock right",
    });
  });

  it("finds why a piece is never removed in the server's sentences, and says nothing for one it has none for", async () => {
    await setLocale("es");
    expect(neverReason("en", "sshd")).toBe("Removing it would lock you out of this machine");
    expect(neverReason("es", "sshd")).toBe("Quitarlo te dejaría sin acceso a esta máquina");
    expect(neverReason("en", "nginx")).toBeNull();
    expect(neverReason("en", "brand_new")).toBeNull();
  });
});
