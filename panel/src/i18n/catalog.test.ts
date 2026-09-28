import { describe, expect, it } from "vitest";

import * as en from "./en";
import * as es from "./es";

/**
 * tsc already refuses a Spanish catalog with a key too many or too few; these check what the
 * types cannot: no empty text, the same placeholders in both languages, and complete plurals.
 */

type Node = string | { readonly [key: string]: Node };

const PLURAL_CATEGORIES = new Set(["zero", "one", "two", "few", "many", "other"]);

function isPlural(node: Node): node is Readonly<Record<string, string>> {
  return typeof node === "object" && typeof node["other"] === "string" && Object.keys(node).every((k) => PLURAL_CATEGORIES.has(k));
}

/** Every message as [dotted key, text or plural forms]. */
function flatten(node: Node, prefix = ""): [string, string | Readonly<Record<string, string>>][] {
  if (typeof node === "string" || isPlural(node)) return [[prefix, node]];
  return Object.entries(node).flatMap(([key, child]) => flatten(child, prefix === "" ? key : `${prefix}.${key}`));
}

function placeholders(message: string | Readonly<Record<string, string>>): string[] {
  const texts = typeof message === "string" ? [message] : Object.values(message);
  return [...new Set(texts.flatMap((text) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1] ?? "")))].sort();
}

const english = new Map(flatten({ ...en }));
const spanish = new Map(flatten({ ...es }));

describe("catalogs", () => {
  it("have the same namespaces in both indexes", () => {
    expect(Object.keys(es).sort()).toEqual(Object.keys(en).sort());
  });

  it("translate every English key into Spanish, and nothing else", () => {
    expect([...spanish.keys()].sort()).toEqual([...english.keys()].sort());
  });

  it.each([
    ["English", english],
    ["Spanish", spanish],
  ])("have no empty text in %s", (_, catalog) => {
    const empty = [...catalog].filter(([, message]) =>
      (typeof message === "string" ? [message] : Object.values(message)).some((text) => text.trim() === ""),
    );
    expect(empty.map(([key]) => key)).toEqual([]);
  });

  it("use the same placeholders for a key in both languages", () => {
    const mismatched = [...english].flatMap(([key, message]) => {
      const translated = spanish.get(key);
      if (translated === undefined) return [];
      const want = placeholders(message);
      const got = placeholders(translated);
      return want.join() === got.join() ? [] : [`${key}: en {${want.join(", ")}} es {${got.join(", ")}}`];
    });
    expect(mismatched).toEqual([]);
  });

  it("give every plural its one and other forms in both languages", () => {
    const incomplete = [...english].flatMap(([key, message]) => {
      if (typeof message === "string") return [];
      const translated = spanish.get(key);
      const complete = (forms: unknown) =>
        typeof forms === "object" && forms !== null && "one" in forms && "other" in forms;
      return complete(message) && complete(translated) ? [] : [key];
    });
    expect(incomplete).toEqual([]);
  });

  it("never give a plain message plural forms in one language only", () => {
    const shapes = [...english].filter(([key, message]) => typeof message !== typeof spanish.get(key));
    expect(shapes.map(([key]) => key)).toEqual([]);
  });
});
