/**
 * What a setting row says (docs/DESIGN.md SW-3 and SW-4): the label names the thing and does
 * not change with its state, the help is one sentence of 18 words or fewer that does not start
 * with the state, the risk is one line of 14 words or fewer.
 *
 * The test resolves the catalog keys a `<Setting>` is given as `label`, `help` and `risk`, in
 * English (the source of the words), and fails on the file and line of any row that breaks the
 * rule. There is no baseline for it: no row of the console is written yet, so every row that
 * lands is held to the rule from its first line, and a row that needs more room is a `riskDetail`
 * ("What changes"), a Disclosure or the documentation.
 *
 * Beside it, the catalog's own habit that the rows are made to end: help that opens with the state
 * ("Off, it is kept but nothing is sent") is the `help-starts-with-state` rule of the structural
 * ratchet, with the count of today.
 */

import ts from "typescript";
import { describe, expect, it } from "vitest";

import * as en from "../i18n/en";
import { SETTING_HELP_WORDS, SETTING_RISK_WORDS, messageLookup, settingCopyProblems } from "./designStructure";
import type { StructureContext } from "./designStructure";

const RAW = import.meta.glob<string>(["../**/*.tsx", "!../**/*.test.tsx", "!../dev/**", "!../test/**", "!../components/ui/Setting.tsx"], {
  eager: true,
  query: "?raw",
  import: "default",
});

const SOURCES: ReadonlyMap<string, string> = new Map(Object.entries(RAW).map(([path, source]) => [path.replace(/^\.\.\//, ""), source]));

const CATALOG: StructureContext = { message: messageLookup(en) };

function problemsIn(path: string, source: string, context: StructureContext): string[] {
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  return settingCopyProblems(file, context).map((problem) => `${path}:${String(file.getLineAndCharacterOfPosition(problem.node.getStart(file)).line + 1)} ${problem.attribute}: ${problem.message}`);
}

/** A catalog of a few sentences, for the examples below. */
const WORDS: StructureContext = {
  message: messageLookup({
    s: {
      label: "Send notifications",
      verb: "Turn on notifications",
      state: "Notifications are on",
      help: "Nothing is sent until a channel is set up and tested.",
      stateFirst: "Off, nothing is sent.",
      long: Array.from({ length: SETTING_HELP_WORDS + 1 }, (_, index) => `word${String(index)}`).join(" "),
      longRisk: Array.from({ length: SETTING_RISK_WORDS + 1 }, (_, index) => `word${String(index)}`).join(" "),
      risk: "Two copies of the app run at once.",
      two: "It runs once. It runs again.",
    },
  }),
};

const row = (props: string): string => `export const A = () => <Setting ${props} checked={on} onCheckedChange={set} />;`;

describe("the rows of the console", () => {
  it("say what they do in a sentence, and name the thing and not its state", () => {
    const found = [...SOURCES].flatMap(([path, source]) => (source.includes("<Setting") ? problemsIn(path, source, CATALOG) : []));
    expect(found, "A Setting row breaks SW-3 or SW-4 (docs/DESIGN.md, 2.10): see styles/setting-copy.test.ts").toEqual([]);
  });

  it("reads the whole console", () => {
    expect(SOURCES.size).toBeGreaterThan(200);
  });
});

describe("what breaks the copy rules of a row", () => {
  it("lets a label that names the thing, a short help and a short risk stand", () => {
    expect(problemsIn("x.tsx", row('label={t("s.label")} help={t("s.help")} risk={t("s.risk")}'), WORDS)).toEqual([]);
  });

  it("refuses a label that changes with the state, names a verb or says the state", () => {
    expect(problemsIn("x.tsx", row('label={on ? t("s.label") : t("s.label")}'), WORDS)).toEqual([expect.stringMatching(/changes with the state/)]);
    expect(problemsIn("x.tsx", row('label={t("s.verb")}'), WORDS)).toHaveLength(1);
    expect(problemsIn("x.tsx", row('label={t("s.state")}'), WORDS)).toHaveLength(1);
  });

  it("refuses a help that is too long, that starts with the state or that is two sentences", () => {
    expect(problemsIn("x.tsx", row('label="x" help={t("s.long")}'), WORDS)).toHaveLength(1);
    expect(problemsIn("x.tsx", row('label="x" help={t("s.stateFirst")}'), WORDS)).toHaveLength(1);
    expect(problemsIn("x.tsx", row('label="x" help={t("s.two")}'), WORDS)).toHaveLength(1);
  });

  it("holds the risk to a line of fewer words than the help", () => {
    expect(SETTING_RISK_WORDS).toBeLessThan(SETTING_HELP_WORDS);
    expect(problemsIn("x.tsx", row('label="x" risk={t("s.longRisk")}'), WORDS)).toHaveLength(1);
    expect(problemsIn("x.tsx", row('label="x" risk={t("s.risk")}'), WORDS)).toEqual([]);
  });

  it("does not judge a text the code computes, nor an element that is not a Setting", () => {
    expect(problemsIn("x.tsx", row('label="x" help={server.reason}'), WORDS)).toEqual([]);
    expect(problemsIn("x.tsx", 'export const A = () => <Field label={t("s.verb")} help={t("s.long")} />;', WORDS)).toEqual([]);
  });

  it("names the file, the line and the attribute", () => {
    const [problem] = problemsIn("features/x/Page.tsx", ["export const A = () => (", "  <Setting", '    label="x"', '    help={t("s.long")}', "    checked={on}", "    onCheckedChange={set}", "  />", ");"].join("\n"), WORDS);
    expect(problem).toMatch(/^features\/x\/Page\.tsx:2 help: /);
  });
});
