// The map of which namespaces a route draws is generated from the source, and `npm test` holds it:
// stale, it names the command that fixes it. The rules the generator follows are checked on a tree
// made up for the purpose, so each one has a case that would break it.

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { current, render, routeNamespaces } from "./i18n-routes.mjs";

/** @type {string[]} */
const made = [];

/**
 * A src directory with the given files.
 *
 * @param {Record<string, string>} files
 * @returns {string}
 */
function tree(files) {
  const root = mkdtempSync(path.join(tmpdir(), "noust-i18n-routes-"));
  made.push(root);
  for (const [name, text] of Object.entries({ ...files })) {
    const file = path.join(root, name);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, text);
  }
  for (const namespace of ["alpha", "beta", "gamma", "delta", "epsilon"]) {
    mkdirSync(path.join(root, "i18n", "en"), { recursive: true });
    writeFileSync(path.join(root, "i18n", "en", `${namespace}.ts`), `export const ${namespace} = {} as const;\n`);
  }
  writeFileSync(path.join(root, "i18n", "en", "index.ts"), "export {};\n");
  return root;
}

afterEach(() => {
  for (const root of made.splice(0)) rmSync(root, { recursive: true, force: true });
});

/**
 * A route file.
 *
 * @param {string} id
 * @param {string} [body]
 */
const route = (id, body = "") => `import { createFileRoute } from "@tanstack/react-router";\n${body}\nexport const Route = createFileRoute("${id}")({});\n`;

describe("the namespaces of a route", () => {
  it("are those named by the literals in the files its page reaches by import", () => {
    const root = tree({
      "routes/__root.tsx": "export const Route = createRootRouteWithContext()({});\n",
      "routes/_console.tsx": route("/_console", 'import { Shell } from "../app/Shell";'),
      "routes/_console/page.tsx": route("/_console/page", 'import { Page } from "../../features/page/Page";'),
      "features/page/Page.tsx": 'import { Part } from "./Part";\nexport const Page = () => "alpha.title";\n',
      "features/page/Part.tsx": 'export const Part = () => `gamma.items.${1}`;\n',
      "app/Shell.tsx": 'export const Shell = () => "epsilon.frame";\n',
    });
    expect(routeNamespaces(root)).toEqual({
      __root__: [],
      "/_console": [],
      "/_console/page": ["alpha", "gamma"],
    });
  });

  it("include what a page loads on demand, which is still the page's", () => {
    const root = tree({
      "routes/page.tsx": route("/page", 'import { Page } from "../features/Page";'),
      "features/Page.tsx": 'export const Dialog = () => import("./Dialog");\n',
      "features/Dialog.tsx": 'export const Dialog = () => "beta.dialog.title";\n',
    });
    expect(routeNamespaces(root)["/page"]).toEqual(["beta"]);
  });

  it("leave out a type, which is erased and brings no text", () => {
    const root = tree({
      "routes/page.tsx": route("/page", 'import type { Shape } from "../features/Shape";\nimport { type Other } from "../features/Other";'),
      "features/Shape.ts": 'export type Shape = "beta.never.drawn";\n',
      "features/Other.ts": 'export type Other = "gamma.never.drawn";\n',
    });
    // `import { type X }` keeps the module for its effects; only the whole-declaration form is erased.
    expect(routeNamespaces(root)["/page"]).not.toContain("beta");
  });

  it("leave out the kit, whose texts belong to whatever shows them", () => {
    const root = tree({
      "routes/page.tsx": route("/page", 'import { Notice } from "../components/Notice";\nimport { Page } from "../features/Page";'),
      "components/Notice.tsx": 'export const Notice = () => "beta.hub.refused";\n',
      "lib/words.ts": 'export const words = "gamma.words";\n',
      "features/Page.tsx": 'import { words } from "../lib/words";\nexport const Page = () => "alpha.title" + words;\n',
    });
    expect(routeNamespaces(root)["/page"]).toEqual(["alpha"]);
  });

  it("read app/nav.ts by export: the tabs a page draws, not every list the file holds", () => {
    const root = tree({
      "routes/page.tsx": route("/page", 'import { TABS } from "../app/nav";'),
      "routes/other.tsx": route("/other", 'import * as nav from "../app/nav";\nexport const all = nav;'),
      "app/nav.ts": [
        'const helper = "epsilon.shared";',
        'export const TABS = [{ label: "delta.tab", extra: helper }];',
        'export const OTHER = [{ label: "beta.other" }];',
        "",
      ].join("\n"),
    });
    const found = routeNamespaces(root);
    expect(found["/page"]).toEqual(["delta", "epsilon"]);
    // The namespace import is every list.
    expect(found["/other"]).toEqual(["beta", "delta", "epsilon"]);
  });

  it("ignore a word that only looks like a key, and a namespace that does not exist", () => {
    const root = tree({
      "routes/page.tsx": route("/page", 'import { Page } from "../features/Page";'),
      "features/Page.tsx": 'export const x = ["nowhere.title", "alphabet.title", "no dot", "alpha"];\n',
    });
    expect(routeNamespaces(root)["/page"]).toEqual([]);
  });

  it("follow a re-export and a cycle without looping", () => {
    const root = tree({
      "routes/page.tsx": route("/page", 'import { A } from "../features";'),
      "features/index.ts": 'export { A } from "./A";\n',
      "features/A.tsx": 'import { B } from "./B";\nexport const A = () => ["alpha.a", B];\n',
      "features/B.tsx": 'import { A } from "./A";\nexport const B = () => ["gamma.b", A];\n',
    });
    expect(routeNamespaces(root)["/page"]).toEqual(["alpha", "gamma"]);
  });

  it("do not read tests", () => {
    const root = tree({
      "routes/page.tsx": route("/page", 'import { Page } from "../features/Page";'),
      "routes/page.test.tsx": route("/page-test"),
      "features/Page.tsx": 'export const Page = () => "alpha.title";\n',
      "features/Page.test.tsx": 'import "./Page"; const x = "beta.test";\n',
    });
    expect(Object.keys(routeNamespaces(root))).toEqual(["/page"]);
  });
});

describe("the generated file", () => {
  it("lists every route of the console with the namespaces it draws", () => {
    const text = render({ "/a": ["x", "y"], "/b": [] });
    expect(text).toContain('"/a": ["x", "y"],');
    expect(text).toContain('"/b": [],');
    expect(text).toContain("Do not edit by hand");
  });

  // Reads and parses the whole of src: a few seconds on a busy machine.
  const SLOW = 60_000;

  it(
    "is what the source produces: run `npm run i18n:routes` when this fails",
    () => {
      expect(current() === render(routeNamespaces()), "src/i18n/routeNamespaces.gen.ts is stale: run `npm run i18n:routes` in panel/").toBe(true);
    },
    SLOW,
  );

  it(
    "describes the roots, the layouts and the pages of the console",
    () => {
      const routes = routeNamespaces();
      expect(Object.keys(routes)).toContain("__root__");
      expect(Object.keys(routes)).toContain("/_console");
      expect(Object.keys(routes).length).toBeGreaterThan(100);
      expect(routes["/_console/databases/"]).toContain("databases");
    },
    SLOW,
  );
});
