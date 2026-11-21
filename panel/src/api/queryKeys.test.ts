/**
 * No two questions share a cache key.
 *
 * TanStack Query keeps one answer for each key, so two queries that ask different routes of the server under the same key read each
 * other's answers: Settings > Approvals asked `GET /api/approvals/policy` and Settings > Approval policy asked
 * `GET /api/config/approval-policy`, both as `["approvals", "policy"]`, and opening the second after the first in the same tab drew a
 * table from a document with no rows ("This page broke / Cannot read properties of undefined (reading 'filter')"). Nothing about it
 * showed until a person walked from one page to the other without reloading, which no test of one page does.
 *
 * This reads the syntax tree of every production file: each place that declares a query (an object with a `queryKey` and a `queryFn`)
 * is resolved to the key it will have, with what the key's parts take from the caller (an id, a filter) standing as `*`, and to the
 * route its `queryFn` asks. A key that stands for two routes is a collision. What the reader cannot resolve (a key built by a function
 * it cannot see through, a `queryFn` that calls a helper) is counted and left alone: the test says how much it saw, so it cannot pass
 * by reading nothing.
 */

import ts from "typescript";
import { describe, expect, it } from "vitest";

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**", "!../**/*.gen.ts"], {
  eager: true,
  query: "?raw",
  import: "default",
});

/** Production sources by path relative to src/ (the glob names this directory's files `./x`). */
const TEXTS: ReadonlyMap<string, string> = new Map(Object.entries(RAW).map(([path, text]) => [path.startsWith("./") ? `api/${path.slice(2)}` : path.replace(/^\.\.\//, ""), text]));

interface Source {
  path: string;
  file: ts.SourceFile;
}

/** What a file declares under a name: a key written as an array, or a function that makes one from what the caller gives. */
type Maker = { kind: "array"; expr: ts.Expression } | { kind: "function"; params: string[]; body: ts.Expression };

function unwrap(node: ts.Expression): ts.Expression {
  let current = node;
  for (;;) {
    if (ts.isParenthesizedExpression(current) || ts.isAsExpression(current) || ts.isSatisfiesExpression(current) || ts.isNonNullExpression(current) || ts.isTypeAssertionExpression(current)) {
      current = current.expression;
    } else {
      return current;
    }
  }
}

/** The expression a function returns, when it is one (an arrow with an expression body, or a block that only returns). */
function returned(fn: ts.ArrowFunction | ts.FunctionExpression): ts.Expression | null {
  if (!ts.isBlock(fn.body)) return fn.body;
  const last = fn.body.statements.at(-1);
  return last !== undefined && ts.isReturnStatement(last) && last.expression !== undefined ? last.expression : null;
}

function makerOf(value: ts.Expression): Maker | null {
  const expr = unwrap(value);
  if (ts.isArrayLiteralExpression(expr)) return { kind: "array", expr };
  if (ts.isArrowFunction(expr) || ts.isFunctionExpression(expr)) {
    const body = returned(expr);
    return body === null ? null : { kind: "function", params: expr.parameters.map((parameter) => (ts.isIdentifier(parameter.name) ? parameter.name.text : "")), body };
  }
  return null;
}

const sources: Source[] = [...TEXTS].flatMap(([name, text]) =>
  text.includes("queryKey") || /Keys\b/.test(text) ? [{ path: name, file: ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, name.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS) }] : [],
);

/** `const xKeys = { list: [...], one: (id) => [...] }`, by the name and the property, with the file it is in. */
const factories = new Map<string, { source: Source; makers: Map<string, Maker> }[]>();
/** `const KEY = [...]` and the like, by file and name. */
const constants = new Map<string, ts.Expression>();

for (const source of sources) {
  const visit = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer !== undefined) {
      const init = unwrap(node.initializer);
      constants.set(`${source.path}\0${node.name.text}`, init);
      if (ts.isObjectLiteralExpression(init)) {
        const makers = new Map<string, Maker>();
        for (const property of init.properties) {
          if (ts.isPropertyAssignment(property) && (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name))) {
            const maker = makerOf(property.initializer);
            if (maker !== null) makers.set(property.name.text, maker);
          }
        }
        if (makers.size > 0) factories.set(node.name.text, [...(factories.get(node.name.text) ?? []), { source, makers }]);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source.file);
}

/** The maker `name.property` stands for, in the file that asks (its own first, else the only one there is). */
function maker(source: Source, name: string, property: string): Maker | null {
  const declared = factories.get(name) ?? [];
  const own = declared.find((entry) => entry.source === source && entry.makers.has(property));
  if (own !== undefined) return own.makers.get(property) ?? null;
  const elsewhere = declared.filter((entry) => entry.makers.has(property));
  return elsewhere.length === 1 ? (elsewhere[0]?.makers.get(property) ?? null) : null;
}

/**
 * The key an expression stands for, one entry per part: a string as it is written, `*` for what the caller gives. Null where the
 * reader cannot follow it.
 */
function keyOf(source: Source, node: ts.Expression, depth = 0, given: ReadonlyMap<string, string> = new Map()): string[] | null {
  if (depth > 8) return null;
  const expr = unwrap(node);
  if (ts.isArrayLiteralExpression(expr)) {
    const parts: string[] = [];
    for (const element of expr.elements) {
      if (ts.isSpreadElement(element)) {
        const inner = keyOf(source, element.expression, depth + 1);
        if (inner === null) return null;
        parts.push(...inner);
      } else if (ts.isIdentifier(element) && given.has(element.text)) {
        parts.push(given.get(element.text) ?? "*");
      } else if (ts.isStringLiteralLike(element)) {
        parts.push(JSON.stringify(element.text));
      } else if (ts.isNumericLiteral(element) || element.kind === ts.SyntaxKind.TrueKeyword || element.kind === ts.SyntaxKind.FalseKeyword || element.kind === ts.SyntaxKind.NullKeyword) {
        parts.push(element.getText());
      } else {
        parts.push("*");
      }
    }
    return parts;
  }
  if (ts.isIdentifier(expr)) {
    const value = constants.get(`${source.path}\0${expr.text}`);
    return value === undefined ? null : keyOf(source, value, depth + 1);
  }
  if (ts.isPropertyAccessExpression(expr) && ts.isIdentifier(expr.expression)) {
    const found = maker(source, expr.expression.text, expr.name.text);
    return found?.kind === "array" ? keyOf(source, found.expr, depth + 1) : null;
  }
  if (ts.isCallExpression(expr) && ts.isPropertyAccessExpression(expr.expression) && ts.isIdentifier(expr.expression.expression)) {
    const found = maker(source, expr.expression.expression.text, expr.expression.name.text);
    if (found?.kind !== "function") return null;
    // A literal the caller writes is part of the key (`configKeys.section("ssl")`); anything else it gives is `*`.
    const values = new Map<string, string>();
    found.params.forEach((name, index) => {
      const argument = expr.arguments[index];
      const literal = argument !== undefined && ts.isStringLiteralLike(argument) ? JSON.stringify(argument.text) : argument !== undefined && ts.isNumericLiteral(argument) ? argument.text : "*";
      values.set(name, literal);
    });
    return keyOf(source, found.body, depth + 1, values);
  }
  return null;
}

/** The routes a `queryFn` asks, as `GET /api/apps/{domain}`: every `request("get", "/api/...")` inside it. */
function routesOf(node: ts.Node): string[] {
  const found = new Set<string>();
  const visit = (child: ts.Node): void => {
    if (ts.isCallExpression(child) && ts.isIdentifier(child.expression) && child.expression.text === "request") {
      const [method, route] = child.arguments;
      if (method !== undefined && route !== undefined && ts.isStringLiteralLike(method) && ts.isStringLiteralLike(route) && route.text.startsWith("/api")) {
        found.add(`${method.text.toUpperCase()} ${route.text}`);
      }
    }
    ts.forEachChild(child, visit);
  };
  visit(node);
  return [...found];
}

interface Site {
  path: string;
  line: number;
  key: string[];
  /** Every route its `queryFn` asks: a query that reads two routes and joins them is one question, and has both. */
  routes: string[];
}

const stats = { sites: 0, keyUnread: 0, routeUnread: 0 };
const sites: Site[] = [];

for (const source of sources) {
  const visit = (node: ts.Node): void => {
    if (ts.isObjectLiteralExpression(node)) {
      const named = (name: string): ts.ObjectLiteralElementLike | undefined => node.properties.find((property) => property.name !== undefined && ts.isIdentifier(property.name) && property.name.text === name);
      const queryKey = named("queryKey");
      const queryFn = named("queryFn");
      if (queryKey !== undefined && queryFn !== undefined) {
        stats.sites += 1;
        const key = ts.isPropertyAssignment(queryKey) ? keyOf(source, queryKey.initializer) : ts.isShorthandPropertyAssignment(queryKey) ? keyOf(source, queryKey.name) : null;
        const routes = routesOf(queryFn);
        if (key === null) stats.keyUnread += 1;
        else if (routes.length === 0) stats.routeUnread += 1;
        else sites.push({ path: source.path, line: source.file.getLineAndCharacterOfPosition(node.getStart()).line + 1, key, routes });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source.file);
}

/** Keys that stand for more than one route, and why that is not a collision. Only ever shorter: a line that no longer collides must go. */
const KNOWN: Readonly<Record<string, string>> = {};

/**
 * The keys that two places declare for different questions. One place that asks several routes under one key is a question that is
 * joined or chosen by what the caller gives (the catalog of a database reads two routes, a view of the fleet's Docker is one route
 * for each kind): the same key by design. Two places are a collision when they do not ask the same routes.
 */
function collisions(): Map<string, { routes: string[]; where: string[] }> {
  const byKey = new Map<string, Site[]>();
  for (const site of sites) {
    // A key made only of what the caller gives is not a key to compare: nothing in it says what it is.
    if (site.key.every((part) => part === "*")) continue;
    const id = `[${site.key.join(", ")}]`;
    byKey.set(id, [...(byKey.get(id) ?? []), site]);
  }
  const found = new Map<string, { routes: string[]; where: string[] }>();
  for (const [id, group] of byKey) {
    const asked = new Set(group.map((site) => [...site.routes].sort().join(" + ")));
    if (asked.size > 1) found.set(id, { routes: [...asked].sort(), where: group.map((site) => `${site.path}:${String(site.line)}`) });
  }
  return found;
}

describe("the cache keys of the queries", () => {
  it("is read from enough of the console not to pass over nothing", () => {
    expect(stats.sites, "places that declare a query").toBeGreaterThan(300);
    expect(sites.length, "of them, with a key and a route that could be read").toBeGreaterThan(250);
    // The few that are not read are the ones built by a helper; if most were, the test would be looking at less than it says.
    expect(sites.length / stats.sites).toBeGreaterThan(0.7);
  });

  it("gives every route its own key: one key never stands for two routes", () => {
    const found = [...collisions()].filter(([id]) => !(id in KNOWN));
    expect(
      found.map(([id, entry]) => `${id} is the key of ${entry.routes.join(" and ")} (${entry.where.join(", ")})`),
      "Two routes under one cache key read each other's answers. Give each its own key.",
    ).toEqual([]);
  });

  it("keeps no excuse for a key that no longer collides", () => {
    const now = collisions();
    expect(
      Object.keys(KNOWN).filter((id) => !now.has(id)),
      "Delete the line: the keys were made apart.",
    ).toEqual([]);
  });

  // The proof that the reader sees the defect it was written for: the two pages that shared `["approvals", "policy"]` are two routes.
  it("sees the two routes that once shared a key", () => {
    const keyOfRoute = (route: string): string[] | undefined => sites.find((site) => site.routes.includes(route))?.key;
    const approvals = keyOfRoute("GET /api/approvals/policy");
    const table = keyOfRoute("GET /api/config/approval-policy");
    expect(approvals).toBeDefined();
    expect(table).toBeDefined();
    expect(table).not.toEqual(approvals);
  });
});
