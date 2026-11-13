/**
 * The Docker zone of Server against the real backend (item 189), on the machine `console_server.py --docker` models: an engine with
 * two applications of Noust (one whose service keeps falling because memory runs out), a stack nobody of Noust made, a container
 * somebody stopped, images with no name and a volume of a wiki that no longer runs. Every page in both themes with axe, the CSP and
 * console gates; the owner of each thing in view; what an application's container offers (its logs and the way to the application,
 * never a verb the server would refuse); and the one thing that loses data for good, which asks for its name.
 *
 * With `NOUST_SCREENS=1` it also writes the screens for review, at 1440x900 and 390 wide, into $NOUST_DOCKER_SCREENS.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test as base } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const test = base.extend<object, { consoleServer: ConsoleServer }>({
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

const OUT = process.env.NOUST_DOCKER_SCREENS ?? path.join(import.meta.dirname, "__screens__", "docker");
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

interface ReadyContext {
  server: ConsoleServer;
  /** Says a console error is the answer the page is meant to get (sudo mode asks once, with a 403). */
  expectError: (pattern: RegExp) => void;
}

interface Screen {
  name: string;
  path: string;
  heading: string | RegExp;
  /** What the page needs before it is looked at: what a person does to reach the screen. `context` has what an action of root-equivalent weight needs. */
  ready?: (page: Page, context: ReadyContext) => Promise<void>;
}

const SCREENS: readonly Screen[] = [
  { name: "summary", path: "/server/docker", heading: "Docker", ready: async (page) => {
      await expect(page.getByRole("region", { name: "Needs you" })).toBeVisible();
    },
  },
  { name: "stacks", path: "/server/docker/stacks", heading: "Stacks", ready: async (page) => {
      await expect(page.getByText("nextcloud").first()).toBeVisible();
    },
  },
  { name: "stack-nobody", path: "/server/docker/stacks/nextcloud", heading: "nextcloud" },
  { name: "stack-noust", path: "/server/docker/stacks/shop-example-net", heading: "shop-example-net" },
  { name: "compose", path: "/server/docker/compose/shop-example-net", heading: "Compose of shop-example-net" },
  {
    name: "bring",
    path: "/server/docker/stacks",
    heading: "Stacks",
    ready: async (page, { server, expectError }) => {
      // What Noust would register is read through a root-equivalent call: the server asks to confirm once, and the dialog opens after it.
      // The stack of the sandbox runs from a folder this machine does not have, so the answer is the server's own refusal, said as it said it.
      expectError(/status of 403 .*\/api\/docker\/stacks\/nextcloud\/bring$/);
      expectError(/status of 409 .*\/api\/docker\/stacks\/nextcloud\/bring$/);
      await page.getByRole("button", { name: "Bring to Noust…" }).first().click();
      await confirmItsYou(page, server);
      const dialog = page.getByRole("alertdialog", { name: "Bring nextcloud to Noust" });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByText(/The folder of nextcloud is not on this server/)).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Bring to Noust" })).toBeDisabled();
    },
  },
  { name: "containers", path: "/server/docker/containers", heading: "Containers", ready: async (page) => {
      await expect(page.getByRole("link", { name: "adminer" })).toBeVisible();
    },
  },
  { name: "container-loop", path: "/server/docker/containers/tools-example-net-api-1", heading: "tools-example-net-api-1" },
  { name: "container-nobody", path: "/server/docker/containers/adminer", heading: "adminer" },
  { name: "container-running", path: "/server/docker/containers/nextcloud-app-1", heading: "nextcloud-app-1" },
  { name: "images", path: "/server/docker/images", heading: "Images" },
  { name: "volumes", path: "/server/docker/volumes", heading: "Volumes", ready: async (page) => {
      await expect(page.getByText("wiki_data")).toBeVisible();
    },
  },
  { name: "networks", path: "/server/docker/networks", heading: "Networks" },
  { name: "engine", path: "/server/docker/engine", heading: "Engine" },
  {
    name: "cleanup",
    path: "/server/docker",
    heading: "Docker",
    ready: async (page) => {
      await page.getByRole("button", { name: "Clean up…" }).click();
      await expect(page.getByRole("alertdialog", { name: "Clean up Docker" })).toBeVisible();
    },
  },
  {
    name: "volume-remove",
    path: "/server/docker/volumes",
    heading: "Volumes",
    ready: async (page) => {
      await page.getByRole("button", { name: "Actions for wiki_data" }).click();
      await page.getByRole("menuitem", { name: "Remove…" }).click();
      await expect(page.getByRole("alertdialog", { name: "Delete the volume wiki_data" })).toBeVisible();
    },
  },
];

test.describe("every page of the Docker zone", () => {
  for (const screen of SCREENS) {
    test(`${screen.name} has no WCAG 2.2 AA violations, no CSP violation and no console error`, async ({ page, consoleServer, problems }) => {
      await page.setViewportSize(DESKTOP);
      await signIn(page, consoleServer, screen.path);
      await expect(page.getByRole("heading", { level: 1, name: screen.heading })).toBeVisible();
      await screen.ready?.(page, { server: consoleServer, expectError: problems.expect });
      await settle(page);
      await expectNoA11yViolations(page, screen.name);
    });
  }

  test("a phone reads the lists as cards and the page never scrolls sideways", async ({ page, consoleServer }) => {
    await page.setViewportSize(PHONE);
    await signIn(page, consoleServer, "/server/docker/containers");
    await expect(page.getByRole("link", { name: "adminer" })).toBeVisible();
    await settle(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expectNoA11yViolations(page, "containers on a phone");
  });
});

test.describe("whose each thing is, and what it offers", () => {
  test("an application's container is read here and changed from the application", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/server/docker/containers?app=shop.example.net");
    const row = page.getByRole("row").filter({ hasText: "shop-example-net-web-1" });
    // The kind of owner is read after the name, and a browser lays the two out as flex items: a space may come before the comma.
    await expect(row.getByRole("link", { name: /shop\.example\.net\s*,\s*Application/ })).toBeVisible();
    await row.getByRole("button", { name: "Actions for shop-example-net-web-1" }).click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem", { name: "See logs" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: /Change from shop\.example\.net/ })).toBeVisible();
    // "Stop" is also a word of the owner's line ("stopping"): the item is the one named exactly so.
    await expect(menu.getByRole("menuitem", { name: "Stop", exact: true })).toHaveCount(0);
    await expect(menu.getByRole("menuitem", { name: /^Remove/ })).toHaveCount(0);
  });

  test("what nobody made is said so, and what is running offers everything", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/server/docker/containers?owner=nobody");
    const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: "nextcloud-app-1", exact: true }) });
    await expect(row.getByText("Noust did not create it")).toBeVisible();
    await row.getByRole("button", { name: "Actions for nextcloud-app-1" }).click();
    for (const verb of ["See logs", "Open console", "Restart", "Stop", "Remove…"]) await expect(page.getByRole("menuitem", { name: verb, exact: true })).toBeVisible();
  });

  test("what is stopped offers to start it, and no console", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/server/docker/containers?owner=nobody");
    const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: "adminer", exact: true }) });
    await row.getByRole("button", { name: "Actions for adminer" }).click();
    for (const verb of ["See logs", "Start", "Remove…"]) await expect(page.getByRole("menuitem", { name: verb, exact: true })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Open console", exact: true })).toHaveCount(0);
  });

  test("a container that keeps falling says why, in the engine's own words", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/server/docker/containers/tools-example-net-api-1");
    await expect(page.getByText(/It exited with 137: it ran out of memory/).first()).toBeVisible();
    // Said twice on purpose: in what it said last, as it printed it, and in the live output.
    await expect(page.getByText("FATAL ERROR: Reached heap limit Allocation failed").first()).toBeVisible();
  });
});

test.describe("what loses data asks for its name", () => {
  test("a volume is not removed until its name is typed, and then it is gone from the list", async ({ page, consoleServer, problems }) => {
    // Removing is root-equivalent: the server answers 403 once, asks "Confirm it's you" and is asked again.
    problems.expect(/status of 403 .*\/api\/docker\/volumes\/wiki_data$/);
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/server/docker/volumes");
    await page.getByRole("button", { name: "Actions for wiki_data" }).click();
    await page.getByRole("menuitem", { name: "Remove…" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete the volume wiki_data" });
    await expect(dialog.getByRole("button", { name: "Delete volume" })).toBeDisabled();
    await dialog.getByRole("textbox").fill("wiki_data");
    await dialog.getByRole("button", { name: "Delete volume" }).click();
    await confirmItsYou(page, consoleServer);
    await expect(page.getByRole("row").filter({ hasText: "wiki_data" })).toHaveCount(0);
  });

  test("the volumes of an application offer no removal: they go with the application", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/server/docker/volumes?owner=app");
    const first = page.getByRole("button", { name: /^Actions for / }).first();
    await first.click();
    await expect(page.getByRole("menuitem", { name: /^Remove|^Delete/ })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: /^Change from/ })).toBeVisible();
  });
});

test.describe("the cleanup is a plan", () => {
  test("it says what would go and whose it is before anything does, and a volume asks for its name", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/server/docker");
    await page.getByRole("button", { name: "Clean up…" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Clean up Docker" });
    await expect(dialog.getByText("Images with no name and no use")).toBeVisible();
    await expect(dialog.getByText("Noust did not create it").first()).toBeVisible();
    // A volume is offered, never ticked by itself, and asks for its name when ticked. The one nobody named is used here: the worker's
    // server is shared with the test that deletes the wiki's, and a plan is only read in this one, nothing of it is confirmed.
    const volume = dialog.getByRole("checkbox", { name: /^0123456789abcdef/ });
    await volume.scrollIntoViewIfNeeded();
    await expect(volume).not.toBeChecked();
    await volume.click();
    const action = dialog.getByRole("button", { name: /^Remove what is ticked/ });
    await expect(action).toBeDisabled();
    await dialog.getByRole("textbox", { name: /^Type 0123456789abcdef/ }).fill("0123456789abcdef".repeat(4));
    await expect(action).toBeEnabled();
  });
});

if (process.env.NOUST_SCREENS === "1") {
  test.describe("the kit pieces of Docker @screens", () => {
    test("the gallery sections of the owner, the version, the object menu and the diff", async ({ page, consoleServer }, testInfo) => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await signIn(page, consoleServer);
      await page.goto("/__design");
      const gallery = page.locator('nav[aria-label="Design system"]');
      const present = await gallery
        .waitFor({ state: "attached", timeout: 5_000 })
        .then(() => true)
        .catch(() => false);
      test.skip(!present, "The gallery is only in a development build.");
      await settle(page);
      await page.evaluate(() => {
        for (const bar of document.querySelectorAll<HTMLElement>("header.sticky")) bar.style.display = "none";
      });
      for (const id of ["kd-owner", "kd-version", "kd-menu", "kd-diff"]) {
        const section = page.locator(`#${id}`);
        await section.scrollIntoViewIfNeeded();
        await expect(section).toBeVisible();
        await section.screenshot({ path: path.join(OUT, testInfo.project.name, `kit-${id}.png`) });
      }
    });
  });

  test.describe("the Docker zone @screens", () => {
    for (const screen of SCREENS) {
      test(`docker ${screen.name}`, async ({ page, consoleServer, problems }, testInfo) => {
        const dir = path.join(OUT, testInfo.project.name);
        await page.setViewportSize(DESKTOP);
        await signIn(page, consoleServer, screen.path);
        await expect(page.getByRole("heading", { level: 1, name: screen.heading })).toBeVisible();
        await screen.ready?.(page, { server: consoleServer, expectError: problems.expect });
        await settle(page);
        await page.waitForTimeout(500);
        await page.screenshot({ path: path.join(dir, `${screen.name}-1440.png`) });
        await page.setViewportSize(PHONE);
        await settle(page);
        await page.waitForTimeout(400);
        await page.screenshot({ path: path.join(dir, `${screen.name}-390.png`) });
      });
    }
  });
}
