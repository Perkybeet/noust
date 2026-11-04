/**
 * A table's cell keeps room above and below its content (owner item 122: "the tags do not respect the bottom margin of
 * the row"). `DataTable` drew every `td` as `h-11`, a minimum height, with no vertical padding, so a name with chips
 * under it (44px of content or more) ran into the row's border: measured, the chips ended 1px above it in Services
 * and 2 to 3px in Databases, and the name began 0 to 2px under the one above.
 *
 * The padding must not make a row taller than it was when there is nothing under the name, so rows without tags are
 * held to the heights they have always had; and in a phone's card the list of tags stays whole, in markup that is valid.
 * The tags are not seeded: the list answers are given tags here, the way a person's own would arrive.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

/** A tag at the longest the server accepts, 32 letters: the widest chip a card has to hold. */
const LONGEST = "t".repeat(32);
const SOME = ["production", "customer-facing", "nightly", "eu-west", "critical", "billing", "legacy"];

/** The air a row keeps above and below a name and its tags, in pixels. */
const ROOM = 6;

/** What a comfortable row has always been. */
const ROW = 44;

/** Gives every third row no tags, the first one the widest, and the others a few. */
async function tagTheList(page: Page, pattern: string, key: string): Promise<void> {
  await page.route(pattern, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, Record<string, unknown>[]>;
    const rows = body[key];
    if (Array.isArray(rows)) {
      rows.forEach((row, index) => {
        row.tags = index % 3 === 2 ? [] : index === 0 ? [LONGEST, ...SOME.slice(0, 2)] : SOME.slice(0, (index % 3) + 1 + (index % 2));
      });
    }
    await route.fulfill({ response, json: body });
  });
}

const LISTS = [
  { name: "services", path: "/server/services", pattern: "**/api/services?*", key: "services" },
  { name: "databases", path: "/databases", pattern: "**/api/databases/databases*", key: "databases" },
] as const;

for (const list of LISTS) {
  test.describe(`the ${list.name} table`, () => {
    test("keeps room around a name with its tags, and rows without tags stay as tall as they were", async ({ page, consoleServer }) => {
      test.setTimeout(240_000);
      await page.setViewportSize({ width: 1440, height: 900 });
      await tagTheList(page, list.pattern, list.key);
      await signIn(page, consoleServer, list.path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("table tbody tr").first()).toBeVisible();
      await expect(page.getByRole("list", { name: "Tags" }).first()).toBeVisible();
      await settle(page);

      const rows = await page.evaluate(() =>
        [...document.querySelectorAll("table tbody tr")].slice(0, 9).map((row) => {
          const box = row.getBoundingClientRect();
          const chips = row.querySelector("ul[aria-label]");
          const first = row.querySelector("td")?.firstElementChild;
          return {
            height: Math.round(box.height),
            tagged: chips !== null,
            // The row's border is its bottom pixel: the gap is measured to the box, which includes it.
            below: chips ? Math.round(box.bottom - chips.getBoundingClientRect().bottom) : null,
            above: first ? Math.round(first.getBoundingClientRect().top - box.top) : null,
          };
        }),
      );
      expect(rows.some((row) => row.tagged), "no row carries tags").toBe(true);
      expect(rows.some((row) => !row.tagged), "every row carries tags").toBe(true);
      for (const row of rows) {
        if (row.tagged) {
          expect(row.below, "the tags end too near the row's border").toBeGreaterThanOrEqual(ROOM);
          expect(row.above, "the name begins too near the row's border").toBeGreaterThanOrEqual(ROOM);
        } else {
          expect(row.height, "a row without tags is taller than it was").toBe(ROW);
        }
      }
      await expectNoA11yViolations(page, `the ${list.name} table with tags`);
    });

    for (const width of [390, 320]) {
      test(`holds the tags whole in a card on a ${String(width)}px screen`, async ({ page, consoleServer }) => {
        test.setTimeout(240_000);
        await page.setViewportSize({ width, height: 844 });
        await tagTheList(page, list.pattern, list.key);
        await signIn(page, consoleServer, list.path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect(page.getByRole("list", { name: "Tags" }).first()).toBeVisible();
        await settle(page);

        const cards = await page.evaluate(() =>
          [...document.querySelectorAll("ul[aria-label] > li")]
            .filter((card) => card.querySelector("ul[aria-label=Tags]") !== null)
            .slice(0, 6)
            .map((card) => {
              const chips = card.querySelector<HTMLElement>("ul[aria-label=Tags]");
              if (chips === null) throw new Error("no tags");
              const holder = chips.closest("p, button, div.truncate, div.min-w-0");
              const holderBox = (holder ?? card).getBoundingClientRect();
              const boxes = [...chips.querySelectorAll("li")].map((chip) => chip.getBoundingClientRect());
              return {
                inParagraph: chips.closest("p") !== null,
                left: Math.min(...boxes.map((box) => box.left)) - holderBox.left,
                right: holderBox.right - Math.max(...boxes.map((box) => box.right)),
                below: holderBox.bottom - Math.max(...boxes.map((box) => box.bottom)),
                chipsWide: chips.scrollWidth - chips.clientWidth,
              };
            }),
        );
        expect(cards.length).toBeGreaterThan(0);
        for (const card of cards) {
          expect(card.inParagraph, "a list inside a paragraph").toBe(false);
          // No chip is cut by the box that holds the card's name: each one is inside it on every side.
          expect(card.left).toBeGreaterThanOrEqual(-0.5);
          expect(card.right).toBeGreaterThanOrEqual(-0.5);
          expect(card.below).toBeGreaterThanOrEqual(-0.5);
          expect(card.chipsWide).toBeLessThanOrEqual(0);
        }
        const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, window: document.documentElement.clientWidth }));
        expect(widths.page, "the page scrolls sideways").toBeLessThanOrEqual(widths.window);
        await expectNoA11yViolations(page, `the ${list.name} cards with tags`);
      });
    }
  });
}
