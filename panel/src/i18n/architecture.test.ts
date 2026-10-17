import { describe, expect, it } from "vitest";

import { CHROME } from "./catalogs";

/**
 * Nothing but the loader may import a catalog.
 *
 * The English catalog once reached the script every page loads first (a fifth of a megabyte of text
 * no first paint draws) because one file wrote `import * as en from "./en"`. The barrels are for the
 * compiler: `types.ts` reads the keys from them, and `import type` costs nothing. At run time the
 * texts come from `catalogs.ts`, a namespace at a time, and the only ones it imports for good are
 * the chrome. `scripts/i18n-split.test.mjs` holds the same line on the bundle itself; this one holds
 * it on every file of the console, so the next import of a barrel fails here, with its name.
 */

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**"], { eager: true, query: "?raw", import: "default" });

/** Production sources by path relative to src/ (the glob names them relative to this file). */
const SOURCES: ReadonlyMap<string, string> = new Map(Object.entries(RAW).map(([file, text]) => [new URL(file, "file:///src/i18n/architecture.test.ts").pathname.replace(/^\/src\//, ""), text]));

const LOADER = "i18n/catalogs.ts";

interface Import {
  readonly file: string;
  readonly specifier: string;
  /** Where it points, relative to src/ and without an extension. */
  readonly target: string;
  readonly dynamic: boolean;
}

/** Resolves a relative specifier against a file, both relative to src/ (a URL does the `..`). */
function resolve(file: string, specifier: string): string {
  return new URL(specifier, `file:///src/${file}`).pathname.replace(/^\/src\//, "");
}

/** The imports of a file that bring code (a type is erased) and point inside the console. */
function imports(file: string, source: string): Import[] {
  const found: Import[] = [];
  // Comments talk about imports ("typeof import("./en")"); only code brings one.
  const text = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:'"`])\/\/.*$/gm, "$1");
  const add = (specifier: string | undefined, dynamic: boolean): void => {
    if (specifier?.startsWith(".") === true) found.push({ file, specifier, target: resolve(file, specifier), dynamic });
  };
  for (const match of text.matchAll(/\b(?:import|export)\s+(type\s+)?[^;'"]*?from\s+"([^"]+)"/g)) {
    if (match[1] === undefined) add(match[2], false);
  }
  for (const match of text.matchAll(/\bimport\(\s*"([^"]+)"\s*\)/g)) add(match[1], true);
  for (const match of text.matchAll(/\bimport\s+"([^"]+)"/g)) add(match[1], false);
  return found;
}

/** Whether a path is a catalog: a language's barrel or one of its namespaces. */
function isCatalog(target: string): boolean {
  return /^i18n\/(en|es)(\/[A-Za-z0-9]+)?$/.test(target);
}

describe("who imports a catalog", () => {
  // The catalogs themselves are not importers: a barrel exports its own namespaces.
  const all = [...SOURCES]
    .filter(([file]) => !/^i18n\/(en|es)\//.test(file))
    .flatMap(([file, text]) => imports(file, text))
    .filter((entry) => isCatalog(entry.target));

  it("reads the whole console", () => {
    expect(SOURCES.size).toBeGreaterThan(500);
    expect(SOURCES.has(LOADER)).toBe(true);
  });

  it("is the loader, and nobody else", () => {
    const strangers = all.filter((entry) => entry.file !== LOADER).map((entry) => `${entry.file} imports ${entry.specifier}`);
    expect(strangers).toEqual([]);
  });

  it("imports for good only the chrome, in English, and never a barrel", () => {
    const own = all.filter((entry) => entry.file === LOADER && !entry.dynamic);
    expect(own.map((entry) => entry.target).sort()).toEqual(CHROME.map((namespace) => `i18n/en/${namespace}`));
  });

  it("reaches every other namespace through the build's own file discovery, a chunk each", () => {
    const text = SOURCES.get(LOADER) ?? "";
    expect(text).toContain('import.meta.glob<NamespaceModule>(["./es/*.ts", "!./es/index.ts"])');
    // English leaves out exactly the chrome: a module both imported and `import()`ed gets a chunk of its own.
    const left = /import\.meta\.glob<NamespaceModule>\(\["\.\/en\/\*\.ts", "!\.\/en\/index\.ts", "!\.\/en\/\{([^}]*)\}\.ts"\]\)/.exec(text)?.[1];
    expect(left?.split(",").sort()).toEqual([...CHROME]);
  });

  it("would see a catalog import if there were one", () => {
    // A guard on the test itself: the same scan, over text that does import one, finds it.
    const probe = imports("features/x/Page.tsx", 'import * as en from "../../i18n/en";\nimport type * as ok from "../../i18n/es";\nconst load = () => import("../../i18n/es/apps");');
    expect(probe.map((entry) => [entry.target, entry.dynamic])).toEqual([
      ["i18n/en", false],
      ["i18n/es/apps", true],
    ]);
    expect(isCatalog("i18n/en")).toBe(true);
    expect(isCatalog("i18n/en/databases")).toBe(true);
    expect(isCatalog("i18n/community/pt")).toBe(false);
    expect(isCatalog("i18n/communityLocales")).toBe(false);
  });
});
