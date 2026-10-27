import { describe, expect, it } from "vitest";

import { SERVER_TEXT } from "../api/serverTextCodes.gen";
import * as en from "./en";
import * as es from "./es";

/**
 * The console keeps up with the server's sentences (`noust.core.server_text`).
 *
 * `src/api/serverTextCodes.gen.ts` is generated from the server's catalog and lists every code with the
 * values its text and its fix take. A code the server can say that English or Spanish lacks is a
 * sentence an operator reads in the wrong language, so it fails here, as does an entry for a code the
 * server no longer says and a translation that puts in other values than the sentence is said with.
 * (A code the console has no entry for is shown in English at run time; this keeps that from being
 * the way anything ships.)
 */

type Node = string | { readonly [key: string]: Node };

/** Every sentence of a catalog as `code` to its `text` and `hint`, found by walking to the objects that hold a `text`. */
function sentencesOf(tree: Node, prefix = ""): Map<string, { text: string; hint: string | undefined }> {
  const found = new Map<string, { text: string; hint: string | undefined }>();
  if (typeof tree === "string") return found;
  const text = tree["text"];
  if (typeof text === "string") {
    const hint = tree["hint"];
    found.set(prefix, { text, hint: typeof hint === "string" ? hint : undefined });
    return found;
  }
  for (const [key, child] of Object.entries(tree)) {
    for (const [code, sentence] of sentencesOf(child, prefix === "" ? key : `${prefix}.${key}`)) found.set(code, sentence);
  }
  return found;
}

function placeholders(text: string | undefined): string[] {
  return [...new Set([...(text ?? "").matchAll(/\{(\w+)\}/g)].map((match) => match[1] ?? ""))].sort();
}

const CODES = Object.keys(SERVER_TEXT).sort();

describe.each([
  ["English", sentencesOf(en.serverText)],
  ["Spanish", sentencesOf(es.serverText)],
])("the server's sentences in %s", (_, catalog) => {
  it("has a text for every code the server says", () => {
    expect(CODES.filter((code) => !catalog.has(code))).toEqual([]);
  });

  it("has no text for a code the server does not say", () => {
    expect([...catalog.keys()].filter((code) => !(code in SERVER_TEXT)).sort()).toEqual([]);
  });

  it("has a fix for exactly the sentences that have one", () => {
    const wrong = CODES.filter((code) => catalog.has(code) && (SERVER_TEXT[code as keyof typeof SERVER_TEXT].hint !== null) !== (catalog.get(code)?.hint !== undefined));
    expect(wrong).toEqual([]);
  });

  it("says each sentence and each fix with the values the server sends for them, no more and no fewer", () => {
    const wrong = CODES.flatMap((code) => {
      const entry = SERVER_TEXT[code as keyof typeof SERVER_TEXT];
      const sentence = catalog.get(code);
      if (sentence === undefined) return [];
      const problems: string[] = [];
      if (placeholders(sentence.text).join() !== [...entry.text].join()) problems.push(`${code}.text: {${placeholders(sentence.text).join(", ")}}, the server sends {${entry.text.join(", ")}}`);
      if (entry.hint !== null && placeholders(sentence.hint).join() !== [...entry.hint].join()) problems.push(`${code}.hint: {${placeholders(sentence.hint).join(", ")}}, the server sends {${entry.hint.join(", ")}}`);
      return problems;
    });
    expect(wrong).toEqual([]);
  });

  it("has no empty text", () => {
    const empty = [...catalog].filter(([, { text, hint }]) => text.trim() === "" || hint?.trim() === "").map(([code]) => code);
    expect(empty).toEqual([]);
  });
});

describe("the codes themselves", () => {
  it("has no code with a part called text or hint, which would hide a sentence in the catalog's tree", () => {
    expect(CODES.filter((code) => code.split(".").some((part) => part === "text" || part === "hint"))).toEqual([]);
  });

  it("has no code that is the beginning of another, which a tree cannot hold", () => {
    expect(CODES.filter((code) => CODES.some((other) => other.startsWith(`${code}.`)))).toEqual([]);
  });

  it("has many sentences: a table that lost them all would pass everything above", () => {
    expect(CODES.length).toBeGreaterThan(60);
  });
});
