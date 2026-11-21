/**
 * "Replace domains and this server's name", for what the browser adds to a report.
 *
 * The support report comes from the server with its names already replaced (`app-1`, `host`) and the server says which
 * it replaced and with what. What the browser puts in the same report (the page it is on, the last errors, the words the
 * person wrote) is replaced with that same list, so a name is one alias in the whole file and the option holds for
 * everything the report carries and not only for the part the server wrote. When the list could not be had (the support
 * report could not be read), anything shaped like a host name is replaced instead, which hides more than it has to.
 */

import type { components } from "../../api/schema.gen";

/** A name the server replaced and what it put instead. */
export type Replacement = components["schemas"]["SupportReplacementOut"];

/** What to do with the names in a text. */
export type Hiding = { kind: "off" } | { kind: "names"; pairs: readonly Replacement[] } | { kind: "shape" };

/** Nothing is hidden. */
export const OFF: Hiding = { kind: "off" };

/**
 * What a host name is made of, read as runs: the edges of a name are where the characters it is made of stop, found in one pass.
 * A pattern that looked for a name from every boundary read to the end of a long run from each one of them, and a hundred thousand
 * characters of what a page printed took longer than anybody waits.
 */
const NAME_RUN = /[a-z0-9.-]+/gi;

/** A host name and nothing else: labels that end in a dot, then a top-level domain of letters. No label holds a dot, so each is read once. */
const HOST = /^(?:[a-z0-9-]+\.)+[a-z]{2,}$/i;

/**
 * Where a run's name begins and ends: a name does not start or end with a dot or a dash, which belong to the sentence around it.
 *
 * @param run - A run of what a host name is made of.
 * @returns The offsets of the name inside the run.
 */
function nameEdges(run: string): { start: number; end: number } {
  let start = 0;
  let end = run.length;
  while (start < end && (run.charAt(start) === "." || run.charAt(start) === "-")) start += 1;
  while (end > start && (run.charAt(end - 1) === "." || run.charAt(end - 1) === "-")) end -= 1;
  return { start, end };
}

/**
 * What a report does with the names, from what was asked and what the server answered.
 *
 * @param asked - The person asked to replace domains.
 * @param pairs - What the server replaced, when it has answered.
 * @returns How to hide them.
 */
export function hidingFrom(asked: boolean, pairs: readonly Replacement[] | undefined): Hiding {
  if (!asked) return OFF;
  return pairs === undefined ? { kind: "shape" } : { kind: "names", pairs };
}

/**
 * Replaces the names in a text.
 *
 * @param text - Text of the browser.
 * @param hiding - How to hide them.
 * @returns The text with the aliases where the names were.
 */
export function hideDomains(text: string, hiding: Hiding): string {
  if (hiding.kind === "off") return text;
  if (hiding.kind === "names") return hiding.pairs.reduce((current, pair) => (pair.real === "" ? current : current.split(pair.real).join(pair.alias)), text);
  const seen = new Map<string, string>();
  return text.replace(NAME_RUN, (run: string) => {
    const { start, end } = nameEdges(run);
    const name = run.slice(start, end);
    if (!HOST.test(name)) return run;
    const key = name.toLowerCase();
    const alias = seen.get(key) ?? `domain-${String(seen.size + 1)}`;
    seen.set(key, alias);
    return `${run.slice(0, start)}${alias}${run.slice(end)}`;
  });
}
