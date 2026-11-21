/**
 * No page of the console draws a message key.
 *
 * The texts of a page are chunks the router fetches before it shows the page, and the rest of the
 * language arrives while the browser is idle. When a page draws a namespace nobody declared for
 * its route, `t()` answers with the key until the chunk lands (or, in Spanish, with English). The
 * unit tests open every route on a cold catalog; this opens every route of the seeded machine in a
 * real browser, loaded cold, in both languages, and fails on a key such as `databases.list.title`
 * on screen, as an operator would read it. The Spanish sweep (spanish.spec.ts) finds the English
 * left behind; this finds the key.
 */

import type { Page } from "@playwright/test";

import { english } from "./catalog";
import { expect, settle, signIn, test } from "./fixtures";
import { ROUTES, routePath } from "./routes";
import { signInSpanish, useSpanish } from "./spanish";

const DESKTOP = { width: 1440, height: 900 };

/** Every key of the English catalog: plain messages, and plurals as the one message they are. */
function messageKeys(): string[] {
  const keys: string[] = [];
  const visit = (node: unknown, prefix: string): void => {
    if (typeof node === "string") {
      keys.push(prefix);
    } else if (typeof node === "object" && node !== null) {
      const entries = Object.entries(node);
      if (entries.some(([name]) => name === "other")) keys.push(prefix);
      else for (const [name, child] of entries) visit(child, prefix === "" ? name : `${prefix}.${name}`);
    }
  };
  visit({ ...english }, "");
  return keys;
}

const KEYS = messageKeys();

/**
 * The first words of every key there is. A text that is `<namespace>.<a>.<b>` and is not in the catalog is a key that was asked
 * for and does not exist: the page drew it because the lookup failed, which is the one case the list above cannot see (a key that
 * is missing is not in it). Two dots keep out what is only a file name or an address (`docker.yaml`, `example.com`).
 */
const NAMESPACES = Object.keys(english);

/** The message keys drawn on the page as text or as an accessible name. */
async function drawnKeys(page: Page): Promise<string[]> {
  return page.evaluate(({ keys, namespaces }) => {
    const known = new Set(keys);
    const spaces = new Set(namespaces);
    const found = new Set<string>();
    const missing = /^([a-z][A-Za-z0-9]*)(?:\.[A-Za-z0-9_-]+){2,}$/;
    // A host name starts like a key when its first label is a namespace (`server.example.com`): the top-level names are not the last word of a key.
    const host = /\.(?:com|net|org|io|dev|local|lan|internal|invalid|example)$/i;
    const check = (text: string | null | undefined): void => {
      const value = text?.trim();
      if (value === undefined) return;
      if (known.has(value)) found.add(value);
      else if (!host.test(value) && spaces.has(missing.exec(value)?.[1] ?? "")) found.add(value);
    };
    check(document.title);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) check(node.textContent);
    for (const element of document.body.querySelectorAll("[aria-label], [title], [placeholder], [alt]")) {
      for (const name of ["aria-label", "title", "placeholder", "alt"]) check(element.getAttribute(name));
    }
    return [...found].sort();
  }, { keys: KEYS, namespaces: NAMESPACES });
}

for (const language of ["en", "es"] as const) {
  for (const route of ROUTES) {
    test(`${route.name} draws no message key in ${language}`, async ({ page, consoleServer }, testInfo) => {
      test.skip(testInfo.project.name !== "light", "the language of a text does not depend on the theme");
      await page.setViewportSize(DESKTOP);
      if (language === "es") {
        await useSpanish(page);
        await signInSpanish(page, consoleServer);
      } else {
        await signIn(page, consoleServer);
      }
      // A cold load of the page itself, not the client-side navigation that follows sign-in.
      await page.goto(await routePath(page, route));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      expect(await drawnKeys(page), `message keys on screen at ${route.name} in ${language}`).toEqual([]);
    });
  }
}

test("the sweep finds a key where one is drawn", async ({ page }) => {
  await page.setContent(`<main><p>${KEYS[0] ?? ""}</p><button aria-label="${KEYS[1] ?? ""}">x</button><p>Applications</p></main>`);
  expect(await drawnKeys(page)).toEqual([KEYS[0], KEYS[1]].sort());
});

test("the sweep finds a key the catalog does not have, which is how a text that was never written is drawn", async ({ page }) => {
  // Settings drew `settingsHome.summary.approval-policy` for two sections whose lines nobody wrote; it was in no list.
  await page.setContent(
    `<main><p>settingsHome.summary.nowhere</p><button aria-label="nav.screens.nothing.label">x</button><p>docker.yaml</p><p>example.com</p><p>noust.example.org</p><p>server.example.com</p><p>docker.shop.example.net</p></main>`,
  );
  expect(await drawnKeys(page)).toEqual(["nav.screens.nothing.label", "settingsHome.summary.nowhere"]);
});
