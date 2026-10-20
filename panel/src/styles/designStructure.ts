/**
 * The design system's structural rules: what a feature must not build because a component
 * already says it, found in the syntax tree and not in the text (docs/DESIGN.md,
 * "Enforcement"). `designRules.ts` is a regular expression per line and cannot count "an
 * `aria-busy="true"` written outside the kit" or "a file with three notices"; this can, and
 * tells a JSX attribute from the same words in a comment or a string.
 *
 * styles/design-structure.test.ts counts every rule in every production file and holds each
 * count to design-structure-baseline.json, which may only go down, with the same semantics as
 * the text ratchet. `scripts/ux-counts.mjs` prints the table of how much of the console is
 * built from each component, from this same code, so the numbers a lane quotes and the
 * numbers the test holds are never two measurements.
 *
 * A rule that has to read what a notice or a setting says gets the English catalog through a
 * `StructureContext` (the test builds it from the catalog, the script from the namespace files):
 * the code never imports the catalog itself, so this file stays self-contained on purpose (only
 * `typescript`, only syntax Node strips): the script runs it without a build, and a rule added
 * here is visible to both at once.
 */

import ts from "typescript";

/** The English text of a catalog key (a plural counts as its `other` form), or undefined. */
export type MessageOf = (key: string) => string | undefined;

/** What a rule may need besides the syntax tree. */
export interface StructureContext {
  message: MessageOf;
}

/** The context of a caller with no catalog: a rule that needs words counts nothing. */
export const NO_CONTEXT: StructureContext = { message: () => undefined };

export interface StructureRule {
  id: string;
  /** What to write instead, for the failure message. */
  fix: string;
  /** Which files the rule applies to (paths relative to src/, "features/apps/AppsPage.tsx"). */
  applies: (path: string) => boolean;
  /** A cheap test on the text: a file without it cannot break the rule, so it is not parsed. */
  hint: RegExp;
  /** The nodes of one parsed file that break the rule. */
  find: (file: ts.SourceFile, context: StructureContext) => readonly ts.Node[];
  /**
   * The rule is about a file as a whole (it counts one per file), so its design-exception
   * comment may sit anywhere in the file and excuses all of it.
   */
  fileLevel?: boolean;
}

/** The kit: the components, the app shell (PageHeader, LinkTabs, the top bar) and the gallery. */
const KIT = /^(components|app|dev)\//;
const outsideKit = (path: string): boolean => !KIT.test(path);

export type Opening = ts.JsxOpeningElement | ts.JsxSelfClosingElement;

/** The opening tag of every JSX element, `<Foo bar />` and `<Foo>` alike. */
function openings(file: ts.SourceFile): Opening[] {
  const found: Opening[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) found.push(node);
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

/** Whether an opening tag is the component `name`. */
function isTag(node: Opening, name: string): boolean {
  return node.tagName.getText() === name;
}

/** The attribute `name` of an opening tag, when it is written there. */
function attribute(node: Opening, name: string): ts.JsxAttribute | undefined {
  return node.attributes.properties.find((property): property is ts.JsxAttribute => ts.isJsxAttribute(property) && property.name.getText() === name);
}

/** The expression an attribute holds: `x` for `a={x}`, the literal for `a="x"`, nothing for a bare `a`. */
function valueOf(property: ts.JsxAttribute | undefined): ts.Expression | undefined {
  const value = property?.initializer;
  if (value === undefined) return undefined;
  return ts.isJsxExpression(value) ? value.expression : value;
}

/** The text of an attribute that is a plain string (`a="x"` or `a={"x"}`), else undefined. */
function stringOf(property: ts.JsxAttribute | undefined): string | undefined {
  const value = valueOf(property);
  return value !== undefined && (ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value)) ? value.text : undefined;
}

/** `aria-busy="true"` and `aria-busy={true}`; a value that is computed (`aria-busy={loading}`) is not. */
function isLiteralTrue(property: ts.JsxAttribute): boolean {
  const value = property.initializer;
  if (value === undefined) return false;
  if (ts.isStringLiteral(value)) return value.text === "true";
  return ts.isJsxExpression(value) && value.expression?.kind === ts.SyntaxKind.TrueKeyword;
}

/** Every string a subtree writes (a class list is one string, or several in a `cx(...)` call). */
function stringsIn(node: ts.Node | undefined): string[] {
  const found: string[] = [];
  const visit = (child: ts.Node): void => {
    if (ts.isStringLiteral(child) || ts.isNoSubstitutionTemplateLiteral(child)) found.push(child.text);
    else if (ts.isTemplateHead(child) || ts.isTemplateMiddle(child) || ts.isTemplateTail(child)) found.push(child.text);
    ts.forEachChild(child, visit);
  };
  if (node !== undefined) visit(node);
  return found;
}

/** The class names an opening tag writes, whichever way (a literal, a template, `cx(...)`). */
function classesOf(node: Opening): Set<string> {
  const names = stringsIn(attribute(node, "className")).flatMap((text) => text.split(/\s+/));
  return new Set(names.filter((name) => name !== ""));
}

/** The tags of the elements a node is drawn inside of, nearest first. */
function enclosingTags(node: ts.Node): string[] {
  const tags: string[] = [];
  // An opening tag's parent is its own element; the elements around it start one level up.
  for (let up: ts.Node = ts.isJsxOpeningElement(node) ? node.parent.parent : node.parent; !ts.isSourceFile(up); up = up.parent) {
    if (ts.isJsxElement(up)) tags.push(up.openingElement.tagName.getText());
  }
  return tags;
}

/** What asks the operator something and stays open until answered: where a success or a note may live. */
const DIALOGS = new Set(["Dialog", "Drawer", "ConfirmDialog", "AlertDialog", "Wizard"]);

const insideDialog = (node: ts.Node): boolean => enclosingTags(node).some((tag) => DIALOGS.has(tag));

/** The key `t("a.b")` or `t.rich("a.b", ...)` asks for, when it is written as a literal. */
function messageKey(node: ts.Node): string | undefined {
  if (!ts.isCallExpression(node)) return undefined;
  const callee = node.expression.getText();
  if (callee !== "t" && callee !== "t.rich") return undefined;
  const [first] = node.arguments;
  return first !== undefined && (ts.isStringLiteral(first) || ts.isNoSubstitutionTemplateLiteral(first)) ? first.text : undefined;
}

/** Every literal catalog key a subtree asks for. */
function keysIn(node: ts.Node | undefined): string[] {
  const found: string[] = [];
  const visit = (child: ts.Node): void => {
    const key = messageKey(child);
    if (key !== undefined) found.push(key);
    ts.forEachChild(child, visit);
  };
  if (node !== undefined) visit(node);
  return found;
}

/** The words of a sentence; a `{placeholder}` is one word, as it will be once it is filled. */
export function countWords(text: string): number {
  return text.split(/\s+/).filter((word) => word !== "").length;
}

/**
 * How many words the operator reads in a subtree, in English: every catalog key it asks for by
 * literal, plain text, and the longer of the two sides of a condition (only one is ever drawn).
 * What the code computes (a server's reason, a list's rows) is not counted: it has no length here.
 */
function wordsIn(node: ts.Node | undefined, context: StructureContext): number {
  if (node === undefined || ts.isJsxAttribute(node)) return 0;
  if (ts.isJsxText(node)) return countWords(node.text);
  if (ts.isJsxExpression(node) && node.expression !== undefined && (ts.isStringLiteral(node.expression) || ts.isNoSubstitutionTemplateLiteral(node.expression))) {
    return countWords(node.expression.text);
  }
  const key = messageKey(node);
  if (key !== undefined) return countWords(context.message(key) ?? "");
  if (ts.isConditionalExpression(node)) return Math.max(wordsIn(node.whenTrue, context), wordsIn(node.whenFalse, context));
  let total = 0;
  ts.forEachChild(node, (child) => {
    total += wordsIn(child, context);
  });
  return total;
}

/** What a notice says, title and body together, in English words. */
export function noticeWords(node: Opening, context: StructureContext): number {
  const body = ts.isJsxOpeningElement(node) && ts.isJsxElement(node.parent) ? node.parent.children : [];
  return wordsIn(valueOf(attribute(node, "title")), context) + body.reduce((sum, child) => sum + wordsIn(child, context), 0);
}

/** The tone a notice is drawn with: `info` unless it says otherwise, undefined when the code computes it. */
function noticeTone(node: Opening): string | undefined {
  const tone = attribute(node, "tone");
  if (tone === undefined) return "info";
  return stringOf(tone);
}

/** Whether a badge's tone could be the colour of a state: `ok`, `warn` or `fail`, or a value the code computes. */
function toneMayBeState(expression: ts.Expression | undefined): boolean {
  if (expression === undefined) return false;
  if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) return ["ok", "warn", "fail"].includes(expression.text);
  if (ts.isParenthesizedExpression(expression) || ts.isAsExpression(expression)) return toneMayBeState(expression.expression);
  if (ts.isConditionalExpression(expression)) return toneMayBeState(expression.whenTrue) || toneMayBeState(expression.whenFalse);
  return true;
}

/** The icons that point backwards: a link that wears one is a back link. */
const BACK_ICONS = new Set(["ChevronLeft", "ArrowLeft"]);

/** A value that is wrong or missing, as a button's `disabled` says it: the signals the error of a form would give. */
const INVALID_SIGNAL =
  /\.trim\(\)\s*[!=]==?\s*""|[!=]==?\s*""|\.(?:length|size)\s*===?\s*0|\bempty\b|\binvalid\b|\bincomplete\b|\bmissing\b|=== undefined|=== null|!\s*[\w.]*(?:valid|ready|ok|complete|matches|filled|allowed|selected|picked|chosen|can[A-Z]\w*)\b/i;

/** The label keys that say a button saves: the catalog's own words for it. */
const SAVE_KEY = /(?:^|\.)(?:save|apply|saveChanges)[A-Za-z]*$/;

/** A catalog string that makes a magic value out of an empty field or a zero. */
const EMPTY_MEANS_OFF = /\b(?:empty|blank)\s+(?:keeps?|means?)\b|\b0\s+(?:turns?\s+(?:it\s+)?off|means?|keeps?|disables?|for no)\b/i;

/** The words a notice may use before it is a wall (the hard ceiling; the NT-2 limits are lower and a review's). */
export const NOTICE_LONG_WORDS = 45;

/**
 * The lists of destinations written by hand. Seven files wrote one in 3.x and the register
 * (app/nav.ts) replaced them all: an array literal of `{ label, to }` objects in a file that
 * deals in tabs or destinations. Breadcrumbs are hierarchy, not destinations.
 */
export function destinationLiterals(file: ts.SourceFile): ts.ArrayLiteralExpression[] {
  // A file that does not deal in tabs or destinations cannot be listing them: it imports their types.
  const dealsInTabs = file.statements.some(
    (statement) =>
      ts.isImportDeclaration(statement) &&
      statement.importClause !== undefined &&
      /\b(LinkTab|LinkTabs|TabItem|NavItem)\b/.test(statement.importClause.getText(file)) &&
      /\/(LinkTabs|nav|navCore)"$/.test(statement.moduleSpecifier.getText(file)),
  );
  if (!dealsInTabs) return [];
  const isDestination = (node: ts.Node): boolean =>
    ts.isObjectLiteralExpression(node) && ["label", "to"].every((name) => node.properties.some((property) => property.name?.getText(file) === name));
  const holdsDestination = (node: ts.Node): boolean =>
    isDestination(node) || (ts.isObjectLiteralExpression(node) && node.properties.some((property) => ts.isPropertyAssignment(property) && isDestination(property.initializer)));
  const isBreadcrumbs = (node: ts.Node): boolean => {
    for (let up: ts.Node = node.parent; !ts.isSourceFile(up); up = up.parent) {
      if (ts.isPropertyAssignment(up) && up.name.getText(file) === "breadcrumbs") return true;
      if ((ts.isFunctionDeclaration(up) || ts.isVariableDeclaration(up)) && /breadcrumb/i.test(up.name?.getText(file) ?? "")) return true;
    }
    return false;
  };
  const found: ts.ArrayLiteralExpression[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isArrayLiteralExpression(node) && node.elements.some(holdsDestination) && !isBreadcrumbs(node)) found.push(node);
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

/** The longest help a setting row takes (SW-3) and the longest risk line (SW-4), in English words. */
export const SETTING_HELP_WORDS = 18;
export const SETTING_RISK_WORDS = 14;

/** What a row's text must not do: start with the state, or name a verb where the thing's name goes. */
const STATE_FIRST = /^(?:on|off|enabled|disabled)\b/i;
const VERB_LABEL = /^(?:enable|disable|turn (?:on|off))\b/i;
const STATE_LABEL = /\b(?:is|are) (?:on|off|enabled|disabled)\b/i;

/** The sentences of a text: a full stop, a question mark or an exclamation followed by a space or the end. */
function sentenceCount(text: string): number {
  return (text.match(/[.!?](?:\s|$)/g) ?? []).length;
}

/** One way a `Setting` row's text breaks SW-3 or SW-4. */
export interface SettingProblem {
  node: ts.Node;
  attribute: "label" | "help" | "risk";
  message: string;
}

/**
 * What the `help`, `risk` and `label` of every `<Setting>` in a file say, held to SW-3 and SW-4:
 * the label names the thing and does not change with its state; the help is one sentence of
 * 18 words or fewer that does not start with the state; the risk is one line of 14 words or fewer.
 * A text the code computes (not a catalog key) is not judged: it has no length here.
 */
export function settingCopyProblems(file: ts.SourceFile, context: StructureContext): SettingProblem[] {
  const problems: SettingProblem[] = [];
  for (const node of openings(file)) {
    if (!isTag(node, "Setting")) continue;
    const label = attribute(node, "label");
    const labelValue = valueOf(label);
    if (labelValue !== undefined && (ts.isConditionalExpression(labelValue) || ts.isTemplateExpression(labelValue))) {
      problems.push({ node, attribute: "label", message: "The label changes with the state: it names the thing, and the control says whether it is on." });
    }
    for (const key of keysIn(label)) {
      const text = context.message(key) ?? "";
      if (VERB_LABEL.test(text)) problems.push({ node, attribute: "label", message: `"${text}" starts with a verb: the label is the name of the thing ("Send notifications"), the verb is the button's.` });
      if (STATE_LABEL.test(text)) problems.push({ node, attribute: "label", message: `"${text}" says the state: the control says it.` });
    }
    for (const name of ["help", "risk"] as const) {
      const limit = name === "help" ? SETTING_HELP_WORDS : SETTING_RISK_WORDS;
      for (const key of keysIn(attribute(node, name))) {
        const text = context.message(key) ?? "";
        const words = countWords(text);
        if (words > limit) problems.push({ node, attribute: name, message: `The ${name} of "${key}" has ${String(words)} words; ${String(limit)} is the most.` });
        if (STATE_FIRST.test(text)) problems.push({ node, attribute: name, message: `The ${name} of "${key}" starts with the state: it says what the setting does.` });
        if (sentenceCount(text) > 1) problems.push({ node, attribute: name, message: `The ${name} of "${key}" is more than one sentence.` });
      }
    }
  }
  return problems;
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
    id: "badge-state",
    fix: "A state is a StatusPill (size sm in a card or a row, appearance inline in a table); a Badge is an attribute (neutral) or something staged (accent).",
    applies: outsideKit,
    hint: /<Badge\b/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!isTag(node, "Badge")) return false;
        const tone = attribute(node, "tone");
        return tone !== undefined && toneMayBeState(valueOf(tone));
      }),
  },
  {
    id: "notice-stack",
    fix: "At most two notices in a file: say the worst one, or aggregate (\"3 things need attention\"); a risk is a Setting risk line and an explanation is help text.",
    applies: outsideKit,
    hint: /<Notice\b/,
    fileLevel: true,
    find: (file) => {
      const notices = openings(file).filter((node) => isTag(node, "Notice"));
      const [first] = notices;
      return first !== undefined && notices.length >= 3 ? [first] : [];
    },
  },
  {
    id: "notice-info-bare",
    fix: "An info notice needs an action or a title of its own; otherwise it is the help text of its field, row or section.",
    applies: outsideKit,
    hint: /<Notice\b/,
    find: (file) =>
      openings(file).filter((node) => isTag(node, "Notice") && noticeTone(node) === "info" && attribute(node, "action") === undefined && attribute(node, "title") === undefined),
  },
  {
    id: "notice-success",
    fix: "Saving is not announced: the interface itself changes, or a toast says what is not on screen. A success Notice lives only in a dialog or a wizard that stays open and offers the next step.",
    applies: outsideKit,
    hint: /<Notice\b/,
    find: (file) => openings(file).filter((node) => isTag(node, "Notice") && noticeTone(node) === "success" && !insideDialog(node)),
  },
  {
    id: "notice-long",
    fix: `A notice is a title of 8 words or fewer and a body of 30 (25 on a banner): more than ${String(NOTICE_LONG_WORDS)} words is a Popover (What changes), a Disclosure, a Drawer or the documentation.`,
    applies: outsideKit,
    hint: /<Notice\b/,
    find: (file, context) => openings(file).filter((node) => isTag(node, "Notice") && noticeWords(node, context) > NOTICE_LONG_WORDS),
  },
  {
    id: "switch-in-form",
    fix: "A Switch applies at once and never shares a surface with a Save button: a Setting row for an immediate setting, or a Checkbox in a form that is saved.",
    applies: outsideKit,
    hint: /<Switch\b/,
    find: (file) => {
      const all = openings(file);
      const saved = all.some((node) => node.tagName.getText() === "form" || isTag(node, "SaveBar") || stringOf(attribute(node, "type")) === "submit");
      return saved ? all.filter((node) => isTag(node, "Switch")) : [];
    },
  },
  {
    id: "card-save",
    fix: "A settings subsection saves from its SaveBar (a form) or applies a row at once (a Setting); a dialog's or a drawer's footer is the only other place for Save.",
    applies: outsideKit,
    hint: /<Button\b/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!isTag(node, "Button") || insideDialog(node)) return false;
        if (!enclosingTags(node).includes("Card")) return false;
        const label = ts.isJsxOpeningElement(node) && ts.isJsxElement(node.parent) ? keysIn(node.parent) : [];
        return label.some((key) => SAVE_KEY.test(key));
      }),
  },
  {
    id: "submit-disabled-invalid",
    fix: "A submit button is disabled only when there is nothing to do or while it works: press it, focus the first field with an error and say what is missing.",
    applies: outsideKit,
    hint: /disabled=/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!isTag(node, "Button")) return false;
        const variant = stringOf(attribute(node, "variant"));
        const submits = stringOf(attribute(node, "type")) === "submit" || variant === "primary" || variant === "danger";
        const disabled = valueOf(attribute(node, "disabled"));
        return submits && disabled !== undefined && INVALID_SIGNAL.test(disabled.getText());
      }),
  },
  {
    id: "own-leave-dialog",
    fix: "Leaving a form with changes asks once through useLeaveGuard and LeaveGuard (SaveBar places them); a page does not write its own question.",
    applies: (path) => path !== "lib/useLeaveGuard.ts" && !path.startsWith("dev/"),
    hint: /useBlocker/,
    find: (file) => {
      const found: ts.Node[] = [];
      const visit = (node: ts.Node): void => {
        if (ts.isCallExpression(node) && node.expression.getText() === "useBlocker") found.push(node);
        ts.forEachChild(node, visit);
      };
      visit(file);
      return found;
    },
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
  {
    id: "hand-error-ternary",
    fix: "A load's four states (skeleton, error, empty, content) are QueryState's; a refresh that fails keeps the last answer on screen with a compact error above it.",
    applies: outsideKit,
    hint: /isError/,
    find: (file) => {
      const found: ts.Node[] = [];
      const isErrorFlag = (node: ts.Node): boolean => /(?:^|\.)isError$/.test(node.getText());
      const isNoData = (node: ts.Node): boolean =>
        ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsEqualsEqualsToken && node.left.getText().endsWith(".data") && node.right.getText() === "undefined";
      const visit = (node: ts.Node): void => {
        if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) {
          if ((isErrorFlag(node.left) && isNoData(node.right)) || (isNoData(node.left) && isErrorFlag(node.right))) found.push(node);
        }
        ts.forEachChild(node, visit);
      };
      visit(file);
      return found;
    },
  },
  {
    id: "inline-empty-by-hand",
    fix: 'An empty list inside a section is EmptyState variant="inline", not a muted paragraph.',
    applies: outsideKit,
    hint: /text-fg-muted/,
    find: (file) =>
      openings(file).filter((node) => {
        if (node.tagName.getText() !== "p") return false;
        const classes = classesOf(node);
        if (!classes.has("text-13") || !classes.has("text-fg-muted")) return false;
        const keys = ts.isJsxOpeningElement(node) && ts.isJsxElement(node.parent) ? keysIn(node.parent) : [];
        const only = keys.length === 1 && ts.isJsxElement(node.parent) && node.parent.children.filter((child) => !ts.isJsxText(child) || child.text.trim() !== "").length === 1;
        return only && /(?:^|\.)(?:\w*empty\w*|none|nothing\w*)$/i.test(keys[0] ?? "");
      }),
  },
  {
    id: "action-dialog",
    fix: "Use ConfirmDialog (size, ready, explainError): ActionDialog is its twin and goes.",
    applies: (path) => path !== "features/server/ActionDialog.tsx" && !path.startsWith("dev/"),
    hint: /ActionDialog/,
    find: (file) => openings(file).filter((node) => isTag(node, "ActionDialog")),
  },
  {
    id: "danger-button-outside-confirm",
    fix: "A destructive last step is ConfirmDialog (friction simple or type) or DangerZone; a danger Button written by hand is a second confirmation component.",
    applies: (path) => path !== "components/ui/ConfirmDialog.tsx" && path !== "components/page/DangerZone.tsx" && !path.startsWith("dev/"),
    hint: /danger/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!isTag(node, "Button")) return false;
        const variant = valueOf(attribute(node, "variant"));
        return variant !== undefined && stringsIn(variant).includes("danger");
      }),
  },
  {
    id: "native-details",
    fix: 'Folded content is Disclosure (variant="card" when it has a frame); a native <details> is a second way to fold.',
    applies: outsideKit,
    hint: /<details\b/,
    find: (file) => openings(file).filter((node) => node.tagName.getText() === "details"),
  },
  {
    id: "row-actions-local",
    fix: "A row's actions are RowMenu (the one label \"Actions for {name}\"), not a RowActions component of the feature.",
    applies: outsideKit,
    hint: /\bRowActions\b/,
    find: (file) => {
      const found: ts.Node[] = [];
      const visit = (node: ts.Node): void => {
        if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText() === "RowActions") found.push(node);
        ts.forEachChild(node, visit);
      };
      visit(file);
      return found;
    },
  },
  {
    id: "bordered-list",
    fix: "A list of rows with its own frame is RowList (one radius, one padding, a label), not a ul with divide-y and a border written by hand.",
    applies: outsideKit,
    hint: /divide-y/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!["ul", "ol", "div"].includes(node.tagName.getText())) return false;
        const classes = classesOf(node);
        return classes.has("divide-y") && classes.has("border") && (classes.has("rounded-card") || classes.has("rounded-control"));
      }),
  },
  {
    id: "row-link-class",
    fix: 'The link on a row\'s name is TextLink variant="row", not the classes copied from one file to the next.',
    applies: outsideKit,
    hint: /hover:underline-offset-2/,
    find: (file) => {
      const found: ts.Node[] = [];
      const visit = (node: ts.Node): void => {
        if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
          if (/\bhover:underline\b/.test(node.text) && /\bhover:underline-offset-2\b/.test(node.text)) found.push(node);
        }
        ts.forEachChild(node, visit);
      };
      visit(file);
      return found;
    },
  },
  {
    id: "copy-box-by-hand",
    fix: "A value the operator copies is CopyValue (in full, wrapped at any character, with its copy button), not a <code> with its own box.",
    applies: outsideKit,
    hint: /bg-bg-sunken/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!["code", "pre", "span", "div", "p", "dd"].includes(node.tagName.getText())) return false;
        const classes = classesOf(node);
        if (!classes.has("bg-bg-sunken")) return false;
        if (classes.has("select-all")) return true;
        // A sunken box with a frame that holds a value and its own copy button is CopyValue, drawn again.
        if (!classes.has("rounded-control") || !ts.isJsxOpeningElement(node) || !ts.isJsxElement(node.parent)) return false;
        return openings(file).some(
          (inner) =>
            inner.getStart(file) > node.getStart(file) &&
            inner.getEnd() < node.parent.getEnd() &&
            (["CopyButton", "CopyTextButton"].includes(inner.tagName.getText()) || classesOf(inner).has("select-all")),
        );
      }),
  },
  {
    id: "field-error-by-hand",
    fix: "A field's error is Field's (or FieldGroup's): an icon, the sentence, aria-describedby; a paragraph in text-fail and a role=\"alert\" by hand have none of that.",
    applies: outsideKit,
    hint: /text-fail|role="alert"/,
    find: (file) =>
      openings(file).filter((node) => {
        const tag = node.tagName.getText();
        if (stringOf(attribute(node, "role")) === "alert" && /^[a-z]/.test(tag)) return true;
        return tag === "p" && classesOf(node).has("text-fail");
      }),
  },
  {
    id: "destination-outside-register",
    fix: "Every screen is declared once in app/nav.ts; the tabs, the sidebar, the settings list and the palette read it (NV-1): use tabsOf(...) and the register's derivations.",
    applies: (path) => path !== "app/nav.ts" && !path.startsWith("dev/"),
    hint: /LinkTab|NavItem|TabItem/,
    find: (file) => destinationLiterals(file),
  },
  {
    id: "own-back-link",
    fix: "A page of detail has breadcrumbs that name its parent list; it draws no back link of its own (NV-15).",
    applies: outsideKit,
    hint: /ChevronLeft|ArrowLeft/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!["TextLink", "Link"].includes(node.tagName.getText()) || !ts.isJsxOpeningElement(node) || !ts.isJsxElement(node.parent)) return false;
        return node.parent.children.some((child) => (ts.isJsxSelfClosingElement(child) || ts.isJsxElement(child)) && BACK_ICONS.has(ts.isJsxElement(child) ? child.openingElement.tagName.getText() : child.tagName.getText()));
      }),
  },
  {
    id: "empty-means-off",
    fix: 'A number that means "off" when it is empty or 0 is a switch that reveals the number (OptionalLimit): "No limit" and "Default" are visible states, not magic values.',
    applies: (path) => path.startsWith("i18n/en/"),
    hint: /empty|blank|\b0\b/i,
    find: (file) => {
      const found: ts.Node[] = [];
      const visit = (node: ts.Node): void => {
        if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && EMPTY_MEANS_OFF.test(node.text)) found.push(node);
        ts.forEachChild(node, visit);
      };
      visit(file);
      return found;
    },
  },
  {
    id: "help-starts-with-state",
    fix: 'Help says what the setting does, not the state it is in ("Off, it is kept but nothing is sent"): the control already shows the state (SW-3).',
    applies: (path) => path.startsWith("i18n/en/"),
    hint: /(?:Help|Description|Note|Hint):\s*["`](?:On|Off)\b/,
    find: (file) => {
      const found: ts.Node[] = [];
      const visit = (node: ts.Node): void => {
        if (ts.isPropertyAssignment(node) && /(?:Help|Description|Note|Hint)$/.test(node.name.getText(file))) {
          const value = node.initializer;
          if ((ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value)) && /^(?:On|Off)(?:,|:)/.test(value.text)) found.push(value);
        }
        ts.forEachChild(node, visit);
      };
      visit(file);
      return found;
    },
  },
  {
    id: "live-switch",
    fix: "Following the output is the viewer's \"Follow\" and the range's \"Live\": a page does not add a second Switch for it (LG-3).",
    applies: outsideKit,
    hint: /<Switch\b/,
    find: (file) =>
      openings(file).filter((node) => {
        if (!isTag(node, "Switch")) return false;
        const named = [attribute(node, "label"), attribute(node, "aria-label")];
        return named.some((property) => keysIn(property).some((key) => /(?:^|\.)(?:live|follow)\w*$/i.test(key)));
      }),
  },
  {
    id: "toast-success",
    fix: "A success toast is only for what is not visible on screen: a row that appears, a row that goes or a dialog that closes says nothing (FM-7).",
    applies: outsideKit,
    hint: /toast\.success/,
    find: (file) => {
      const found: ts.Node[] = [];
      const visit = (node: ts.Node): void => {
        if (ts.isCallExpression(node) && node.expression.getText() === "toast.success") found.push(node);
        ts.forEachChild(node, visit);
      };
      visit(file);
      return found;
    },
  },
];

/** `design-exception: <rule-id> <reason>` on a line or the one above it excuses that line. */
export const STRUCTURE_EXCEPTION = /design-exception:\s*([a-z-]+)\s+(\S.*)?/;

/** The line (0-based) each node starts on, for every rule that matched in one file. */
export function structureMatches(path: string, source: string, context: StructureContext = NO_CONTEXT): Record<string, number[]> {
  const rules = STRUCTURE_RULES.filter((rule) => rule.applies(path) && rule.hint.test(source));
  const matches: Record<string, number[]> = {};
  if (rules.length === 0) return matches;
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, path.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  for (const rule of rules) {
    const lines = rule.find(file, context).map((node) => file.getLineAndCharacterOfPosition(node.getStart(file)).line);
    if (lines.length > 0) matches[rule.id] = lines;
  }
  return matches;
}

/**
 * Matches of each rule in one file's source, skipping lines excused by a design-exception
 * comment for that rule (a rule about a whole file is excused by one anywhere in it).
 */
export function countStructure(path: string, source: string, context: StructureContext = NO_CONTEXT): Record<string, number> {
  const lines = source.split("\n");
  const excusedAt = (line: number, rule: string): boolean =>
    [lines[line], lines[line - 1]].some((text) => {
      const match = text === undefined ? null : STRUCTURE_EXCEPTION.exec(text);
      return match?.[1] === rule;
    });
  const excusedAnywhere = (rule: string): boolean => lines.some((text) => STRUCTURE_EXCEPTION.exec(text)?.[1] === rule);
  const counts: Record<string, number> = {};
  for (const [id, found] of Object.entries(structureMatches(path, source, context))) {
    const wholeFile = STRUCTURE_RULES.find((rule) => rule.id === id)?.fileLevel === true;
    const count = wholeFile && excusedAnywhere(id) ? 0 : found.filter((line) => wholeFile || !excusedAt(line, id)).length;
    if (count > 0) counts[id] = count;
  }
  return counts;
}

/**
 * Every message of an English catalog as a lookup by dotted key. `tree` is the object of `import * as en`
 * (or of the namespace files); a plural is its `other` form, which is the longest English one.
 */
export function messageLookup(tree: unknown): MessageOf {
  const PLURAL = new Set(["zero", "one", "two", "few", "many", "other"]);
  const flat = new Map<string, string>();
  const visit = (node: unknown, prefix: string): void => {
    if (typeof node === "string") {
      flat.set(prefix, node);
      return;
    }
    if (typeof node !== "object" || node === null) return;
    const entries = Object.entries(node as Record<string, unknown>);
    const other = (node as Record<string, unknown>)["other"];
    if (typeof other === "string" && entries.every(([key]) => PLURAL.has(key))) {
      flat.set(prefix, other);
      return;
    }
    for (const [key, child] of entries) visit(child, prefix === "" ? key : `${prefix}.${key}`);
  };
  visit(tree, "");
  return (key) => flat.get(key);
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
