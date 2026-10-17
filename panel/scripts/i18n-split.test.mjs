//
// The catalogs in a build. The console once shipped the whole English catalog in the script every
// page loads first (200 kB of gzip that no first paint draws), because one file imported the barrel
// `./en`. A budget catches that after a full build; this catches it in half a second, at the module
// graph of the catalogs and with the bundler that ships them: the entry holds the chrome and nothing
// else, and every other namespace of both languages is a chunk of its own, fetched when a page needs it.

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { build } from "vite";
import { beforeAll, describe, expect, it } from "vitest";

const panel = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const i18n = path.join(panel, "src", "i18n");

/**
 * The namespaces of a language: one file each.
 *
 * @param {string} language
 * @returns {string[]}
 */
function namespacesOf(language) {
  return readdirSync(path.join(i18n, language))
    .filter((name) => name.endsWith(".ts") && name !== "index.ts")
    .map((name) => name.replace(/\.ts$/, ""))
    .sort();
}

/** The chrome as catalogs.ts bundles it, read from the object it is declared in. @returns {string[]} */
function chrome() {
  const text = readFileSync(path.join(i18n, "catalogs.ts"), "utf8");
  const declared = /const CHROME_MODULES = \{([^}]*)\}/.exec(text)?.[1];
  if (declared === undefined) throw new Error("catalogs.ts no longer declares CHROME_MODULES: update this test with it");
  return declared
    .split(",")
    .map((name) => name.trim())
    .filter((name) => name !== "")
    .sort();
}

/**
 * The catalog module a path is, if it is one.
 *
 * @param {string} id
 * @returns {{ language: string, namespace: string, barrel: boolean } | null}
 */
function catalogModule(id) {
  const match = /[\\/]src[\\/]i18n[\\/](en|es)[\\/]([A-Za-z0-9]+)\.ts$/.exec(id);
  return match?.[1] === undefined || match[2] === undefined ? null : { language: match[1], namespace: match[2], barrel: match[2] === "index" };
}

/** @type {import("rolldown").OutputChunk[]} */
let chunks = [];

beforeAll(async () => {
  const result = await build({
    root: panel,
    configFile: false,
    logLevel: "silent",
    build: { write: false, minify: true, lib: { entry: path.join(i18n, "catalogs.ts"), formats: ["es"], fileName: "catalogs" } },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((one) => ("output" in one ? one.output : []));
  chunks = /** @type {import("rolldown").OutputChunk[]} */ (outputs.filter((output) => output.type === "chunk"));
}, 120_000);

describe("the catalogs in a build", () => {
  it("put the chrome in the entry and nothing else", () => {
    const entry = chunks.find((chunk) => chunk.isEntry);
    expect(entry).toBeDefined();
    const inEntry = (entry?.moduleIds ?? []).flatMap((id) => {
      const found = catalogModule(id);
      return found === null ? [] : [`${found.language}/${found.namespace}`];
    });
    expect(inEntry.sort()).toEqual(chrome().map((namespace) => `en/${namespace}`));
  });

  it("never carry a barrel, which is the whole catalog in one import", () => {
    const barrels = chunks.flatMap((chunk) => chunk.moduleIds.flatMap((id) => (catalogModule(id)?.barrel === true ? [id] : [])));
    expect(barrels).toEqual([]);
  });

  it("make every other namespace of both languages a chunk of its own", () => {
    const bundled = new Set(chrome());
    for (const language of ["en", "es"]) {
      for (const namespace of namespacesOf(language)) {
        if (language === "en" && bundled.has(namespace)) continue;
        const holders = chunks.filter((chunk) => chunk.moduleIds.some((id) => catalogModule(id)?.language === language && catalogModule(id)?.namespace === namespace));
        expect(holders.length, `${language}/${namespace} is in one chunk`).toBe(1);
        const holder = holders[0];
        expect(holder?.isEntry, `${language}/${namespace} is not in the entry`).toBe(false);
        const catalogs = (holder?.moduleIds ?? []).filter((id) => catalogModule(id) !== null);
        expect(catalogs.length, `${language}/${namespace} shares its chunk with another namespace`).toBe(1);
      }
    }
  });

  it("weigh little in the entry, and a catalog's worth in the chunks the entry does not load", () => {
    const gzip = (/** @type {string} */ code) => gzipSync(code, { level: 9 }).length;
    const entry = chunks.find((chunk) => chunk.isEntry);
    const lazy = chunks.filter((chunk) => !chunk.isEntry).reduce((sum, chunk) => sum + gzip(chunk.code), 0);
    // The whole English catalog is about 200 kB of gzip and the Spanish one a little more; the chrome is a few kB.
    expect(gzip(entry?.code ?? "")).toBeLessThan(40 * 1024);
    expect(lazy).toBeGreaterThan(300 * 1024);
  });
});
