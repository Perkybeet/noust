/**
 * A table answers to the room its page gives it, and the actions at the end of its rows are never cut (docs/DESIGN.md 6.8). A laptop is
 * 1280, 1366 or 1440 pixels wide and the console keeps two columns of navigation open beside the page, so a table has 816 to 1010
 * pixels, not the window's: at 1440 the menu of a container's row was cut in half by the table's edge, because the table measured the
 * window and not its container. Every page of the console is opened at the three widths and every table in it is read as a person
 * sees it: the cell of actions of each row sits whole inside its table, a cell that was cut to make room says what it holds in a
 * `title`, and the page itself never scrolls sideways (L-4).
 *
 * The Docker zone is read on the machine `console_server.py --docker` models, where it has something to list.
 */

import type { Page } from "@playwright/test";

import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { ROUTES, routePath } from "./routes";

const LAPTOPS = [1280, 1366, 1440] as const;

/** The pages of the Docker zone that hold a table: not in ROUTES, which is the machine without Docker. */
const DOCKER = ["/server/docker/containers", "/server/docker/images", "/server/docker/volumes", "/server/docker/networks", "/server/docker/stacks"] as const;

/** What is wrong with the tables on the page as it is, in words, or nothing. */
async function problemsWithTables(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    for (const table of document.querySelectorAll<HTMLElement>("main table")) {
      const name = table.querySelector("caption")?.textContent ?? "a table";
      const region = table.closest<HTMLElement>('[role="region"]');
      if (region === null) continue;
      const edge = region.getBoundingClientRect();
      const heads = [...table.querySelectorAll("thead th")];
      const hasActions = heads.at(-1)?.querySelector(".sr-only")?.textContent.toLowerCase().includes("action") === true;
      if (hasActions) {
        for (const row of table.querySelectorAll("tbody tr")) {
          const cell = row.lastElementChild;
          if (cell === null) continue;
          const box = cell.getBoundingClientRect();
          // Whole inside the table: a menu cut at the edge was what a person saw at 1440.
          if (box.right > edge.right + 1 || box.left < edge.left - 1) found.push(`${name}: the cell of actions of a row is cut (${String(Math.round(box.left))} to ${String(Math.round(box.right))}, the table is ${String(Math.round(edge.left))} to ${String(Math.round(edge.right))})`);
          // Its controls whole as well, not wrapped over two lines by a column that was squeezed.
          const controls = cell.querySelectorAll("button");
          if (controls.length > 1) {
            const tops = new Set([...controls].map((control) => Math.round(control.getBoundingClientRect().top)));
            if (tops.size > 1) found.push(`${name}: the controls of a row's actions wrap over more than one line`);
          }
          break;
        }
      }
      for (const cut of table.querySelectorAll<HTMLElement>("td[data-cut]")) {
        if (cut.scrollWidth > cut.clientWidth + 1 && cut.title === "") found.push(`${name}: a cell was cut and does not say what it holds`);
      }
    }
    const sideways = document.documentElement.scrollWidth - document.documentElement.clientWidth;
    if (sideways > 0) found.push(`the page scrolls sideways by ${String(sideways)} pixels`);
    return [...new Set(found)];
  });
}

async function readAt(page: Page, target: string): Promise<void> {
  await page.setViewportSize({ width: LAPTOPS[0], height: 900 });
  await page.goto(target);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);
  for (const width of LAPTOPS) {
    // The same page made wider is the table's own measuring again: nothing is loaded a second time.
    await page.setViewportSize({ width, height: 900 });
    await settle(page);
    expect(await problemsWithTables(page), `${target} at ${String(width)} pixels`).toEqual([]);
  }
}

test.describe("the tables of the console at a laptop's width", () => {
  for (const route of ROUTES) {
    test(`${route.name} keeps the actions of every row in view`, async ({ page, consoleServer }) => {
      await signIn(page, consoleServer);
      await readAt(page, await routePath(page, route));
    });
  }
});

const docker = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "300", "--docker"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 120_000 },
  ],
});

docker.describe("the tables of the Docker zone at a laptop's width", () => {
  for (const path of DOCKER) {
    docker(`${path} keeps the actions of every row in view`, async ({ page, consoleServer }) => {
      await signIn(page, consoleServer);
      await readAt(page, path);
    });
  }
});
