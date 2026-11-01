import { expect } from "vitest";

/**
 * WCAG 2.5.3, Label in Name: whoever speaks to a control says what they see, so the accessible name of
 * a control that draws words must contain those words, in the order they are drawn. axe 4.14 judges it
 * as `label-content-name-mismatch` in the browser (`e2e/fixtures.ts` runs it on every page); jsdom draws
 * nothing, so axe finds no visible text there and says nothing. This is the same judgement, for the
 * component tests and the catalog.
 *
 * Both texts are cut into words as axe cuts them: accents folded, what is in parentheses dropped (a
 * name may add "(opens in a new tab)"), everything that is not a letter or a digit a separator. The
 * visible words must be a run of the name's words with nothing between them, so "Open the service x"
 * does not hold "Open service" and "Deploy 12, succeeded, 3 days ago" does not hold "12 3 days ago".
 *
 * @param text - Any text, a placeholder such as `{name}` being one word.
 * @returns The words of the text, lower case.
 */
export function wordsOf(text: string): string[] {
  let folded = text.normalize("NFKD").toLowerCase();
  let previous = "";
  while (previous !== folded) {
    previous = folded;
    folded = folded.replace(/\([^()]*\)/g, "");
  }
  return folded
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .split(" ")
    .filter((word) => word !== "");
}

/**
 * Whether a control's name holds what it draws.
 *
 * @param visible - The words the control draws.
 * @param name - Its accessible name.
 * @returns True when the visible words are a contiguous run of the name's words, or there are none.
 */
export function nameHoldsLabel(visible: string, name: string): boolean {
  const label = wordsOf(visible);
  if (label.length === 0) return true;
  const words = wordsOf(name);
  for (let start = 0; start + label.length <= words.length; start += 1) {
    if (label.every((word, offset) => word === words[start + offset])) return true;
  }
  return false;
}

/**
 * What an element draws, the way axe reads it: the text nodes below it, less the words only a screen reader
 * gets (`sr-only`), joined as they are. Two elements side by side with no space text between them, however
 * they are laid out (two lines of a column, the items of a flex row), are one word to axe: "sessions" over
 * "redis" is "sessionsredis", and no name holds that. A space is a `{" "}` between them in the markup.
 */
export function visibleText(element: Element): string {
  const parts: string[] = [];
  const read = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      parts.push(node.textContent ?? "");
      return;
    }
    if (!(node instanceof Element) || node.classList.contains("sr-only")) return;
    for (const child of node.childNodes) read(child);
  };
  read(element);
  return parts.join("").replace(/\s+/g, " ").trim();
}

/**
 * Fails when an element named by `aria-label` does not hold the words it draws. An element with no
 * `aria-label` is named by its content and holds them by construction.
 *
 * @param element - A link, a button or any control that has text.
 */
export function expectLabelInName(element: Element): void {
  const label = element.getAttribute("aria-label");
  if (label === null) return;
  const visible = visibleText(element);
  expect(nameHoldsLabel(visible, label), `the name "${label}" does not hold what the control draws, "${visible}" (WCAG 2.5.3)`).toBe(true);
}
