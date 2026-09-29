/**
 * Driving the console in Spanish: the language set before the first page loads, the Spanish
 * sign-in, and the check that no English text of the catalogs is left on screen.
 */

import type { Page } from "@playwright/test";

import { englishLeftoverCandidates, spanish } from "./catalog";
import { expect } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

/**
 * Makes every page of this test's browser start in Spanish, the way a browser that picked
 * Español once does: `wasm.locale` is in storage before any of the console's scripts run.
 */
export async function useSpanish(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("wasm.locale", "es");
  });
}

/** Signs in through the sign-in page as it reads in Spanish. */
export async function signInSpanish(page: Page, server: ConsoleServer, next?: string): Promise<void> {
  await page.goto(next === undefined ? "/login" : `/login?next=${encodeURIComponent(next)}`);
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  await page.getByLabel(spanish.auth.accessToken).fill(server.token);
  await page.getByRole("button", { name: spanish.auth.signIn }).click();
  if (server.totpSecret !== null) {
    const code = page.getByLabel(spanish.auth.twoFactorCode);
    await expect(code).toBeFocused();
    await code.fill(server.secondFactor());
    await page.getByRole("button", { name: spanish.auth.verify }).click();
  }
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

/** Answers "Confirma que eres tú" with an unspent second factor, or the access token. */
export async function confirmItsYouSpanish(page: Page, server: ConsoleServer): Promise<void> {
  const dialog = page.getByRole("dialog", { name: spanish.auth.elevate.title });
  await expect(dialog).toBeVisible();
  if (server.totpSecret !== null) await dialog.getByLabel(spanish.auth.elevate.authenticationCode).fill(server.secondFactor());
  else await dialog.getByLabel(spanish.auth.accessToken).fill(server.token);
  await dialog.getByRole("button", { name: spanish.auth.elevate.confirm }).click();
  await expect(dialog).toBeHidden();
}

const CANDIDATES = englishLeftoverCandidates();

/**
 * Text WASM's server wrote, which the console shows as it came and 2.3 leaves in English
 * (spec 2026-09-28, section 1: the CLI and the server's own text are out of its scope), that
 * happens to read like a catalog text. Listed by the page it is on, each with its origin,
 * so a real leftover on another page is still found.
 */
const SERVER_TEXT: readonly { page: string; text: RegExp; origin: string }[] = [
  // managers/health.py: the names and verdicts of the server-wide health report.
  { page: "server", text: /^(Applications|Memory|Running|Not installed)$/, origin: "managers/health.py" },
  // A job's description, recorded with the job by whoever queued it.
  { page: "activity", text: /^Deploying \S+$/, origin: "the job's description" },
  // managers/diagnose.py: the evidence heading of the journal it read.
  { page: "app-diagnose", text: /^Last \d+ journal line\(s\) for \S+$/, origin: "managers/diagnose.py" },
  // Not the server's words but data: a seeded commit's subject, which reads like "Show the {label}".
  { page: "overview", text: /^Show the refund status on receipts$/, origin: "a commit message" },
];

/**
 * Every text a person reads or hears on the page as it is: the text of each visible
 * element (its own text nodes, and all of it for an element that holds a sentence with
 * markup inside), its accessible attributes, and the document title. Left out: what the
 * console marks as data or system output (`translate="no"`, code, a terminal) and what
 * is marked as another language (the autonyms of the language switch).
 */
async function textsOnScreen(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const normalize = (text: string) => text.replace(/\s+/g, " ").trim();
    const found = new Set<string>();
    const add = (text: string | null | undefined) => {
      const value = normalize(text ?? "");
      if (value !== "" && value.length <= 600) found.add(value);
    };
    add(document.title);
    const excluded = (element: Element) =>
      element.closest('code, pre, kbd, samp, [translate="no"]') !== null ||
      !(element.closest("[lang]")?.getAttribute("lang") ?? "es").startsWith("es");
    for (const element of document.body.querySelectorAll("*")) {
      if (element instanceof HTMLScriptElement || element instanceof HTMLStyleElement) continue;
      if (!element.checkVisibility()) continue;
      if (excluded(element)) continue;
      add([...element.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent).join(""));
      // A sentence with an element inside (a link, a name in bold) is one catalog text.
      if (element.children.length > 0 && element.children.length <= 6) {
        add(element instanceof HTMLElement ? element.innerText : element.textContent);
      }
      for (const name of ["aria-label", "aria-description", "placeholder", "title", "alt"]) add(element.getAttribute(name));
    }
    return [...found];
  });
}

/**
 * Fails listing every English catalog text still on screen, with its key, when the
 * console is in Spanish.
 */
export async function expectNoEnglishLeftovers(page: Page, label: string): Promise<void> {
  const texts = await textsOnScreen(page);
  const leftovers: string[] = [];
  for (const text of texts) {
    const match = CANDIDATES.find((candidate) => candidate.pattern.test(text));
    if (SERVER_TEXT.some((allowed) => allowed.page === label && allowed.text.test(text))) continue;
    if (match !== undefined) leftovers.push(`${match.key}: ${JSON.stringify(text)}`);
  }
  expect(leftovers, `English text left on ${label} in Spanish:\n${leftovers.join("\n")}`).toEqual([]);
}
