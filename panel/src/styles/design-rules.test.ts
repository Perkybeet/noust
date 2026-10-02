/**
 * The design ratchet (docs/DESIGN.md, "Enforcement"). Every rule of designRules.ts is counted
 * in every production file and held to design-baseline.json, file by file:
 *
 * - a count above its baseline fails: that is a regression, fix it (or excuse the one line
 *   with `// design-exception: <rule-id> <reason>`);
 * - a count below its baseline fails too, asking for `npm run design:baseline`, so the
 *   progress is locked in and cannot be lost by the next change.
 *
 * `npm run design:baseline` (UPDATE_DESIGN_BASELINE=1) rewrites the file, and refuses to when
 * any count went up.
 */

import { describe, expect, it } from "vitest";

import eslintConfig from "../../eslint.config.js?raw";
import baseline from "./design-baseline.json";
import { ARBITRARY_DIMENSION, COLOUR_FUNCTION, DESIGN_RULES, EXCEPTION, countRules, lintViolations } from "./designRules";

type Counts = Record<string, Record<string, number>>;

interface Baseline {
  $comment?: string;
  rules: Counts;
  lintLegacy: string[];
  pendingAdoption: string[];
}

const RAW = import.meta.glob<string>(
  ["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../dev/**", "!../test/**", "!../styles/**", "!../**/*.gen.ts"],
  { eager: true, query: "?raw", import: "default" },
);

/** Production sources by path relative to src/. */
const SOURCES: ReadonlyMap<string, string> = new Map(Object.entries(RAW).map(([path, source]) => [path.replace(/^\.\.\//, ""), source]));

const KIT = /^(components|app|dev)\//;

/** The system's components a feature must be able to use: none may sit unused. */
const ADOPTION = [
  "Card",
  "Notice",
  "EmptyCell",
  "JobProgress",
  "FilterBar",
  "Subsection",
  "SaveBar",
  "ListPage",
  "DetailPage",
  "SettingsLayout",
  "DashboardPage",
  "Wizard",
  "Stepper",
  "FileEditorPage",
  "AuthLayout",
] as const;

function measure(): Counts {
  const counts: Counts = {};
  for (const rule of DESIGN_RULES) counts[rule.id] = {};
  for (const [path, source] of SOURCES) {
    for (const [rule, count] of Object.entries(countRules(path, source))) {
      const byFile = counts[rule];
      if (byFile) byFile[path] = count;
    }
  }
  return counts;
}

function adopted(name: string): boolean {
  const imported = new RegExp(`import\\s*\\{[^}]*\\b${name}\\b[^}]*\\}\\s*from\\s*"[^"]*components/(?:ui|page)[^"]*"`);
  return [...SOURCES].some(([path, source]) => !KIT.test(path) && imported.test(source));
}

function sortedObject<T>(entries: [string, T][]): Record<string, T> {
  return Object.fromEntries(entries.sort(([a], [b]) => a.localeCompare(b)));
}

const CURRENT = measure();
const BASE = baseline as Baseline;
const MODE = import.meta.env["UPDATE_DESIGN_BASELINE"] as string | undefined;

function regressions(): string[] {
  const out: string[] = [];
  for (const rule of DESIGN_RULES) {
    for (const [path, count] of Object.entries(CURRENT[rule.id] ?? {})) {
      const allowed = BASE.rules[rule.id]?.[path] ?? 0;
      if (count > allowed) out.push(`${rule.id}: ${path} has ${String(count)} (baseline ${String(allowed)}). ${rule.fix}`);
    }
  }
  return out;
}

function improvements(): string[] {
  const out: string[] = [];
  for (const rule of DESIGN_RULES) {
    for (const [path, allowed] of Object.entries(BASE.rules[rule.id] ?? {})) {
      const count = CURRENT[rule.id]?.[path] ?? 0;
      if (count < allowed) out.push(`${rule.id}: ${path} is down to ${String(count)} from ${String(allowed)}`);
    }
  }
  return out;
}

/** Files ESLint's design rules may skip: legacy files that still break one of them. */
function lintLegacy(all: boolean): string[] {
  const kept = new Set(BASE.lintLegacy);
  const out: string[] = [];
  for (const path of SOURCES.keys()) {
    if (KIT.test(path)) continue;
    const counts: Record<string, number> = {};
    for (const rule of DESIGN_RULES) counts[rule.id] = CURRENT[rule.id]?.[path] ?? 0;
    const file = `src/${path}`;
    if (lintViolations(counts) > 0 && (all || kept.has(file))) out.push(file);
  }
  return out.sort();
}

if (MODE !== undefined && MODE !== "") {
  describe("design baseline", () => {
    it("is rewritten from today's counts", async () => {
      const init = MODE === "init";
      if (!init) expect(regressions(), "Counts went up: fix them before moving the baseline.").toEqual([]);
      const next: Baseline = {
        $comment:
          "Written by `npm run design:baseline` (styles/design-rules.test.ts); may only go down. rules: count per rule and file. lintLegacy: files ESLint's design rules skip until migrated. pendingAdoption: system components no feature uses yet.",
        rules: sortedObject(
          DESIGN_RULES.map((rule) => [rule.id, sortedObject(Object.entries(CURRENT[rule.id] ?? {}))] as [string, Record<string, number>]),
        ),
        lintLegacy: lintLegacy(init),
        pendingAdoption: ADOPTION.filter((name) => !adopted(name) && (init || BASE.pendingAdoption.includes(name))),
      };
      // Node's fs, reached only in this mode (vitest runs from panel/): the console's own code
      // never touches a file.
      const [fsModule, processModule] = ["node:fs", "node:process"];
      const fs = (await import(/* @vite-ignore */ fsModule)) as { writeFileSync: (path: string, data: string) => void };
      const { cwd } = (await import(/* @vite-ignore */ processModule)) as { cwd: () => string };
      fs.writeFileSync(`${cwd()}/src/styles/design-baseline.json`, `${JSON.stringify(next, null, 2)}\n`);
    });
  });
} else {
  describe("design ratchet", () => {
    it("reads the whole console", () => {
      expect(SOURCES.size).toBeGreaterThan(200);
    });

    it("breaks no rule more often than the baseline allows", () => {
      expect(regressions(), "New design-rule violations (docs/DESIGN.md, Enforcement)").toEqual([]);
    });

    it("locks in every improvement: run `npm run design:baseline` when a count goes down", () => {
      expect(improvements(), "Counts went down: run `npm run design:baseline` and commit design-baseline.json").toEqual([]);
    });

    it("excuses a line only with a known rule and a reason", () => {
      const ids = new Set(DESIGN_RULES.map((rule) => rule.id));
      const bad: string[] = [];
      for (const [path, source] of SOURCES) {
        source.split("\n").forEach((line, index) => {
          if (!line.includes("design-exception:")) return;
          const match = EXCEPTION.exec(line);
          if (!match?.[1] || !ids.has(match[1]) || !match[2]?.trim()) bad.push(`${path}:${String(index + 1)}: ${line.trim()}`);
        });
      }
      expect(bad, "Write `design-exception: <rule-id> <reason>`").toEqual([]);
    });

    it("never silences a design lint rule without saying which exception and why", () => {
      const bad: string[] = [];
      for (const [path, source] of SOURCES) {
        if (KIT.test(path)) continue;
        source.split("\n").forEach((line, index) => {
          if (/eslint-disable.*no-restricted-(syntax|imports)/.test(line) && !line.includes("design-exception:")) {
            bad.push(`${path}:${String(index + 1)}`);
          }
        });
      }
      expect(bad).toEqual([]);
    });

    it("keeps ESLint's legacy list to files that still need it", () => {
      const list = BASE.lintLegacy;
      expect([...list].sort(), "lintLegacy is sorted").toEqual(list);
      expect(new Set(list).size, "lintLegacy has no duplicates").toBe(list.length);
      const needed = new Set(lintLegacy(true));
      const done = list.filter((file) => !needed.has(file));
      expect(done, "These files break no design lint rule any more: run `npm run design:baseline`").toEqual([]);
    });

    it("lints with the same patterns it counts: one definition of each rule", () => {
      expect(eslintConfig).toContain(ARBITRARY_DIMENSION.source);
      expect(eslintConfig).toContain(COLOUR_FUNCTION.source);
      for (const rule of DESIGN_RULES.filter((entry) => entry.lint === true)) expect(eslintConfig, rule.id).toContain(`${rule.id}:`);
    });

    it("keeps the chart series colours inside the chart", () => {
      const leaks = [...SOURCES].filter(([path, source]) => path !== "components/ui/Chart.tsx" && /\bviz-[1-9]\b/.test(source)).map(([path]) => path);
      expect(leaks).toEqual([]);
    });

    it("leaves no component of the system unused, except those still waiting for their pages", () => {
      const pending = new Set(BASE.pendingAdoption);
      const dead = ADOPTION.filter((name) => !pending.has(name) && !adopted(name));
      const arrived = ADOPTION.filter((name) => pending.has(name) && adopted(name));
      expect(dead, "No feature uses these any more").toEqual([]);
      expect(arrived, "These are used now: run `npm run design:baseline` to take them off pendingAdoption").toEqual([]);
    });
  });
}

describe("feature-state-notice (owner item 56)", () => {
  it("counts a neutral notice that says on or off, and not the FeatureState that replaces it", () => {
    const before = [
      '<Notice title={t("approvals.page.offTitle")}>{t("approvals.page.off")}</Notice>',
      '<Notice>{t("appSettings.builds.on")}</Notice>',
      '<Notice tone="warning" title={t("server.ssh.passwordsTitle")}>{t("server.ssh.passwordsDescription")}</Notice>',
    ].join("\n");
    expect(countRules("features/x/Page.tsx", before)["feature-state-notice"]).toBe(2);
    const after = '<FeatureState state="off" title={t("approvals.page.offTitle")}>{t("approvals.page.off")}</FeatureState>';
    expect(countRules("features/x/Page.tsx", after)["feature-state-notice"] ?? 0).toBe(0);
  });
});
