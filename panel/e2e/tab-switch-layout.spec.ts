/**
 * Switching tabs never moves the page sideways (backlog item 43). Going from a long tab to a
 * short one takes the page's scrollbar away; unless its room is kept (`scrollbar-gutter:
 * stable` on the root, app.css), everything centred or full-width slides by the scrollbar's
 * width, and so does the tab the operator just clicked.
 *
 * Headless Chromium hides scrollbars (`--hide-scrollbars`), which is why layout-shift.spec.ts
 * cannot see this. This spec launches it without that flag, so Linux's classic 15px
 * scrollbars take room, and first proves that they do: a run where scrollbars take no room
 * could never fail, and fails instead of passing for nothing.
 */

import type { Locator, Page } from "@playwright/test";

import { expect, settle, signIn, test } from "./fixtures";

test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } });

interface TabbedPage {
  name: string;
  /** Where the long tab is. */
  long: string;
  /** The short tab: how to find it on the page. */
  short: (page: Page) => Locator;
  /** Where the short tab's URL ends up, to wait for the switch. */
  shortUrl: RegExp;
  /** What opens the way to the short view when it is not on the strip (a menu). */
  reveal?: (page: Page) => Promise<void>;
}

const PAGES: readonly TabbedPage[] = [
  {
    name: "an application",
    long: "/apps/shop.example.net/settings/deploy-on-push",
    short: (page) => page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Domains" }),
    shortUrl: /\/apps\/shop\.example\.net\/domains$/,
  },
  {
    name: "an application, logs",
    long: "/apps/shop.example.net/settings/deploy-on-push",
    short: (page) => page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Logs" }),
    shortUrl: /\/apps\/shop\.example\.net\/logs$/,
  },
  {
    // Diagnose left the strip (eight tabs at most) for the header's menu: reaching it that way
    // must not move the page either.
    name: "an application, diagnose from its menu",
    long: "/apps/shop.example.net/settings/deploy-on-push",
    reveal: async (page) => {
      await page.locator("main header").filter({ has: page.getByRole("heading", { level: 1 }) }).getByRole("button", { name: "More actions" }).click();
    },
    short: (page) => page.getByRole("menuitem", { name: "Diagnose" }),
    shortUrl: /\/apps\/shop\.example\.net\/diagnose$/,
  },
  {
    name: "settings",
    long: "/settings/notifications",
    short: (page) => page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "API tokens" }),
    shortUrl: /\/settings\/tokens$/,
  },
  {
    name: "domains and certificates",
    long: "/domains/sites",
    short: (page) => page.getByRole("navigation", { name: "Domains and certificates sections" }).getByRole("link", { name: /^Certificates/ }),
    shortUrl: /\/domains$/,
  },
];

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];

interface Column {
  x: number;
  width: number;
  /**
   * Room a present vertical scrollbar takes. Chromium does not count a kept but empty gutter in
   * clientWidth, so this proves the premise on the long tab only; the column's box is the test.
   */
  gutter: number;
  scrolls: boolean;
}

async function column(page: Page): Promise<Column> {
  return page.evaluate(() => {
    const content = document.querySelector("main#main > div");
    if (!content) throw new Error("no content column under main#main");
    const box = content.getBoundingClientRect();
    const root = document.documentElement;
    return {
      x: Math.round(box.x * 100) / 100,
      width: Math.round(box.width * 100) / 100,
      gutter: window.innerWidth - root.clientWidth,
      scrolls: root.scrollHeight > root.clientHeight,
    };
  });
}

for (const viewport of VIEWPORTS) {
  for (const tabbed of PAGES) {
    test(`switching tabs on ${tabbed.name} at ${String(viewport.width)}px keeps the content column in place`, async ({
      page,
      consoleServer,
    }) => {
      await page.setViewportSize(viewport);
      await signIn(page, consoleServer);
      await page.goto(tabbed.long);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      const before = await column(page);
      // The premise: this tab scrolls, and its scrollbar takes room. Without it the test proves nothing.
      expect(before.scrolls, `${tabbed.long} is expected to be longer than the screen`).toBe(true);
      expect(before.gutter, "scrollbars take no room in this browser: the test cannot see a shift").toBeGreaterThan(0);

      await tabbed.reveal?.(page);
      await tabbed.short(page).click();
      await expect(page).toHaveURL(tabbed.shortUrl);
      await settle(page);
      const after = await column(page);

      expect({ x: after.x, width: after.width }, `the content column moved switching from ${tabbed.long}`).toEqual({
        x: before.x,
        width: before.width,
      });
    });
  }
}
