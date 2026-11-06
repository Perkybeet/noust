/**
 * A table's cell keeps room above and below its content (owner item 122: "the tags do not respect the bottom margin of
 * the row"). The tags used to stand under the name, which made the row taller than the others and put them against its
 * border; they now follow the name on its own line (`NameWithTags`): the first ones as pebbles, the rest behind "+N", and
 * every row is the one height it has always had, with or without tags. In a phone's card the line stays inside the card.
 * The tags are not seeded: the list answers are given tags here, the way a person's own would arrive.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

/** A tag at the longest the server accepts, 32 letters: the widest pebble a card has to hold. */
const LONGEST = "t".repeat(32);
const SOME = ["production", "customer-facing", "nightly", "eu-west", "critical", "billing", "legacy"];

/** The air a row keeps above and below what its first cell holds, in pixels: it never touches the row's border. */
const ROOM = 4;

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
    test("keeps every row the height it has, with its tags on the line of its name and room above and below them", async ({ page, consoleServer }) => {
      test.setTimeout(240_000);
      await page.setViewportSize({ width: 1440, height: 900 });
      await tagTheList(page, list.pattern, list.key);
      await signIn(page, consoleServer, list.path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("table tbody tr").first()).toBeVisible();
      await expect(page.locator("table tbody tr [data-slot=name-with-tags] > span:not([translate])").first()).toBeVisible();
      await settle(page);

      const rows = await page.evaluate(() =>
        [...document.querySelectorAll("table tbody tr")].slice(0, 9).map((row) => {
          const box = row.getBoundingClientRect();
          // What the first cell holds: the name with its tags, and under it a line of its own where the row has one.
          const content = row.querySelector("td")?.firstElementChild ?? null;
          const line = row.querySelector("td [data-slot=name-with-tags]");
          const pebbles = line === null ? [] : [...line.querySelectorAll(":scope > span:not([translate]) > span")];
          const held = content?.getBoundingClientRect();
          return {
            height: Math.round(box.height),
            tagged: pebbles.length > 0,
            // A "+N" is a button in a table that does not open its rows and text in one that does.
            more: line !== null && line.querySelector("button, span[title]:not([translate])") !== null,
            twoLines: content !== null && getComputedStyle(content).flexDirection === "column" && content.children.length > 1,
            // The row's border is its bottom pixel: the gap is measured to the box, which includes it.
            below: held ? Math.round(box.bottom - held.bottom) : null,
            above: held ? Math.round(held.top - box.top) : null,
          };
        }),
      );
      expect(rows.some((row) => row.tagged), "no row carries tags").toBe(true);
      expect(rows.some((row) => !row.tagged), "every row carries tags").toBe(true);
      expect(rows.some((row) => row.more), "no row hides tags behind +N").toBe(true);
      for (const row of rows) {
        expect(row.below, "what the first cell holds ends too near the row's border").toBeGreaterThanOrEqual(ROOM);
        expect(row.above, "what the first cell holds begins too near the row's border").toBeGreaterThanOrEqual(ROOM);
      }
      // Tags do not make a row taller: rows of the same shape have the same height, with them or without, and a row of one line is the one height there has always been.
      for (const twoLines of [false, true]) {
        const same = new Set(rows.filter((row) => row.twoLines === twoLines).map((row) => row.height));
        expect(same.size, `rows of ${twoLines ? "two lines" : "one line"} are not all the same height: ${[...same].join(", ")}`).toBeLessThanOrEqual(1);
      }
      for (const row of rows.filter((candidate) => !candidate.twoLines)) expect(row.height, "a row of one line is taller than it was").toBe(ROW);
      await expectNoA11yViolations(page, `the ${list.name} table with tags`);
    });

    for (const width of [390, 320]) {
      test(`holds the tags on the line of the name inside a card on a ${String(width)}px screen`, async ({ page, consoleServer }) => {
        test.setTimeout(240_000);
        await page.setViewportSize({ width, height: 844 });
        await tagTheList(page, list.pattern, list.key);
        await signIn(page, consoleServer, list.path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect(page.locator("[data-slot=name-with-tags] > span:not([translate])").first()).toBeVisible();
        await settle(page);

        const cards = await page.evaluate(() =>
          [...document.querySelectorAll("ul[aria-label] > li")]
            .map((card) => ({ card, line: card.querySelector("[data-slot=name-with-tags]") }))
            .filter((found): found is { card: Element; line: Element } => found.line !== null && found.line.querySelector(":scope > span:not([translate])") !== null)
            .slice(0, 6)
            .map(({ card, line }) => {
              const box = card.getBoundingClientRect();
              const lineBox = line.getBoundingClientRect();
              const pebbles = [...line.querySelectorAll(":scope > span:not([translate]) > span")].map((pebble) => pebble.getBoundingClientRect());
              return {
                inParagraph: line.closest("p") !== null,
                left: Math.min(...pebbles.map((pebble) => pebble.left)) - box.left,
                right: box.right - Math.max(...pebbles.map((pebble) => pebble.right)),
                // One line: the name and its tags are not stacked.
                oneLine: lineBox.height <= 32,
              };
            }),
        );
        expect(cards.length).toBeGreaterThan(0);
        for (const card of cards) {
          expect(card.inParagraph, "a list inside a paragraph").toBe(false);
          // No tag is cut by the card that holds it: each one is inside it on both sides.
          expect(card.left).toBeGreaterThanOrEqual(-0.5);
          expect(card.right).toBeGreaterThanOrEqual(-0.5);
          expect(card.oneLine, "the tags are stacked under the name").toBe(true);
        }
        const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, window: document.documentElement.clientWidth }));
        expect(widths.page, "the page scrolls sideways").toBeLessThanOrEqual(widths.window);
        await expectNoA11yViolations(page, `the ${list.name} cards with tags`);
      });
    }
  });
}
