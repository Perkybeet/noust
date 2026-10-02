/**
 * Every query factory keeps in its key every parameter it was given. A parameter that reaches
 * the request but not the key makes TanStack Query answer from the cache of another request:
 * "Show 50" on the processes table showed the cached ten and never asked again (owner item 55).
 * This reads the factories' source rather than calling them, so a new factory is covered the
 * day it is written.
 */

import { describe, expect, it } from "vitest";

const sources = import.meta.glob<string>(["./queries/*.ts", "../features/*/queries.ts", "../features/*/data.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
});

/** `export const name = (params) =>` followed by `queryOptions({ ... queryKey: <expr>,`. */
const FACTORY = /export const (\w+) = \(([^)]*)\) =>\s*queryOptions\(\{\s*queryKey:\s*([^\n]+?),?\s*\n/g;

/** The names a parameter list binds, destructured ones included. */
function parameterNames(list: string): string[] {
  const names: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of list) {
    if (char === "{" || char === "[" || char === "<") depth += 1;
    if (char === "}" || char === "]" || char === ">") depth -= 1;
    if (char === "," && depth === 0) {
      names.push(...bound(current));
      current = "";
    } else {
      current += char;
    }
  }
  names.push(...bound(current));
  return names;
}

function bound(parameter: string): string[] {
  const text = parameter.trim();
  if (text === "") return [];
  if (text.startsWith("{")) {
    const inner = text.slice(1, text.indexOf("}"));
    return inner
      .split(",")
      .map((part) => part.split(/[:=]/)[0]?.trim() ?? "")
      .filter((name) => /^\w+$/.test(name));
  }
  const name = /^(\w+)/.exec(text)?.[1];
  return name ? [name] : [];
}

const factories = Object.entries(sources).flatMap(([file, source]) =>
  [...source.matchAll(FACTORY)].map((match) => ({
    file,
    name: match[1] ?? "",
    parameters: parameterNames(match[2] ?? ""),
    key: match[3] ?? "",
  })),
);

describe("query factories", () => {
  it("are found, so this test reads something", () => {
    expect(factories.length).toBeGreaterThan(30);
  });

  it("put every parameter in the key", () => {
    const missing = factories.flatMap(({ file, name, parameters, key }) =>
      parameters
        .filter((parameter) => !new RegExp(`\\b${parameter}\\b`).test(key))
        .map((parameter) => `${file} ${name}: "${parameter}" is not in ${key}`),
    );

    expect(missing).toEqual([]);
  });
});
