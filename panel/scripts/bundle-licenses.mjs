// The notice of the third-party software the console is built from.
//
// The console's build is committed and shipped (wheel, sdist, deb, rpm), and what it holds is not only
// Noust's: React, Base UI, TanStack, lucide and the rest are MIT, ISC and the like, licences that ask for
// their copyright line and their text to travel with every copy. This module finds the npm packages whose
// code, stylesheets or fonts went into a build and writes one text file with each package's licence, which
// vite.config.ts emits next to index.html as `third-party-licenses.txt` and NOTICE points to.
//
// It reads package.json and the licence files of each package where the build found it, so a package
// added or upgraded is in the notice with no one remembering it, and one that was tree-shaken out is not.
// It adds no dependency and does nothing at run time. The output depends on nothing but the packages
// (no date, no path), so two builds of one tree write the same bytes.

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

/** The file the build writes, at the root of the build directory. NOTICE and a test name it. */
export const LICENSES_FILE = "third-party-licenses.txt";

/** The credit, spelled as noust.__copyright__ spells it (tests/test_copyright_headers.py holds every file to it). */
const COPYRIGHT = "Copyright (c) 2024-2026 Yago López Prado";

/** Licence files of a package: LICENSE, LICENCE, COPYING, with or without an extension or a suffix (LICENSE-MIT). */
const LICENCE_FILE = /^(?:licen[cs]e|copying|unlicen[cs]e)(?:[-_.][\w.-]+)?$/i;

/** Notice files (Apache-2.0 asks for the NOTICE of a package to be kept with it). */
const NOTICE_FILE = /^notice(?:\.(?:md|txt))?$/i;

/** Names that are documentation, not a licence, though they start like one (LICENSE.d.ts, license-checker's output). */
const NOT_A_LICENCE = /\.(?:d\.ts|js|mjs|cjs|json|ts)$/i;

/**
 * @typedef {object} Package
 * @property {string} name
 * @property {string} version
 * @property {string} license The SPDX expression the package declares, or "unknown".
 * @property {string | null} repository
 * @property {string | null} author
 * @property {string[]} copyrightLines The `Copyright ...` lines of the header of the package's entry file, for a package with no licence file.
 * @property {{ file: string, text: string }[]} licenceTexts The licence files the package carries.
 * @property {{ file: string, text: string }[]} notices Its NOTICE files.
 */

/**
 * The directory of the npm package a module of a build belongs to.
 *
 * Plugins decorate a module id with a prefix (`\0`) or a query (`?commonjs-es-import`); a package
 * installed under another (`node_modules/a/node_modules/b`) is the innermost, and pnpm's store nests
 * `node_modules/.pnpm/<name>@<version>/node_modules/<name>`, which the same rule resolves.
 *
 * @param {string} moduleId
 * @returns {string | null} The package's directory, or null when the module is not in a package.
 */
export function packageRootOf(moduleId) {
  const clean = moduleId.replace(/^\0/, "").replace(/[?#].*$/, "").split("\\").join("/");
  // The last `node_modules/` that starts a path segment. An asset's original name is relative to the
  // project, so it starts at node_modules itself, with no slash before it.
  const marker = "node_modules/";
  let at = -1;
  for (let found = clean.indexOf(marker); found !== -1; found = clean.indexOf(marker, found + 1)) {
    if (found === 0 || clean[found - 1] === "/") at = found;
  }
  if (at === -1) return null;
  const rest = clean.slice(at + marker.length).split("/");
  const first = rest[0];
  if (first === undefined || first === "" || first === ".pnpm" || first === ".bin") return null;
  const parts = first.startsWith("@") ? rest.slice(0, 2) : rest.slice(0, 1);
  // A scope alone (`node_modules/@scope`) is not a package.
  if (parts.length !== (first.startsWith("@") ? 2 : 1) || parts.some((part) => part === "")) return null;
  return `${clean.slice(0, at + marker.length)}${parts.join("/")}`;
}

/**
 * The text of a file, with its line ends as Unix writes them and no trailing blanks, so the same
 * licence reads the same on any machine.
 *
 * @param {string} file
 * @returns {string}
 */
function readText(file) {
  return readFileSync(file, "utf8").replace(/\r\n?/g, "\n").replace(/\s+$/, "");
}

/**
 * @param {unknown} value
 * @returns {string | null}
 */
function asText(value) {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

/**
 * @param {unknown} value
 * @returns {Record<string, unknown> | null} The value when it is an object (not an array), else null.
 */
function asRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? /** @type {Record<string, unknown>} */ (value) : null;
}

/**
 * What package.json says the licence is: a string, `{ type }` or the old `licenses: [{ type }]`.
 *
 * @param {Record<string, unknown>} manifest
 * @returns {string}
 */
function declaredLicense(manifest) {
  const single = manifest.license;
  if (typeof single === "string" && single.trim() !== "") return single.trim();
  const typed = asRecord(single);
  if (typed !== null) return asText(typed.type) ?? "unknown";
  const several = manifest.licenses;
  if (Array.isArray(several)) {
    const types = /** @type {unknown[]} */ (several).flatMap((entry) => {
      const type = asText(asRecord(entry)?.type);
      return type === null ? [] : [type];
    });
    if (types.length > 0) return types.join(" OR ");
  }
  return "unknown";
}

/**
 * @param {Record<string, unknown>} manifest
 * @returns {string | null}
 */
function declaredRepository(manifest) {
  const repository = manifest.repository;
  const url = typeof repository === "string" ? repository : asText(asRecord(repository)?.url);
  if (url === null) return asText(manifest.homepage);
  return url.replace(/^git\+/, "").replace(/\.git$/, "").replace(/^git:\/\//, "https://").replace(/^github:/, "https://github.com/");
}

/**
 * @param {Record<string, unknown>} manifest
 * @returns {string | null}
 */
function declaredAuthor(manifest) {
  const author = manifest.author;
  if (typeof author === "string") return asText(author)?.replace(/\s*[<(][^>)]*[>)]/g, "") ?? null;
  return asText(asRecord(author)?.name);
}

/** The text of the MIT licence, which a package that declares it and carries no copy of it is given with its own copyright line. */
const MIT_TEXT = `Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.`;

/**
 * The copyright lines at the head of a package's entry file. A minifier drops that comment from the
 * bundle, and a package that ships no LICENSE file (qrcode-generator) says who holds the copyright
 * only there.
 *
 * @param {string} root
 * @param {Record<string, unknown>} manifest
 * @returns {string[]}
 */
function headerCopyright(root, manifest) {
  const inside = path.resolve(root);
  for (const field of ["module", "main"]) {
    const entry = asText(manifest[field]);
    if (entry === null) continue;
    const file = path.resolve(inside, entry);
    // A manifest is untrusted input: its entry stays inside the package.
    if (!file.startsWith(`${inside}${path.sep}`) || !existsSync(file) || !statSync(file).isFile()) continue;
    const head = readFileSync(file, "utf8").slice(0, 4096).split(/\r?\n/).slice(0, 40);
    const found = head.flatMap((line) => {
      const match = /^\s*(?:\/\/|\/?\*+|#)?\s*(Copyright\b.*?)\s*$/i.exec(line);
      return match?.[1] === undefined ? [] : [match[1]];
    });
    if (found.length > 0) return found;
  }
  return [];
}

/**
 * Reads a package from its directory: its name, version and declared licence, and every licence and
 * notice file it carries.
 *
 * @param {string} root
 * @returns {Package | null} Null when the directory has no readable package.json with a name.
 */
export function readPackage(root) {
  const manifestFile = path.join(root, "package.json");
  if (!existsSync(manifestFile)) return null;
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(manifestFile, "utf8"));
  } catch (error) {
    if (error instanceof SyntaxError) return null;
    throw error;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const manifest = /** @type {Record<string, unknown>} */ (parsed);
  const name = asText(manifest.name);
  if (name === null) return null;

  const files = readdirSync(root)
    .filter((entry) => statSync(path.join(root, entry)).isFile())
    .sort();
  const pick = (/** @type {RegExp} */ pattern) =>
    files
      .filter((entry) => pattern.test(entry) && !NOT_A_LICENCE.test(entry))
      .map((entry) => ({ file: entry, text: readText(path.join(root, entry)) }))
      .filter((entry) => entry.text !== "");
  // The same licence under two names (LICENSE and LICENSE.md) is said once.
  const seen = new Set();
  const licenceTexts = pick(LICENCE_FILE).filter((entry) => !seen.has(entry.text) && seen.add(entry.text));
  return {
    name,
    version: asText(manifest.version) ?? "unknown",
    license: declaredLicense(manifest),
    repository: declaredRepository(manifest),
    author: declaredAuthor(manifest),
    copyrightLines: licenceTexts.length === 0 ? headerCopyright(root, manifest) : [],
    licenceTexts,
    notices: pick(NOTICE_FILE),
  };
}

/**
 * The directories of the packages a build is made of: the modules that wrote code into a chunk, and the
 * assets (a font) that were copied from a package. A module that a chunk lists but wrote nothing from
 * (tree-shaken to nothing) is not in the build and not in the notice.
 *
 * @param {Record<string, unknown>} bundle The `bundle` of the `generateBundle` hook.
 * @param {string} projectDir What the bundler's relative paths (an asset's original name) are relative to.
 * @returns {Set<string>} Absolute directories.
 */
export function packageRootsOf(bundle, projectDir) {
  /** @type {Set<string>} */
  const roots = new Set();
  /** @param {string} id */
  const add = (id) => {
    const root = packageRootOf(id);
    if (root !== null) roots.add(path.resolve(projectDir, root));
  };
  for (const output of Object.values(bundle)) {
    const entry = asRecord(output);
    if (entry === null) continue;
    const modules = asRecord(entry.modules);
    if (entry.type === "chunk" && modules !== null) {
      for (const [id, rendered] of Object.entries(modules)) {
        // A chunk that does not say how much a module rendered is taken to have rendered it.
        const length = asRecord(rendered)?.renderedLength ?? 1;
        if (typeof length === "number" && length > 0) add(id);
      }
    }
    if (entry.type === "asset" && Array.isArray(entry.originalFileNames)) {
      for (const original of /** @type {unknown[]} */ (entry.originalFileNames)) if (typeof original === "string") add(original);
    }
  }
  return roots;
}

/**
 * The packages a stylesheet pulls in with a bare `@import` (`@import "tailwindcss"`), following its
 * relative imports. Their CSS is compiled into the build's stylesheet without being a module of the
 * bundle, so the modules above never name them.
 *
 * @param {string} stylesheet The stylesheet's path.
 * @param {string} packagesDir The directory bare imports resolve in (the project's node_modules).
 * @param {Set<string>} [visited]
 * @returns {Set<string>} Package directories.
 */
export function stylesheetPackageRoots(stylesheet, packagesDir, visited = new Set()) {
  /** @type {Set<string>} */
  const roots = new Set();
  if (visited.has(stylesheet) || !existsSync(stylesheet)) return roots;
  visited.add(stylesheet);
  const source = readFileSync(stylesheet, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  for (const match of source.matchAll(/@import\s+(?:url\()?["']([^"']+)["']/g)) {
    const specifier = match[1];
    // An address (https:, data:) is fetched, not installed.
    if (specifier === undefined || /^[a-z][a-z0-9+.-]*:/i.test(specifier)) continue;
    if (specifier.startsWith(".") || specifier.startsWith("/")) {
      for (const root of stylesheetPackageRoots(path.resolve(path.dirname(stylesheet), specifier), packagesDir, visited)) roots.add(root);
      continue;
    }
    const parts = specifier.split("/");
    const name = specifier.startsWith("@") ? parts.slice(0, 2).join("/") : (parts[0] ?? "");
    if (name !== "") roots.add(path.join(packagesDir, name));
  }
  return roots;
}

const RULE = "-".repeat(78);

/**
 * The notice: one block per package, by name, with its licence and anything it asks to keep.
 *
 * @param {readonly Package[]} packages
 * @returns {string}
 */
export function renderLicenses(packages) {
  const sorted = [...packages].sort((a, b) => a.name.localeCompare(b.name, "en") || a.version.localeCompare(b.version, "en"));
  const lines = [
    "Third-party software in the Noust console",
    "=========================================",
    "",
    `The Noust console is ${COPYRIGHT}, under the GNU Affero`,
    "General Public License, version 3 or later (LICENSE). It is built from the packages below. Each is",
    "the work of its authors and is used under the licence it declares, whose text follows its name.",
    "This file is written by the build (panel/vite.config.ts) from the packages that went into it. The",
    "fonts are under the SIL Open Font License, version 1.1 (LICENSE-OFL-1.1).",
    "",
    `${String(sorted.length)} packages`,
    "",
  ];
  for (const entry of sorted) {
    lines.push(RULE, `${entry.name} ${entry.version}`, `License: ${entry.license}`);
    if (entry.repository !== null) lines.push(`Source: ${entry.repository}`);
    lines.push(RULE, "");
    if (entry.licenceTexts.length === 0 && entry.license === "MIT" && entry.copyrightLines.length > 0) {
      // The author says MIT and who holds the copyright, in the file the notice reads; the text is the licence's own.
      lines.push("MIT License", "", ...entry.copyrightLines, "", MIT_TEXT, "");
    } else if (entry.licenceTexts.length === 0) {
      // Said plainly rather than made up: the package declares a licence and ships no text for it.
      lines.push(
        `${entry.name} declares the licence above in its package.json and carries no licence file.`,
        ...entry.copyrightLines,
        ...(entry.copyrightLines.length === 0 && entry.author !== null ? [`Copyright holder named by the package: ${entry.author}.`] : []),
        "",
      );
    }
    for (const text of entry.licenceTexts) lines.push(text.text, "");
    for (const notice of entry.notices) lines.push(`NOTICE of ${entry.name}`, "", notice.text, "");
  }
  return `${lines.join("\n").replace(/\n+$/, "")}\n`;
}

/**
 * The notice of a build.
 *
 * @param {Record<string, unknown>} bundle The `bundle` of the `generateBundle` hook.
 * @param {{ projectDir: string, stylesheets?: readonly string[] }} options The project (its node_modules is where bare imports resolve) and its entry stylesheets, whose bare imports are packages too.
 * @returns {string}
 */
export function licensesOf(bundle, options) {
  const roots = packageRootsOf(bundle, options.projectDir);
  for (const sheet of options.stylesheets ?? []) {
    for (const root of stylesheetPackageRoots(path.resolve(options.projectDir, sheet), path.join(options.projectDir, "node_modules"))) roots.add(root);
  }
  /** @type {Map<string, Package>} */
  const byIdentity = new Map();
  for (const root of [...roots].sort()) {
    const found = readPackage(root);
    if (found !== null) byIdentity.set(`${found.name}@${found.version}`, found);
  }
  return renderLicenses([...byIdentity.values()]);
}
