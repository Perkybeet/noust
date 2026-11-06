/**
 * Screenshots of the Applications area for review (4.2, F3-APPS): the list read four ways, the five ways to start a new
 * application, an application that fails and the way to why, the page of a first deploy, the four pages of
 * configuration, Export, Several servers and Delete. Both themes (the light and dark projects) at 1440 and 1920 wide
 * and on a 390px phone, each one the first screen as a person sees it and the whole page below it. Tagged @screens, so left out
 * of the default run; write them with
 *
 *     NOUST_SCREENS=1 NOUST_APPS_SCREENS=/tmp/apps-screens npx playwright test e2e/apps-area-screens.spec.ts
 *
 * into $NOUST_APPS_SCREENS/<theme>/<name>-<width>.png (the first screen) and <name>-<width>-full.png (the page).
 * The pages still pass the CSP and console gates of every test, so a screenshot is never of a page that is quietly broken.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import path from "node:path";

import { openFold } from "./part-folds";
import { expect, settle, signIn, test } from "./fixtures";

const OUT = process.env.NOUST_APPS_SCREENS ?? "/tmp/apps-screens";

const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1920", width: 1920, height: 1080 },
  { name: "390", width: 390, height: 844 },
] as const;

interface Screen {
  name: string;
  path: string;
  /** What the page is made to show before it is photographed. */
  ready?: (page: import("@playwright/test").Page) => Promise<void>;
}

const SCREENS: readonly Screen[] = [
  { name: "apps-all", path: "/apps" },
  { name: "apps-projects", path: "/apps?view=projects" },
  { name: "apps-states", path: "/apps?view=states" },
  { name: "apps-deployments", path: "/apps?view=deployments" },
  { name: "new-repo", path: "/apps/new" },
  {
    name: "new-files",
    path: "/apps/new",
    ready: async (page) => {
      await page.getByRole("radio", { name: /^Upload files/ }).click();
      await expect(page.getByRole("button", { name: "Compose" })).toBeVisible();
    },
  },
  {
    name: "new-existing",
    path: "/apps/new",
    ready: async (page) => {
      await page.getByRole("radio", { name: /^Something that already runs here/ }).click();
      await expect(page.getByRole("radio", { name: "Running on this server" })).toBeChecked();
    },
  },
  { name: "app-summary", path: "/apps/shop.example.net" },
  { name: "app-failing", path: "/apps/clientes.example.com" },
  { name: "app-code", path: "/apps/shop.example.net/code" },
  { name: "app-triggers", path: "/apps/tienda.example.org/triggers" },
  { name: "app-resources", path: "/apps/shop.example.net/resources" },
  { name: "app-access", path: "/apps/tienda.example.org/access" },
  { name: "app-export", path: "/apps/shop.example.net/export" },
  { name: "app-servers", path: "/apps/tienda.example.org/servers" },
  { name: "app-delete", path: "/apps/shop.example.net/delete" },
  {
    name: "app-triggers-previews-open",
    path: "/apps/portal.example.org/triggers",
    ready: async (page) => {
      await openFold(page, "Previews");
    },
  },
];

test.describe("apps area @screens", () => {
  for (const screen of SCREENS) {
    test(`apps area ${screen.name}`, async ({ page, consoleServer }, testInfo) => {
      test.setTimeout(120_000);
      const dir = path.join(OUT, testInfo.project.name);
      await page.setViewportSize({ width: 1440, height: 900 });
      await signIn(page, consoleServer, "/apps");
      for (const size of SIZES) {
        await page.setViewportSize({ width: size.width, height: size.height });
        await page.goto(screen.path);
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        await screen.ready?.(page);
        await settle(page);
        // Streams and skeletons settle a beat after the network goes idle.
        await page.waitForTimeout(700);
        // What a person is asked to read: the words of the page's main area that are drawn (a folded part is not), and how tall it is.
        const measured = await page.evaluate(() => {
          const main = document.querySelector("main");
          const words = (main?.innerText ?? "").split(/\s+/).filter((word) => word !== "").length;
          return { words, height: Math.ceil(document.documentElement.scrollHeight) };
        });
        mkdirSync(dir, { recursive: true });
        appendFileSync(path.join(dir, "measures.tsv"), `${screen.name}\t${size.name}\t${String(measured.words)}\t${String(measured.height)}\n`);
        await page.screenshot({ path: path.join(dir, `${screen.name}-${size.name}.png`) });
        await page.screenshot({ path: path.join(dir, `${screen.name}-${size.name}-full.png`), fullPage: true });
      }
    });
  }
});
