// How much of the console is built from each component the 4.0 wave moves, counted in the syntax tree.
//
//   node scripts/ux-counts.mjs [src-dir]              the table by directory (default: src)
//   node scripts/ux-counts.mjs [src-dir] --by-file    the same, one row per file (what a lane migrates)
//   node scripts/ux-counts.mjs [src-dir] --json       the table as JSON, to diff before and after
//   node scripts/ux-counts.mjs [src-dir] --rules      what the structural ratchet holds (design-structure-baseline.json)
//
// The counting is src/styles/designStructure.ts, the code styles/design-structure.test.ts holds the
// ratchet with: a number quoted from here and a number the test fails on are one measurement. That file
// is TypeScript that Node strips (Node 22.18 or newer, which panel/.nvmrc gives), so the script needs no
// build. It reads files and writes nothing.
//
// Columns: one JSX element per use of Notice, FeatureState, ActionDialog, Switch, SaveBar, LoadingRegion,
// QueryState, ConfirmDialog and Badge, and every `aria-busy` attribute. Tests, the gallery and the
// components' own files are left out. The table of docs/superpowers/specs/2026-10-17-noust-4.0-ux-design.md
// (annex B) is this output.

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** @type {typeof import("../src/styles/designStructure.ts")} */
let structure;
try {
  structure = await import("../src/styles/designStructure.ts");
} catch (error) {
  const code = error instanceof Error && "code" in error ? error.code : undefined;
  if (code !== "ERR_UNKNOWN_FILE_EXTENSION") throw error;
  console.error("ux-counts reads TypeScript directly: use Node 22.18 or newer (nvm use, see panel/.nvmrc).");
  process.exit(1);
}
const { STRUCTURE_RULES, UX_COLUMNS, countStructure, tallyUx, uxDirectory } = structure;

/**
 * Every .ts and .tsx file under a directory, as a path relative to it.
 *
 * @param {string} root
 * @param {string} [dir]
 * @returns {string[]}
 */
export function sourceFiles(root, dir = root) {
  /** @type {string[]} */
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...sourceFiles(root, file));
    else if (/\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts") && !entry.name.endsWith(".gen.ts")) found.push(path.relative(root, file).split(path.sep).join("/"));
  }
  return found.sort();
}

/**
 * The table: how many times each column is drawn, per directory (or per file).
 *
 * @param {string} root
 * @param {boolean} byFile
 * @returns {Record<string, Record<string, number>>}
 */
export function tally(root, byFile = false) {
  /** @type {Record<string, Record<string, number>>} */
  const table = {};
  for (const rel of sourceFiles(root)) {
    const row = tallyUx(rel, readFileSync(path.join(root, rel), "utf8"));
    if (row === null) continue;
    const key = byFile ? rel : uxDirectory(rel);
    if (byFile && Object.values(row).every((count) => count === 0)) continue;
    const into = (table[key] ??= Object.fromEntries(UX_COLUMNS.map((column) => [column, 0])));
    for (const column of UX_COLUMNS) into[column] = (into[column] ?? 0) + row[column];
  }
  return Object.fromEntries(Object.entries(table).sort(([a], [b]) => a.localeCompare(b)));
}

/**
 * What the structural ratchet counts: per rule, per file, after the design-exception comments.
 *
 * @param {string} root
 * @returns {Record<string, Record<string, number>>}
 */
export function ruleCounts(root) {
  /** @type {Record<string, Record<string, number>>} */
  const counts = Object.fromEntries(STRUCTURE_RULES.map((rule) => [rule.id, {}]));
  for (const rel of sourceFiles(root)) {
    if (/\.test\.tsx?$/.test(rel) || rel.startsWith("dev/") || rel.startsWith("test/") || rel.startsWith("styles/")) continue;
    for (const [rule, count] of Object.entries(countStructure(rel, readFileSync(path.join(root, rel), "utf8")))) {
      const into = counts[rule];
      if (into) into[rel] = count;
    }
  }
  return counts;
}

/** @param {Record<string, Record<string, number>>} table */
function totals(table) {
  return Object.fromEntries(UX_COLUMNS.map((column) => [column, Object.values(table).reduce((sum, row) => sum + (row[column] ?? 0), 0)]));
}

/** @param {Record<string, Record<string, number>>} table */
function files(table) {
  return UX_COLUMNS.map((column) => [column, Object.values(table).filter((row) => (row[column] ?? 0) > 0).length]);
}

const invoked = process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invoked) {
  const args = process.argv.slice(2);
  const flags = new Set(args.filter((arg) => arg.startsWith("--")));
  const root = path.resolve(args.find((arg) => !arg.startsWith("--")) ?? path.join(here, "..", "src"));
  if (flags.has("--rules")) {
    const counts = ruleCounts(root);
    for (const [rule, byPath] of Object.entries(counts)) {
      const total = Object.values(byPath).reduce((sum, count) => sum + count, 0);
      console.log(`${rule}: ${String(total)} in ${String(Object.keys(byPath).length)} files`);
    }
  } else {
    const byFile = flags.has("--by-file");
    const table = tally(root, byFile);
    if (flags.has("--json")) {
      console.log(JSON.stringify({ rows: table, total: totals(table) }, null, 2));
    } else {
      console.table({ ...table, total: totals(table) });
      if (!byFile) {
        console.log(`files that use each: ${files(tally(root, true)).map(([column, count]) => `${String(column)} ${String(count)}`).join(", ")}`);
      }
    }
  }
}
