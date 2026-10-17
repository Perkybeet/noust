// Which namespaces of the catalogs a route draws, so the router can load them before the page is shown.
//
//   npm run i18n:routes              rewrites src/i18n/routeNamespaces.gen.ts
//   npm run i18n:routes -- --check   fails if that file is not what the source produces (`npm test` runs this)
//
// A message key is a string like "databases.list.title"; its first segment is a namespace, a file of
// src/i18n/en. A route draws every namespace named by a literal in the files its page reaches by
// import, dynamic imports included (a dialog loaded on demand is part of its page). That is an
// over-approximation, on purpose: a namespace loaded for nothing costs a few kB, one that is missing
// costs a page that shows English for a moment. The test of the routes (src/routes/namespaces.test.tsx)
// renders them with a cold catalog and holds the other side: nothing a page draws may be missing.
//
// Two things are left out of what a route reaches, and both for the same reason, that they are not the
// page's: the shared kit (components/, lib/, api/, nodes/, realtime/ and the shell in app/, which
// names a notice or an error that any page may show), and the frame around every console page, which
// is the chrome the first paint always has. A kit text that is not chrome is fetched when it is first
// needed, and the page is drawn again when it arrives (src/i18n/translate.ts); app/nav.ts is the one
// exception, because the tabs of an application, of settings, of teams and of the fleet are drawn
// from it. Its texts are attributed by export: a page that reads APP_TABS draws the application's
// tabs, not the labels of every settings section the same file lists.

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const panel = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(panel, "src");
const output = path.join(src, "i18n", "routeNamespaces.gen.ts");

/** The roots that frame every console page. Their texts are the chrome, not a route's. */
const FRAME = ["__root__", "/_console"];

/** Where the kit lives, under src: what it says is the page's only when the page says it too. */
const KIT = ["components", "lib", "api", "nodes", "realtime", "app"];
/** Kit files whose texts are a page's, read export by export: the lists the tabs are drawn from. */
const BY_EXPORT = ["app/nav.ts"];

/** @param {string} dir @param {string[]} [out] @returns {string[]} */
function walk(dir, out = []) {
  for (const name of readdirSync(dir).sort()) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".d.ts")) out.push(full);
  }
  return out;
}

/** The namespaces: one file each in i18n/en. @param {string} root @returns {string[]} */
function namespaces(root) {
  return readdirSync(path.join(root, "i18n", "en"))
    .filter((name) => name.endsWith(".ts") && name !== "index.ts")
    .map((name) => name.replace(/\.ts$/, ""))
    .sort();
}

/**
 * @param {string} from
 * @param {string} specifier
 * @returns {string | null}
 */
function resolveImport(from, specifier) {
  if (!specifier.startsWith(".")) return null;
  const base = path.resolve(path.dirname(from), specifier);
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")]) {
    if (existsSync(candidate) && statSync(candidate).isFile() && /\.tsx?$/.test(candidate)) return candidate;
  }
  return null;
}

/**
 * What a file imports (and which names of each), and which namespaces its string literals name:
 * all of them, and those of each top-level export separately.
 *
 * @param {string} file
 * @param {ReadonlySet<string>} known
 * @returns {{ imports: { target: string, names: Set<string> | null }[], used: Set<string>, shared: Set<string>, exported: Map<string, Set<string>> }}
 */
function read(file, known) {
  const text = readFileSync(file, "utf8");
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  /** @type {{ target: string, names: Set<string> | null }[]} */
  const imports = [];
  /** @type {Set<string>} */
  const used = new Set();
  /** @type {Set<string>} */
  const shared = new Set();
  /** @type {Map<string, Set<string>>} */
  const exported = new Map();
  /** The names the statement being read exports, or null outside one. @type {string[] | null} */
  let owners = null;

  /** @param {string} namespace */
  const note = (namespace) => {
    used.add(namespace);
    if (owners === null || owners.length === 0) shared.add(namespace);
    else for (const owner of owners) (exported.get(owner) ?? exported.set(owner, new Set()).get(owner))?.add(namespace);
  };

  /** @param {ts.Node} node */
  const visit = (node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier !== undefined && ts.isStringLiteral(node.moduleSpecifier)) {
      // A type is erased: it brings no code, and no texts.
      const typeOnly = ts.isImportDeclaration(node) ? node.importClause?.phaseModifier === ts.SyntaxKind.TypeKeyword : node.isTypeOnly;
      const target = typeOnly ? null : resolveImport(file, node.moduleSpecifier.text);
      if (target !== null) {
        /** @type {Set<string> | null} */
        let names = null;
        if (ts.isImportDeclaration(node)) {
          const bindings = node.importClause?.namedBindings;
          if (node.importClause?.name === undefined && bindings !== undefined && ts.isNamedImports(bindings)) {
            names = new Set(bindings.elements.map((element) => (element.propertyName ?? element.name).text));
          }
        } else if (node.exportClause !== undefined && ts.isNamedExports(node.exportClause)) {
          names = new Set(node.exportClause.elements.map((element) => (element.propertyName ?? element.name).text));
        }
        imports.push({ target, names });
      }
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const argument = node.arguments[0];
      const target = argument !== undefined && ts.isStringLiteralLike(argument) ? resolveImport(file, argument.text) : null;
      if (target !== null) imports.push({ target, names: null });
    }
    if (ts.isStringLiteralLike(node) || ts.isTemplateHead(node)) {
      const first = /^([A-Za-z0-9]+)\./.exec(node.text)?.[1];
      if (first !== undefined && known.has(first)) note(first);
    }
    ts.forEachChild(node, visit);
  };

  for (const statement of source.statements) {
    const isExported = ts.canHaveModifiers(statement) && ts.getModifiers(statement)?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword) === true;
    /** @type {string[]} */
    let names = [];
    if (isExported && ts.isVariableStatement(statement)) {
      names = statement.declarationList.declarations.flatMap((declaration) => (ts.isIdentifier(declaration.name) ? [declaration.name.text] : []));
    } else if (isExported && (ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement)) && statement.name !== undefined) {
      names = [statement.name.text];
    }
    owners = names;
    visit(statement);
    owners = null;
  }
  return { imports, used, shared, exported };
}

/**
 * Each route's namespaces, by route id (`/_console/databases/`), as the source says them.
 *
 * @param {string} [root] The src directory to read: the console's own unless a test gives its own.
 * @returns {Record<string, string[]>}
 */
export function routeNamespaces(root = src) {
  const known = new Set(namespaces(root));
  const kitDirs = KIT.map((dir) => path.join(root, dir) + path.sep);
  const byExport = new Set(BY_EXPORT.map((file) => path.join(root, file)));
  /** @type {Map<string, ReturnType<typeof read>>} */
  const cache = new Map();
  /** @param {string} file */
  const info = (file) => {
    let entry = cache.get(file);
    if (entry === undefined) {
      entry = read(file, known);
      cache.set(file, entry);
    }
    return entry;
  };

  /** @type {Record<string, string[]>} */
  const routes = {};
  for (const file of walk(path.join(root, "routes"))) {
    const text = readFileSync(file, "utf8");
    const id = /createFileRoute\(\s*"([^"]+)"/.exec(text)?.[1] ?? (text.includes("createRootRoute") ? "__root__" : null);
    if (id === null) continue;
    if (FRAME.includes(id)) {
      routes[id] = [];
      continue;
    }
    /** What has been read of each file: the names asked for, or "*" for all of it. @type {Map<string, Set<string>>} */
    const seen = new Map();
    /** @type {Set<string>} */
    const wanted = new Set();
    /** @type {[string, Set<string> | null][]} */
    const stack = [[file, null]];
    for (let next = stack.pop(); next !== undefined; next = stack.pop()) {
      const [target, names] = next;
      const done = seen.get(target) ?? new Set();
      const asked = names === null ? ["*"] : [...names];
      const fresh = asked.filter((name) => !done.has(name) && !done.has("*"));
      if (fresh.length === 0) continue;
      const first = done.size === 0;
      for (const name of fresh) done.add(name);
      seen.set(target, done);
      const entry = info(target);
      const kit = kitDirs.some((dir) => target.startsWith(dir));
      if (byExport.has(target)) {
        for (const namespace of entry.shared) wanted.add(namespace);
        for (const name of fresh) {
          const from = name === "*" ? entry.used : (entry.exported.get(name) ?? new Set());
          for (const namespace of from) wanted.add(namespace);
        }
      } else if (!kit) {
        for (const namespace of entry.used) wanted.add(namespace);
      }
      if (first) for (const { target: imported, names: importedNames } of entry.imports) stack.push([imported, importedNames]);
    }
    routes[id] = [...wanted].sort();
  }
  return Object.fromEntries(Object.entries(routes).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/**
 * The text of the generated file.
 *
 * @param {Record<string, string[]>} routes
 * @returns {string}
 */
export function render(routes) {
  const lines = Object.entries(routes).map(([id, list]) => `  ${JSON.stringify(id)}: [${list.map((namespace) => JSON.stringify(namespace)).join(", ")}],`);
  return [
    "// Generated by `npm run i18n:routes` (scripts/i18n-routes.mjs). Do not edit by hand: a test fails when it is stale.",
    "// The namespaces of the catalogs each route draws, by route id. See src/i18n/routeNamespaces.ts for how they are used.",
    "",
    'import type { Namespace } from "./types";',
    "",
    "export const ROUTE_NAMESPACES: Readonly<Record<string, readonly Namespace[]>> = {",
    ...lines,
    "};",
    "",
  ].join("\n");
}

/** The file as it is on disk, or the empty string. @returns {string} */
export function current() {
  return existsSync(output) ? readFileSync(output, "utf8") : "";
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const wanted = render(routeNamespaces());
  if (process.argv.includes("--check")) {
    if (current() !== wanted) {
      console.error(`${path.relative(panel, output)} is stale. Run: npm run i18n:routes`);
      process.exit(1);
    }
    console.log("The namespaces of the routes are current.");
  } else {
    writeFileSync(output, wanted);
    console.log(`Wrote ${path.relative(panel, output)}.`);
  }
}
