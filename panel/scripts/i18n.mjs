// Bringing the console to another language, without writing TypeScript (checklist M-63).
//
//   npm run i18n:new -- pt Português     registers the language and creates src/i18n/community/pt.json
//   npm run i18n:status                  how far each contributed language is
//   npm run i18n:status -- pt            the same for one, with the next keys to translate
//   npm run i18n:missing -- pt 40        a JSON skeleton of the next 40 untranslated keys, with
//                                        English as the text to replace; paste it into pt.json
//   npm run i18n:check                   what `npm test` runs on every contributed file
//
// docs/TRANSLATING.md is the guide. English is read through Vite's module loader, because the
// catalogs are TypeScript; nothing here changes a file but the one `new` creates and the one
// registry line it adds.

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

/** @typedef {typeof import("../src/i18n/community.ts")} CommunityModule */
/** @typedef {import("../src/i18n/community.ts").Message} Message */
/** @typedef {import("../src/i18n/community.ts").CommunityStatus} Status */

const panel = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const i18n = path.join(panel, "src", "i18n");
const communityDir = path.join(i18n, "community");
const registryFile = path.join(i18n, "communityLocales.ts");

/**
 * @param {string} message
 * @returns {never}
 */
function fail(message) {
  console.error(message);
  process.exit(1);
}

/**
 * English, the standard, and the pure helpers, loaded through Vite so the TypeScript needs no build.
 * @returns {Promise<{ community: CommunityModule, english: Map<string, Message> }>}
 */
async function load() {
  const server = await createServer({ root: panel, configFile: false, appType: "custom", logLevel: "error", server: { middlewareMode: true } });
  try {
    const community = /** @type {CommunityModule} */ (await server.ssrLoadModule("/src/i18n/community.ts"));
    const english = /** @type {Record<string, unknown>} */ (await server.ssrLoadModule("/src/i18n/en/index.ts"));
    return { community, english: new Map(community.flattenEnglish({ ...english })) };
  } finally {
    await server.close();
  }
}

/**
 * The registered languages, read from the registry's own text.
 * @returns {{ code: string, autonym: string, status: Status }[]}
 */
function registered() {
  const text = readFileSync(registryFile, "utf8");
  return [...text.matchAll(/\{ code: "([^"]+)", autonym: "([^"]+)", status: "(draft|released)" \}/g)].map((match) => ({
    code: match[1] ?? "",
    autonym: match[2] ?? "",
    status: /** @type {Status} */ (match[3]),
  }));
}

/**
 * @param {string} code
 * @returns {Record<string, unknown>}
 */
function fileOf(code) {
  const file = path.join(communityDir, `${code}.json`);
  if (!existsSync(file)) fail(`There is no ${path.relative(panel, file)}. Create the language first: npm run i18n:new -- ${code} <Name>`);
  /** @type {unknown} */
  const parsed = JSON.parse(readFileSync(file, "utf8"));
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) fail(`${path.relative(panel, file)} must be a JSON object of keys and texts.`);
  return /** @type {Record<string, unknown>} */ (parsed);
}

/**
 * @param {string | undefined} code
 * @param {string | undefined} autonym
 */
function create(code, autonym) {
  if (code === undefined || autonym === undefined) fail("Usage: npm run i18n:new -- <code> <Name of the language in itself>   (for example: pt Português)");
  if (!/^[a-z]{2,3}(-[A-Z]{2})?$/.test(code) || code === "en" || code === "es") fail(`${code} is not a code to add: write pt or pt-BR (English and Spanish already exist).`);
  if (registered().some((entry) => entry.code === code)) fail(`${code} is already registered.`);
  if (autonym.includes('"') || autonym.includes("\\")) fail("The name must not contain quotes or backslashes.");
  mkdirSync(communityDir, { recursive: true });
  writeFileSync(path.join(communityDir, `${code}.json`), "{}\n");
  const text = readFileSync(registryFile, "utf8");
  const marker = "] as const satisfies";
  if (!text.includes(marker)) fail("communityLocales.ts has changed shape: add the line by hand (see docs/TRANSLATING.md).");
  writeFileSync(registryFile, text.replace(marker, `  { code: "${code}", autonym: "${autonym}", status: "draft" },\n${marker}`));
  console.log(`Created src/i18n/community/${code}.json and registered ${code} (${autonym}) as a draft.`);
  console.log(`Next: npm run i18n:missing -- ${code} 40, translate what it prints into the file, then npm run i18n:check.`);
  console.log(`See it in the console: in the browser's developer tools, localStorage.setItem('noust.locale', '${code}'), then reload.`);
}

/** @param {string | undefined} code */
async function status(code) {
  const { community, english } = await load();
  const entries = registered().filter((entry) => code === undefined || entry.code === code);
  if (entries.length === 0) {
    console.log(code === undefined ? "No language has been contributed yet. Start one: npm run i18n:new -- pt Português" : `${code} is not registered.`);
    return;
  }
  for (const entry of entries) {
    const file = fileOf(entry.code);
    const share = community.completeness(file, english);
    const problems = community.check(file, english, entry.code);
    const state = problems.length === 0 ? "valid" : `${String(problems.length)} problem(s)`;
    console.log(`${entry.code}  ${entry.autonym}  ${entry.status}  ${(share * 100).toFixed(1)}% of ${String(english.size)} messages  ${state}`);
    for (const problem of problems.slice(0, 10)) console.log(`    ${problem.key}: ${problem.message}`);
    if (code !== undefined) {
      const left = community.missing(file, english).slice(0, 10);
      if (left.length > 0) console.log("  Next to translate:");
      for (const [key, message] of left) console.log(`    ${key}: ${JSON.stringify(message)}`);
    }
  }
}

/**
 * @param {string | undefined} code
 * @param {string | undefined} count
 */
async function skeleton(code, count) {
  if (code === undefined) fail("Usage: npm run i18n:missing -- <code> [how many, default 40]");
  const { community, english } = await load();
  const left = community.missing(fileOf(code), english).slice(0, Number(count ?? 40));
  console.log(JSON.stringify(Object.fromEntries(left), null, 2));
}

function check() {
  const result = spawnSync("npx", ["vitest", "run", "src/i18n/community.test.ts", "src/i18n/communityLocales.test.ts"], { cwd: panel, stdio: "inherit" });
  process.exit(result.status ?? 1);
}

const [command, ...rest] = process.argv.slice(2);
switch (command) {
  case "new":
    create(rest[0], rest.slice(1).join(" ") || undefined);
    break;
  case "status":
    await status(rest[0]);
    break;
  case "missing":
    await skeleton(rest[0], rest[1]);
    break;
  case "check":
    check();
    break;
  default:
    fail("Usage: node scripts/i18n.mjs new <code> <Name> | status [code] | missing <code> [count] | check");
}
