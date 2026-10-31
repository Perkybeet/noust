/**
 * How an application's pages share their width, in the two languages and at the two widths that
 * showed it: the overview at 1440px with both bars, and the deployments on a phone. What a browser
 * lays out is what is measured; jsdom has no layout.
 *
 * - The history of deployments has the whole width: a commit message is what its lines are read for,
 *   and two thirds of the page cut it while the card of the health beside it held two facts.
 * - The label of the uptime fits its place in Spanish, whose words run longest.
 * - On a phone the trail keeps the two names that matter, whole, and a card says its facts apart by
 *   room, with no mark that wraps alone to a line of its own.
 * - A figure and its unit are one piece in Spanish ("41 s"): in the mono face a plain space was a cell.
 */

import type { Locator, Page } from "@playwright/test";

import { expect, settle, signIn, test } from "./fixtures";
import { signInSpanish, useSpanish } from "./spanish";

const APP = "shop.example.net";

/** The narrow no-break space Spanish puts between a figure and its unit. */
const NARROW_SPACE = String.fromCodePoint(0x202f);

/** The texts inside `scope` that an ellipsis hides part of. */
async function cutTexts(scope: Locator): Promise<string[]> {
  return scope.evaluate((root) => {
    const cut: string[] = [];
    for (const element of [root, ...root.querySelectorAll("*")]) {
      if (!(element instanceof HTMLElement) || !element.checkVisibility()) continue;
      if (getComputedStyle(element).textOverflow === "ellipsis" && element.scrollWidth > element.clientWidth + 1) cut.push((element.innerText || "").trim().slice(0, 60));
    }
    return cut;
  });
}

async function width(locator: Locator): Promise<number> {
  const box = await locator.boundingBox();
  if (box === null) throw new Error("nothing drawn");
  return box.width;
}

async function openSpanish(page: Page, consoleServer: Parameters<typeof signIn>[1], size: { width: number; height: number }, path: string): Promise<void> {
  await useSpanish(page);
  await page.setViewportSize(size);
  await signInSpanish(page, consoleServer, path);
  await expect(page.getByRole("heading", { level: 1, name: APP })).toBeVisible();
  await settle(page);
}

test("the overview gives the history of deployments the whole width", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${APP}`);
  const highlights = page.getByRole("region", { name: `Highlights of ${APP}` });
  const history = page.getByRole("list", { name: `Last deployments of ${APP}` });
  await expect(history).toBeVisible();
  // As wide as the highlights over it: the same left edge and the same right edge.
  expect(Math.abs((await width(history)) - (await width(highlights)))).toBeLessThanOrEqual(1);
  expect(await cutTexts(history)).toEqual([]);
  await settle(page);
});

test("the labels of the health are whole in Spanish at 1440px", async ({ page, consoleServer }) => {
  await openSpanish(page, consoleServer, { width: 1440, height: 900 }, `/apps/${APP}`);
  const health = page.getByRole("heading", { level: 2, name: "Salud" }).locator("xpath=ancestor::section[1]");
  await expect(health.getByText("Tiempo activo")).toBeVisible();
  expect(await cutTexts(health)).toEqual([]);
});

test("a phone keeps the two names of the trail whole and the facts of a card apart", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, `/apps/${APP}/deployments`);
  const trail = page.getByRole("main").getByRole("navigation", { name: "Breadcrumb" });
  await expect(trail).toBeVisible();
  expect(await cutTexts(trail)).toEqual([]);
  // The page and what it is under: not a third step cut to nothing.
  await expect(trail.getByRole("link", { name: APP })).toBeVisible();
  await expect(trail.getByRole("link", { name: "Applications" })).toBeHidden();
  const cards = page.getByRole("list", { name: `Deploys of ${APP}, newest first` });
  await expect(cards.getByRole("listitem").first()).toBeVisible();
  // No mark between the facts of a card: nothing to wrap alone.
  expect(await cards.evaluate((list, mark) => list.textContent.includes(mark), String.fromCodePoint(0xb7))).toBe(false);
});

test("the trail's separators are even, the ones beside a switcher too", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${APP}/deployments`);
  const trail = page.getByRole("banner").getByRole("navigation", { name: "Breadcrumb" });
  await expect(trail.getByRole("button", { name: /^Switch/ }).first()).toBeVisible();
  const gaps = await trail.evaluate((nav) => {
    const pieces: { kind: string; left: number; right: number }[] = [];
    for (const element of nav.querySelectorAll("a, span[aria-current], svg")) {
      let box = element.getBoundingClientRect();
      if (element.tagName !== "svg") {
        const range = document.createRange();
        range.selectNodeContents(element);
        box = range.getBoundingClientRect();
      }
      pieces.push({ kind: element.tagName === "svg" ? (element.classList.contains("lucide-chevron-right") ? "separator" : "switch") : "name", left: box.left, right: box.right });
    }
    return pieces.slice(1).map((piece, index) => ({ between: `${pieces[index]?.kind ?? ""} and ${piece.kind}`, room: piece.left - (pieces[index]?.right ?? 0) }));
  });
  const plain = gaps.filter((gap) => !gap.between.includes("switch")).map((gap) => gap.room);
  const beside = gaps.filter((gap) => gap.between.includes("switch")).map((gap) => gap.room);
  expect(plain.length).toBeGreaterThan(0);
  expect(beside.length).toBeGreaterThan(0);
  // Whatever the glyph's own padding, the room around a switcher reads as the room around a separator: within 2px of it.
  for (const room of beside) expect(Math.abs(room - (plain[0] ?? 0)), JSON.stringify(gaps)).toBeLessThanOrEqual(2);
});

test("a duration in Spanish keeps its figure and its unit together", async ({ page, consoleServer }) => {
  await openSpanish(page, consoleServer, { width: 1440, height: 900 }, `/apps/${APP}`);
  const history = page.getByRole("list", { name: `Últimos despliegues de ${APP}` });
  await expect(history).toBeVisible();
  const text = (await history.textContent()) ?? "";
  // The figure and the unit are joined by a narrow no-break space, in the text a browser keeps.
  expect(text).toMatch(new RegExp(`\\d${NARROW_SPACE}(?:ms|s|min)`));
  expect(text).not.toMatch(/\d (?:ms|s|min)\b/);
});
