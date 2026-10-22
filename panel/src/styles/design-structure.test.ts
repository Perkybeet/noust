/**
 * The structural ratchet (docs/DESIGN.md, "Enforcement"): rules found in the syntax tree, which
 * a regular expression per line cannot see. Same contract as styles/design-rules.test.ts, with
 * its own baseline, design-structure-baseline.json:
 *
 * - a count above its baseline fails: that is a regression, fix it (or excuse the one line
 *   with `// design-exception: <rule-id> <reason>`);
 * - a count below its baseline fails too, asking for `npm run design:baseline`, so the
 *   progress is locked in and cannot be lost by the next change.
 *
 * `npm run design:baseline` (UPDATE_DESIGN_BASELINE=1) rewrites the file, and refuses to when
 * any count went up. A rule is added in designStructure.ts, with today's count as its baseline
 * (G-3): nothing counted there may ever grow.
 */

import { describe, expect, it } from "vitest";

import * as en from "../i18n/en";
import { EXCEPTION } from "./designRules";
import { STRUCTURE_EXCEPTION, STRUCTURE_RULES, UX_COLUMNS, countStructure, messageLookup, tallyUx, uxDirectory } from "./designStructure";
import type { StructureContext } from "./designStructure";
import baseline from "./design-structure-baseline.json";

type Counts = Record<string, Record<string, number>>;

interface Baseline {
  $comment?: string;
  rules: Counts;
}

const RAW = import.meta.glob<string>(
  ["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../dev/**", "!../test/**", "!../styles/**", "!../**/*.gen.ts"],
  { eager: true, query: "?raw", import: "default" },
);

/** Production sources by path relative to src/. */
const SOURCES: ReadonlyMap<string, string> = new Map(Object.entries(RAW).map(([path, source]) => [path.replace(/^\.\.\//, ""), source]));

function sortedObject<T>(entries: [string, T][]): Record<string, T> {
  return Object.fromEntries(entries.sort(([a], [b]) => a.localeCompare(b)));
}

/** The English catalog, for the rules that read what a notice says. */
const CONTEXT: StructureContext = { message: messageLookup(en) };

function measure(): Counts {
  const counts: Counts = {};
  for (const rule of STRUCTURE_RULES) counts[rule.id] = {};
  for (const [path, source] of SOURCES) {
    for (const [rule, count] of Object.entries(countStructure(path, source, CONTEXT))) {
      const byFile = counts[rule];
      if (byFile) byFile[path] = count;
    }
  }
  return counts;
}

const CURRENT = measure();
const BASE = baseline as Baseline;
const MODE = import.meta.env["UPDATE_DESIGN_BASELINE"] as string | undefined;

function regressions(): string[] {
  const out: string[] = [];
  for (const rule of STRUCTURE_RULES) {
    for (const [path, count] of Object.entries(CURRENT[rule.id] ?? {})) {
      const allowed = BASE.rules[rule.id]?.[path] ?? 0;
      if (count > allowed) out.push(`${rule.id}: ${path} has ${String(count)} (baseline ${String(allowed)}). ${rule.fix}`);
    }
  }
  return out;
}

function improvements(): string[] {
  const out: string[] = [];
  for (const rule of STRUCTURE_RULES) {
    for (const [path, allowed] of Object.entries(BASE.rules[rule.id] ?? {})) {
      const count = CURRENT[rule.id]?.[path] ?? 0;
      if (count < allowed) out.push(`${rule.id}: ${path} is down to ${String(count)} from ${String(allowed)}`);
    }
  }
  return out;
}

if (MODE !== undefined && MODE !== "") {
  describe("design structure baseline", () => {
    it("is rewritten from today's counts", async () => {
      if (MODE !== "init") expect(regressions(), "Counts went up: fix them before moving the baseline.").toEqual([]);
      const next: Baseline = {
        $comment:
          "Written by `npm run design:baseline` (styles/design-structure.test.ts); may only go down. rules: count per rule and file, found in the syntax tree (styles/designStructure.ts).",
        rules: sortedObject(
          STRUCTURE_RULES.map((rule) => [rule.id, sortedObject(Object.entries(CURRENT[rule.id] ?? {}))] as [string, Record<string, number>]),
        ),
      };
      // Node's fs, reached only in this mode (vitest runs from panel/): the console's own code
      // never touches a file.
      const [fsModule, processModule] = ["node:fs", "node:process"];
      const fs = (await import(/* @vite-ignore */ fsModule)) as { writeFileSync: (path: string, data: string) => void };
      const { cwd } = (await import(/* @vite-ignore */ processModule)) as { cwd: () => string };
      fs.writeFileSync(`${cwd()}/src/styles/design-structure-baseline.json`, `${JSON.stringify(next, null, 2)}\n`);
    });
  });
} else {
  describe("design structure ratchet", () => {
    it("reads the whole console", () => {
      expect(SOURCES.size).toBeGreaterThan(200);
    });

    it("breaks no rule more often than the baseline allows", () => {
      expect(regressions(), "New structural violations (docs/DESIGN.md, Enforcement)").toEqual([]);
    });

    it("locks in every improvement: run `npm run design:baseline` when a count goes down", () => {
      expect(improvements(), "Counts went down: run `npm run design:baseline` and commit design-structure-baseline.json").toEqual([]);
    });

    it("has a baseline entry for every rule, and for no rule that does not exist", () => {
      expect(Object.keys(BASE.rules).sort()).toEqual(STRUCTURE_RULES.map((rule) => rule.id).sort());
    });

    it("excuses a line the way the text ratchet does: one definition of the comment", () => {
      expect(STRUCTURE_EXCEPTION.source).toBe(EXCEPTION.source);
    });
  });
}

describe("what the structural rules count", () => {
  it("counts an element and not the same words in a comment, a string or an import", () => {
    const source = [
      'import { FeatureState } from "../../components/ui";',
      "// <FeatureState state=\"on\" /> was here",
      'const label = "<FeatureState>";',
      'export const A = () => <FeatureState state="on" title="x" />;',
      "export const B = () => <FeatureState state=\"off\">x</FeatureState>;",
    ].join("\n");
    expect(countStructure("features/x/Page.tsx", source)["feature-state"]).toBe(2);
  });

  it("does not count the kit's own definition, nor a rule where it does not apply", () => {
    expect(countStructure("components/ui/FeatureState.tsx", "export const A = () => <FeatureState />;")["feature-state"]).toBeUndefined();
    expect(countStructure("features/x/Page.tsx", 'export const A = () => <div aria-busy="true" />;')["hand-aria-busy"]).toBe(1);
    expect(countStructure("components/page/LoadingRegion.tsx", 'export const A = () => <div aria-busy="true" />;')["hand-aria-busy"]).toBeUndefined();
  });

  it("counts a literal aria-busy and not one the code computes", () => {
    const source = [
      'export const A = () => <div aria-busy="true" />;',
      "export const B = () => <div aria-busy={true} />;",
      "export const C = ({ loading }: { loading: boolean }) => <div aria-busy={loading} />;",
      'export const D = () => <div aria-busy="false" />;',
    ].join("\n");
    expect(countStructure("features/x/Page.tsx", source)["hand-aria-busy"]).toBe(2);
  });

  it("skips a line excused with a known rule and a reason, on that line or the one above", () => {
    const source = [
      "// design-exception: hand-aria-busy the kit cannot draw this one yet",
      'export const A = () => <div aria-busy="true" />;',
      'export const B = () => <div aria-busy="true" />;',
    ].join("\n");
    expect(countStructure("features/server/Page.tsx", source)["hand-aria-busy"]).toBe(1);
  });

  it("tallies each component of the table once per use, and leaves out what is not the console's", () => {
    const source = 'export const A = () => (<><Notice /><Notice tone="warning" /><Switch /><div aria-busy={false} /></>);';
    const row = tallyUx("features/x/Page.tsx", source);
    expect(row).toMatchObject({ Notice: 2, Switch: 1, "aria-busy": 1, FeatureState: 0 });
    expect(Object.keys(row ?? {})).toEqual([...UX_COLUMNS]);
    expect(tallyUx("features/x/Page.test.tsx", source)).toBeNull();
    expect(tallyUx("dev/Kit.tsx", source)).toBeNull();
    expect(tallyUx("components/ui/Notice.tsx", source)).toBeNull();
    expect(tallyUx("features/x/helpers.ts", source)).toBeNull();
  });

  it("names the row of the table a file belongs to", () => {
    expect(uxDirectory("features/app/settings/DeploysSettings.tsx")).toBe("features/app/settings");
    expect(uxDirectory("features/app/AppLayout.tsx")).toBe("features/app (page)");
    expect(uxDirectory("features/databases/DatabasesPage.tsx")).toBe("features/databases");
    expect(uxDirectory("components/ui/Card.tsx")).toBe("components");
  });
});
