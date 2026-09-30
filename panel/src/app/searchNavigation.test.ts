import { describe, expect, it } from "vitest";

import { inPlace } from "./searchNavigation";

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../**/*.gen.ts"], {
  eager: true,
  query: "?raw",
  import: "default",
});

/** Every navigation that changes only the search params: `navigate({ search: ... })`, no `to`. */
const SEARCH_ONLY = /navigate\(\{\s*search:[^\n]*/g;

describe("search-only navigations", () => {
  it("never scroll, and replace only when asked", () => {
    expect(inPlace()).toEqual({ resetScroll: false, replace: false });
    expect(inPlace({ replace: true })).toEqual({ resetScroll: false, replace: true });
    expect(inPlace({ replace: undefined })).toEqual({ resetScroll: false, replace: false });
  });

  it("are written in place everywhere: a range, a filter or a view never jumps to the top", () => {
    const offenders: string[] = [];
    let found = 0;
    for (const [path, source] of Object.entries(RAW)) {
      for (const match of source.matchAll(SEARCH_ONLY)) {
        found += 1;
        if (!match[0].includes("inPlace(")) offenders.push(`${path}: ${match[0].trim()}`);
      }
    }
    // The scan has to see the routes it guards, or it would pass by finding nothing.
    expect(found).toBeGreaterThan(20);
    expect(offenders).toEqual([]);
  });
});
