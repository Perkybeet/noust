/**
 * One implementation of each thing (CONTRIBUTING, rule 3), for the three the console had twice: the debounce, the question
 * "may this account do that", and the dialog that opens a port. A copy is cheap to write and costs a fix made in one place
 * and not the other, so the sources are read for a second definition and the test fails on it, naming where it is.
 */

import { describe, expect, it } from "vitest";

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../dev/**", "!../test/**", "!../**/*.gen.ts"], {
  eager: true,
  query: "?raw",
  import: "default",
});

/** Production sources by path relative to src/. */
const SOURCES: ReadonlyMap<string, string> = new Map(Object.entries(RAW).map(([path, source]) => [path.replace(/^\.\.\//, "").replace(/^\.\//, "lib/"), source]));

/** The files that declare a function or a constant of this name, whatever the form. */
function declaring(name: string): string[] {
  const declaration = new RegExp(`(?:function\\s+${name}\\b|(?:const|let)\\s+${name}\\s*[=:])`);
  return [...SOURCES].filter(([, source]) => declaration.test(source)).map(([path]) => path);
}

describe("one implementation of each thing", () => {
  it("has the sources it reads", () => {
    expect(SOURCES.size).toBeGreaterThan(500);
    expect(SOURCES.has("lib/useDebounced.ts")).toBe(true);
  });

  it("debounces a value in one hook: the one in lib/, which the wizard's port dialog and a database's Connect both read", () => {
    expect(declaring("useDebounced")).toEqual(["lib/useDebounced.ts"]);
  });

  it("asks whether the account holds a permission in one hook, the kit's, whether for one permission or for a list of them", () => {
    expect(declaring("useCan")).toEqual(["components/page/permissions.tsx"]);
    // A module of its own that only re-exports it is a second place to look for it.
    expect([...SOURCES.keys()].filter((path) => /(^|\/)useCan\.tsx?$/.test(path))).toEqual([]);
  });

  it("opens a port in one dialog: nothing else adds a firewall rule or publishes a port", () => {
    const makes = (source: string): boolean => /request\(\s*"post",\s*"\/api\/(?:server\/security\/firewall\/rules|streams)"/.test(source);
    const writers = [...SOURCES].filter(([, source]) => makes(source)).map(([path]) => path);
    // The dialog that every place that opens a port opens (features/network); the published ports' page lists them and removes them.
    expect(writers).toEqual(["features/network/OpenPortDialog.tsx"]);
  });
});
