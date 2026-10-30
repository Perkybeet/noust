/**
 * A route's options are loaded with the first page; its component is not.
 *
 * `autoCodeSplitting` moves a route's `component`, its pending and error components and its loader
 * into a chunk fetched when the route is opened, and leaves the rest (`validateSearch`,
 * `beforeLoad`, the static data) in the route file, which the router needs before it knows which
 * page it is on. What those options import is therefore in the script every visitor fetches first.
 * A `validateSearch` that lived in the same module as its page (`import { Page, validateSearch }`)
 * took the page with it: the volumes' drawers and the log viewer, the file editor, the table of the
 * server's messages. That was 110 kB of the 507 the first load weighed, drawn by none of the first
 * pages.
 *
 * So the options of a route may only import modules that reach no component: nothing with JSX
 * (`.tsx`) in the chain of runtime imports. A search parameter's reader goes to a `search.ts` beside
 * its page, which the page imports too. A type-only import costs nothing at run time and is not
 * followed.
 */

import ts from "typescript";
import { describe, expect, it } from "vitest";

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**", "!../dev/**", "!../**/*.gen.ts"], {
  eager: true,
  query: "?raw",
  import: "default",
});

/** Production sources by path relative to src/, with the routes among them (the glob names this directory's files `./x`). */
const SOURCES: ReadonlyMap<string, string> = new Map(
  Object.entries(RAW).map(([path, source]) => [path.startsWith("./") ? `routes/${path.slice(2)}` : path.replace(/^\.\.\//, ""), source]),
);

/** What the plugin moves out of the route file into the chunk of the page; anything else stays. */
const SPLIT = new Set(["component", "pendingComponent", "errorComponent", "notFoundComponent", "loader"]);

interface Import {
  /** The module as written, `../../features/logs/search`. */
  from: string;
  /** Whether the import carries anything at run time. */
  value: boolean;
}

/** The file a relative specifier names, or undefined for a package or a file this test does not read. */
function resolve(from: string, specifier: string): string | undefined {
  if (!specifier.startsWith(".")) return undefined;
  const parts = from.split("/").slice(0, -1);
  for (const part of specifier.split("/")) {
    if (part === "..") parts.pop();
    else if (part !== ".") parts.push(part);
  }
  const base = parts.join("/");
  return [`${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`, base].find((candidate) => SOURCES.has(candidate));
}

function parse(path: string): ts.SourceFile {
  return ts.createSourceFile(path, SOURCES.get(path) ?? "", ts.ScriptTarget.Latest, true, path.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
}

/** Every import and re-export of a file, with whether it keeps anything once the types are erased. */
function importsOf(path: string): Import[] {
  const found: Import[] = [];
  for (const node of parse(path).statements) {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.importClause;
      const named = clause?.namedBindings;
      const typeOnly =
        clause !== undefined &&
        (clause.phaseModifier === ts.SyntaxKind.TypeKeyword || (clause.name === undefined && named !== undefined && ts.isNamedImports(named) && named.elements.length > 0 && named.elements.every((element) => element.isTypeOnly)));
      found.push({ from: node.moduleSpecifier.text, value: !typeOnly });
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier !== undefined && ts.isStringLiteral(node.moduleSpecifier)) {
      found.push({ from: node.moduleSpecifier.text, value: !node.isTypeOnly });
    }
  }
  return found;
}

/** The chain of runtime imports from a module to the first component it reaches, if any. */
function reachesAComponent(start: string): string[] | undefined {
  const seen = new Set<string>();
  const walk = (path: string, chain: string[]): string[] | undefined => {
    if (seen.has(path)) return undefined;
    seen.add(path);
    if (path.endsWith(".tsx")) return [...chain, path];
    for (const edge of importsOf(path)) {
      const target = edge.value ? resolve(path, edge.from) : undefined;
      const found = target === undefined ? undefined : walk(target, [...chain, path]);
      if (found !== undefined) return found;
    }
    return undefined;
  };
  return walk(start, []);
}

/** The identifiers a node mentions. */
function names(node: ts.Node): Set<string> {
  const found = new Set<string>();
  const visit = (child: ts.Node): void => {
    if (ts.isIdentifier(child)) found.add(child.text);
    ts.forEachChild(child, visit);
  };
  visit(node);
  return found;
}

/** The modules a route file's options import at run time, by what they import them for. */
function optionImports(path: string): { option: string; from: string }[] {
  const file = parse(path);
  const bound = new Map<string, string>();
  const local = new Map<string, ts.Node>();
  for (const node of file.statements) {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier) && node.importClause !== undefined && node.importClause.phaseModifier !== ts.SyntaxKind.TypeKeyword) {
      const clause = node.importClause;
      if (clause.name !== undefined) bound.set(clause.name.text, node.moduleSpecifier.text);
      const named = clause.namedBindings;
      if (named !== undefined && ts.isNamedImports(named)) {
        for (const element of named.elements) if (!element.isTypeOnly) bound.set(element.name.text, node.moduleSpecifier.text);
      }
    } else if (ts.isFunctionDeclaration(node) && node.name !== undefined) {
      local.set(node.name.text, node);
    }
  }

  const options: ts.ObjectLiteralExpression[] = [];
  const find = (node: ts.Node): void => {
    // createFileRoute("/path")({ ...options })
    if (ts.isCallExpression(node) && ts.isCallExpression(node.expression) && node.expression.expression.getText(file) === "createFileRoute") {
      const argument = node.arguments[0];
      if (argument !== undefined && ts.isObjectLiteralExpression(argument)) options.push(argument);
    }
    ts.forEachChild(node, find);
  };
  find(file);

  const used: { option: string; from: string }[] = [];
  for (const literal of options) {
    for (const property of literal.properties) {
      if (property.name === undefined) continue;
      const option = property.name.getText(file);
      if (SPLIT.has(option)) continue;
      // The identifiers of the option, and of the functions of this file it calls, once.
      const pending = [...names(property)];
      const visited = new Set<string>();
      for (let name = pending.pop(); name !== undefined; name = pending.pop()) {
        if (visited.has(name)) continue;
        visited.add(name);
        const specifier = bound.get(name);
        if (specifier !== undefined) used.push({ option, from: specifier });
        const declaration = local.get(name);
        if (declaration !== undefined) pending.push(...names(declaration));
      }
    }
  }
  return used;
}

const ROUTES = [...SOURCES.keys()].filter((path) => path.startsWith("routes/")).sort();

describe("the options of a route", () => {
  it("find every route file", () => {
    expect(ROUTES.length).toBeGreaterThan(100);
  });

  it.each(ROUTES)("%s imports no component in what the router reads before the page", (route) => {
    const offenders = optionImports(route).flatMap(({ option, from }) => {
      const target = resolve(route, from);
      const chain = target === undefined ? undefined : reachesAComponent(target);
      return chain === undefined ? [] : [`${option} comes from ${from}, which reaches a component: ${chain.join(" > ")}`];
    });
    expect(offenders).toEqual([]);
  });
});

describe("the reading of a route file", () => {
  it("names the imports of the options that stay and not those of the component", () => {
    // volumes.tsx imports its page and its search reader; only the reader is an option.
    const used = optionImports("routes/_console/backups/volumes.tsx");
    expect(used.map((entry) => entry.option)).toContain("validateSearch");
    expect(used.every((entry) => entry.option !== "component")).toBe(true);
  });

  it("follows a function declared in the route file", () => {
    // The home route declares `validateSearch` itself and the reader it calls is imported.
    const home = optionImports("routes/_console/index.tsx");
    expect(home.length).toBeGreaterThan(0);
  });

  it("finds a component behind an import chain and not behind a type", () => {
    // A page is a component...
    expect(reachesAComponent("features/backups/volumes/VolumesPage.tsx")).toEqual(["features/backups/volumes/VolumesPage.tsx"]);
    // ...a reader of the address is not, and the types it imports do not count.
    expect(reachesAComponent("features/backups/volumes/search.ts")).toBeUndefined();
  });
});
