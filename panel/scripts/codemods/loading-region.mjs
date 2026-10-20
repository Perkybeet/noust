// Rewrites the loading region every page used to write by hand into the kit's LoadingRegion.
//
//   node scripts/codemods/loading-region.mjs <file|dir>...           what would change, and what it leaves
//   node scripts/codemods/loading-region.mjs --write <file|dir>...   rewrites the files
//
// The pattern it knows is the common one, in 117 files of the console when this was written:
//
//   <div aria-busy="true" className="flex flex-col gap-2">
//     <span className="sr-only">{t("area.loading")}</span>
//     <Skeleton className="h-4 w-24" />
//   </div>
//
// which becomes
//
//   <LoadingRegion label={t("area.loading")} className="flex flex-col gap-2">
//     <Skeleton className="h-4 w-24" />
//   </LoadingRegion>
//
// and adds LoadingRegion to the import of components/page (or a new import of it). The element
// keeps its className, which LoadingRegion puts on the wrapper of the skeleton; everything between
// the tags stays as it was, byte for byte.
//
// What it will not guess is left alone and reported with its line, because rewriting it would change
// what the page does: an element that is not a div (a list with its own aria-label), one that carries
// anything besides className (a data-testid a test reads), one with no screen-reader label as its
// first child (there is no sentence to make the label from), and a label that is not a plain message
// (`t.rich`, a template). Those are written by hand with QueryState or LoadingRegion, as the lane
// that owns the file sees fit. Running it again changes nothing: it is idempotent.
//
// LoadingRegion puts the className on the wrapper of the skeleton, inside its own column, so a
// page whose skeleton was laid out against its parent (a fixed height, a grid of the page's) is
// looked at after the rewrite, in a capture, and a test that finds the region by a class its
// element had looks one level in. Run over the whole console it rewrote 116 regions and left 16, and
// 3,525 of the 3,526 unit tests of features, routes and nodes still passed: the one that did not
// finds its skeleton as `[aria-busy="true"].grid`.
//
// docs/DESIGN.md ST-1: everything that loads draws its skeleton through QueryState, or through
// LoadingRegion when it is not a query. The ratchet `hand-aria-busy` counts what is left.

import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

/**
 * @typedef {{ line: number, reason: string }} Skipped
 * @typedef {{ code: string, rewritten: number, skipped: Skipped[] }} Result
 */

const panel = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

/** @param {ts.JsxAttributes} attributes @param {string} name @returns {ts.JsxAttribute | undefined} */
function attributeOf(attributes, name) {
  const found = attributes.properties.find((property) => ts.isJsxAttribute(property) && property.name.getText() === name);
  return found !== undefined && ts.isJsxAttribute(found) ? found : undefined;
}

/** `aria-busy="true"` and `aria-busy={true}`: the literal the ratchet counts. @param {ts.JsxAttribute} attribute */
function isLiteralTrue(attribute) {
  const value = attribute.initializer;
  if (value === undefined) return false;
  if (ts.isStringLiteral(value)) return value.text === "true";
  return ts.isJsxExpression(value) && value.expression?.kind === ts.SyntaxKind.TrueKeyword;
}

/** The children of an element that are not whitespace between lines. @param {ts.JsxElement} element */
function realChildren(element) {
  return element.children.filter((child) => !(ts.isJsxText(child) && child.text.trim() === ""));
}

/**
 * The label a screen-reader span holds, as the text of the `label` attribute, or why it cannot be one.
 *
 * @param {ts.JsxChild | undefined} span
 * @param {ts.SourceFile} file
 * @returns {{ label: string } | { reason: string }}
 */
function labelOf(span, file) {
  if (span === undefined || !ts.isJsxElement(span) || span.openingElement.tagName.getText(file) !== "span") {
    return { reason: "its first child is not the screen-reader span" };
  }
  const attributes = span.openingElement.attributes.properties;
  const only = attributes.length === 1 ? attributes[0] : undefined;
  const isSrOnly =
    only !== undefined &&
    ts.isJsxAttribute(only) &&
    only.name.getText(file) === "className" &&
    only.initializer !== undefined &&
    ts.isStringLiteral(only.initializer) &&
    only.initializer.text === "sr-only";
  if (!isSrOnly) return { reason: "its first span is not exactly className=\"sr-only\"" };
  const inside = realChildren(span);
  const [content] = inside;
  if (inside.length !== 1 || content === undefined) return { reason: "its screen-reader span holds more than one thing" };
  if (ts.isJsxText(content)) {
    const text = content.text.trim().replace(/\s+/g, " ");
    // A literal with a quote or a brace needs escaping the codemod does not try.
    return /["{}<>\\]/.test(text) ? { reason: "its label is a text with characters to escape" } : { label: `"${text}"` };
  }
  if (ts.isJsxExpression(content) && content.expression !== undefined) {
    const expression = content.expression;
    if (ts.isCallExpression(expression) && expression.expression.getText(file) === "t") return { label: `{${expression.getText(file)}}` };
    if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) return { label: `{${expression.getText(file)}}` };
  }
  return { reason: "its label is not a plain message (t.rich, a template, a computed value)" };
}

/** Where to put a new import of LoadingRegion, and how it reads. @param {ts.SourceFile} file @param {string} filePath @param {string} srcRoot */
function importEdit(file, filePath, srcRoot) {
  const imports = file.statements.filter(ts.isImportDeclaration);
  for (const declaration of imports) {
    const specifier = declaration.moduleSpecifier.getText(file).slice(1, -1);
    const bindings = declaration.importClause?.namedBindings;
    if (/(?:^|\/)components\/page$/.test(specifier) && bindings !== undefined && ts.isNamedImports(bindings)) {
      if (bindings.elements.some((element) => element.name.text === "LoadingRegion")) return null;
      const names = bindings.elements.map((element) => element.getText(file));
      const sorted = names.every((name, index) => index === 0 || (names[index - 1] ?? "") <= name);
      const next = [...names, "LoadingRegion"];
      if (sorted) next.sort();
      const braces = bindings.getText(file);
      const oneLine = !braces.includes("\n");
      const text = oneLine ? `{ ${next.join(", ")} }` : `{\n${next.map((name) => `  ${name},`).join("\n")}\n}`;
      return { start: bindings.getStart(file), end: bindings.getEnd(), text };
    }
  }
  const already = imports.some((declaration) => declaration.importClause?.namedBindings !== undefined && ts.isNamedImports(declaration.importClause.namedBindings) && declaration.importClause.namedBindings.elements.some((element) => element.name.text === "LoadingRegion"));
  if (already) return null;
  let relative = path.relative(path.dirname(filePath), path.join(srcRoot, "components", "page")).split(path.sep).join("/");
  if (!relative.startsWith(".")) relative = `./${relative}`;
  const line = `import { LoadingRegion } from "${relative}";`;
  const last = imports[imports.length - 1];
  if (last === undefined) return { start: 0, end: 0, text: `${line}\n\n` };
  return { start: last.getEnd(), end: last.getEnd(), text: `\n${line}` };
}

/**
 * Rewrites the hand-written loading regions of one file.
 *
 * @param {string} source The text of the file.
 * @param {string} filePath Its path, to make the relative import.
 * @param {{ srcRoot?: string }} [options] The src directory, when the path does not say it.
 * @returns {Result}
 */
export function transform(source, filePath, options = {}) {
  if (!source.includes("aria-busy")) return { code: source, rewritten: 0, skipped: [] };
  const srcRoot = options.srcRoot ?? filePath.slice(0, filePath.lastIndexOf(`${path.sep}src${path.sep}`) + 4);
  const file = ts.createSourceFile(filePath, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  /** @type {{ start: number, end: number, text: string }[]} */
  const edits = [];
  /** @type {Skipped[]} */
  const skipped = [];
  const lineOf = (/** @type {ts.Node} */ node) => file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;

  /** @param {ts.Node} node */
  const visit = (node) => {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const opening = ts.isJsxElement(node) ? node.openingElement : node;
      const busy = attributeOf(opening.attributes, "aria-busy");
      if (busy !== undefined && isLiteralTrue(busy)) {
        const reason = rewrite(node, opening);
        if (reason !== null) skipped.push({ line: lineOf(node), reason });
      }
    }
    ts.forEachChild(node, visit);
  };

  /**
   * Plans the three edits of one region, or says why it cannot.
   *
   * @param {ts.JsxElement | ts.JsxSelfClosingElement} element
   * @param {ts.JsxOpeningElement | ts.JsxSelfClosingElement} opening
   * @returns {string | null}
   */
  const rewrite = (element, opening) => {
    if (!ts.isJsxElement(element)) return "it has no children to keep";
    if (opening.tagName.getText(file) !== "div") return `it is a <${opening.tagName.getText(file)}>, which LoadingRegion cannot stand in for`;
    const others = opening.attributes.properties.filter((property) => !(ts.isJsxAttribute(property) && ["aria-busy", "className"].includes(property.name.getText(file))));
    if (others.length > 0) return `it carries ${others.map((property) => (ts.isJsxAttribute(property) ? property.name.getText(file) : "a spread")).join(", ")}, which LoadingRegion does not`;
    const [first, ...rest] = realChildren(element);
    const label = labelOf(first, file);
    if ("reason" in label) return label.reason;
    if (rest.length === 0) return "it holds only its label: there is no skeleton to keep";
    const className = attributeOf(opening.attributes, "className");
    const attributes = `label=${label.label}${className === undefined ? "" : ` ${className.getText(file)}`}`;
    const span = /** @type {ts.JsxElement} */ (first);
    edits.push(
      { start: opening.getStart(file), end: opening.getEnd(), text: `<LoadingRegion ${attributes}>` },
      // The span and the whitespace before it: what stays is the whitespace before the next child.
      { start: opening.getEnd(), end: span.getEnd(), text: "" },
      { start: element.closingElement.getStart(file), end: element.closingElement.getEnd(), text: "</LoadingRegion>" },
    );
    return null;
  };

  visit(file);
  const rewritten = edits.length / 3;
  if (rewritten === 0) return { code: source, rewritten: 0, skipped };
  const imported = importEdit(file, filePath, srcRoot);
  if (imported !== null) edits.push(imported);
  let code = source;
  for (const edit of edits.sort((a, b) => b.start - a.start || b.end - a.end)) code = code.slice(0, edit.start) + edit.text + code.slice(edit.end);
  return { code, rewritten, skipped };
}

/** Every .tsx file under a path, tests and the kit's own components left out. @param {string} target @returns {string[]} */
function filesUnder(target) {
  if (statSync(target).isFile()) return [target];
  return readdirSync(target, { withFileTypes: true }).flatMap((entry) => {
    const next = path.join(target, entry.name);
    if (entry.isDirectory()) return entry.name === "node_modules" ? [] : filesUnder(next);
    return entry.name.endsWith(".tsx") && !entry.name.endsWith(".test.tsx") ? [next] : [];
  });
}

/** @param {string[]} argv @returns {number} The exit code. */
export function main(argv) {
  const write = argv.includes("--write");
  const targets = argv.filter((argument) => !argument.startsWith("--"));
  if (targets.length === 0) {
    console.error("usage: node scripts/codemods/loading-region.mjs [--write] <file|dir>...");
    return 2;
  }
  let rewritten = 0;
  let left = 0;
  for (const target of targets) {
    for (const file of filesUnder(path.resolve(target))) {
      // The kit draws the region itself: LoadingRegion is the one place aria-busy is written.
      if (file.includes(`${path.sep}components${path.sep}`) || file.includes(`${path.sep}dev${path.sep}`)) continue;
      const source = readFileSync(file, "utf8");
      const result = transform(source, file, { srcRoot: path.join(panel, "src") });
      const name = path.relative(panel, file);
      if (result.rewritten > 0) {
        rewritten += result.rewritten;
        console.log(`${name}: ${String(result.rewritten)} rewritten`);
        if (write) writeFileSync(file, result.code);
      }
      for (const entry of result.skipped) {
        left += 1;
        console.log(`${name}:${String(entry.line)}: left alone, ${entry.reason}`);
      }
    }
  }
  console.log(`${write ? "rewrote" : "would rewrite"} ${String(rewritten)}; left ${String(left)} for the hand`);
  return 0;
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
