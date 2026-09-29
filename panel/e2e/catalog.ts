/**
 * The console's catalogs, read by the Spanish sweep. They are plain TypeScript modules, so
 * the E2E suite imports them directly rather than keeping a copy of any string: a key added
 * to the console is checked the day it is added.
 */

import * as en from "../src/i18n/en";
import * as es from "../src/i18n/es";

export { en as english, es as spanish };

/** A text of the English catalog that must not be on screen in Spanish. */
export interface EnglishText {
  /** The dotted key, for the report: "nav.apps.label", "time.minutes.other". */
  key: string;
  /** The English text, as written in the catalog. */
  text: string;
  /**
   * Matches a whole on-screen text rendered from it: the text itself, or with each
   * `{placeholder}` standing for whatever the console put there.
   */
  pattern: RegExp;
}

/** The fewest letters, outside its placeholders, a text needs to be looked for. */
const MIN_LETTERS = 4;

const PLACEHOLDER = /\{[A-Za-z0-9_]+\}/g;

/**
 * Placeholders that hold a number, formatted and maybe with its unit ("3", "1.2 GB"): they
 * must contain a digit, so "{count} requests" is not found in "pull requests".
 */
const NUMERIC = new Set([
  "applied", "average", "bytes", "cores", "count", "cpu", "days", "fifteen", "five", "free", "high", "hours",
  "id", "interval", "kept", "left", "limit", "lines", "load1", "load15", "load5", "low", "max", "memory", "min",
  "minutes", "number", "one", "peak", "pid", "port", "removed", "required", "running", "seconds", "secrets",
  "shown", "size", "stopped", "timeout", "total", "used", "failed", "records", "deliveries", "wait",
]);

/** Placeholders that hold words: a time, a title, a reason. Up to four of them. */
const PHRASE = new Set([
  "cause", "date", "description", "detail", "details", "doing", "duration", "engine", "expiry", "label", "lifetime",
  "list", "platform", "problem", "reason", "state", "status", "summary", "time", "type", "uptime",
  "value", "verdict", "when", "why", "window",
]);

/** Placeholders that name something, which may take two words: "Uptime Kuma". */
const NAME = new Set(["name", "title", "app", "account", "owner"]);

/** What a placeholder may stand for on screen; anything else is one word (a domain, a path). */
function placeholderPattern(name: string): string {
  if (NUMERIC.has(name)) return "[^\\s]*\\d[^\\s]*(?: [^\\s]+)?";
  if (PHRASE.has(name)) return "[^\\s]+(?: [^\\s]+){0,3}";
  if (NAME.has(name)) return "[^\\s]+(?: [^\\s]+)?";
  return "[^\\s]+";
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Every leaf of a catalog tree, by dotted key. Plural forms are leaves of their own. */
function flatten(tree: unknown, prefix = "", into = new Map<string, string>()): Map<string, string> {
  if (typeof tree === "string") {
    into.set(prefix, tree);
    return into;
  }
  if (tree !== null && typeof tree === "object") {
    for (const [key, value] of Object.entries(tree)) flatten(value, prefix === "" ? key : `${prefix}.${key}`, into);
  }
  return into;
}

/**
 * The English texts a Spanish console must never show: every leaf whose Spanish differs
 * from it, leaving out what cannot be told apart from data or from Spanish - texts with
 * fewer than four letters outside their placeholders ("{count}s", "OK", "{a} / {b}").
 * Whitespace is collapsed, the way the page's text is read.
 */
export function englishLeftoverCandidates(): EnglishText[] {
  const english = flatten(en);
  const spanish = flatten(es);
  const candidates: EnglishText[] = [];
  for (const [key, text] of english) {
    const translated = spanish.get(key);
    const normalized = text.replace(/\s+/g, " ").trim();
    if (translated?.replace(/\s+/g, " ").trim() === normalized) continue;
    const literal = normalized.replace(PLACEHOLDER, "");
    if ((literal.match(/[A-Za-z]/g) ?? []).length < MIN_LETTERS) continue;
    const names = [...normalized.matchAll(PLACEHOLDER)].map((match) => match[0].slice(1, -1));
    const source = normalized
      .split(PLACEHOLDER)
      .map((part, index) => escape(part) + (index < names.length ? placeholderPattern(names[index] ?? "") : ""))
      .join("");
    candidates.push({ key, text: normalized, pattern: new RegExp(`^${source}$`) });
  }
  return candidates;
}
