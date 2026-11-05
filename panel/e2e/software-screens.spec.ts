/**
 * Screenshots of the server's software for review (@screens, left out of the default run): the page, the three ways
 * removing can go (a question, the server's refusal, a piece that was not Noust's), and the notice that says a page lacks
 * a piece, with its install failing, and the software of a fleet; at 1440 and at 390, in the project's theme.
 *
 *   NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/software-screens npx playwright test software-screens
 *
 * It runs on a console server of its own, like software.spec.ts: the install at the end changes the modelled machine.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { confirmItsYou, expect, settle, signIn, startConsoleServer, test as base } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const OUT = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__", "software");
const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

const test = base.extend<object, { own: ConsoleServer }>({
  own: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "200"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  baseURL: async ({ own }, use) => {
    await use(own.url);
  },
});

async function shot(page: Page, theme: string, size: string, name: string): Promise<void> {
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}-${theme}-${size}.png`) });
}

for (const size of SIZES) {
  test(`@screens the software of the server at ${size.name}`, async ({ page, own, problems }, testInfo) => {
    test.setTimeout(180_000);
    problems.expect(/status of 403 .*\/api\/server\/software\/cloudflared\/install$/);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
    await signIn(page, own, "/server/software");
    // A table on a desk, a list of cards on a phone.
    await expect(page.getByRole("table", { name: "Software: The machine" }).or(page.getByRole("list", { name: "Software: The machine" }))).toBeVisible();
    await shot(page, theme, size.name, "20-software");

    // A piece in use: the server refuses, with its reason and what uses it.
    await page.getByRole("button", { name: "Remove nginx" }).click();
    await expect(page.getByRole("dialog", { name: "nginx cannot be removed now" })).toBeVisible();
    await shot(page, theme, size.name, "22-software-refused");
    await page.getByRole("button", { name: "Understood" }).click();

    // What Noust put there and nothing uses: one question.
    await page.getByRole("button", { name: "Remove fail2ban" }).click();
    await expect(page.getByRole("alertdialog", { name: "Remove fail2ban" }).getByText("python3-pyinotify")).toBeVisible();
    await shot(page, theme, size.name, "21-software-remove");
    await page.getByRole("button", { name: "Cancel" }).click();

    // What was there before Noust: a yes and the name.
    await page.getByRole("button", { name: "Remove chrony" }).click();
    await expect(page.getByRole("alertdialog", { name: "Remove chrony" }).getByRole("checkbox")).toBeVisible();
    await shot(page, theme, size.name, "23-software-not-noust");
    await page.getByRole("button", { name: "Cancel" }).click();

    // A page that lacks a piece says so where its own warning was.
    await page.goto("/network");
    await expect(page.getByText("nginx stream module is missing")).toBeVisible();
    await shot(page, theme, size.name, "24-missing-ports");
    await page.goto("/network/tunnels");
    await expect(page.getByText("cloudflared is missing")).toBeVisible();
    await shot(page, theme, size.name, "25-missing-tunnels");

    // And its install, which this machine cannot finish: the tool's own words, and the way to try again.
    await page.getByRole("button", { name: "Install" }).click();
    await confirmItsYou(page, own);
    await expect(page.getByText("cloudflared was not installed")).toBeVisible({ timeout: 45_000 });
    await shot(page, theme, size.name, "25-missing-tunnels-failed");
  });
}

/** A central and a node, both of them this build, for what the fleet says of every server's software. */
const fleet = base.extend<object, { node: ConsoleServer; central: ConsoleServer }>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([]);
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  central: [
    async ({ node }, use) => {
      const central = await startConsoleServer(["--totp", "--backup-codes", "200", "--fleet-node", `fleet-node=127.0.0.1:${new URL(node.url).port}`]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
  baseURL: async ({ central }, use) => {
    await use(central.url);
  },
});

for (const size of SIZES) {
  fleet(`@screens the software of the fleet at ${size.name}`, async ({ page, central }, testInfo) => {
    test.setTimeout(180_000);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
    await signIn(page, central, "/");
    // The first request a central makes to a node opens the tunnel, which takes seconds: it is spent here, once.
    const reached = await page.request.get("/api/nodes/fleet-node/api/system/version", { timeout: 90_000 });
    expect(reached.ok(), await reached.text()).toBe(true);
    await page.goto("/fleet/software");
    await expect(page.getByRole("link", { name: "Open the software of fleet-node" })).toBeVisible({ timeout: 60_000 });
    await shot(page, theme, size.name, "26-software-fleet");
  });
}
