/**
 * A colour class that names no colour of the system compiles to nothing.
 *
 * Tailwind v4 drops a class it does not know, silently: the build passes, the page renders, and the
 * element simply has no colour. That is how six error messages were painted without red
 * (`text-danger`, a class that does not exist: the token is `--fail`, the utility `text-fail`).
 * The colours are the `--color-*` entries of app.css, each of which points at a custom property
 * that tokens.css defines; this test reads both and fails on any `text-*`, `bg-*`, `border-*`,
 * `ring-*`, `fill-*`, `stroke-*`, `divide-*`, `outline-*`, `decoration-*`, `caret-*` or
 * gradient-stop class, in a class list of any production file, that is neither a colour nor one of
 * the non-colour words the same prefix also takes (`text-center`, `border-t`, `ring-2`).
 *
 * A line that has to name something else that looks like a class says so, on that line or the one
 * above: `// design-exception: dead-class <reason>`.
 */

import ts from "typescript";
import { describe, expect, it } from "vitest";

import app from "./app.css?raw";
import tokens from "./tokens.css?raw";
import { EXCEPTION } from "./designRules";

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**", "!../styles/**", "!../i18n/**", "!../**/*.gen.ts"], {
  eager: true,
  query: "?raw",
  import: "default",
});

/** Production sources by path relative to src/. */
const SOURCES: ReadonlyMap<string, string> = new Map(Object.entries(RAW).map(([path, source]) => [path.replace(/^\.\.\//, ""), source]));

/** Custom properties tokens.css defines. */
const DEFINED = new Set([...tokens.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm)].map((match) => match[1] ?? ""));

/** Every utility app.css exposes as a colour: `--color-fg-muted: var(--text-muted)` is `fg-muted`. */
const COLOUR_ENTRIES = [...app.matchAll(/^\s*--color-([a-z0-9-]+)\s*:\s*var\((--[a-z0-9-]+)\)\s*;/gm)].map(
  (match): [name: string, token: string] => [match[1] ?? "", match[2] ?? ""],
);
const COLOURS = new Set(COLOUR_ENTRIES.map(([name]) => name));

/** Words Tailwind takes in place of a colour, which no theme removes. */
const KEYWORD_COLOURS = new Set(["transparent", "current", "inherit"]);

/** What each prefix also takes that is not a colour: its widths, styles, alignments, sizes. */
const NOT_A_COLOUR: Readonly<Record<string, RegExp>> = {
  // Sizes are the type roles (text-12...); the names Tailwind removed are off-role-text's to count.
  text: /^(?:\d+|xs|sm|base|lg|[2-9]?xl|left|center|right|justify|start|end|balance|pretty|wrap|nowrap|ellipsis|clip)$/,
  bg: /^(?:none|auto|cover|contain|fixed|local|scroll|repeat(?:-[xy]|-round|-space)?|no-repeat|center|(?:left|right)(?:-(?:top|bottom))?|(?:top|bottom)(?:-(?:left|right))?|(?:clip|origin|position|size|blend|linear|radial|conic|gradient|image)(?:-.+)?)$/,
  border: /^(?:\d+|solid|dashed|dotted|double|hidden|none|collapse|separate|spacing(?:-.+)?)$/,
  ring: /^(?:\d+|inset|offset-\d+)$/,
  outline: /^(?:\d+|none|solid|dashed|dotted|double|hidden|offset-\d+)$/,
  fill: /^none$/,
  stroke: /^(?:\d+|none)$/,
  divide: /^(?:[xy](?:-\d+|-reverse)?|solid|dashed|dotted|double|none)$/,
  decoration: /^(?:\d+|solid|double|dotted|dashed|wavy|auto|from-font|clone|slice)$/,
  caret: /^$/,
  from: /^\d+%$/,
  via: /^\d+%$/,
  to: /^\d+%$/,
  // The two shadows of the system (tokens.css --shadow-raised, --shadow-overlay).
  shadow: /^(?:raised|overlay|none)$/,
};

const PREFIX = /^(text|bg|border|ring|outline|fill|stroke|divide|decoration|caret|from|via|to|shadow)-(.+)$/;
/** `border-t`, `border-x-fail`: the side is part of the prefix, not of the colour. */
const SIDE = /^[trblxyse](?:-(.+))?$/;
/** `ring-offset-surface`: the offset's colour. */
const RING_OFFSET = /^offset-(.+)$/;

/** The part of a class after its variants (`hover:`, `data-[status=active]:`, `max-sm:`) and `!`. */
function utilityOf(token: string): string {
  let depth = 0;
  let start = 0;
  for (let index = 0; index < token.length; index += 1) {
    const char = token[index];
    if (char === "[" || char === "(") depth += 1;
    else if (char === "]" || char === ")") depth -= 1;
    else if (char === ":" && depth === 0) start = index + 1;
  }
  return token.slice(start).replace(/^!/, "").replace(/!$/, "");
}

/** The colour a utility names, without an opacity: `bg-bg/85` is `bg`. */
function withoutOpacity(rest: string): string {
  const slash = rest.lastIndexOf("/");
  if (slash === -1) return rest;
  const opacity = rest.slice(slash + 1);
  return /^(?:\d+(?:\.\d+)?|\[[^\]]+\])$/.test(opacity) ? rest.slice(0, slash) : rest;
}

/** Whether `rest` is a colour (or an arbitrary value, or a word the prefix takes) for `prefix`. */
function fine(prefix: string, rest: string): boolean {
  const name = withoutOpacity(rest);
  if (name === "") return true; // `text-${tone}`: built at run time, nothing to read.
  if (name.startsWith("[") || name.startsWith("(")) return true; // other rules own arbitrary values
  if (COLOURS.has(name) || KEYWORD_COLOURS.has(name)) return true;
  if (NOT_A_COLOUR[prefix]?.test(name) === true) return true;
  if (prefix === "border") {
    const side = SIDE.exec(name);
    if (side) return side[1] === undefined || fine("border", side[1]);
  }
  if (prefix === "ring") {
    const offset = RING_OFFSET.exec(name);
    if (offset?.[1] !== undefined) return fine("ring", offset[1]);
  }
  if (prefix === "outline") {
    const offset = RING_OFFSET.exec(name);
    if (offset?.[1] !== undefined) return fine("outline", offset[1]);
  }
  return false;
}

/** The classes in `text` that look like a colour utility and name no colour. */
export function deadClasses(text: string): string[] {
  const dead: string[] = [];
  for (const token of text.split(/\s+/)) {
    if (token === "") continue;
    const utility = utilityOf(token);
    const match = PREFIX.exec(utility);
    if (match?.[1] === undefined || match[2] === undefined) continue;
    if (!fine(match[1], match[2])) dead.push(token);
  }
  return dead;
}

/** A string that is part of a class list in the source: where the dead classes are looked for. */
function strings(file: ts.SourceFile, classNamesOnly: boolean): { text: string; line: number }[] {
  const found: { text: string; line: number }[] = [];
  const add = (node: ts.Node, text: string): void => {
    found.push({ text, line: file.getLineAndCharacterOfPosition(node.getStart(file)).line });
  };
  const visit = (node: ts.Node, inClass: boolean): void => {
    const here =
      inClass ||
      (ts.isJsxAttribute(node) && node.name.getText() === "className") ||
      (ts.isCallExpression(node) && ["cx", "clsx", "cn"].includes(node.expression.getText()));
    if (!classNamesOnly || here) {
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) add(node, node.text);
      else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) add(node, node.text);
    }
    ts.forEachChild(node, (child) => {
      visit(child, here);
    });
  };
  visit(file, false);
  return found;
}

/** The dead classes of one file, each with the line it is on, after the design-exception comments. */
export function deadClassesIn(path: string, source: string): { line: number; class: string }[] {
  const lines = source.split("\n");
  const excused = (line: number): boolean =>
    [lines[line], lines[line - 1]].some((text) => {
      const match = text === undefined ? null : EXCEPTION.exec(text);
      return match?.[1] === "dead-class";
    });
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, path.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  // The gallery lists token names as data ("text-muted"); only what it puts in a className is a class.
  return strings(file, path.startsWith("dev/")).flatMap(({ text, line }) =>
    excused(line) ? [] : deadClasses(text).map((name) => ({ line: line + 1, class: name })),
  );
}

describe("the colours a class can name", () => {
  it("are read from app.css, and each one points at a custom property tokens.css defines", () => {
    expect(COLOUR_ENTRIES.length).toBeGreaterThan(30);
    const undefinedTokens = COLOUR_ENTRIES.filter(([, token]) => !DEFINED.has(token)).map(([name, token]) => `${name} -> ${token}`);
    expect(undefinedTokens, "A --color-* utility whose variable is not in tokens.css").toEqual([]);
  });

  it("include the ones the console uses all day, and not the ones it once invented", () => {
    for (const name of ["fg", "fg-muted", "fg-faint", "accent-fg", "fail", "fail-soft", "fail-border", "ok", "warn", "idle", "surface-raised", "bg-sunken", "border-strong"]) {
      expect(COLOURS.has(name), name).toBe(true);
    }
    for (const name of ["danger", "danger-fg", "error", "success", "primary", "muted", "destructive", "red-500"]) expect(COLOURS.has(name), name).toBe(false);
  });
});

describe("what counts as a dead class", () => {
  it("catches the class that painted six errors without red", () => {
    expect(deadClasses("mt-1 text-13 text-danger")).toEqual(["text-danger"]);
    expect(deadClasses("text-danger-fg bg-danger-soft border-danger")).toEqual(["text-danger-fg", "bg-danger-soft", "border-danger"]);
  });

  it("catches it behind variants, an important mark and an opacity", () => {
    expect(deadClasses("hover:text-danger data-[status=active]:bg-primary !text-error")).toEqual(["hover:text-danger", "data-[status=active]:bg-primary", "!text-error"]);
    expect(deadClasses("bg-muted/50")).toEqual(["bg-muted/50"]);
  });

  it("lets a colour of the system through, with its variants and its opacity", () => {
    expect(deadClasses("text-fail bg-fail-soft border-fail-border hover:text-fg data-checked:bg-accent focus-visible:outline-focus bg-bg/85")).toEqual([]);
    expect(deadClasses("border-transparent text-current fill-current bg-inherit")).toEqual([]);
  });

  it("lets the words a prefix takes besides a colour through", () => {
    const words = "text-12 text-center text-balance border border-t border-b-0 border-y border-2 border-dashed border-collapse ring-2 ring-inset outline-2 outline-offset-2 outline-none stroke-2 fill-none divide-y divide-x-2 shadow-raised shadow-none decoration-dotted";
    expect(deadClasses(words)).toEqual([]);
  });

  it("reads a side, a ring offset and a gradient stop as the colour they carry", () => {
    expect(deadClasses("border-t-fail border-x-border ring-offset-surface from-accent to-transparent")).toEqual([]);
    expect(deadClasses("border-t-danger ring-offset-glow from-brand")).toEqual(["border-t-danger", "ring-offset-glow", "from-brand"]);
  });

  it("leaves what it cannot read to the rules that own it", () => {
    expect(deadClasses("bg-[color-mix(in_oklab,var(--fail-strong)_86%,black)] text-[0.85em] shadow-[inset_2px_0_0_var(--fail)] text-(--tone)")).toEqual([]);
    expect(deadClasses("text- bg-")).toEqual([]);
  });

  it("does not take a word for a class", () => {
    expect(deadClasses("the text colour is a token and the border is one pixel")).toEqual([]);
  });

  it("finds the class in a string of the source and not in a comment, and honours the exception", () => {
    const source = [
      "// text-danger in a comment",
      'export const A = () => <p className="text-13 text-danger">x</p>;',
      'export const B = () => <p className="text-fail">x</p>;',
      "// design-exception: dead-class it names a CSS property in a test fixture",
      'export const C = "text-align";',
      'export const D = cx("bg-muted", flag && "border-fail");',
      "export const E = `text-${tone}`;",
    ].join("\n");
    expect(deadClassesIn("features/x/Page.tsx", source)).toEqual([
      { line: 2, class: "text-danger" },
      { line: 6, class: "bg-muted" },
    ]);
  });

  it("reads only class lists in the gallery, which also lists token names as data", () => {
    const source = ['const TOKENS = [{ token: "text-muted" }];', 'export const A = () => <p className="text-muted">x</p>;'].join("\n");
    expect(deadClassesIn("dev/Foundations.tsx", source)).toEqual([{ line: 2, class: "text-muted" }]);
  });
});

describe("the console", () => {
  it("names only colours that exist", () => {
    const dead: string[] = [];
    for (const [path, source] of SOURCES) {
      for (const found of deadClassesIn(path, source)) dead.push(`${path}:${String(found.line)} ${found.class}`);
    }
    expect(dead, "A colour class that names no colour is dropped silently by Tailwind: use a token utility (text-fail, bg-surface...), see styles/dead-class.test.ts").toEqual([]);
  });

  it("reads the whole console", () => {
    expect(SOURCES.size).toBeGreaterThan(200);
  });
});
