// Holds the console's weight to a budget (checklist M-64). A page that is slow to load is slow on
// the first visit to a server on a modest connection, which is exactly when an operator is waiting
// for it; the number that matters is what the browser must fetch before anything is drawn.
//
//   initialJs   gzip bytes of the scripts index.html loads or preloads: the entry and what it imports
//   initialCss  gzip bytes of the stylesheets index.html loads
//   chunk       gzip bytes of the largest single script, which is the slowest page to open
//   totalJs     gzip bytes of every script: what a visit to every page would fetch
//   catalogEn   gzip bytes of the English texts that are loaded by area (the chunks `i18n-en-*`; the
//               namespaces the first screen draws are bundled with the entry and counted in initialJs)
//   catalogEs   gzip bytes of the Spanish texts (the chunks `i18n-es-*`: every namespace, the chrome included)
//   catalogCommunity  gzip bytes of the heaviest language contributed as data (the chunks
//               `i18n-community-<code>.<hash>.js`, one per language, src/i18n/community/<code>.json)
//
// The texts weigh as much as the code of a dozen pages and grow with each feature, so they have a number
// of their own: a regression of the catalogs shows as itself and not as an unexplained totalJs.
// vite.config.ts gives the catalog chunks those names (a build whose chunks are not named so would measure 0).
// A contributed language is fetched whole, and only by somebody who chose it: it is weighed on its own and
// left out of `chunk` and `totalJs`, which stay what a visit in English or Spanish costs, so adding a language
// moves its own number and nothing else.
//
// The budget is panel/bundle-budget.json. A number there only goes down by hand when the build gets
// lighter; going up is a decision that names the reason in the commit that raises it.
//
//     node scripts/check-bundle-budget.mjs [build-dir] [budget.json]
//
// The build directory defaults to the committed build, which this only reads.

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const here = path.dirname(fileURLToPath(import.meta.url));
const dir = path.resolve(process.argv[2] ?? path.join(here, "..", "..", "src", "noust", "web", "static"));
const budgetPath = path.resolve(process.argv[3] ?? path.join(here, "..", "bundle-budget.json"));

/**
 * @param {string} message
 * @returns {never}
 */
function fail(message) {
  console.error(message);
  process.exit(1);
}

if (!existsSync(path.join(dir, "index.html"))) fail(`${path.join(dir, "index.html")} does not exist. Build first: npm run build`);
if (!existsSync(budgetPath)) fail(`${budgetPath} does not exist.`);

/** @param {string} file */
const gzipSize = (file) => gzipSync(readFileSync(file), { level: 9 }).length;
/** @param {number} bytes */
const kb = (bytes) => `${(bytes / 1024).toFixed(1)} kB`;

const html = readFileSync(path.join(dir, "index.html"), "utf8");
/** @param {RegExp} pattern */
const hrefs = (pattern) => [...html.matchAll(pattern)].map((match) => match[1]).filter((href) => href !== undefined);
/** @param {string} href */
const local = (href) => path.join(dir, href.replace(/^\//, ""));

const scripts = new Set([
  ...hrefs(/<script[^>]*\bsrc="([^"]+\.js)"/g),
  ...hrefs(/<link[^>]*rel="modulepreload"[^>]*href="([^"]+)"/g),
]);
const styles = new Set(hrefs(/<link[^>]*rel="stylesheet"[^>]*href="([^"]+\.css)"/g));
if (scripts.size === 0) fail(`${path.join(dir, "index.html")} loads no script: is this a build of the console?`);

const assets = path.join(dir, "assets");
const COMMUNITY = /^i18n-community-([^.]+)\.[^.]+\.js$/;
const scriptsIn = readdirSync(assets)
  .filter((name) => name.endsWith(".js"))
  .map((name) => ({ name, gzip: gzipSize(path.join(assets, name)) }));
const chunks = scriptsIn.filter((chunk) => !COMMUNITY.test(chunk.name));
/** @type {Map<string, number>} */
const languages = new Map();
for (const chunk of scriptsIn) {
  const code = COMMUNITY.exec(chunk.name)?.[1];
  if (code !== undefined) languages.set(code, (languages.get(code) ?? 0) + chunk.gzip);
}
const heaviestLanguage = [...languages.entries()].sort((a, b) => b[1] - a[1])[0];

/** @type {Record<string, number>} */
const measured = {
  initialJs: [...scripts].reduce((sum, href) => sum + gzipSize(local(href)), 0),
  initialCss: [...styles].reduce((sum, href) => sum + gzipSize(local(href)), 0),
  chunk: Math.max(...chunks.map((chunk) => chunk.gzip)),
  totalJs: chunks.reduce((sum, chunk) => sum + chunk.gzip, 0),
  catalogEn: chunks.filter((chunk) => chunk.name.startsWith("i18n-en-")).reduce((sum, chunk) => sum + chunk.gzip, 0),
  catalogEs: chunks.filter((chunk) => chunk.name.startsWith("i18n-es-")).reduce((sum, chunk) => sum + chunk.gzip, 0),
  catalogCommunity: heaviestLanguage?.[1] ?? 0,
};
const largest = chunks.find((chunk) => chunk.gzip === measured.chunk)?.name ?? "";

/** @type {unknown} */
const parsed = JSON.parse(readFileSync(budgetPath, "utf8"));
const budget = typeof parsed === "object" && parsed !== null ? /** @type {Record<string, unknown>} */ (parsed) : {};
const over = [];
for (const [name, value] of Object.entries(measured)) {
  const limit = budget[name];
  if (typeof limit !== "number") fail(`${budgetPath} has no number for "${name}".`);
  const which = name === "chunk" ? `  (${largest})` : name === "catalogCommunity" && heaviestLanguage ? `  (${heaviestLanguage[0]})` : "";
  const line = `${name.padEnd(10)} ${kb(value).padStart(10)} of ${kb(limit)}${which}`;
  console.log(line);
  if (value > limit) over.push(`${name} is ${kb(value)}, ${kb(value - limit)} over its budget of ${kb(limit)}`);
}

if (over.length > 0) {
  console.error("\nThe console is heavier than its budget:");
  for (const line of over) console.error(`  ${line}`);
  console.error("Lighten it (lazy-load what the first page does not draw), or raise panel/bundle-budget.json and say why.");
  process.exit(1);
}
console.log("The console is within its budget.");
