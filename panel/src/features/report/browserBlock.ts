/**
 * What the browser knows that a maintainer would otherwise ask for: the page, the console's version, the browser, the
 * size of the window and the last errors the tab saw.
 *
 * It is one JSON file in the report. Nothing here is fetched from the server, so it exists when the server
 * does not answer, and it is masked here with the server's own shapes (`redact.ts`) before the person reads
 * it, because the page's address and an error's text can carry a token.
 */

import type { ErrorEntry } from "../../lib/errorBuffer";
import { redact } from "./redact";

/** What the block is made of, before it is written down. */
export interface BrowserFacts {
  /** The path of the console and its query, as on screen. */
  page: string;
  /** The version of Noust that serves the console, when the server has said it. */
  version: string | null;
  userAgent: string;
  viewport: { width: number; height: number };
  theme: "light" | "dark";
  /** The language the console is in. */
  language: string;
  errors: readonly ErrorEntry[];
}

export interface BrowserBlock {
  /** The JSON text, with a mask where a secret was. */
  text: string;
  /** How many secrets were hidden in it. */
  holes: number;
}

/** The system a user agent names, in a word. */
function systemOf(userAgent: string): string | null {
  if (userAgent.includes("Android")) return "Android";
  if (/iPhone|iPad|iPod/.test(userAgent)) return "iOS";
  if (userAgent.includes("CrOS")) return "ChromeOS";
  if (userAgent.includes("Windows")) return "Windows";
  if (/Mac OS X|Macintosh/.test(userAgent)) return "macOS";
  if (/Linux|X11/.test(userAgent)) return "Linux";
  return null;
}

/** The browser a user agent names, with its major version. The order matters: Chrome's string says "Safari" too. */
function browserOf(userAgent: string): string | null {
  const known: [RegExp, string][] = [
    [/Edg(?:e|A|iOS)?\/(\d+)/, "Edge"],
    [/OPR\/(\d+)/, "Opera"],
    [/Firefox\/(\d+)|FxiOS\/(\d+)/, "Firefox"],
    [/Chrome\/(\d+)|CriOS\/(\d+)/, "Chrome"],
    [/Version\/(\d+)[^ ]* (?:Mobile\/\S+ )?Safari/, "Safari"],
  ];
  for (const [pattern, name] of known) {
    const match = pattern.exec(userAgent);
    if (match === null) continue;
    // One of the alternatives of the pattern holds the version; the others did not take part.
    const version = match.slice(1).find((part: string | undefined) => part !== undefined);
    return version === undefined ? name : `${name} ${version}`;
  }
  return null;
}

/**
 * The browser and the system, as a person would say them.
 *
 * @param userAgent - `navigator.userAgent`.
 * @returns "Firefox 132 on Linux"; the user agent itself, cut, when it names neither.
 */
export function browserName(userAgent: string): string {
  const browser = browserOf(userAgent);
  const system = systemOf(userAgent);
  if (browser === null && system === null) return userAgent.slice(0, 120) || "unknown";
  if (browser === null) return system ?? "unknown";
  return system === null ? browser : `${browser} on ${system}`;
}

/**
 * Writes the block of the browser.
 *
 * @param facts - What the browser knows.
 * @returns The JSON, masked, and how many secrets it hid.
 */
export function browserBlock(facts: BrowserFacts): BrowserBlock {
  let holes = 0;
  const hide = (text: string): string => {
    const result = redact(text);
    holes += result.holes;
    return result.text;
  };
  const body = {
    page: hide(facts.page),
    console: facts.version,
    browser: hide(browserName(facts.userAgent)),
    viewport: `${String(facts.viewport.width)}x${String(facts.viewport.height)}`,
    theme: facts.theme,
    language: facts.language,
    errors: facts.errors.map((entry) => ({ at: entry.at, kind: entry.kind, message: hide(entry.message) })),
  };
  return { text: `${JSON.stringify(body, null, 2)}\n`, holes };
}

/**
 * What this window knows of itself, read now.
 *
 * @param page - The page on screen with its query, as the router has it.
 * @param version - The Noust version the server reported, when it did.
 * @param language - The console's language.
 * @param errors - The last errors of the tab.
 * @returns The facts for `browserBlock`.
 */
export function currentFacts(page: string, version: string | null, language: string, errors: readonly ErrorEntry[]): BrowserFacts {
  const pinned = window.document.documentElement.dataset["theme"];
  const dark = pinned === undefined ? window.matchMedia("(prefers-color-scheme: dark)").matches : pinned === "dark";
  return {
    page,
    version,
    userAgent: navigator.userAgent,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    theme: dark ? "dark" : "light",
    language,
    errors,
  };
}
