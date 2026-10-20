/**
 * The design system's structural rules: what a feature must not build because a component
 * already says it, found in the syntax tree and not in the text (docs/DESIGN.md,
 * "Enforcement"). `designRules.ts` is a regular expression per line and cannot count "an
 * `aria-busy="true"` written outside the kit" or "a `<FeatureState` in a file"; this can, and
 * tells a JSX attribute from the same words in a comment or a string.
 *
 * styles/design-structure.test.ts counts every rule in every production file and holds each
 * count to design-structure-baseline.json, which may only go down, with the same semantics as
 * the text ratchet. `scripts/ux-counts.mjs` prints the table of how much of the console is
 * built from each component, from this same code, so the numbers a lane quotes and the
 * numbers the test holds are never two measurements.
 *
 * Self-contained on purpose (only `typescript`, only syntax Node strips): the script runs it
 * without a build, and a rule added here is visible to both at once.
 */

import ts from "typescript";

export interface StructureRule {
  id: string;
  /** What to write instead, for the failure message. */
  fix: string;
  /** Which files the rule applies to (paths relative to src/, "features/apps/AppsPage.tsx"). */
  applies: (path: string) => boolean;
  /** A cheap test on the text: a file without it cannot break the rule, so it is not parsed. */
  hint: RegExp;
  /** The nodes of one parsed file that break the rule. */
  find: (file: ts.SourceFile) => readonly ts.Node[];
}

/** The kit: the components, the app shell (PageHeader, LinkTabs, the top bar) and the gallery. */
const KIT = /^(components|app|dev)\//;
const outsideKit = (path: string): boolean => !KIT.test(path);

/** The opening tag of every JSX element, `<Foo bar />` and `<Foo>` alike. */
function openings(file: ts.SourceFile): (ts.JsxOpeningElement | ts.JsxSelfClosingElement)[] {
  const found: (ts.JsxOpeningElement | ts.JsxSelfClosingElement)[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) found.push(node);
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

/** Whether an opening tag is the component `name`. */
function isTag(node: ts.JsxOpeningElement | ts.JsxSelfClosingElement, name: string): boolean {
  return node.tagName.getText() === name;
}

/** The attribute `name` of an opening tag, when it is written there. */
function attribute(node: ts.JsxOpeningElement | ts.JsxSelfClosingElement, name: string): ts.JsxAttribute | undefined {
  return node.attributes.properties.find((property): property is ts.JsxAttribute => ts.isJsxAttribute(property) && property.name.getText() === name);
}

/** `aria-busy="true"` and `aria-busy={true}`; a value that is computed (`aria-busy={loading}`) is not. */
function isLiteralTrue(property: ts.JsxAttribute): boolean {
  const value = property.initializer;
  if (value === undefined) return false;
  if (ts.isStringLiteral(value)) return value.text === "true";
  return ts.isJsxExpression(value) && value.expression?.kind === ts.SyntaxKind.TrueKeyword;
}

export const STRUCTURE_RULES: readonly StructureRule[] = [
  {
    id: "feature-state",
    fix: "A feature's state is a StatusPill in the header of its Card (Card status), or a Setting row; FeatureState is retired.",
    applies: (path) => path !== "components/ui/FeatureState.tsx" && !path.startsWith("dev/"),
    hint: /FeatureState/,
    find: (file) => openings(file).filter((node) => isTag(node, "FeatureState")),
  },
  {
    id: "action-dialog",
    fix: "Use ConfirmDialog (size, ready, explainError): ActionDialog is its twin and goes.",
    applies: (path) => path !== "features/server/ActionDialog.tsx" && !path.startsWith("dev/"),
    hint: /ActionDialog/,
    find: (file) => openings(file).filter((node) => isTag(node, "ActionDialog")),
  },
  {
    id: "hand-aria-busy",
    fix: 'Everything that loads goes through QueryState, or LoadingRegion when it is not a query; aria-busy="true" is written only in the kit.',
    applies: outsideKit,
    hint: /aria-busy/,
    find: (file) =>
      openings(file).flatMap((node) => {
        const busy = attribute(node, "aria-busy");
        return busy !== undefined && isLiteralTrue(busy) ? [busy] : [];
      }),
  },
];

/** `design-exception: <rule-id> <reason>` on a line or the one above it excuses that line. */
export const STRUCTURE_EXCEPTION = /design-exception:\s*([a-z-]+)\s+(\S.*)?/;

/** The line (0-based) each node starts on, for every rule that matched in one file. */
export function structureMatches(path: string, source: string): Record<string, number[]> {
  const rules = STRUCTURE_RULES.filter((rule) => rule.applies(path) && rule.hint.test(source));
  const matches: Record<string, number[]> = {};
  if (rules.length === 0) return matches;
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  for (const rule of rules) {
    const lines = rule.find(file).map((node) => file.getLineAndCharacterOfPosition(node.getStart(file)).line);
    if (lines.length > 0) matches[rule.id] = lines;
  }
  return matches;
}

/**
 * Matches of each rule in one file's source, skipping lines excused by a design-exception
 * comment for that rule.
 */
export function countStructure(path: string, source: string): Record<string, number> {
  const lines = source.split("\n");
  const excused = (line: number, rule: string): boolean =>
    [lines[line], lines[line - 1]].some((text) => {
      const match = text === undefined ? null : STRUCTURE_EXCEPTION.exec(text);
      return match?.[1] === rule;
    });
  const counts: Record<string, number> = {};
  for (const [rule, found] of Object.entries(structureMatches(path, source))) {
    const count = found.filter((line) => !excused(line, rule)).length;
    if (count > 0) counts[rule] = count;
  }
  return counts;
}

/** The components the 4.0 wave moves, and the attribute it counts beside them. */
export const UX_TAGS = ["Notice", "FeatureState", "ActionDialog", "Switch", "SaveBar", "LoadingRegion", "QueryState", "ConfirmDialog", "Badge"] as const;
export const UX_COLUMNS = [...UX_TAGS, "aria-busy"] as const;
export type UxColumn = (typeof UX_COLUMNS)[number];

/** The components' own files draw themselves; counting them would count the definition. */
const UX_OWN = /^components\/ui\/(FeatureState|Notice|Badge|ConfirmDialog)\.tsx$/;

/** The row of the table a file belongs to: its directory, the application's page and its settings apart. */
export function uxDirectory(path: string): string {
  const [first, second, third] = path.split("/");
  if (first !== "features") return first ?? "";
  if (second === "app") return third === "settings" ? "features/app/settings" : "features/app (page)";
  return `features/${second ?? ""}`;
}

/**
 * How many times one file draws each component of the table (any `aria-busy` attribute, not
 * only the literal one), or null for a file the table leaves out: the gallery, tests and the
 * components' own files.
 */
export function tallyUx(path: string, source: string): Record<UxColumn, number> | null {
  if (!path.endsWith(".tsx") || path.startsWith("dev/") || path.startsWith("test/") || path.includes(".test.") || UX_OWN.test(path)) return null;
  const row = Object.fromEntries(UX_COLUMNS.map((column) => [column, 0])) as Record<UxColumn, number>;
  if (!UX_COLUMNS.some((column) => source.includes(column))) return row;
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  for (const node of openings(file)) {
    const tag = node.tagName.getText();
    for (const column of UX_TAGS) if (tag === column) row[column] += 1;
    if (attribute(node, "aria-busy") !== undefined) row["aria-busy"] += 1;
  }
  return row;
}
