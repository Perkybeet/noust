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
import appCss from "./app.css?raw";
import { ARBITRARY_DIMENSION, COLOUR_FUNCTION, DESIGN_RULES, EXCEPTION, KIT_HEIGHTS, countRules, heightViolations, heightsUsed, lintViolations } from "./designRules";
import fontsCss from "./fonts.css?raw";
import tokensCss from "./tokens.css?raw";
import { STRUCTURE_RULES } from "./designStructure";

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
      // The structural rules (designStructure.ts) are excused with the same comment.
      const ids = new Set([...DESIGN_RULES.map((rule) => rule.id), ...STRUCTURE_RULES.map((rule) => rule.id)]);
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
  it("counts a neutral notice that says on or off, and not the pill in a card's head that replaces it", () => {
    const before = [
      '<Notice title={t("approvals.page.offTitle")}>{t("approvals.page.off")}</Notice>',
      '<Notice>{t("appSettings.builds.on")}</Notice>',
      '<Notice tone="warning" title={t("server.ssh.passwordsTitle")}>{t("server.ssh.passwordsDescription")}</Notice>',
    ].join("\n");
    expect(countRules("features/x/Page.tsx", before)["feature-state-notice"]).toBe(2);
    const after = '<Card title={t("approvals.page.title")} status={<StatusPill state="stopped" label={t("approvals.page.offTitle")} size="sm" />} />';
    expect(countRules("features/x/Page.tsx", after)["feature-state-notice"] ?? 0).toBe(0);
  });
});

/**
 * The tics of a templated interface, and the heights by role (docs/DESIGN.md 2.6 and 2.12).
 * The owner named the stripe of colour on the edge of a card himself; the rest are the marks that
 * give a templated interface away. Each detector has samples that must be caught and samples that
 * must not, so a pattern that stopped matching cannot pass for a console without tics.
 */
describe("the tics that are never written", () => {
  const FEATURE = "features/x/Page.tsx";
  const count = (rule: string, source: string, path = FEATURE): number => countRules(path, source)[rule] ?? 0;

  const BAD: Record<string, string[]> = {
    "side-stripe": [
      '<li className="border-l-2 border-fail pl-3">',
      '<li className="border-accent border-l-4">',
      '<div className="rounded-card border-t-2 border-warn-border">',
      '<div className="border-r-ok">',
      '<div className="shadow-[inset_2px_0_0_var(--warn)]">',
      '<div className="shadow-[inset_0_3px_0_var(--accent)]">',
      '<div className="state-rail-fail">',
    ],
    gradient: [
      '<div className="bg-linear-to-r from-accent to-bg">',
      '<div className="bg-gradient-to-b">',
      '<div className="bg-radial from-surface">',
      "const fill = ctx.createLinearGradient(0, 0, 0, 10);",
      '<div className="tab-strip mask-linear-to-r">',
      '<div className="bg-[radial-gradient(var(--border)_1px,transparent_1px)]">',
    ],
    blur: ['<header className="bg-bg/85 backdrop-blur-md">', '<div className="blur-sm">', '<div className="backdrop-filter">', '<div className="blur-[2px]">'],
    "diffuse-shadow": ['<div className="shadow-md">', '<div className="shadow-xl">', '<div className="drop-shadow-lg">', '<div className="shadow-sm">'],
    "caps-label": ['<span className="text-12 uppercase tracking-wider">', '<span className="tracking-widest">', '<span className="small-caps">', '<span className="capitalize">'],
    "middle-dot": ['<span>{a} · {b}</span>', 'const text = "Next.js · :3001";', "<span>a &middot; b</span>"],
    "arrow-glyph": ['<Button>Continue →</Button>', 'const label = "Back →";', "<span>a &rarr; b</span>"],
    "arrow-button": ["<Button trailingIcon={<ArrowRight />}>Next</Button>", "<Button trailingIcon={<ChevronRight className=\"x\" />}>Next</Button>"],
    "icon-tile": [
      '<span className="flex size-10 items-center justify-center rounded-control border border-border bg-surface">',
      '<div className="size-8 rounded-card bg-accent-soft flex items-center justify-center">',
      '<div className="inline-flex size-9 items-center justify-center rounded-chip bg-bg-sunken text-fg-muted">',
    ],
    "tooltip-by-hand": ["<Tooltip content={t('x')}><span>?</span></Tooltip>"],
    "elev-outside-kit": ['<div className="elev-seated rounded-card">', '<button className="elev-raised">', '<div className="elev-lift">'],
  };

  const GOOD: Record<string, string[]> = {
    "side-stripe": [
      '<li className="border-b border-border">',
      '<div className="border border-fail-border bg-fail-soft">',
      '<div className="border-t border-border">',
      '<div className="border-l border-border-strong">',
      '<div className="divide-y divide-border">',
      '<div className="shadow-[inset_0_0_0_2px_var(--accent)]">',
    ],
    gradient: ['<div className="bg-surface">', "const gradientBackoff = 3;"],
    blur: ['<header className="bg-bg">', '<div className="blurb">'],
    "diffuse-shadow": ['<div className="elev-seated">', '<div className="shadow-none">'],
    "caps-label": ['<span className="title text-12">', "const uppercased = 1;"],
    "middle-dot": ['<span>{a}, {b}</span>', "const dot = 'a.b';"],
    "arrow-glyph": ["<Button>Continue</Button>", "const t = 'a -> b';"],
    "arrow-button": ["<Button icon={<ArrowRight />}>Next</Button>", "<TextLink>Back</TextLink>"],
    "icon-tile": [
      '<span className="flex size-10 items-center justify-center rounded-pill bg-accent text-on-accent">',
      '<span className="flex size-6 items-center justify-center rounded-control bg-surface">',
      '<svg className="size-10 text-fg-muted">',
    ],
    "tooltip-by-hand": ["<IconButton label={t('x')} icon={<X />} />", "const tooltipText = 1;"],
    "elev-outside-kit": ['<div className="rounded-card">', "const elevation = 1;"],
  };

  it.each(Object.entries(BAD).flatMap(([rule, samples]) => samples.map((sample) => [rule, sample] as const)))("%s catches %s", (rule, sample) => {
    expect(count(rule, sample), sample).toBeGreaterThan(0);
  });

  it.each(Object.entries(GOOD).flatMap(([rule, samples]) => samples.map((sample) => [rule, sample] as const)))("%s lets %s through", (rule, sample) => {
    expect(count(rule, sample), sample).toBe(0);
  });

  it("holds the kit to the rules that apply everywhere, a stripe on a card included", () => {
    expect(count("side-stripe", '<div className="border-l-2 border-fail">', "components/ui/Card.tsx")).toBe(1);
    expect(count("blur", '<div className="backdrop-blur-md">', "app/Topbar.tsx")).toBe(1);
  });

  it("lets only LogoTile draw a tile, and the kit alone draw a height", () => {
    const tile = '<span className="flex size-10 items-center justify-center rounded-control bg-bg-sunken">';
    expect(count("icon-tile", tile, "components/ui/LogoTile.tsx")).toBe(0);
    expect(count("icon-tile", tile, "components/ui/EmptyState.tsx")).toBe(1);
    expect(count("elev-outside-kit", '<div className="elev-seated">', "components/ui/Card.tsx")).toBe(0);
  });

  it("is excused by a design-exception with its reason, like every rule", () => {
    const source = '// design-exception: middle-dot the glyph of a terminal charset\nconst dot = "·";';
    expect(count("middle-dot", source)).toBe(0);
  });

  describe("in the stylesheets", () => {
    const SHEETS = { "app.css": appCss, "tokens.css": tokensCss, "fonts.css": fontsCss };
    // The rules of the tokens and of app.css are plain CSS: what the text rules above find in a class, these find in a rule.
    const NO = {
      "a gradient": /(?:linear|radial|conic|repeating-linear|repeating-radial)-gradient\(/,
      "a blur": /backdrop-filter|filter:\s*blur|blur\(/,
      "capitals": /text-transform:\s*uppercase|font-variant(?:-caps)?:\s*small-caps/,
      "a wide tracking": /letter-spacing:\s*(?:0?\.\d+|[1-9]\d*(?:\.\d+)?)(?:px|em|rem)/,
      "a coloured side": /border-(?:left|right|top|bottom)(?:-color)?:[^;]*var\(--(?:ok|warn|fail|idle|accent)(?![\w-])/,
      "a coloured one-sided shadow": /(?:box-shadow|--elev-[a-z-]+):\s*(?:inset\s+)?(?:-?\d+px\s+0\s+0|0\s+-?\d+px\s+0)\s+var\(--(?:ok|warn|fail|idle|accent)(?![\w-])/,
    } as const;

    it.each(Object.entries(SHEETS).flatMap(([name, css]) => Object.entries(NO).map(([tic, pattern]) => [name, tic, css, pattern] as const)))(
      "%s has no %s",
      (_name, _tic, css, pattern) => {
        expect(css.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(pattern);
      },
    );

    it("catches each of them when one is written (the detectors are not dead)", () => {
      const bad: [keyof typeof NO, string][] = [
        ["a gradient", ".x { background: linear-gradient(red, blue); }"],
        ["a gradient", ".x { mask-image: linear-gradient(to right, #000, transparent); }"],
        ["a blur", ".x { backdrop-filter: blur(4px); }"],
        ["a blur", ".x { filter: blur(2px); }"],
        ["capitals", ".x { text-transform: uppercase; }"],
        ["a wide tracking", ".x { letter-spacing: 0.08em; }"],
        ["a coloured side", ".x { border-left: 3px solid var(--fail); }"],
        ["a coloured side", ".x { border-bottom-color: var(--accent); }"],
        ["a coloured one-sided shadow", ".x { box-shadow: inset 3px 0 0 var(--ok); }"],
        ["a coloured one-sided shadow", ":root { --elev-x: 0 -2px 0 var(--warn); }"],
      ];
      for (const [tic, css] of bad) expect(css, tic).toMatch(NO[tic]);
      expect(".x { letter-spacing: -0.01em; }").not.toMatch(NO["a wide tracking"]);
      expect(".x { border-bottom: 1px solid var(--border); }").not.toMatch(NO["a coloured side"]);
    });
  });
});

describe("the three heights, by role", () => {
  const KIT_FILES = [...SOURCES].filter(([path]) => /^(?:components|app)\//.test(path));

  it("gives every piece of the kit that draws a height the height of its role, and no other", () => {
    const wrong = KIT_FILES.flatMap(([path, source]) => heightViolations(path, source));
    expect(wrong, "A piece takes the height of its role (styles/designRules.ts, KIT_HEIGHTS)").toEqual([]);
  });

  it("has every role in the table drawn by the file it names", () => {
    const missing = Object.entries(KIT_HEIGHTS)
      .filter(([path, heights]) => {
        const source = SOURCES.get(path);
        if (source === undefined) return true;
        const used = heightsUsed(source);
        return heights.some((height) => !used.has(height));
      })
      .map(([path]) => path);
    expect(missing, "KIT_HEIGHTS names a role the file does not draw (or a file that does not exist)").toEqual([]);
  });

  it("makes what is typed or chosen inside carved, through one frame the fields share", () => {
    for (const file of ["components/ui/Select.tsx", "components/ui/Combobox.tsx", "components/ui/Textarea.tsx"]) {
      expect(SOURCES.get(file), file).toMatch(/\bCONTROL_FRAME\b/);
    }
    expect(SOURCES.get("components/ui/Input.tsx")).toMatch(/CONTROL_FRAME =[^;]*elev-carved/);
  });

  it("makes every button raised and every panel seated", () => {
    expect([...heightsUsed(SOURCES.get("components/ui/Button.tsx") ?? "")]).toEqual(["raised"]);
    expect([...heightsUsed(SOURCES.get("components/ui/Card.tsx") ?? "")]).toEqual(["seated"]);
    expect([...heightsUsed(SOURCES.get("components/ui/DataTable.tsx") ?? "")]).toEqual(["seated"]);
  });

  describe("the checker is not dead", () => {
    it("fails a panel that is raised and a button that is carved", () => {
      expect(heightViolations("components/ui/Card.tsx", '<div className="elev-raised">')).toHaveLength(1);
      expect(heightViolations("components/ui/Button.tsx", '<button className="elev-carved">')).toHaveLength(1);
      expect(heightViolations("components/ui/Dialog.tsx", '<div className="elev-lift">')).toHaveLength(1);
    });

    it("fails a piece that has no role and draws a height", () => {
      expect(heightViolations("components/ui/NewThing.tsx", '<div className="elev-seated">')).toEqual([expect.stringContaining("no role")]);
    });

    it("lets a piece draw what its role says, and a piece with no height draw nothing", () => {
      expect(heightViolations("components/ui/Card.tsx", '<div className="elev-seated">')).toEqual([]);
      expect(heightViolations("components/ui/Switch.tsx", '<span className="elev-carved-fine"><i className="elev-rest" /></span>')).toEqual([]);
      expect(heightViolations("components/ui/Badge.tsx", '<span className="bg-bg-sunken">')).toEqual([]);
    });

    it("reads every utility of each height", () => {
      expect([...heightsUsed('"elev-carved-fine"')]).toEqual(["carved"]);
      expect([...heightsUsed('"elev-raised-accent"')]).toEqual(["raised"]);
      expect([...heightsUsed('"elev-raised-chosen"')]).toEqual(["raised"]);
      expect([...heightsUsed('"elev-lift"')]).toEqual(["raised"]);
      expect([...heightsUsed('"elev-rest"')]).toEqual(["raised"]);
      expect([...heightsUsed('"elev-seated"')]).toEqual(["seated"]);
      expect(heightsUsed('"elevation"').size).toBe(0);
    });
  });
});

/**
 * Who can do what, and why a page shows a command (design 11.1.4 and item 123). The two rules count what a page still
 * owes, per file, against a baseline that only goes down; the pages redrawn with the content kit owe nothing.
 */
describe("what a page declares: the permission of its writes and the reason of its commands", () => {
  const PAGE = "features/x/Page.tsx";
  const count = (rule: string, source: string, path = PAGE): number => countRules(path, source)[rule] ?? 0;

  it.each(["remove.mutate(name);", "await save.mutateAsync({ name });", "<Button onClick={() => test.mutate(login.name)}>Test</Button>"])(
    "counts the write in %s as one that declares no permission",
    (source) => {
      expect(count("write-without-permission", source)).toBe(1);
    },
  );

  it("counts a write once per call, however many a file has", () => {
    expect(count("write-without-permission", "a.mutate(1);\nb.mutate(2);\nc.mutateAsync(3);")).toBe(3);
  });

  it("lets a declaration cover a write, one for one, and never more than there are", () => {
    const writes = "a.mutate(1);\nb.mutate(2);\nc.mutate(3);";
    expect(count("write-without-permission", `const can = useCan("apps.manage");\n${writes}`)).toBe(2);
    expect(count("write-without-permission", `<GuardedButton permission="apps.manage" />\n<Can permission="apps.manage" />\n${writes}`)).toBe(1);
    expect(count("write-without-permission", `<GuardedButton /><Can /><ReadOnlyLine />\n${writes}`)).toBe(0);
    expect(count("write-without-permission", 'if (org.can("apps.manage")) {}\n')).toBe(0);
  });

  it("reads a team's own question as a declaration, and a variable called can as none", () => {
    expect(count("write-without-permission", 'const ok = org.can("apps.manage", team);\nsave.mutate(1);')).toBe(0);
    expect(count("write-without-permission", "const can = 3;\nsave.mutate(1);")).toBe(1);
  });

  it("counts only a feature: the kit and the gallery are not pages", () => {
    expect(count("write-without-permission", "save.mutate(1);", "components/ui/Setting.tsx")).toBe(0);
    expect(count("write-without-permission", "save.mutate(1);", "app/Shell.tsx")).toBe(0);
  });

  it("is excused by a design-exception with its reason, like every rule", () => {
    expect(count("write-without-permission", "// design-exception: write-without-permission the server refuses what a role cannot do and this is a read\nsave.mutate(1);")).toBe(0);
  });

  it.each([
    '<CommandHint command="noust list" />',
    '<CommandHint command="noust list" label={t("common.words.fromTerminal")} />',
    '<CommandHint\n  command="noust web expose-hooks hooks.example.com"\n  label={t("x")}\n/>',
    "<CommandHint command={`noust app ${name} => x`} label=\"a\" />",
  ])("counts the command in %s: it says no reason", (source) => {
    expect(count("command-hint-reason", source)).toBe(1);
  });

  it.each([
    '<CommandHint command="noust central seal" reason="solo-terminal" />',
    '<CommandHint\n  command="noust fleet authorize"\n  label={t("x")}\n  reason="otro-servidor"\n/>',
    '<CommandHint reason="salida-del-sistema" command="nginx -t" />',
  ])("lets %s through: it says why it is there", (source) => {
    expect(count("command-hint-reason", source)).toBe(0);
  });

  it("counts every command of a file, the one with a reason beside the ones without", () => {
    const source = '<CommandHint command="a" reason="solo-terminal" />\n<CommandHint command="b" />\n<CommandHint command="c" label="d" />';
    expect(count("command-hint-reason", source)).toBe(2);
  });

  it("finds the four example pages clean: they draw no command and declare what they write", () => {
    const EXAMPLES = ["features/cloud/", "features/settings/registries/", "features/settings/DeployKeys", "features/settings/IntegrationsSettings"];
    const owing = DESIGN_RULES.filter((rule) => rule.id === "write-without-permission" || rule.id === "command-hint-reason").flatMap((rule) =>
      Object.keys(CURRENT[rule.id] ?? {})
        .filter((path) => EXAMPLES.some((prefix) => path.startsWith(prefix)))
        .map((path) => `${rule.id}: ${path}`),
    );
    expect(owing, "The pages redrawn with the content kit owe no permission and no command (K1)").toEqual([]);
  });
});
