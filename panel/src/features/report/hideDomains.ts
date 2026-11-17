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

const HOST_SHAPE = /\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b/gi;

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
  return text.replace(HOST_SHAPE, (name: string) => {
    const known = seen.get(name.toLowerCase());
    if (known !== undefined) return known;
    const alias = `domain-${String(seen.size + 1)}`;
    seen.set(name.toLowerCase(), alias);
    return alias;
  });
}
