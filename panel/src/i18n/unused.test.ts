import { describe, expect, it } from "vitest";

import * as en from "./en";

/**
 * Every text of the catalogs is read by some screen.
 *
 * A key nobody names is a text the operator never sees and every browser still downloads, in both languages:
 * the 4.0 pages replaced whole screens and left 126 of them behind, 4.7 kB of the gzip weight of the console.
 * The scan is cheap and forgiving, so it finds what is certainly dead and not more:
 *
 * - a key counts as read when a production file writes it whole between quotes;
 * - a template literal that begins like a key (`auditEvents.action.${name}`, `codes.c${code}`) reads every key that
 *   begins with that start;
 * - a string that is the beginning of keys ("domains.siteRoute.trace", to which a helper appends `.port`) reads
 *   the whole subtree under it.
 *
 * A key only a test reads is dead for the operator: list it in KEPT with the reason, or let the test read the
 * text it needs. When a screen goes, delete its keys from `en/` and from `es/` in the same change.
 */

type Node = string | { readonly [key: string]: Node };

const PLURAL_CATEGORIES = new Set(["zero", "one", "two", "few", "many", "other"]);

function isPlural(node: Node): boolean {
  return typeof node === "object" && typeof node["other"] === "string" && Object.keys(node).every((key) => PLURAL_CATEGORIES.has(key));
}

/** Every message as a dotted key; a plural is one message. */
function keysOf(node: Node, prefix = ""): string[] {
  if (typeof node === "string" || isPlural(node)) return [prefix];
  return Object.entries(node).flatMap(([key, child]) => keysOf(child, prefix === "" ? key : `${prefix}.${key}`));
}

/** The production sources: no test, no test helper, no catalog, no generated file. */
const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**", "!../i18n/en/**", "!../i18n/es/**", "!../**/*.gen.ts"], {
  eager: true,
  query: "?raw",
  import: "default",
});

/** Keys that only a test reads, each with why. */
const KEPT: Readonly<Record<string, string>> = {
  "backups.area": "the lazy-loading tests use it as the example of a key that is not in the chrome",
};

/** What the sources name: whole keys between quotes, and the starts of template literals. */
function reads(sources: Iterable<string>): { whole: Set<string>; starts: string[] } {
  const whole = new Set<string>();
  const starts: string[] = [];
  for (const text of sources) {
    for (const match of text.matchAll(/["'`]([A-Za-z]\w*(?:\.[A-Za-z0-9_-]+)+)["'`]/g)) if (match[1] !== undefined) whole.add(match[1]);
    // The start may stop inside a word (`network.access.routes.codes.c${code}`): every key that begins with it is read.
    for (const match of text.matchAll(/`([A-Za-z]\w*\.[\w.-]*)\$\{/g)) if (match[1] !== undefined) starts.push(match[1]);
  }
  return { whole, starts };
}

/** Whether something reads the key: itself, the start of a template it matches, or a start of keys above it. */
function isRead(key: string, found: { whole: Set<string>; starts: string[] }): boolean {
  if (found.whole.has(key)) return true;
  if (found.starts.some((start) => key.startsWith(start))) return true;
  const parts = key.split(".");
  for (let length = 2; length < parts.length; length += 1) if (found.whole.has(parts.slice(0, length).join("."))) return true;
  return false;
}

describe("the keys nobody reads", () => {
  const found = reads(Object.values(RAW));
  const keys = keysOf({ ...en });

  it("reads the whole console and the whole catalog", () => {
    expect(Object.keys(RAW).length).toBeGreaterThan(500);
    expect(keys.length).toBeGreaterThan(5000);
  });

  it("leaves no catalog text that no screen draws", () => {
    const unread = keys.filter((key) => !isRead(key, found) && !(key in KEPT));
    expect(unread, "Keys no production file names: delete them from i18n/en and i18n/es, or read them").toEqual([]);
  });

  it("keeps a key for a test only while the key exists and nothing else reads it", () => {
    const stale = Object.keys(KEPT).filter((key) => !keys.includes(key) || isRead(key, found));
    expect(stale).toEqual([]);
  });

  it("would see a key written whole, a template that starts like one and a start of keys", () => {
    const probe = reads(['const a = t("shop.title");', "const b = t(`kinds.${kind}.label`);", 'const prefix = "domains.trace";', "const c = t(`codes.c${code}`);"]);
    expect(isRead("shop.title", probe)).toBe(true);
    expect(isRead("kinds.web.label", probe)).toBe(true);
    expect(isRead("domains.trace.port", probe)).toBe(true);
    expect(isRead("codes.c302", probe)).toBe(true);
    expect(isRead("shop.other", probe)).toBe(false);
    expect(isRead("domains.other.port", probe)).toBe(false);
  });
});
