/**
 * The terminal is the terminal and the web is the web (owner item 123, docs/DESIGN.md CL-1): in the areas of
 * applications, data and network a command is shown only where the console cannot do what it does, and then it says
 * why. This holds those areas to it directly, and not through the design ratchet's baseline, so a command that comes
 * back is a failure with its file named, not a count that happens to fit.
 */

import { describe, expect, it } from "vitest";

import { COMMAND_HINT_WITHOUT_REASON } from "../styles/designRules";

const AREAS = ["app", "new-app", "databases", "domains", "network", "catalog", "variables", "teams", "backups", "apps", "container", "cron"] as const;

const RAW = import.meta.glob<string>(["./**/*.{ts,tsx}", "!./**/*.test.{ts,tsx}", "!./**/*.gen.ts"], { eager: true, query: "?raw", import: "default" });

/** Production sources of the areas, by path relative to features/. */
const SOURCES: [string, string][] = Object.entries(RAW)
  .map(([path, source]): [string, string] => [path.replace(/^\.\//, ""), source])
  .filter(([path]) => AREAS.some((area) => path.startsWith(`${area}/`)));

/** Each command the areas still draw, with the reason it gives: the console cannot do it, or the system said it. */
const KEPT: Readonly<Record<string, readonly string[]>> = {
  "app/SchemaChangeDialog.tsx": ["solo-terminal", "solo-terminal"],
  "app/metrics/MetricsTab.tsx": ["salida-del-sistema"],
  "backups/MisplacedBackupsNotice.tsx": ["salida-del-sistema"],
  "catalog/CatalogPackage.tsx": ["solo-terminal"],
  "catalog/RecipeRequirements.tsx": ["salida-del-sistema"],
  "container/ContainerFields.tsx": ["solo-terminal"],
  "databases/import/ImportDumpDialog.tsx": ["solo-terminal"],
  "new-app/RemoteImport.tsx": ["salida-del-sistema"],
};

/** The attributes of every `<CommandHint ...>` of a source, read to its closing `/>`. */
function hints(source: string): string[] {
  return [...source.matchAll(/<CommandHint\b((?:[^/]|\/(?!>))*)\/>/g)].map((match) => match[1] ?? "");
}

/** The reason of one hint's attributes, or null when it says none. */
function reasonOf(attributes: string): string | null {
  return /\breason="([a-z-]+)"/.exec(attributes)?.[1] ?? null;
}

/** The source of every `<EmptyState ...>` opening tag, braces and all, up to the `>` that closes it. */
function emptyStates(source: string): string[] {
  const found: string[] = [];
  for (const start of source.matchAll(/<EmptyState\b/g)) {
    let depth = 0;
    let end = start.index;
    for (; end < source.length; end += 1) {
      const char = source[end];
      if (char === "{") depth += 1;
      else if (char === "}") depth -= 1;
      else if (char === ">" && depth === 0 && source[end - 1] !== "=") break;
    }
    found.push(source.slice(start.index, end + 1));
  }
  return found;
}

describe("the terminal hints of applications, data and network", () => {
  it("reads the areas: there is something to hold", () => {
    expect(SOURCES.length).toBeGreaterThan(200);
  });

  it("has none that says no reason", () => {
    const owing = SOURCES.filter(([, source]) => new RegExp(COMMAND_HINT_WITHOUT_REASON.source).test(source)).map(([path]) => path);
    expect(owing, "A command with no reason repeats a button: take it out, or say why only the terminal can do it").toEqual([]);
  });

  it("keeps exactly the commands the console cannot do or the system said, each with its reason", () => {
    const drawn: Record<string, string[]> = {};
    for (const [path, source] of SOURCES) {
      const reasons = hints(source).map((attributes) => reasonOf(attributes) ?? "none");
      if (reasons.length > 0) drawn[path] = reasons;
    }
    expect(drawn).toEqual(KEPT);
  });

  it("draws no command in a first-use empty state: its button does what the command does", () => {
    const drawing = SOURCES.filter(([, source]) => emptyStates(source).some((tag) => /\n\s*command=|\scommand=\{|\scommand="/.test(tag))).map(([path]) => path);
    expect(drawing, "An EmptyState's command repeats its action: take it out").toEqual([]);
  });
});
