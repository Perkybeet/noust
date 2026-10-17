/**
 * The console reaches the server in one place (rule 3): `request()` in `client.ts` adds the
 * session, the CSRF header, the sudo-mode retry and the one error contract. A page that called
 * `fetch` itself would have none of them, so this fails on the first one that does, and on a
 * remote import (the Coolify and Dokploy reader of the new-application wizard) that asks for
 * anything but its two routes through `request()`.
 */

import { describe, expect, it } from "vitest";

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../**/*.gen.ts", "!../dev/**", "!../test/**"], {
  eager: true,
  query: "?raw",
  import: "default",
});

/** Production sources by path relative to src/ (this folder's own files come as `./name`). */
const SOURCES: ReadonlyMap<string, string> = new Map(
  Object.entries(RAW).map(([path, source]) => [path.startsWith("./") ? `api/${path.slice(2)}` : path.replace(/^\.\.\//, ""), source]),
);

/** Code with the comments left out, so a sentence that names `fetch(` is not a call. */
function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

describe("the console's one way to the server", () => {
  it("reads the whole console", () => {
    expect(SOURCES.size).toBeGreaterThan(200);
  });

  it("calls fetch, or opens an XMLHttpRequest, only in api/client.ts", () => {
    const offenders = [...SOURCES]
      .filter(([path]) => path !== "api/client.ts")
      .filter(([, source]) => /(?<![\w.])fetch\s*\(|\bwindow\.fetch\b|\bnew XMLHttpRequest\b|\bnavigator\.sendBeacon\b/.test(code(source)))
      .map(([path]) => path);
    expect(offenders, "Reach the server through request() (api/client.ts)").toEqual([]);
  });

  it("reads another platform's server through request(), with its two routes and nothing else", () => {
    const source = code(SOURCES.get("features/new-app/RemoteImport.tsx") ?? "");
    expect(source).not.toBe("");
    const calls = [...source.matchAll(/request\(\s*"(\w+)",\s*"([^"]+)"/g)].map((match) => `${(match[1] ?? "").toUpperCase()} ${match[2] ?? ""}`);
    expect(calls.sort()).toEqual(["POST /api/import/applications", "POST /api/import/proposal"]);
    // The token a person typed goes in the body of those calls and nowhere else.
    expect(source).not.toMatch(/localStorage|sessionStorage|document\.cookie/);
  });

  it("has one reader of Coolify and Dokploy: no other source names their import routes", () => {
    const readers = [...SOURCES]
      .filter(([, source]) => code(source).includes("/api/import/applications") || code(source).includes("/api/import/proposal"))
      .map(([path]) => path);
    expect(readers.sort()).toEqual(["features/new-app/RemoteImport.tsx"]);
  });
});
