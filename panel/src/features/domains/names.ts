/**
 * Domain names as the operator types them. The server is the authority on what it accepts
 * (`strict_domain`); this only catches the obvious before a round trip, and says so in the
 * same terms.
 */

import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import type { Locale } from "../../i18n";

const LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;
const TLD = /^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$/;

/** Trims and lowercases, the two changes the server accepts silently. */
export function normalizeDomain(value: string): string {
  return value.trim().toLowerCase();
}

/**
 * Why a name is not a domain the server would take, or null when it looks like one.
 * Schemes, ports and paths are refused rather than stripped: the server refuses them too.
 */
export function domainProblem(value: string, locale: Locale = getLocale()): string | null {
  const name = normalizeDomain(value);
  if (name === "") return translate(locale, "domains.domainValidation.enterADomain");
  if (/^[a-z][a-z0-9+.-]*:\/\//.test(name)) return translate(locale, "domains.domainValidation.noScheme");
  if (/[/:?#@\s]/.test(name)) return translate(locale, "domains.domainValidation.noPath");
  if (name.length > 253) return translate(locale, "domains.domainValidation.tooLong");
  const labels = name.split(".");
  if (labels.length < 2) return translate(locale, "domains.domainValidation.twoParts");
  if (labels.some((label) => label === "")) return translate(locale, "domains.domainValidation.emptyParts");
  if (!labels.every((label) => LABEL.test(label))) {
    return translate(locale, "domains.domainValidation.badCharacters");
  }
  if (!TLD.test(labels.at(-1) ?? "")) return translate(locale, "domains.domainValidation.badTld");
  return null;
}

/**
 * The names in free text: separated by spaces, commas or new lines, lowercased, each once,
 * in the order typed.
 */
export function parseNames(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[\s,;]+/)) {
    const name = normalizeDomain(part);
    if (name !== "") seen.add(name);
  }
  return [...seen];
}

/** The `www.` twin of a name, or null for a name that already is one. */
export function wwwOf(domain: string): string | null {
  const name = normalizeDomain(domain);
  return name.startsWith("www.") ? null : `www.${name}`;
}

/**
 * A list of names for a dense cell: the first `max`, joined, and how many more there are.
 * The full list belongs in a tooltip; this only decides what fits inline.
 */
export function truncatedNames(names: readonly string[], max = 3): { shown: string; rest: number } {
  const shown = names.slice(0, max);
  return { shown: shown.join(", "), rest: names.length - shown.length };
}
