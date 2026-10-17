import { afterEach, describe, expect, it } from "vitest";

import {
  CHROME,
  NAMESPACES,
  areNamespacesLoaded,
  catalogFor,
  catalogsIdle,
  dropNamespaces,
  hasNamespace,
  isCatalogLoaded,
  isNamespace,
  loadCatalog,
  loadNamespaces,
  loadedNamespaces,
} from "./catalogs";
import * as en from "./en";
import * as es from "./es";
import type { Catalog, Messages } from "./types";

/**
 * The real catalogs, split by namespace: what exists, what is bundled, what a chunk delivers. The
 * tests are warm (`setup.ts` loads English whole), so the ones that need a browser that has just
 * arrived forget what was loaded and put it back.
 */

afterEach(async () => {
  await catalogsIdle();
  await loadCatalog("en");
  // Spanish is only ever loaded by the test that wants it.
  dropNamespaces("es", []);
});

describe("the namespaces", () => {
  it("are the ones both barrels export, so a file nobody exported cannot be a chunk nobody can type", () => {
    expect([...NAMESPACES]).toEqual(Object.keys(en).sort());
    expect([...NAMESPACES]).toEqual(Object.keys(es).sort());
  });

  it("know which names are theirs", () => {
    expect(isNamespace("databases")).toBe(true);
    expect(isNamespace("zz")).toBe(false);
    expect(isNamespace("")).toBe(false);
  });
});

describe("the chrome", () => {
  it("is some namespaces of the console, a handful and not the catalog", () => {
    expect(CHROME.length).toBeGreaterThan(0);
    expect(CHROME.length).toBeLessThan(NAMESPACES.length / 4);
    expect(CHROME.every((namespace) => NAMESPACES.includes(namespace))).toBe(true);
    for (const namespace of ["common", "nav", "auth", "shell", "time"] as const) expect(CHROME).toContain(namespace);
  });

  it("is what English has before anything is fetched", () => {
    dropNamespaces("en", CHROME);
    expect(loadedNamespaces("en")).toEqual([...CHROME]);
    expect(isCatalogLoaded("en")).toBe(true);
    expect(isCatalogLoaded("es")).toBe(false);
  });
});

describe("a namespace's chunk", () => {
  it("delivers what the barrel exports, in each language", async () => {
    await loadCatalog("es");
    for (const namespace of NAMESPACES) {
      expect(catalogFor("en")?.[namespace]).toEqual(en[namespace]);
      expect(catalogFor("es")?.[namespace]).toEqual(es[namespace]);
    }
  });

  it("is fetched when asked for, and a language is ready to paint with only its chrome", async () => {
    dropNamespaces("en", CHROME);
    expect(hasNamespace("en", "databases")).toBe(false);
    expect(areNamespacesLoaded("en", ["databases", "common"])).toBe(false);
    await loadNamespaces("en", ["databases"]);
    expect(hasNamespace("en", "databases")).toBe(true);
    expect(areNamespacesLoaded("en", ["databases", "common"])).toBe(true);

    expect(isCatalogLoaded("es")).toBe(false);
    await loadNamespaces("es", [...CHROME]);
    expect(isCatalogLoaded("es")).toBe(true);
    expect(hasNamespace("es", "databases")).toBe(false);
  });

  it("brings a language up to the namespaces another one has", async () => {
    dropNamespaces("en", CHROME);
    await loadNamespaces("en", ["databases", "backups"]);
    await loadNamespaces("es", loadedNamespaces("en"));
    expect(loadedNamespaces("es")).toEqual(loadedNamespaces("en"));
  });
});

describe("the compiler", () => {
  it("holds a language to every namespace English has", () => {
    // The check `catalogs.ts` makes of the Spanish barrel, on a barrel missing one.
    type Complete<T extends Catalog<Messages>> = T;
    // @ts-expect-error: Spanish without a namespace English has is not a complete catalog.
    type Broken = Complete<Omit<typeof es, "databases">>;
    const proof: Broken | null = null;
    expect(proof).toBeNull();
  });
});
