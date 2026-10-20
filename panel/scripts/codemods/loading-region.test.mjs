// The codemod that turns a hand-written loading region into LoadingRegion, held on the cases it
// knows and on the ones it must leave alone: a rewrite that changes what a page does is worse than
// the pattern it removes.

import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import ts from "typescript";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { countStructure } from "../../src/styles/designStructure.ts";
import { main, transform } from "./loading-region.mjs";

const SRC = "/panel/src";
const FILE = `${SRC}/features/x/Page.tsx`;

const HEAD = 'import { Skeleton } from "../../components/ui";\nimport { Card, QueryState } from "../../components/page";\n';

/** A component that draws the given JSX, under the usual imports. @param {string} body */
const page = (body) => `${HEAD}\nexport function A() {\n  return (\n${body}\n  );\n}\n`;

describe("the common pattern", () => {
  it("becomes a LoadingRegion with the same label, className and skeleton", () => {
    const before = page(`    <div aria-busy="true" className="flex flex-col gap-2">
      <span className="sr-only">{t("server.power.checksLoading")}</span>
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-24 w-full" />
    </div>`);
    const result = transform(before, FILE, { srcRoot: SRC });
    expect(result.rewritten).toBe(1);
    expect(result.skipped).toEqual([]);
    expect(result.code).toBe(
      page(`    <LoadingRegion label={t("server.power.checksLoading")} className="flex flex-col gap-2">
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-24 w-full" />
    </LoadingRegion>`).replace('import { Card, QueryState } from "../../components/page";', 'import { Card, LoadingRegion, QueryState } from "../../components/page";'),
    );
  });

  it("needs no className, and takes a plain text or a string as its label", () => {
    const before = page(`    <>
      <div aria-busy={true}>
        <span className="sr-only">{t("a.loading")}</span>
        <Skeleton className="h-8" />
      </div>
      <div aria-busy="true">
        <span className="sr-only">Loading the thing</span>
        <Skeleton className="h-8" />
      </div>
      <div aria-busy="true">
        <span className="sr-only">{"Loading it"}</span>
        <Skeleton className="h-8" />
      </div>
    </>`);
    const { code, rewritten } = transform(before, FILE, { srcRoot: SRC });
    expect(rewritten).toBe(3);
    expect(code).toContain('<LoadingRegion label={t("a.loading")}>');
    expect(code).toContain('<LoadingRegion label="Loading the thing">');
    expect(code).toContain('<LoadingRegion label={"Loading it"}>');
    expect(code.match(/<\/LoadingRegion>/g)).toHaveLength(3);
    expect(code).not.toContain("</div>");
  });

  it("keeps a className that is an expression, and a nested element, untouched", () => {
    const before = page(`    <div aria-busy="true" className={cx("grid", wide && "lg:grid-cols-2")}>
      <span className="sr-only">{t("a.loading", { count: 3 })}</span>
      <div aria-hidden="true" className="grid gap-4">
        <Skeleton />
      </div>
    </div>`);
    const { code } = transform(before, FILE, { srcRoot: SRC });
    expect(code).toContain('<LoadingRegion label={t("a.loading", { count: 3 })} className={cx("grid", wide && "lg:grid-cols-2")}>');
    expect(code).toContain('      <div aria-hidden="true" className="grid gap-4">\n        <Skeleton />\n      </div>\n    </LoadingRegion>');
  });

  it("rewrites a region inside a region and several in one file", () => {
    const before = page(`    <div aria-busy="true">
      <span className="sr-only">{t("outer")}</span>
      <div aria-busy="true">
        <span className="sr-only">{t("inner")}</span>
        <Skeleton />
      </div>
    </div>`);
    const { code, rewritten } = transform(before, FILE, { srcRoot: SRC });
    expect(rewritten).toBe(2);
    expect(code).toContain('<LoadingRegion label={t("outer")}>\n      <LoadingRegion label={t("inner")}>\n        <Skeleton />\n      </LoadingRegion>\n    </LoadingRegion>');
  });

  it("changes nothing when run again, and leaves a file without aria-busy as it is", () => {
    const before = page(`    <div aria-busy="true">
      <span className="sr-only">{t("a")}</span>
      <Skeleton />
    </div>`);
    const once = transform(before, FILE, { srcRoot: SRC }).code;
    const twice = transform(once, FILE, { srcRoot: SRC });
    expect(twice.code).toBe(once);
    expect(twice.rewritten).toBe(0);
    expect(transform("export const A = () => <div />;\n", FILE, { srcRoot: SRC }).code).toBe("export const A = () => <div />;\n");
  });

  it("produces source that parses, and that the ratchet no longer counts", () => {
    const before = page(`    <div aria-busy="true" className="flex">
      <span className="sr-only">{t("a")}</span>
      <Skeleton />
    </div>`);
    expect(countStructure("features/x/Page.tsx", before)["hand-aria-busy"]).toBe(1);
    const { code } = transform(before, FILE, { srcRoot: SRC });
    const output = ts.transpileModule(code, { fileName: FILE, reportDiagnostics: true, compilerOptions: { jsx: ts.JsxEmit.Preserve } });
    expect(output.diagnostics).toEqual([]);
    expect(countStructure("features/x/Page.tsx", code)["hand-aria-busy"]).toBeUndefined();
  });
});

describe("the import", () => {
  const region = `    <div aria-busy="true">
      <span className="sr-only">{t("a")}</span>
      <Skeleton />
    </div>`;

  it("joins the import of components/page, in order, when there is one", () => {
    const { code } = transform(`import { Card, QueryState } from "../../components/page";\nexport const A = () => (\n${region}\n);\n`, FILE, { srcRoot: SRC });
    expect(code.split("\n")[0]).toBe('import { Card, LoadingRegion, QueryState } from "../../components/page";');
  });

  it("keeps a multi-line import in lines, and appends to a list that is not sorted", () => {
    const multi = transform(`import {\n  Card,\n  QueryState,\n} from "../../components/page";\nexport const A = () => (\n${region}\n);\n`, FILE, { srcRoot: SRC });
    expect(multi.code.split("\n").slice(0, 6)).toEqual(["import {", "  Card,", "  LoadingRegion,", "  QueryState,", '} from "../../components/page";', "export const A = () => ("]);
    const unsorted = transform(`import { QueryState, Card } from "../../components/page";\nexport const A = () => (\n${region}\n);\n`, FILE, { srcRoot: SRC });
    expect(unsorted.code.split("\n")[0]).toBe('import { QueryState, Card, LoadingRegion } from "../../components/page";');
  });

  it("does not import twice, whichever way it was imported", () => {
    const { code } = transform(`import { LoadingRegion } from "../../components/page";\nexport const A = () => (\n${region}\n);\n`, FILE, { srcRoot: SRC });
    expect(code.match(/LoadingRegion }/g)).toHaveLength(1);
    expect(code.match(/import /g)).toHaveLength(1);
  });

  it("adds a line of its own, with the path from the file to the kit, when the file has no import of it", () => {
    const { code } = transform(`import { Skeleton } from "../../components/ui";\nexport const A = () => (\n${region}\n);\n`, FILE, { srcRoot: SRC });
    expect(code.split("\n").slice(0, 3)).toEqual(['import { Skeleton } from "../../components/ui";', 'import { LoadingRegion } from "../../components/page";', "export const A = () => ("]);
    const deep = transform(`export const A = () => (\n${region}\n);\n`, `${SRC}/features/app/settings/Deep.tsx`, { srcRoot: SRC });
    expect(deep.code.startsWith('import { LoadingRegion } from "../../../components/page";\n\n')).toBe(true);
    const shallow = transform(`export const A = () => (\n${region}\n);\n`, `${SRC}/Top.tsx`, { srcRoot: SRC });
    expect(shallow.code.startsWith('import { LoadingRegion } from "./components/page";')).toBe(true);
  });
});

describe("what it leaves alone, and says why", () => {
  /** @param {string} body @returns {string[]} */
  const reasons = (body) => {
    const result = transform(page(body), FILE, { srcRoot: SRC });
    expect(result.rewritten).toBe(0);
    expect(result.code).toBe(page(body));
    return result.skipped.map((entry) => entry.reason);
  };

  it("an element that is not a div", () => {
    expect(reasons(`    <ul aria-busy="true" aria-label={t("a")} className="divide-y">\n      <li><Skeleton /></li>\n    </ul>`)).toEqual([expect.stringMatching(/^it is a <ul>/)]);
  });

  it("an element that carries more than a className, such as the id a test reads", () => {
    expect(reasons(`    <div aria-busy="true" data-testid="loading">\n      <span className="sr-only">{t("a")}</span>\n      <Skeleton />\n    </div>`)).toEqual([expect.stringMatching(/data-testid/)]);
    expect(reasons(`    <div aria-busy="true" {...rest}>\n      <span className="sr-only">{t("a")}</span>\n      <Skeleton />\n    </div>`)).toEqual([expect.stringMatching(/a spread/)]);
  });

  it("a region with no screen-reader label first: there is no sentence to make the label from", () => {
    expect(reasons(`    <div aria-busy="true">\n      <Skeleton />\n    </div>`)).toEqual([expect.stringMatching(/first child is not the screen-reader span/)]);
    expect(reasons(`    <div aria-busy="true">\n      <Skeleton />\n      <span className="sr-only">{t("a")}</span>\n    </div>`)).toEqual([expect.stringMatching(/first child/)]);
  });

  it("a label that is not a plain message", () => {
    expect(reasons(`    <div aria-busy="true">\n      <span className="sr-only">{t.rich("a", { x: <b /> })}</span>\n      <Skeleton />\n    </div>`)).toEqual([expect.stringMatching(/not a plain message/)]);
    expect(reasons(`    <div aria-busy="true">\n      <span className="sr-only">{\`a \${b}\`}</span>\n      <Skeleton />\n    </div>`)).toEqual([expect.stringMatching(/not a plain message/)]);
    expect(reasons(`    <div aria-busy="true">\n      <span className="sr-only">He said "no"</span>\n      <Skeleton />\n    </div>`)).toEqual([expect.stringMatching(/characters to escape/)]);
  });

  it("a span that is not exactly sr-only, and a region that holds nothing but its label", () => {
    expect(reasons(`    <div aria-busy="true">\n      <span className="sr-only text-12">{t("a")}</span>\n      <Skeleton />\n    </div>`)).toEqual([expect.stringMatching(/not exactly/)]);
    expect(reasons(`    <div aria-busy="true">\n      <span className="sr-only">{t("a")}</span>\n    </div>`)).toEqual([expect.stringMatching(/no skeleton to keep/)]);
  });

  it("a busy flag the code computes is not the pattern", () => {
    const body = `    <div aria-busy={loading}>\n      <span className="sr-only">{t("a")}</span>\n      <Skeleton />\n    </div>`;
    const result = transform(page(body), FILE, { srcRoot: SRC });
    expect(result.rewritten).toBe(0);
    expect(result.skipped).toEqual([]);
  });

  it("names the line of each region it leaves", () => {
    const body = `    <>\n      <div aria-busy="true">\n        <Skeleton />\n      </div>\n    </>`;
    const [entry] = transform(page(body), FILE, { srcRoot: SRC }).skipped;
    expect(entry?.line).toBe(page(body).split("\n").findIndex((line) => line.includes("aria-busy")) + 1);
  });
});

describe("the command", () => {
  /** @type {string[]} */
  const made = [];
  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });
  afterEach(() => {
    for (const root of made.splice(0)) rmSync(root, { recursive: true, force: true });
  });

  /** A src tree with one page that has the pattern and one the kit draws. */
  function tree() {
    const root = mkdtempSync(path.join(tmpdir(), "noust-codemod-"));
    made.push(root);
    mkdirSync(path.join(root, "features", "x"), { recursive: true });
    mkdirSync(path.join(root, "components", "page"), { recursive: true });
    const region = '<div aria-busy="true">\n<span className="sr-only">{t("a")}</span>\n<Skeleton />\n</div>';
    writeFileSync(path.join(root, "features", "x", "Page.tsx"), `export const A = () => (\n${region}\n);\n`);
    writeFileSync(path.join(root, "components", "page", "LoadingRegion.tsx"), `export const L = () => (\n${region}\n);\n`);
    return root;
  }

  it("says what it would do and writes nothing without --write", () => {
    const root = tree();
    const before = readFileSync(path.join(root, "features", "x", "Page.tsx"), "utf8");
    expect(main([root])).toBe(0);
    expect(readFileSync(path.join(root, "features", "x", "Page.tsx"), "utf8")).toBe(before);
  });

  it("rewrites with --write, and leaves the kit's own region alone", () => {
    const root = tree();
    expect(main(["--write", root])).toBe(0);
    expect(readFileSync(path.join(root, "features", "x", "Page.tsx"), "utf8")).toContain("<LoadingRegion label={t(\"a\")}>");
    expect(readFileSync(path.join(root, "components", "page", "LoadingRegion.tsx"), "utf8")).toContain('aria-busy="true"');
  });

  it("asks for a path", () => {
    expect(main([])).toBe(2);
  });
});
